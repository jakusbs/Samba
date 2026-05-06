#!/usr/bin/env python3
"""
samba_cryo.py — Samba Cryo — ETH Zürich Intermag Lab
Cryostat-specific fork of Samba.

Differences from standard samba.py:
  • Single setup: "Cryo" only (no Green/IR pill buttons)
  • CryoHardwarePanel: Keithley + AttoDRY (replaces Field & Relay)
  • No DC_HYST scan mode — replaced by Temperature Sweep (uses FIELD engine)
  • Polls AttoDRY for field + temperature readbacks
  • Cryo-blue branding, separate QSettings key
  • CryoMonitor dialog accessible from hardware panel

Shared (unchanged): scan.py, plot_widgets.py, data_browser.py, hardware.py,
                      calibration.py, device_registry.py, config.py
"""
import sys, os, copy, logging, threading, time as _time
from logging.handlers import RotatingFileHandler
from pathlib import Path
import numpy as np

# Ensure repo root is on sys.path so that `import core` resolves correctly,
# regardless of the working directory when the script is launched.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Use a cryo-specific config dir so it doesn't mix with standard Samba.
# Set before any config imports so CONFIG_DIR picks it up.
os.environ.setdefault("SAMBA_CONFIG_DIR",
                      str(Path.home() / ".config" / "moke_scan_cryo"))
from typing import Dict, Optional, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QTabWidget, QTabBar, QTextEdit, QMessageBox, QSplitter,
    QComboBox, QLineEdit, QPushButton, QFileDialog, QButtonGroup, QFrame
)
from PyQt6.QtCore import QTimer, QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QShortcut, QKeySequence, QTextCharFormat, QColor, QTextCursor, QIcon

log = logging.getLogger(__name__)

try:
    import tango
    TANGO_AVAILABLE = True
except ImportError:
    TANGO_AVAILABLE = False

from config  import SETUP_NAMES, X_NATURAL, X_TIME, DEFAULT_SENSORS, load_setup, save_setup, make_default_config
from hardware import get_proxy, safe_read, evict_proxy, _pcache
from scan    import ScanWorker, ScanlistWorker
from lab_notebook import append_measurement, notebook_path as _nb_path
from plot_widgets import Live2DWidget, Live1DWidget
from panels  import (ConfigListPanel, RightPanel,
                     TrajectoryPanel, ScanlistPanel)
from panels_cryo import CryoHardwarePanel
from data_browser import DataBrowserPanel
from script_console import ScriptConsolePanel
from calibration import CryoCalibrationPanel
from device_registry import DeviceRegistryPanel, load_registry, registry_to_sensors
from defaults_panel  import SetupDefaultsPanel
import play_intro

try:
    from setup_lock import acquire_lock, release_lock
except Exception:
    def acquire_lock(name): return True, ""   # type: ignore[misc]
    def release_lock(name): pass              # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# Hardware snapshot helper
# ─────────────────────────────────────────────────────────────────────────────

def _read_hw_snapshot(setup: dict, scan_type: str, is_temp_sweep: bool = False) -> dict:
    """Read key hardware state immediately before a Cryo scan starts.

    ``scan_type`` == "FIELD" suppresses hw_field_mT (field is being swept).
    ``is_temp_sweep`` == True suppresses hw_temperature_K (temp is being swept).
    """
    snap: dict = {}

    def _read(device_path: str, attr: str):
        if not device_path or not attr:
            return None
        try:
            p = get_proxy(device_path)
            val, rerr = safe_read(p, attr)
            return val if not rerr else None
        except Exception:
            return None

    # Keithley AC excitation state
    k_dev = setup.get("keithley_device", "")
    for hw_key, attr_key in [
        ("hw_keithley_amplitude_mA",  "keithley_amplitude_attr"),
        ("hw_keithley_frequency_Hz",  "keithley_frequency_attr"),
        ("hw_keithley_range",         "keithley_range_attr"),
        ("hw_keithley_compliance_V",  "keithley_compliance_attr"),
    ]:
        v = _read(k_dev, setup.get(attr_key, ""))
        if v is not None:
            snap[hw_key] = v

    # Lock-in amplifier settings
    zi_dev = setup.get("zi_device", "")
    for hw_key, attr_key in [
        ("hw_zi_tc_s",       "zi_tc_attr"),
        ("hw_zi_order",      "zi_order_attr"),
        ("hw_zi_settling_s", "zi_settling_attr"),
    ]:
        v = _read(zi_dev, setup.get(attr_key, ""))
        if v is not None:
            snap[hw_key] = v

    # Field at scan start — skip when field is swept
    if scan_type != "FIELD" and not is_temp_sweep:
        v = _read(setup.get("attodry_device", ""),
                  setup.get("attodry_attr_field_rb", "MagneticField"))
        if v is not None:
            snap["hw_field_mT"] = v * 1000.0  # T → mT

    # Temperature — skip when temperature is being swept
    if not is_temp_sweep:
        v = _read(setup.get("attodry_device", ""),
                  setup.get("attodry_attr_temp_rb", "Temperature"))
        if v is not None:
            snap["hw_temperature_K"] = v

    # Stage position at scan start
    v = _read(setup.get("act1_device", ""), setup.get("act1_attr", ""))
    if v is not None:
        snap["hw_act1_pos"] = v
    v = _read(setup.get("act2_device", ""), setup.get("act2_attr", ""))
    if v is not None:
        snap["hw_act2_pos"] = v

    return snap


# ─────────────────────────────────────────────────────────────────────────────
# Stylesheet — cryo-blue accent
# ─────────────────────────────────────────────────────────────────────────────
CRYO_STYLE = """
QMainWindow,QWidget{background:#1e1e2e;color:#cdd6f4;
  font-family:'Segoe UI',Ubuntu,sans-serif;font-size:12px;}
QGroupBox{border:1px solid #45475a;border-radius:6px;
  margin-top:9px;padding-top:9px;font-weight:bold;color:#0080fe;}
QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}
QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox{
  background:#313244;border:1px solid #45475a;
  border-radius:4px;padding:3px 6px;color:#cdd6f4;}
QLineEdit:focus,QDoubleSpinBox:focus,QSpinBox:focus{border:1px solid #0080fe;}
QPushButton{background:#313244;border:1px solid #45475a;
  border-radius:5px;padding:5px 12px;color:#cdd6f4;}
QPushButton:hover{background:#45475a;}
QPushButton:pressed{background:#585b70;}
QPushButton:checked{background:#585b70;border:1px solid #0080fe;color:#cdd6f4;}
QPushButton#start_btn{background:#a6e3a1;color:#1e1e2e;font-weight:bold;border:none;border-radius:5px;padding:0 12px;}
QPushButton#start_btn:hover{background:#94d992;}
QPushButton#abort_btn{background:#f38ba8;color:#1e1e2e;font-weight:bold;border:none;border-radius:5px;padding:0 12px;}
QPushButton#abort_btn:hover{background:#e07a97;}
QPushButton#pause_btn{background:#fab387;color:#1e1e2e;font-weight:bold;border:none;border-radius:5px;padding:0 12px;}
QPushButton#pause_btn:hover{background:#e8976e;}
QProgressBar{background:#313244;border:1px solid #45475a;
  border-radius:4px;text-align:center;color:#cdd6f4;}
QProgressBar::chunk{background:#0080fe;border-radius:3px;}
QTextEdit{background:#12121f;border:1px solid #313244;
  border-radius:4px;color:#a6e3a1;font-family:'Courier New',monospace;font-size:10px;}
QCheckBox{spacing:6px;}
QCheckBox::indicator{width:14px;height:14px;
  border:1px solid #45475a;border-radius:3px;background:#313244;}
QCheckBox::indicator:checked{background:#0080fe;}
QRadioButton{spacing:5px;}
QRadioButton::indicator{width:14px;height:14px;}
QTabWidget::pane{border:1px solid #45475a;border-radius:5px;}
QTabBar::tab{background:#313244;padding:6px 14px;border-radius:4px 4px 0 0;color:#6c7086;}
QTabBar::tab:selected{background:#45475a;color:#cdd6f4;}
QListWidget{background:#181825;border:1px solid #313244;border-radius:4px;}
QListWidget::item{padding:5px 7px;}
QListWidget::item:selected{background:#45475a;color:#cdd6f4;}
QScrollBar:vertical{background:#1e1e2e;width:8px;border-radius:4px;}
QScrollBar::handle:vertical{background:#45475a;border-radius:4px;}
QScrollBar:horizontal{background:#1e1e2e;height:8px;border-radius:4px;}
QScrollBar::handle:horizontal{background:#45475a;border-radius:4px;}
QSplitter::handle{background:#313244;}
QSplitter::handle:horizontal{width:4px;}
QSplitter::handle:vertical{height:4px;}
"""

CRYO_SETUP = "Cryo"

# ── Named tab indices (avoid magic numbers) ──────────────────────────────
# Bottom tabs
TAB_TRAJECTORY   = 0
TAB_SCANLIST     = 1
TAB_DATA_BROWSER = 2
TAB_SCRIPT       = 3
TAB_DEV_REGISTRY = 4
TAB_DEFAULTS     = 5

# Live (top) tabs
TAB_MAP2D       = 0
TAB_PLOT1D      = 1
TAB_CALIBRATION = 2
TAB_LOG         = 3


# ─────────────────────────────────────────────────────────────────────────────
# ReadbackWorker — polls TANGO devices off the GUI thread (#9)
# ─────────────────────────────────────────────────────────────────────────────
class ReadbackWorker(QThread):
    """Polls AttoDRY + optional AC monitor + calibration stage positions
    on a background thread, emitting results via signals."""

    attodry_readback = pyqtSignal(object, object, object, object)  # fld, tmp, vti, mgt
    fallback_field   = pyqtSignal(object)                          # field from magnet_device
    ac_monitor       = pyqtSignal(object)                          # monitor value
    stage_positions  = pyqtSignal(dict)                            # {axis: value}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        # These are set from the main thread before each poll cycle
        self.setup: dict = {}
        self.scan_running: bool = False
        self.scan_cfg: dict = {}
        self.monitor_device: str = ""
        self.monitor_attr: str = ""
        self.poll_calib: bool = False
        self.calib_axis_info: dict = {}

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            self._poll_once()
            self.msleep(500)

    def _poll_once(self):
        setup = self.setup
        if not setup:
            return

        # ── AttoDRY field + temperatures ──────────────────────────────────
        ad_dev = setup.get("attodry_device", "")
        if ad_dev:
            p = get_proxy(ad_dev)
            fld, _ = safe_read(p, "MagneticField")
            tmp, _ = safe_read(p, "Temperature")
            vti, _ = safe_read(p, "VtiTemperature")
            mgt, _ = safe_read(p, "MagnetTemperature")
            self.attodry_readback.emit(fld, tmp, vti, mgt)
        else:
            dev = setup.get("magnet_device", "")
            fld_attr = setup.get("magnet_field_attr", "field_polar_corr")
            if dev:
                p = get_proxy(dev)
                v, _ = safe_read(p, fld_attr)
                self.fallback_field.emit(v)

        # ── AC field monitor during field scan ────────────────────────────
        if self.scan_running and self.scan_cfg:
            scan_t = self.scan_cfg.get("scan_type", "")
            if scan_t == "FIELD" and self.monitor_device and self.monitor_attr:
                mp = get_proxy(self.monitor_device)
                mv, _ = safe_read(mp, self.monitor_attr)
                self.ac_monitor.emit(mv)

        # ── Stage positions for calibration tab ───────────────────────────
        if self.poll_calib:
            vals = {}
            for axis_key in ("x", "y", "z"):
                dev, attr = self.calib_axis_info.get(axis_key, ("", ""))
                if dev:
                    p = get_proxy(dev)
                    v, _ = safe_read(p, attr)
                    vals[axis_key] = v
            self.stage_positions.emit(vals)


# ─────────────────────────────────────────────────────────────────────────────
# CryoMainWindow
# ─────────────────────────────────────────────────────────────────────────────
class CryoMainWindow(QMainWindow):
    # Used to safely post callables to the main thread from background threads.
    # See also: hardware_panel.py for the same pattern.
    _post_to_main = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._post_to_main.connect(lambda fn: fn())
        self.setWindowTitle("Samba Cryo — ETH Zürich")
        self.setMinimumSize(1360, 920)

        self._setups:            Dict[str, dict]          = {}
        self._worker:            Optional[ScanWorker]     = None
        self._sl_worker:         Optional[ScanlistWorker] = None
        self._scan_running:      bool                     = False
        self._scan_data:         Dict[str, np.ndarray]    = {}
        self._last_fn:           Optional[str]            = None
        self._active_setup_name: str                      = CRYO_SETUP
        self._active_cfg_idx:    int                      = 0
        self._current_scan_cfg:  dict                     = {}
        self._calib_timescan:    bool                     = False
        self._scan_start_time:   float                    = 0.0
        self._scan_total_pts:    int                      = 0
        self._dir_queue:         list                     = []   # pending direction cfgs

        # Only load Cryo setup
        self._setups[CRYO_SETUP] = load_setup(CRYO_SETUP)

        self.setStyleSheet(CRYO_STYLE)
        self._build_ui()
        self._connect_signals()
        self._load_active_config()

        # Background readback thread (replaces GUI-thread QTimer) (#9)
        self._rb_worker = ReadbackWorker(self)
        self._rb_worker.attodry_readback.connect(self._on_attodry_readback)
        self._rb_worker.fallback_field.connect(self._on_fallback_field)
        self._rb_worker.ac_monitor.connect(self._on_ac_monitor)
        self._rb_worker.stage_positions.connect(self._on_stage_positions)
        self._rb_worker.setup = self._active_setup()
        self._rb_worker.start()

        # Lightweight GUI-thread timer just to push state into the worker
        self._rb_sync_timer = QTimer(self)
        self._rb_sync_timer.setInterval(400)
        self._rb_sync_timer.timeout.connect(self._sync_readback_state)
        self._rb_sync_timer.start()

        self._restore_geometry()

        # Read hardware panels once the window is shown
        QTimer.singleShot(400, self._initial_hw_read)

    def _probe_devices(self, status_callback=None):
        """Check critical hardware devices at startup and warn if any are unreachable.

        When *status_callback* is provided (a callable accepting a str), probes run
        in parallel background threads and the callback is invoked on the GUI thread
        as each result arrives — suitable for updating a splash screen.
        Without a callback, probes run sequentially (blocking) and a QMessageBox is
        shown for any unavailable devices.
        """
        import threading as _threading
        from hardware import fresh_proxy, is_sim_proxy
        setup = self._active_setup()

        candidates = {
            "Keithley": setup.get("keithley_device", ""),
            "AttoDRY":  setup.get("attodry_device",  ""),
        }
        configs = setup.get("configs", [])
        if configs:
            idx = setup.get("active_idx", 0)
            cfg = configs[min(idx, len(configs) - 1)]
            geo = cfg.get("geometry",   "Faraday")
            st  = cfg.get("stage_type", "anm200")
            piezo_block = setup.get(f"stage_{geo.lower()}", {}).get(st, {})
            stage_dev = piezo_block.get("act1_device", "")
            if stage_dev:
                candidates["Stage"] = stage_dev

        _PROBE_TIMEOUT = 6.0   # seconds — shorter than default CORBA timeout

        results: dict = {}
        threads: dict = {}
        for name, path in candidates.items():
            if not path:
                results[name] = (None, "no path configured")
                continue
            def _probe(n=name, p=path):
                results[n] = fresh_proxy(p)
            t = _threading.Thread(target=_probe, daemon=True)
            t.start()
            threads[name] = t

        if status_callback:
            # Parallel mode: poll from GUI thread so splash stays responsive
            status_callback(f"Checking {len(threads)} device(s)…")
            reported: set = set()
            deadline = _time.monotonic() + _PROBE_TIMEOUT + 2.0
            while len(reported) < len(threads) and _time.monotonic() < deadline:
                for name, t in threads.items():
                    if name not in reported and not t.is_alive():
                        reported.add(name)
                        proxy, err = results.get(name, (None, "timeout"))
                        ok = not err and not is_sim_proxy(proxy)
                        status_callback(f"{'✓' if ok else '⚠'} {name}: {'OK' if ok else 'unavailable'}")
                QApplication.instance().processEvents()
                _time.sleep(0.05)
            # Threads still running past deadline: mark as timeout
            for name in threads:
                if name not in reported:
                    results[name] = (None, "connection timed out")
        else:
            for t in threads.values():
                t.join(_PROBE_TIMEOUT)

        unavailable = []
        for name, path in candidates.items():
            proxy, err = results.get(name, (None, "timeout"))
            if not path:
                log.warning("Startup probe: %s — no device path configured", name)
                unavailable.append(f"{name}: no path")
            elif err or is_sim_proxy(proxy):
                log.warning("Startup probe: %s (%s) — %s", name, path, err or "unreachable")
                unavailable.append(f"{name} ({path})")
            else:
                log.info("Startup probe: %s (%s) — OK", name, path)

        if unavailable:
            log.warning("Startup probe: %d device(s) unavailable", len(unavailable))
            if not status_callback:
                msg = (
                    "The following devices could not be reached at startup:\n\n"
                    + "\n".join(f"  • {d}" for d in unavailable)
                    + "\n\nScans will run in simulation mode for these devices.\n"
                    "Check your TANGO_HOST and device server status."
                )
                QMessageBox.warning(self, "Hardware Unavailable", msg)
        else:
            log.info("Startup probe: all critical devices reachable")

    def _active_setup(self) -> dict:
        return self._setups[CRYO_SETUP]

    # ── UI layout ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        main_v  = QVBoxLayout(central); main_v.setContentsMargins(6, 6, 6, 6); main_v.setSpacing(4)

        # ── Action bar ────────────────────────────────────────────────────────
        action_bar = QWidget(); action_bar.setFixedHeight(44)
        action_bar.setObjectName("action_bar")
        action_bar.setStyleSheet(
            "#action_bar{background:#12121f;border:1px solid #313244;border-radius:6px;}")
        ab = QHBoxLayout(action_bar)
        ab.setContentsMargins(8, 4, 8, 4); ab.setSpacing(0)

        def _sep():
            f = QFrame(); f.setFrameShape(QFrame.Shape.VLine)
            f.setFixedWidth(1); f.setFixedHeight(26)
            f.setStyleSheet("background:#313244;border:none;")
            w = QWidget(); wl = QHBoxLayout(w)
            wl.setContentsMargins(8, 0, 8, 0); wl.addWidget(f)
            return w

        # ── Cryo label (no setup pills — single setup) ───────────────────────
        cryo_lbl = QLabel("❄  CRYO")
        cryo_lbl.setStyleSheet(
            "color:#0080fe;font-size:14px;font-weight:bold;padding:0 12px;")
        ab.addWidget(cryo_lbl)
        ab.addWidget(_sep())

        # ── Scan control buttons ──────────────────────────────────────────────
        _BTN_H = 30
        _style = self.style()
        from PyQt6.QtWidgets import QStyle
        _ico_play  = _style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        _ico_pause = _style.standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        _ico_stop  = _style.standardIcon(QStyle.StandardPixmap.SP_MediaStop)

        self.start_btn = QPushButton()
        self.start_btn.setObjectName("start_btn")
        self.start_btn.setFixedHeight(_BTN_H); self.start_btn.setMinimumWidth(90)
        self.start_btn.setIcon(_ico_play); self.start_btn.setText("Start")
        self.start_btn.setToolTip("Start the scan (F5)")

        self.pause_btn = QPushButton()
        self.pause_btn.setObjectName("pause_btn")
        self.pause_btn.setFixedHeight(_BTN_H); self.pause_btn.setMinimumWidth(90)
        self.pause_btn.setIcon(_ico_pause); self.pause_btn.setText("Pause")
        self.pause_btn.setToolTip("Pause the scan at the current point (click again to resume)")

        self.abort_btn = QPushButton()
        self.abort_btn.setObjectName("abort_btn")
        self.abort_btn.setFixedHeight(_BTN_H); self.abort_btn.setMinimumWidth(90)
        self.abort_btn.setIcon(_ico_stop); self.abort_btn.setText("Abort")
        self.abort_btn.setToolTip("Stop and cancel the scan — data acquired so far will be saved")

        for b in [self.start_btn, self.pause_btn, self.abort_btn]:
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ab.addWidget(b); ab.setSpacing(4)

        ab.addWidget(_sep())

        # ── Save directory ────────────────────────────────────────────────────
        ab.addSpacing(4)
        _dir_lbl = QLabel("Dir:")
        _dir_lbl.setStyleSheet("color:#0080fe;font-size:11px;font-weight:bold;")
        ab.addWidget(_dir_lbl); ab.addSpacing(4)
        self.save_dir = QLineEdit(os.path.expanduser("~/moke_data"))
        self.save_dir.setMinimumWidth(180); self.save_dir.setFixedHeight(28)
        self.save_dir.setPlaceholderText("Save directory…")
        self.save_dir.setStyleSheet(
            "QLineEdit{background:#313244;border:1px solid #585b70;border-radius:4px;"
            "padding:2px 6px;color:#cdd6f4;font-size:11px;}"
            "QLineEdit:focus{border:1px solid #0080fe;}")
        ab.addWidget(self.save_dir, stretch=1)
        ab.addSpacing(4)
        browse_btn = QPushButton("…")
        browse_btn.setFixedSize(28, 28)
        browse_btn.setStyleSheet(
            "QPushButton{background:#252538;border:1px solid #45475a;border-radius:4px;padding:0;}"
            "QPushButton:hover{background:#313244;}")
        browse_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        browse_btn.setToolTip("Browse for a different save directory")
        browse_btn.clicked.connect(self._browse_save_dir)
        ab.addWidget(browse_btn)

        main_v.addWidget(action_bar)

        # ── Main content ─────────────────────────────────────────────────────
        v_split = QSplitter(Qt.Orientation.Vertical)
        h_split = QSplitter(Qt.Orientation.Horizontal)

        self.cfg_list = ConfigListPanel()
        self.cfg_list.setMinimumWidth(140)
        h_split.addWidget(self.cfg_list)

        center = QWidget(); cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(4)

        self.live_tabs = QTabWidget()
        for title, widget_attr, widget_cls in [
                ("2D Map", "map2d",  Live2DWidget),
                ("1D Plot","plot1d", Live1DWidget),
        ]:
            tab = QWidget(); lay = QVBoxLayout(tab); lay.setContentsMargins(2, 4, 2, 0)
            w   = widget_cls(); setattr(self, widget_attr, w); lay.addWidget(w)
            self.live_tabs.addTab(tab, title)

        self.calib_panel = CryoCalibrationPanel(self._active_setup,
                                                  config_getter=self._build_full_config)
        self.live_tabs.addTab(self.calib_panel, "Calibration")
        self.live_tabs.currentChanged.connect(self._on_live_tab_changed)

        tlog = QWidget(); tlol = QVBoxLayout(tlog); tlol.setContentsMargins(2, 4, 2, 0)
        log_hdr = QHBoxLayout(); log_hdr.setSpacing(6)
        log_hdr.addWidget(QLabel("Filter:"))
        self.log_filter = QComboBox()
        self.log_filter.addItems(["All", "Errors only", "Warnings + Errors"])
        self.log_filter.setFixedWidth(140)
        log_hdr.addWidget(self.log_filter); log_hdr.addStretch()
        clear_btn = QLabel("<a style='color:#6c7086;font-size:10px;' href='#'>Clear</a>")
        clear_btn.linkActivated.connect(lambda: self.log_text.clear())
        log_hdr.addWidget(clear_btn)
        tlol.addLayout(log_hdr)
        self.log_text = QTextEdit(); self.log_text.setReadOnly(True); tlol.addWidget(self.log_text)
        self.live_tabs.addTab(tlog, "Log")
        cl.addWidget(self.live_tabs)

        pr = QHBoxLayout()
        self.pbar = QProgressBar(); self.pbar.setFixedHeight(16)
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color:#6c7086;font-size:11px;")
        self.status_lbl.setWordWrap(True)
        pr.addWidget(self.pbar, stretch=1); pr.addWidget(self.status_lbl, stretch=2)
        cl.addLayout(pr)
        h_split.addWidget(center)

        self.right_panel = RightPanel(); h_split.addWidget(self.right_panel)
        h_split.setSizes([215, 640, 480])
        h_split.setStretchFactor(0, 0)
        h_split.setStretchFactor(1, 1)
        h_split.setStretchFactor(2, 0)
        v_split.addWidget(h_split)

        # ── Bottom tabs ──────────────────────────────────────────────────────
        bottom_w = QWidget(); bw_l = QVBoxLayout(bottom_w)
        bw_l.setContentsMargins(0, 0, 0, 0); bw_l.setSpacing(3)

        self.bottom_tabs = QTabWidget(); self.bottom_tabs.setMinimumHeight(80)
        self.traj_panel  = TrajectoryPanel(self._active_setup,
                                           hw_panel_class=CryoHardwarePanel)
        self.sl_panel    = ScanlistPanel(self._active_setup,
                                         hw_panel_class=CryoHardwarePanel)
        self.data_browser = DataBrowserPanel(
            lambda: self._active_setup().get("save_dir", "~/moke_data"))

        # ── Geometry & Piezo toggles — injected into the scan type row ───────
        # Pill-button factory matching the scan type button style.
        def _pill(label, *, checked=False, checked_color, left=False, right=False):
            if left:
                r = ("border-top-left-radius:6px;border-bottom-left-radius:6px;"
                     "border-top-right-radius:0;border-bottom-right-radius:0;")
            elif right:
                r = ("border-top-right-radius:6px;border-bottom-right-radius:6px;"
                     "border-top-left-radius:0;border-bottom-left-radius:0;")
            else:
                r = "border-radius:0;"
            b = QPushButton(label)
            b.setCheckable(True); b.setChecked(checked)
            b.setFixedHeight(28)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(
                f"QPushButton{{background:#252538;border:1px solid #45475a;"
                f"color:#6c7086;font-size:11px;font-weight:bold;padding:0 12px;{r}}}"
                f"QPushButton:hover{{background:#313244;color:#cdd6f4;}}"
                f"QPushButton:checked{{background:{checked_color};color:#1e1e2e;"
                f"border-color:{checked_color};}}")
            return b

        geo_tip = ("Select the optical geometry for this scan.\n"
                   "Stage actuator device paths are injected from the\n"
                   "matching Faraday or Voigt block in Setup Defaults.")
        self.geo_faraday_btn = _pill("Faraday", checked=True,
                                     checked_color="#cba6f7", left=True)
        self.geo_voigt_btn   = _pill("Voigt",   checked_color="#cba6f7", right=True)
        for b in (self.geo_faraday_btn, self.geo_voigt_btn):
            b.setToolTip(geo_tip)
        self._geo_btn_grp = QButtonGroup(self)
        self._geo_btn_grp.addButton(self.geo_faraday_btn)
        self._geo_btn_grp.addButton(self.geo_voigt_btn)
        self._geo_btn_grp.setExclusive(True)

        piezo_tip = ("Select which piezo stage to use for this scan.\n"
                     "ANM200 = fine scanner (nm);  ANC300 = coarse stepper (steps).")
        self.piezo_anm_btn = _pill("ANM200", checked=True,
                                   checked_color="#a6e3a1", left=True)
        self.piezo_anc_btn = _pill("ANC300", checked_color="#a6e3a1", right=True)
        for b in (self.piezo_anm_btn, self.piezo_anc_btn):
            b.setToolTip(piezo_tip)
        self._piezo_btn_grp = QButtonGroup(self)
        self._piezo_btn_grp.addButton(self.piezo_anm_btn)
        self._piezo_btn_grp.addButton(self.piezo_anc_btn)
        self._piezo_btn_grp.setExclusive(True)

        # Append to the scan type row (remove trailing stretch, add widgets, re-add)
        tr = self.traj_panel._type_row
        tr.takeAt(tr.count() - 1)   # remove stretch

        def _row_sep():
            f = QFrame(); f.setFrameShape(QFrame.Shape.VLine)
            f.setFixedWidth(1); f.setFixedHeight(22)
            f.setStyleSheet("background:#45475a;border:none;")
            return f

        def _row_lbl(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#6c7086;font-size:10px;font-weight:bold;")
            return lbl

        tr.addSpacing(12); tr.addWidget(_row_sep()); tr.addSpacing(8)
        tr.addWidget(_row_lbl("Geometry:"))
        tr.addSpacing(4)
        tr.addWidget(self.geo_faraday_btn); tr.addWidget(self.geo_voigt_btn)
        tr.addSpacing(12); tr.addWidget(_row_sep()); tr.addSpacing(8)
        tr.addWidget(_row_lbl("Piezo:"))
        tr.addSpacing(4)
        tr.addWidget(self.piezo_anm_btn); tr.addWidget(self.piezo_anc_btn)
        tr.addStretch()

        self.bottom_tabs.addTab(self.traj_panel,   "Trajectory")
        self.bottom_tabs.addTab(self.sl_panel,     "Scanlist")
        self.bottom_tabs.addTab(self.data_browser,  "Data Browser")
        self.script_console = ScriptConsolePanel()
        self.bottom_tabs.addTab(self.script_console, "Script")
        self.dev_registry = DeviceRegistryPanel()
        self.bottom_tabs.addTab(self.dev_registry, "Device Registry")
        self.defaults_panel = SetupDefaultsPanel()
        self.bottom_tabs.addTab(self.defaults_panel, "Setup Defaults")
        bw_l.addWidget(self.bottom_tabs, stretch=1)

        v_split.addWidget(bottom_w)
        v_split.setSizes([600, 300])
        v_split.setStretchFactor(0, 1)
        v_split.setStretchFactor(1, 1)
        self._v_split = v_split
        self._split_initialised = False

        main_v.addWidget(v_split)

    def _connect_signals(self):
        # ConfigListPanel — load only Cryo setup
        self.cfg_list.load_setups(self._setups)
        self.cfg_list.config_selected.connect(self._on_config_selected)
        self.cfg_list.new_config_requested.connect(self._on_new_config)
        self.cfg_list.config_deleted.connect(self._on_config_deleted)
        self.cfg_list.config_renamed.connect(self._on_config_renamed)
        self.cfg_list.save_requested.connect(self._explicit_save)

        # Action bar
        self.start_btn.clicked.connect(self._unified_start)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.abort_btn.clicked.connect(self._unified_abort)

        self.right_panel.refresh_requested.connect(self._refresh_plot)
        self.right_panel.display_changed.connect(self._on_display_changed)
        self.right_panel.x_axis_changed.connect(self._on_x_axis_changed)
        self.right_panel.plot_config_changed.connect(self._on_plot_config_changed)
        self.dev_registry.registry_changed.connect(self._on_registry_changed)
        self.defaults_panel.defaults_changed.connect(self._on_defaults_changed)
        self.traj_panel.scan_mode_changed.connect(self._on_scan_mode_changed)
        self._geo_btn_grp.buttonClicked.connect(self._on_geometry_changed)
        self._piezo_btn_grp.buttonClicked.connect(self._on_stage_type_changed)

        self._browser_loaded = False
        self.bottom_tabs.currentChanged.connect(self._on_bottom_tab_changed)

        self.script_console.set_context(
            setup_getter=self._active_setup,
            config_getter=self._build_full_config)

        QShortcut(QKeySequence("F5"),       self, activated=self._unified_start)
        QShortcut(QKeySequence("Ctrl+L"),   self, activated=self.log_text.clear)
        QShortcut(QKeySequence("Ctrl+R"),   self, activated=self.data_browser.refresh)

    # ── Bottom tab handling ──────────────────────────────────────────────────
    def _on_bottom_tab_changed(self, idx):
        if idx == TAB_DATA_BROWSER and not self._browser_loaded:
            self.data_browser.refresh(); self._browser_loaded = True
        if idx == TAB_TRAJECTORY:
            self.traj_panel.hw.refresh()
        elif idx == TAB_SCANLIST:
            self.sl_panel.hw.refresh()

    def _on_live_tab_changed(self, _idx):
        if self.live_tabs.currentWidget() is self.calib_panel:
            self.calib_panel._read_all()

    # ── Config management ─────────────────────────────────────────────────────
    def _on_new_config(self):
        self._save_active_config()
        new_cfg = make_default_config("new_scan")
        new_cfg["sensors"] = []
        self._active_setup()["configs"].append(new_cfg)
        new_idx = len(self._active_setup()["configs"]) - 1
        self._active_cfg_idx = new_idx
        self._active_setup()["active_idx"] = new_idx
        self.cfg_list.add_item(new_cfg["name"])
        self._safe_save()

    def _on_config_selected(self, idx):
        if idx == -1:
            self._save_active_config()
            src = copy.deepcopy(self._active_setup()["configs"][self._active_cfg_idx])
            src["name"] = f"copy_{src['name']}"
            self._active_setup()["configs"].append(src)
            new_idx = len(self._active_setup()["configs"]) - 1
            self._active_cfg_idx = new_idx
            self._active_setup()["active_idx"] = new_idx
            self.cfg_list.add_item(src["name"])
            self._safe_save(); return
        self._save_active_config()
        self._active_cfg_idx = idx
        self._active_setup()["active_idx"] = idx
        self._safe_save()
        self._load_active_config()

    def _on_config_deleted(self, idx):
        configs = self._active_setup()["configs"]
        if len(configs) <= 1: return
        name = configs[idx].get("name", f"config {idx+1}")
        ans = QMessageBox.question(
            self, "Delete scan config",
            f"Delete '{name}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        configs.pop(idx); self.cfg_list.remove_item(idx)
        new_idx = min(idx, len(configs) - 1)
        self._active_cfg_idx = new_idx
        self._active_setup()["active_idx"] = new_idx
        self._safe_save()
        self._load_active_config()

    def _on_config_renamed(self, idx, name):
        configs = self._active_setup()["configs"]
        if 0 <= idx < len(configs):
            configs[idx]["name"] = name
            self.cfg_list.rename_item(idx, name)
            self._safe_save()

    def _load_active_config(self):
        configs = self._active_setup().get("configs", [])
        if not configs: return
        idx = min(self._active_cfg_idx, len(configs)-1); cfg = configs[idx]
        setup = self._active_setup()
        registry = self.dev_registry.get_registry()
        self.traj_panel.populate_monitor_combo(registry)
        self.traj_panel.load_config(cfg)
        self.traj_panel.load_monitor_settings(cfg)
        self.right_panel.load_sensors(cfg.get("sensors", DEFAULT_SENSORS))
        self.right_panel.set_display(cfg.get("display_sensor","ZI2 x1"), cfg.get("colormap","RdBu_r"))
        self.right_panel.set_dc_mode(False)   # no DC_HYST in cryo
        self.sl_panel.set_active_name(cfg.get("name","—"))
        sd = os.path.expanduser(setup.get("save_dir", "~/moke_data"))
        self.save_dir.setText(sd)
        # Restore geometry + piezo toggles (blockSignals to avoid recursive saves)
        geo = cfg.get("geometry",   "Faraday")
        st  = cfg.get("stage_type", "anm200")
        self._geo_btn_grp.blockSignals(True)
        self._piezo_btn_grp.blockSignals(True)
        (self.geo_voigt_btn if geo == "Voigt" else self.geo_faraday_btn).setChecked(True)
        (self.piezo_anc_btn if st  == "anc300" else self.piezo_anm_btn).setChecked(True)
        self._geo_btn_grp.blockSignals(False)
        self._piezo_btn_grp.blockSignals(False)
        # Sync Setup Defaults panel
        self.defaults_panel.set_registry(registry)
        self.defaults_panel.load(setup)
        self._apply_defaults(setup)

    def _safe_save(self):
        """Persist setup config, showing errors in status bar (#10)."""
        try:
            save_setup(CRYO_SETUP, self._active_setup())
        except Exception as e:
            log.error("Config save failed: %s", e, exc_info=True)
            self.status_lbl.setText(f"⚠ Save failed: {e}")
            self.status_lbl.setStyleSheet("color:#f38ba8;font-size:11px;")

    def _save_active_config(self):
        configs = self._active_setup().get("configs", [])
        if not configs: return
        idx = min(self._active_cfg_idx, len(configs)-1)
        old = configs[idx]; old.update(self.traj_panel.get_config_partial())
        old["sensors"]        = self.right_panel.get_sensors()
        old["display_sensor"] = self.right_panel.get_display_sensor()
        old["colormap"]       = self.right_panel.get_colormap()
        old["geometry"]       = self._get_current_geometry()
        old["stage_type"]     = self._get_current_stage_type()
        self._active_setup()["save_dir"] = self.save_dir.text().strip()
        self.cfg_list.sync_name(idx, old["name"])
        self._safe_save()
        self._update_estimate()

    def _update_estimate(self):
        """Show a breakdown pre-scan time estimate in status_lbl when idle.

        ZI settling is read in a background thread so the GUI is never blocked.
        """
        if self._scan_running:
            return
        try:
            cfg   = self._build_full_config()
            setup = self._active_setup()
            mode, n_x, n_y = self._scan_dims(cfg)
        except Exception:
            return

        def _fmt(s):
            if s < 120:  return f"{s:.0f} s"
            if s < 3600: return f"{s/60:.1f} min"
            return       f"{s/3600:.1f} h"

        int_t  = float(cfg.get("integration_time", 0.1))
        settle = float(cfg.get("settle_time", 0.05))
        if mode == "FIELD":  settle = max(settle, 0.05)
        elif mode == "TIME": settle = 0.0
        n_pts = n_x * n_y
        pts   = f"{n_x}" if n_y == 1 else f"{n_x}×{n_y}"
        move_note = " + moves" if mode not in ("TIME", "FIELD") else ""

        def _show(zi_settle=0.0):
            if self._scan_running:
                return
            parts = []
            if settle    > 0: parts.append(f"{settle:.3g}s settle")
            if zi_settle > 0: parts.append(f"{zi_settle:.3g}s ZI")
            parts.append(f"{int_t:.3g}s integ")
            total = n_pts * (settle + zi_settle + int_t)
            self.status_lbl.setText(
                f"≈ {_fmt(total)}  ({pts} pts × [{' + '.join(parts)}]{move_note})")
            self.status_lbl.setStyleSheet("color:#6c7086;font-size:11px;")

        _show(0.0)

        zi_path  = setup.get("zi_device", "")
        zi_s_attr = setup.get("zi_settling_attr", "settlingtime")
        if not zi_path or not zi_s_attr:
            return

        def _read_zi():
            try:
                dp = get_proxy(zi_path)
                val, _ = safe_read(dp, zi_s_attr, timeout=0.5)
                if val is not None:
                    zi = float(val)
                    self._post_to_main.emit(lambda zi=zi: _show(zi))
            except Exception:
                pass

        threading.Thread(target=_read_zi, daemon=True).start()

    def _explicit_save(self):
        self._save_active_config()
        # Only show success if _safe_save didn't already set an error
        if not self.status_lbl.text().startswith("⚠"):
            self.status_lbl.setText("Config saved ✓")
            self.status_lbl.setStyleSheet("color:#6c7086;font-size:11px;")

    def _on_registry_changed(self):
        registry = self.dev_registry.get_registry()
        self.right_panel.set_registry(registry)
        self.traj_panel.populate_monitor_combo(registry)
        self.defaults_panel.set_registry(registry)
        self.status_lbl.setText("Device registry saved ✓")

    def _on_defaults_changed(self):
        """Called when Setup Defaults panel values change — persist and apply."""
        vals = self.defaults_panel.get_values()
        setup = self._active_setup()
        setup.update(vals)
        self._safe_save()
        self._apply_defaults(setup)
        self.status_lbl.setText("Setup defaults saved ✓")
        self.status_lbl.setStyleSheet("color:#6c7086;font-size:11px;")

    def _get_current_geometry(self) -> str:
        return "Faraday" if self.geo_faraday_btn.isChecked() else "Voigt"

    def _get_current_stage_type(self) -> str:
        return "anm200" if self.piezo_anm_btn.isChecked() else "anc300"

    def _persist_scan_profile(self):
        """Save geometry + stage_type from the toggles into the active config."""
        configs = self._active_setup().get("configs", [])
        if configs:
            cfg = configs[self._active_cfg_idx]
            cfg["geometry"]   = self._get_current_geometry()
            cfg["stage_type"] = self._get_current_stage_type()
        self._apply_defaults(self._active_setup())
        self._safe_save()

    def _on_geometry_changed(self, _btn=None):
        self._persist_scan_profile()

    def _on_stage_type_changed(self, _btn=None):
        self._persist_scan_profile()

    def _apply_defaults(self, setup: dict):
        """Push setup defaults into trajectory actuators and calibration FL device."""
        geo = self._get_current_geometry()
        st  = self._get_current_stage_type()
        piezo_block = setup.get(f"stage_{geo.lower()}", {}).get(st, {})
        self.traj_panel.set_actuator_defaults(
            act1_dev=piezo_block.get("act1_device", ""),
            act1_attr=piezo_block.get("act1_attr",  "x"),
            act1_lbl=piezo_block.get("act1_label",  "X"),
            act1_unit=piezo_block.get("act1_unit",  "nm"),
            act2_dev=piezo_block.get("act2_device", ""),
            act2_attr=piezo_block.get("act2_attr",  "y"),
            act2_lbl=piezo_block.get("act2_label",  "Y"),
            act2_unit=piezo_block.get("act2_unit",  "nm"),
        )
        fl_dev = setup.get("focus_averagein", "")
        if fl_dev:
            self.calib_panel.set_fl_device(fl_dev)
        # ANC300 device — same device regardless of geometry, take from any piezo block
        anc_dev = (setup.get("stage_faraday", {}).get("anc300", {}).get("act1_device", "")
                   or setup.get("stage_voigt", {}).get("anc300", {}).get("act1_device", ""))
        self.calib_panel.set_anc_device(anc_dev)
        self.calib_panel.configure_stage(
            piezo_block.get("act1_device", ""), piezo_block.get("act1_attr", "x"),
            piezo_block.get("act2_device", ""), piezo_block.get("act2_attr", "y"),
            piezo_block.get("z_device",    ""), piezo_block.get("z_attr",    "z"),
        )

    def _on_scan_mode_changed(self, mode):
        # Temperature sweep uses the standard FIELD engine — no DC mode needed
        self.right_panel.set_dc_mode(False)

    def _browse_save_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Save directory", self.save_dir.text())
        if d: self.save_dir.setText(d)

    # ── Scan start ───────────────────────────────────────────────────────────
    def _unified_start(self):
        if self._scan_running: return
        self._active_setup()["save_dir"] = self.save_dir.text().strip()
        self.traj_panel._save_dir = self.save_dir.text().strip()
        if self.live_tabs.currentWidget() is self.calib_panel:
            self._start_calib_timescan(); return
        if self.bottom_tabs.currentIndex() == TAB_SCANLIST:
            self._start_scanlist()
        else:
            self._start_scan()

    def _unified_abort(self):
        if self._sl_worker:
            self._abort_scanlist()
        else:
            self._abort_scan()

    def _set_running(self, running):
        if not running:
            from PyQt6.QtWidgets import QStyle
            self.pause_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.pause_btn.setText("Pause")
        # Disable hardware Read buttons during scan to prevent concurrent TANGO
        # access on the ZI device (Device_4Impl is single-threaded; simultaneous
        # state() + read_attribute() calls cause IMP_LIMIT CORBA exceptions).
        for panel in (self.traj_panel.hw, self.sl_panel.hw):
            if hasattr(panel, 'set_scan_running'):
                panel.set_scan_running(running)

    def _build_full_config(self) -> dict:
        partial  = self.traj_panel.get_config_partial()
        configs  = self._active_setup().get("configs", [])
        partial["name"] = configs[self._active_cfg_idx]["name"] if configs else "scan"
        partial["sensors"]        = self.right_panel.get_sensors()
        partial["display_sensor"] = self.right_panel.get_display_sensor()
        partial["colormap"]       = self.right_panel.get_colormap()
        # Geometry + stage_type from active config (saved per-scan)
        geo = "Faraday"; st = "anm200"
        if configs:
            cfg = configs[self._active_cfg_idx]
            geo = cfg.get("geometry",   "Faraday")
            st  = cfg.get("stage_type", "anm200")
        partial["geometry"]   = geo
        partial["stage_type"] = st
        # Inject device/attr from the matching piezo block in Setup Defaults
        setup = self._active_setup()
        piezo_block = setup.get(f"stage_{geo.lower()}", {}).get(st, {})
        for pfx, dkey, akey, lkey, ukey in [
            ("act1", "act1_device", "act1_attr", "act1_label", "act1_unit"),
            ("act2", "act2_device", "act2_attr", "act2_label", "act2_unit"),
        ]:
            if piezo_block.get(dkey):
                partial.setdefault(f"{pfx}_device", piezo_block[dkey])
                partial.setdefault(f"{pfx}_attr",   piezo_block[akey])
                partial.setdefault(f"{pfx}_label",  piezo_block.get(lkey, ""))
                partial.setdefault(f"{pfx}_unit",   piezo_block.get(ukey, "nm"))
        if piezo_block.get("z_device"):
            partial.setdefault("z_device", piezo_block["z_device"])
            partial.setdefault("z_attr",   piezo_block.get("z_attr", "z"))
        return partial

    # ── Scan geometry ────────────────────────────────────────────────────────
    def _scan_dims(self, cfg) -> Tuple[str, int, int]:
        st = cfg.get("scan_type","SPATIAL")
        sx = cfg.get("scan_x",  True)
        sy = cfg.get("scan_y",  False)
        if st == "FIELD":         return "FIELD",  int(cfg.get("field_npts",101)), 1
        if sx and sy:             return "2D",     int(cfg.get("act1_npts",51)),   int(cfg.get("act2_npts",51))
        if sy and not sx:         return "1D_Y",   int(cfg.get("act2_npts",51)),   1
        if not sx and not sy:     return "TIME",   int(cfg.get("act1_npts",101)),  1
        return "1D",                               int(cfg.get("act1_npts",51)),   1

    def _alloc_scan_data(self, cfg, active):
        _, n_x, n_y = self._scan_dims(cfg)
        self._scan_data = {s["label"]: np.full((n_y, n_x), np.nan) for s in active}
        self._scan_data[X_TIME] = np.full((n_y, n_x), np.nan)

    def _setup_live_display(self, cfg, active):
        mode, n_x, n_y = self._scan_dims(cfg)
        if mode == "2D":
            x_arr = np.linspace(cfg["act1_start"], cfg["act1_stop"], n_x)
            y_arr = np.linspace(cfg["act2_start"], cfg["act2_stop"], n_y)
            self.map2d.setup(x_arr, y_arr,
                             f"{cfg['act1_label']} ({cfg['act1_unit']})",
                             f"{cfg['act2_label']} ({cfg['act2_unit']})",
                             cfg.get("display_sensor",""), cfg.get("colormap","RdBu_r"))
            self.live_tabs.setCurrentIndex(TAB_MAP2D)
        else:
            if   mode == "FIELD":   xl, xu = cfg.get("field_x_label", "Field"), cfg.get("field_x_unit", "T")
            elif mode == "1D_Y":    xl, xu = cfg["act2_label"], cfg["act2_unit"]
            elif mode == "TIME":    xl, xu = "Time", "s"
            else:                   xl, xu = cfg["act1_label"], cfg["act1_unit"]
            self.plot1d.alloc(n_x, xl, xu, active)
            self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(),
                                     self.right_panel.get_x_key())
            if not self._calib_timescan:
                self.live_tabs.setCurrentIndex(TAB_PLOT1D)
            x_opts = [(X_NATURAL, f"{xl} ({xu})" if xu else xl), (X_TIME, "Time (s)")]
            for s in active: x_opts.append((s["label"], s["label"]))
            self.right_panel.set_x_options(x_opts)

    # ── Worker wiring (shared by scan + calib timescan) ────────────────────
    def _wire_worker(self, cfg, setup):
        """Create a ScanWorker and connect its signals. Returns the worker."""
        worker = ScanWorker(cfg, setup)
        worker.point_done.connect(self._on_point)
        worker.progress.connect(self._on_progress)
        worker.status_msg.connect(self._on_status)
        worker.log_msg.connect(self._log_append)
        worker.scan_done.connect(lambda fn: setattr(self, "_last_fn", fn))
        worker.error_msg.connect(
            lambda m: self._log_append(f"\n⚠ ERROR:\n{m}", level="error"))
        worker.finished.connect(self._on_worker_finished)
        return worker

    # ── Single scan ──────────────────────────────────────────────────────────
    # Max allowed points per dimension to prevent memory exhaustion.
    _MAX_POINTS_1D = 10_000
    _MAX_POINTS_2D = 500_000   # 1000×500 ≈ typical upper bound for spatial maps

    def _validate_scan_config(self, cfg: dict) -> Optional[str]:
        """Validate scan parameters before starting.

        Returns an error string if the config is invalid, or None if OK.
        Checks are intentionally conservative — only catch values that would
        cause immediate problems (OOM, hangs, nonsensical geometry).
        """
        scan_type = cfg.get("scan_type", "SPATIAL")

        if scan_type in ("SPATIAL",):
            n_x = int(cfg.get("act1_npts", 1))
            n_y = int(cfg.get("act2_npts", 1))
            scan_2d = cfg.get("scan_x", True) and cfg.get("scan_y", False)

            if n_x < 1:
                return f"X points must be ≥ 1 (got {n_x})."
            if n_y < 1:
                return f"Y points must be ≥ 1 (got {n_y})."
            if n_x > self._MAX_POINTS_1D:
                return (f"X points ({n_x:,}) exceeds the safety limit of "
                        f"{self._MAX_POINTS_1D:,}.")
            total = n_x * n_y if scan_2d else n_x
            if total > self._MAX_POINTS_2D:
                return (f"Total scan points ({total:,}) = {n_x}×{n_y} exceeds "
                        f"the safety limit of {self._MAX_POINTS_2D:,}.\n"
                        "Reduce n_pts or scan range.")

        elif scan_type == "FIELD":
            segs = cfg.get("field_segments", [])
            total_field_pts = sum(int(s[2]) for s in segs if len(s) >= 3)
            if total_field_pts < 2:
                return "Field scan requires at least 2 points."
            if total_field_pts > self._MAX_POINTS_1D:
                return (f"Field scan points ({total_field_pts:,}) exceeds "
                        f"the safety limit of {self._MAX_POINTS_1D:,}.")

        elif scan_type == "TIME":
            n_t = int(cfg.get("act1_npts", 1))
            if n_t < 1:
                return "Time scan requires at least 1 point."
            if n_t > self._MAX_POINTS_1D:
                return (f"Time scan points ({n_t:,}) exceeds the safety limit "
                        f"of {self._MAX_POINTS_1D:,}.")

        integ = float(cfg.get("integration_time", 0.1))
        if integ <= 0:
            return f"Integration time must be > 0 (got {integ})."

        return None  # all OK

    def _start_scan(self):
        if self._scan_running: return
        self._calib_timescan = False
        self._save_active_config()
        cfg = self._build_full_config(); setup = self._active_setup()

        active = [s for s in cfg["sensors"] if s["enabled"]]
        if not active:
            QMessageBox.warning(self, "No sensors", "Enable at least one sensor."); return

        err = self._validate_scan_config(cfg)
        if err:
            QMessageBox.warning(self, "Invalid scan parameters", err); return

        # ── Setup lock ────────────────────────────────────────────────────────
        ok, who = acquire_lock(self._active_setup_name)
        if not ok:
            QMessageBox.warning(
                self, "Setup busy",
                f"Setup '{self._active_setup_name}' is already in use:\n{who}\n\n"
                "Abort that scan first, then retry.")
            return

        # ── ANM200 temperature-driven scaling ────────────────────────────────
        if cfg.get("stage_type") == "anm200":
            self._apply_anm200_scaling(cfg)

        # ── Build direction queue ─────────────────────────────────────────────
        # Each axis can carry up to 2 [start, stop] directions.
        # For 2D: directions are paired by index (zip, not product) → max 2 maps.
        # For 1D: each direction on the active axis → up to 2 files.
        mode, _, __ = self._scan_dims(cfg)
        scan_x = cfg.get("scan_x", True)
        scan_y = cfg.get("scan_y", False)
        dirs1 = cfg.get("act1_directions", [[cfg["act1_start"], cfg["act1_stop"]]])
        dirs2 = cfg.get("act2_directions", [[cfg["act2_start"], cfg["act2_stop"]]])

        if scan_x and scan_y:
            n = max(len(dirs1), len(dirs2))
            combos = [(dirs1[min(i, len(dirs1)-1)], dirs2[min(i, len(dirs2)-1)]) for i in range(n)]
        elif scan_x:
            combos = [(d, None) for d in dirs1]
        elif scan_y:
            combos = [(None, d) for d in dirs2]
        else:
            combos = [(None, None)]   # TIME scan

        use_suffix = len(combos) > 1
        base_name  = cfg["name"]
        cfgs = []
        for i, (d1, d2) in enumerate(combos):
            c = copy.deepcopy(cfg)
            if d1 is not None:
                c["act1_start"], c["act1_stop"] = d1[0], d1[1]
            if d2 is not None:
                c["act2_start"], c["act2_stop"] = d2[0], d2[1]
            dir_name = "trace" if i == 0 else "retrace"
            c["name"] = f"{base_name}_{dir_name}" if use_suffix else base_name
            cfgs.append(c)

        first_cfg = cfgs[0]
        self._dir_queue = cfgs[1:]   # remaining directions run after first completes

        self._current_scan_cfg = first_cfg
        self._setup_live_display(first_cfg, active); self._alloc_scan_data(first_cfg, active)
        _, n_x, n_y = self._scan_dims(first_cfg)
        total = n_x * n_y
        self.pbar.setMaximum(total); self.pbar.setValue(0)
        lbl = "trace" if use_suffix else ""
        self.pbar.setFormat(f"{lbl} %v / %m pts" if lbl else "%v / %m pts")
        self._scan_start_time = _time.time(); self._scan_total_pts = total
        self.log_text.clear()

        # ── Hardware snapshot (written to HDF5 metadata + lab notebook) ─────
        attodry_dev = setup.get("attodry_device", "")
        is_temp_sweep = (
            first_cfg.get("scan_type") == "FIELD"
            and bool(attodry_dev)
            and first_cfg.get("act1_device", "") == attodry_dev
        )
        hw_snap = _read_hw_snapshot(setup, first_cfg.get("scan_type", "SPATIAL"),
                                    is_temp_sweep=is_temp_sweep)
        for c in cfgs:
            c.update(hw_snap)

        self._worker = self._wire_worker(first_cfg, setup)
        self._scan_running = True; self._set_running(True); self._last_fn = None
        self._worker.start()

    def _apply_anm200_scaling(self, cfg: dict):
        """Read sample temperature, interpolate ANM200 scaling [V/µm], write to device."""
        setup = self._active_setup()
        attodry_dev = setup.get("attodry_device", "")
        anm_dev = cfg.get("act1_device", "")
        if not anm_dev or not attodry_dev:
            return
        try:
            from hardware import fresh_proxy, safe_read, safe_write
            ad_p, err = fresh_proxy(attodry_dev)
            if err:
                log.warning("ANM200 scaling: cannot reach AttoDRY: %s", err); return
            temp, e = safe_read(ad_p, "SampleTemperature")
            if e or temp is None:
                log.warning("ANM200 scaling: cannot read SampleTemperature: %s", e); return
            # Linear interpolation between calibration points
            # At   4 K: scaling = 1/3  V/µm  (10 V / 30 µm)
            # At 300 K: scaling = 1/15 V/µm  (4 V  / 60 µm)
            S_4K   = 1.0 / 3.0
            S_300K = 4.0 / 60.0
            t = float(temp)
            s = S_4K + (t - 4.0) * (S_300K - S_4K) / (300.0 - 4.0)
            s = max(S_300K, min(S_4K, s))   # clamp to calibrated range
            anm_p, err = fresh_proxy(anm_dev)
            if err:
                log.warning("ANM200 scaling: cannot reach device: %s", err); return
            safe_write(anm_p, "scaling", s)
            log.info("ANM200 scaling set to %.5f V/µm  (T = %.1f K)", s, t)
            self._log_append(
                f"ANM200 scaling → {s:.5f} V/µm  (T = {t:.1f} K)", level="info")
        except Exception as exc:
            log.warning("ANM200 scaling update failed: %s", exc)

    # ── Calibration time scan ────────────────────────────────────────────
    def _start_calib_timescan(self):
        if self._scan_running: return
        self._save_active_config()
        cfg   = self._build_full_config()
        setup = self._active_setup()
        cfg["scan_type"] = "SPATIAL"
        cfg["scan_x"] = False; cfg["scan_y"] = False

        active = [s for s in cfg["sensors"] if s["enabled"]]
        if not active:
            QMessageBox.warning(self, "No sensors", "Enable at least one sensor."); return

        n_pts = int(cfg.get("act1_npts", 101))
        self._current_scan_cfg = cfg
        self._calib_timescan = True

        plot_sensors = [s for s in active
                        if s.get("plot_visible", True)
                        and s.get("y_axis", s.get("plot_axis", "Y1")) not in ("hidden", "—", "X")]
        self.calib_panel.focus_plot.setup_timescan(n_pts, plot_sensors if plot_sensors else active)
        self._setup_live_display(cfg, active)
        self._alloc_scan_data(cfg, active)
        self.pbar.setMaximum(n_pts); self.pbar.setValue(0)

        self._worker = self._wire_worker(cfg, setup)
        self._scan_running = True; self._set_running(True); self._last_fn = None
        self._worker.start()

    def _on_point(self, ix, iy, x_actual, vals):
        for lbl, v in vals.items():
            if lbl in self._scan_data: self._scan_data[lbl][iy, ix] = v
        mode, _, __ = self._scan_dims(self._current_scan_cfg)
        if mode == "2D":
            disp = self.right_panel.get_display_sensor()
            self.map2d.update_point(ix, iy, vals.get(disp, float("nan")))
        else:
            self.plot1d.update_point(ix, x_actual, vals)
        if getattr(self, '_calib_timescan', False):
            self.calib_panel.focus_plot.update_timescan_point(ix, x_actual, vals)

    def _log_append(self, msg: str, level: str = "auto"):
        if level == "auto":
            ml = msg.lower()
            if "⚠" in msg or "error" in ml or "traceback" in ml:
                level = "error"
            elif "warning" in ml or "mismatch" in ml:
                level = "warning"
            else:
                level = "info"
        filt = self.log_filter.currentIndex()
        if filt == 1 and level != "error": return
        if filt == 2 and level == "info": return

        colors = {"info": "#a6e3a1", "warning": "#fab387", "error": "#f38ba8"}
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colors.get(level, "#a6e3a1")))
        cursor.insertText(msg + "\n", fmt)
        self.log_text.setTextCursor(cursor)

    def _on_status(self, msg):
        self.status_lbl.setText(msg); self._log_append(msg)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum())
        if self._worker and self._worker.is_paused():
            from PyQt6.QtWidgets import QStyle
            self.pause_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")

    def _on_progress(self, done: int, total: int):
        """Update progress bar and show elapsed + ETA."""
        self.pbar.setValue(done)
        if done <= 0 or not self._scan_start_time:
            return
        elapsed = _time.time() - self._scan_start_time
        if done < total:
            eta = elapsed / done * (total - done)
            def _fmt(s):
                m, sec = divmod(int(s), 60)
                return f"{m}m {sec:02d}s" if m else f"{sec}s"
            self.pbar.setFormat(
                f"%v / %m pts  —  {_fmt(elapsed)} elapsed  ~{_fmt(eta)} left")
        else:
            self.pbar.setFormat(f"%v / %m pts  —  done")

    def _on_worker_finished(self):
        # Append to lab notebook for this completed scan direction
        if self._last_fn and self._current_scan_cfg and not getattr(self, '_calib_timescan', False):
            setup = self._active_setup()
            nb = _nb_path(setup.get("save_dir", "~/moke_data"), "Cryo")
            entry = dict(self._current_scan_cfg)
            entry["_scan_start_time"] = self._scan_start_time
            entry["_hdf5_path"] = os.path.abspath(self._last_fn)
            append_measurement(nb, entry)

        # If more directions are queued, start the next one without releasing the lock.
        if self._dir_queue:
            next_cfg = self._dir_queue.pop(0)
            setup = self._active_setup()
            active = [s for s in next_cfg["sensors"] if s["enabled"]]
            self._current_scan_cfg = next_cfg
            self._setup_live_display(next_cfg, active)
            self._alloc_scan_data(next_cfg, active)
            _, n_x, n_y = self._scan_dims(next_cfg)
            total = n_x * n_y
            self.pbar.setMaximum(total); self.pbar.setValue(0)
            dir_suffix = next_cfg["name"].rsplit("_", 1)[-1] if "_" in next_cfg["name"] else ""
            self.pbar.setFormat(f"{dir_suffix} %v / %m pts" if dir_suffix else "%v / %m pts")
            self._scan_start_time = _time.time()
            self._last_fn = None
            self._worker = self._wire_worker(next_cfg, setup)
            self._worker.start()
            return

        release_lock(self._active_setup_name)
        self._scan_running = False; self._set_running(False)
        self._calib_timescan = False
        self.pbar.setFormat("%v / %m pts")
        try:
            self.data_browser.refresh()
        except Exception:
            log.debug("Failed to refresh data browser after scan", exc_info=True)
        if self._last_fn:
            QMessageBox.information(self, "Scan complete", f"Saved:\n{self._last_fn}")
            self._last_fn = None
        self._update_estimate()

    def _toggle_pause(self):
        if not self._scan_running or not self._worker: return
        from PyQt6.QtWidgets import QStyle
        if self._worker.is_paused():
            self._worker.resume()
            self.pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.pause_btn.setText("Pause")
        else:
            self._worker.pause()
            self.pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")

    def _abort_scan(self):
        if not self._scan_running: return
        self._dir_queue = []   # cancel any pending direction passes
        if self._worker: self._worker.abort()
        if self._sl_worker: self._sl_worker.abort()
        self.status_lbl.setText("Aborting…")

    # ── Scanlist ─────────────────────────────────────────────────────────────
    def _start_scanlist(self):
        if self._scan_running: return
        self._save_active_config()
        cfg = self._build_full_config(); setup = self._active_setup()

        active = [s for s in cfg["sensors"] if s["enabled"]]
        if not active:
            QMessageBox.warning(self, "No sensors", "Enable at least one sensor."); return

        err = self._validate_scan_config(cfg)
        if err:
            QMessageBox.warning(self, "Invalid scan parameters", err); return

        self._current_scan_cfg = cfg; sl = self.sl_panel.get_settings()
        self._setup_live_display(cfg, active); self._alloc_scan_data(cfg, active)

        self._sl_worker = ScanlistWorker(cfg, setup, sl["n_scans"], sl["list_name"],
                                         sl["relay_flip"], sl["field_flip"])
        self._sl_worker.point_done.connect(self._on_point)
        self._sl_worker.progress.connect(self._on_progress)
        self._sl_worker.list_progress.connect(
            lambda c, t: self.sl_panel.list_bar.setValue(int(c * 100 // t)))
        self._sl_worker.cycle_done.connect(self._on_cycle_done)
        self._sl_worker.status_msg.connect(self._on_status)
        self._sl_worker.log_msg.connect(self._log_append)
        self._sl_worker.all_done.connect(self._on_scanlist_done)
        self._sl_worker.relay_changed.connect(self._on_scanlist_relay_changed)
        self._sl_worker.error_msg.connect(
            lambda m: self._log_append(f"\n⚠ ERROR:\n{m}", level="error"))
        self._sl_worker.finished.connect(
            lambda: (self._set_running(False), setattr(self, "_scan_running", False)))

        self.sl_panel.list_bar.setMaximum(100); self.sl_panel.list_bar.setValue(0)
        _, n_x, n_y = self._scan_dims(cfg)
        self.pbar.setMaximum(n_x * n_y); self.pbar.setValue(0)
        self.pbar.setFormat("%v / %m pts")
        self._scan_start_time = _time.time()
        self._scan_running = True; self._set_running(True); self.log_text.clear()
        self._sl_worker.start()

    def _on_scanlist_done(self, txt_path):
        try:
            self.data_browser.refresh()
        except Exception:
            log.debug("Failed to refresh data browser after scanlist", exc_info=True)
        QMessageBox.information(self, "Scanlist complete", f"Saved:\n{txt_path}")

    def _on_scanlist_relay_changed(self, state):
        for hw in (self.traj_panel.hw, self.sl_panel.hw):
            # CryoHardwarePanel has no relay — skip gracefully
            if hasattr(hw, '_relay_state'):
                hw._relay_state = state
            if hasattr(hw, '_update_relay_label'):
                hw._update_relay_label()

    def _on_cycle_done(self, cycle_idx):
        cfg  = self._current_scan_cfg
        mode, _, __ = self._scan_dims(cfg)
        if mode == "2D": return
        active = [s for s in cfg["sensors"] if s["enabled"]]
        self._alloc_scan_data(cfg, active)
        self._setup_live_display(cfg, active)

    def _abort_scanlist(self):
        if self._sl_worker: self._sl_worker.abort()
        self.status_lbl.setText("Aborting scanlist…")

    # ── Plot helpers ─────────────────────────────────────────────────────────
    def _on_plot_config_changed(self):
        if not self._current_scan_cfg: return
        mode, _, __ = self._scan_dims(self._current_scan_cfg)
        if mode == "2D": return
        self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(),
                                 self.right_panel.get_x_key())

    def _on_x_axis_changed(self, key, label):
        if not self._current_scan_cfg: return
        mode, _, __ = self._scan_dims(self._current_scan_cfg)
        if mode == "2D": return
        self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(), key)

    def _refresh_plot(self):
        cfg = self._current_scan_cfg or self._build_full_config()
        mode, _, __ = self._scan_dims(cfg)
        if mode == "2D":
            disp = self.right_panel.get_display_sensor()
            if disp and disp in self._scan_data and self.map2d._img is not None:
                self.map2d.switch_sensor(self._scan_data[disp], disp)
        else:
            self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(),
                                     self.right_panel.get_x_key())

    def _on_display_changed(self, sensor, cmap):
        if sensor and sensor in self._scan_data and self.map2d._img is not None:
            self.map2d.switch_sensor(self._scan_data[sensor], sensor)
        self.map2d.set_colormap(cmap)

    # ── Polling: AttoDRY readbacks ───────────────────────────────────────────
    # ── Readback signal handlers (from ReadbackWorker thread) ──────────────
    def _on_attodry_readback(self, fld, tmp, vti, mgt):
        self.traj_panel.hw.update_field_readback(fld)
        self.traj_panel.hw.update_cryo_readbacks(tmp, vti, mgt)
        self.sl_panel.hw.update_field_readback(fld)
        self.sl_panel.hw.update_cryo_readbacks(tmp, vti, mgt)

    def _on_fallback_field(self, v):
        self.traj_panel.hw.update_field_readback(v)
        self.sl_panel.hw.update_field_readback(v)

    def _on_ac_monitor(self, mv):
        self.traj_panel.update_field_monitor(mv)

    def _on_stage_positions(self, vals):
        self.calib_panel.update_positions(vals)

    def _sync_readback_state(self):
        """Push current GUI state into the background ReadbackWorker."""
        self._rb_worker.setup = self._active_setup()
        self._rb_worker.scan_running = self._scan_running
        self._rb_worker.scan_cfg = self._current_scan_cfg
        if self._scan_running and self._current_scan_cfg:
            mon_dev, mon_attr = self.traj_panel.get_monitor_device()
            self._rb_worker.monitor_device = mon_dev or ""
            self._rb_worker.monitor_attr = mon_attr or ""
        self._rb_worker.poll_calib = (
            self.live_tabs.currentWidget() is self.calib_panel)
        if self._rb_worker.poll_calib:
            self._rb_worker.calib_axis_info = self.calib_panel.get_axis_info()

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def _initial_hw_read(self):
        """Read all hardware panels once on startup (fired 400 ms after __init__).
        Staggered to avoid simultaneous ZI reads that cause IMP_LIMIT CORBA errors."""
        self.traj_panel.hw.refresh()
        QTimer.singleShot(800, self.sl_panel.hw.refresh)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not self._split_initialised and self.height() > 100:
            top = int(self.height() * 0.55)
            self._v_split.setSizes([top, self.height() - top])
            self._split_initialised = True

    def _restore_geometry(self):
        s = QSettings("ETH-Intermag", "SambaCryo")
        g = s.value("geometry")
        if g: self.restoreGeometry(bytes(g))

    def closeEvent(self, ev):
        if self._scan_running:
            r = QMessageBox.question(self, "Scan running", "Abort and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r == QMessageBox.StandardButton.No: ev.ignore(); return
            for w in [self._worker, self._sl_worker]:
                if w: w.abort(); w.wait(2000)
        # Stop the readback thread
        self._rb_worker.stop()
        self._rb_worker.wait(2000)
        self._save_active_config()
        QSettings("ETH-Intermag", "SambaCryo").setValue("geometry", self.saveGeometry())
        ev.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def _setup_logging():
    """Configure root logger with both console and rotating file output.

    Log files are stored in ~/.config/moke_scan/logs/.  Each file is capped at
    2 MB and up to 5 backups are kept, giving ~10 MB max on disk.  This allows
    post-mortem debugging of hardware issues without flooding the disk.
    """
    from config import CONFIG_DIR
    log_dir = CONFIG_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "samba_cryo.log"

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Rotating file handler — 2 MB per file, keep 5 backups
    fh = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024,
                             backupCount=5, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler — INFO and above only (keeps terminal clean)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    logging.getLogger(__name__).info("Logging to %s", log_path)


def main():
    import platform
    if platform.system() == "Linux":
        os.environ.setdefault("RESOURCE_NAME", "samba_cryo")

    _setup_logging()

    app = QApplication(["samba_cryo"])
    app.setApplicationName("Samba Cryo")
    app.setOrganizationName("ETH Zürich - Intermag")
    app.setDesktopFileName("samba_cryo")

    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samba_icon_256.png")
    _app_icon = None
    if os.path.exists(_icon_path):
        _app_icon = QIcon(_icon_path)
        app.setWindowIcon(_app_icon)

    from play_intro import show_splash, update_splash, finish_splash
    splash = show_splash(app)

    update_splash(splash, "Loading Cryo configuration…")
    if not TANGO_AVAILABLE:
        update_splash(splash, "pytango not found — simulation mode")

    update_splash(splash, "Building Cryo window…")
    win = CryoMainWindow()

    if _app_icon:
        win.setWindowIcon(_app_icon)

    if TANGO_AVAILABLE:
        win._probe_devices(status_callback=lambda msg: update_splash(splash, msg))

    update_splash(splash, "Ready!")
    finish_splash(splash, win, min_seconds=3)

    if not TANGO_AVAILABLE:
        QMessageBox.information(win, "Simulation Mode",
            "pytango not installed — running with simulated hardware.\n\n"
            "Install:  pip install pytango\n"
            "Connect:  export TANGO_HOST=192.168.1.1:10000")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
