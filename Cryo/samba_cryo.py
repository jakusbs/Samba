#!/usr/bin/env python3
"""
samba_cryo.py — Samba Cryo — ETH Zürich Intermag Lab

SAMBA — Strnad & Goldenberger Application for Magnetism Based Analysis
    S  trnad & Goldenberger
    A  pplication
       for
    M  agnetism
    B  ased
    A  nalysis

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
    QLabel, QTabWidget, QTabBar, QTextEdit, QMessageBox, QSplitter,
    QComboBox, QLineEdit, QPushButton, QFileDialog, QButtonGroup, QFrame,
    QStatusBar
)
from PyQt6.QtCore import QTimer, QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QShortcut, QKeySequence, QTextCharFormat, QColor, QTextCursor, QIcon

log = logging.getLogger(__name__)

try:
    import tango
    TANGO_AVAILABLE = True
except ImportError:
    TANGO_AVAILABLE = False

from config  import (SETUP_NAMES, X_NATURAL, X_TIME, DEFAULT_SENSORS, load_setup,
                     save_setup, make_default_config, KEITHLEY_RANGES)
from current_sweep import (SETTLE_PLATEAU, fmt_hms, format_current_list,
                           pick_keithley_range, validate_sweep)
from current_sweep_ui import ThermalSettleWorker
from hardware import (get_proxy, fresh_proxy, safe_read, safe_write,
                      evict_proxy, _pcache)
from applog import setup_logging
from validation import (validate_scan_config,
                        MAX_POINTS_1D, MAX_POINTS_2D)
from scan    import ScanWorker, ScanlistWorker
from lab_notebook import append_measurement, notebook_path as _nb_path
from plot_widgets import Live2DWidget, Live1DWidget
from panels  import (ConfigListPanel, RightPanel,
                     TrajectoryPanel, ScanlistPanel, SensorPickerRow)
from panels_cryo import CryoHardwarePanel
from data_browser import DataBrowserPanel
from script_console import ScriptConsolePanel
from calibration import CryoCalibrationPanel
from device_registry import DeviceRegistryPanel, load_registry, registry_to_sensors
from defaults_panel  import SetupDefaultsPanel
from core.bd_calibration import BDCalibrationPanel
import play_intro

try:
    from setup_lock import acquire_lock, release_lock
except Exception:
    def acquire_lock(name): return True, ""   # type: ignore[misc]
    def release_lock(name): pass              # type: ignore[misc]

from server_sync import sync_setup


# ─────────────────────────────────────────────────────────────────────────────
# Status-bar duration formatter
# ─────────────────────────────────────────────────────────────────────────────

def _sb_fmt(sec: float) -> str:
    """Format a duration (seconds) as '2m 05s' or '7s'."""
    m, s = divmod(int(sec), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


# ─────────────────────────────────────────────────────────────────────────────
# Hardware snapshot helper
# ─────────────────────────────────────────────────────────────────────────────

_HW_SNAP_TIMEOUT_S = 1.5   # per-attribute bound for the pre-scan snapshot


def _read_hw_snapshot(setup: dict, scan_type: str, is_temp_sweep: bool = False) -> dict:
    """Read key hardware state immediately before a Cryo scan starts.

    ``scan_type`` == "FIELD" suppresses hw_field_mT (field is being swept).
    ``is_temp_sweep`` == True suppresses hw_temperature_K (temp is being swept).
    """
    snap: dict = {}
    _dead: set = set()      # devices that already failed once in this snapshot

    def _read(device_path: str, attr: str):
        # This runs on the GUI thread at scan start.  With the default 10 s
        # I/O timeout, ~14 attribute reads spread over ~5 devices meant one
        # powered-off device froze the UI for tens of seconds between clicking
        # Start and anything visibly happening.  Two bounds fix that: a short
        # per-read timeout, and skipping a device's remaining attributes once
        # one of its reads has failed.  The snapshot is best-effort metadata,
        # so losing a key on a flaky device is the right trade.
        if not device_path or not attr or device_path in _dead:
            return None
        try:
            p = get_proxy(device_path)
            val, rerr = safe_read(p, attr, timeout=_HW_SNAP_TIMEOUT_S)
            if rerr:
                _dead.add(device_path)
                return None
            return val
        except Exception:
            _dead.add(device_path)
            return None

    # Keithley AC excitation state
    # Cryo uses keithley_attr_* keys; Samba_main uses keithley_*_attr keys.
    k_dev = setup.get("keithley_device", "")
    for hw_key, attr_key_cryo, attr_key_main in [
        ("hw_keithley_amplitude_mA",  "keithley_attr_amplitude",  "keithley_amplitude_attr"),
        ("hw_keithley_frequency_Hz",  "keithley_attr_frequency",  "keithley_frequency_attr"),
        ("hw_keithley_range",         "keithley_attr_range",      "keithley_range_attr"),
        ("hw_keithley_compliance_V",  "keithley_attr_compliance", "keithley_compliance_attr"),
    ]:
        attr = setup.get(attr_key_cryo) or setup.get(attr_key_main, "")
        v = _read(k_dev, attr)
        if v is not None:
            snap[hw_key] = v
    # Keithley output current readback ("I out" in the hardware panel)
    cur_attr = setup.get("keithley_attr_current") or setup.get("keithley_current_attr", "current")
    v = _read(k_dev, cur_attr)
    if v is not None:
        snap["hw_keithley_current_mA"] = v

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

    # Extra cryostat temperatures (VTI + magnet) — always recorded
    v = _read(setup.get("attodry_device", ""),
              setup.get("attodry_attr_vti_temp", "VtiTemperature"))
    if v is not None:
        snap["hw_vti_temp_K"] = v
    v = _read(setup.get("attodry_device", ""),
              setup.get("attodry_attr_mag_temp", "MagnetTemperature"))
    if v is not None:
        snap["hw_magnet_temp_K"] = v

    # Stage position at scan start — only relevant for SPATIAL scans
    if scan_type not in ("FIELD",) and not is_temp_sweep:
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
TAB_TRAJECTORY      = 0
TAB_SCANLIST        = 1
TAB_BD_CALIBRATION  = 2
TAB_DATA_BROWSER    = 3
TAB_SCRIPT          = 4
TAB_DEV_REGISTRY    = 5
TAB_DEFAULTS        = 6

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
        try:
            from core.easter_egg import install_easter_egg
            install_easter_egg(self)
        except Exception:
            pass
        # Modest minimum so the window fits smaller laptop screens; the larger
        # *preferred* opening size is applied (screen-clamped) in _restore_geometry.
        self.setMinimumSize(1180, 640)

        self._setups:              Dict[str, dict]          = {}
        self._worker:              Optional[ScanWorker]     = None
        self._sl_worker:           Optional[ScanlistWorker] = None
        self._scan_running:        bool                     = False
        self._meta_syncing:        bool                     = False
        self._timing_syncing:      bool                     = False
        self._last_sample_id:      str                      = ""
        self._autopause_notified:  bool                     = False

        # ── Current sweep — one scanlist per excitation current ─────────────
        # _cs_active stays True through the settle and refocus phases, when no
        # worker exists, so the app still counts as "running" and Abort/Pause
        # have something to talk to.
        self._cs_active:    bool  = False
        self._cs_abort:     bool  = False
        self._cs_paused:    bool  = False
        self._cs_currents:  list  = []
        self._cs_idx:       int   = 0
        self._cs_base_list: list  = []
        self._cs_settle:    Optional[ThermalSettleWorker] = None
        self._cs_fl_t:      list  = []     # focus trace during the settle
        self._cs_fl_v:      list  = []
        self._cs_fl_label:  str   = ""
        self._scan_data:         Dict[str, np.ndarray]    = {}
        self._scan_data_retrace: Dict[str, np.ndarray]    = {}
        self._last_fn:           Optional[str]            = None
        self._last_fn_retrace:   Optional[str]            = None
        self._active_setup_name: str                      = CRYO_SETUP
        self._active_cfg_idx:    int                      = 0
        self._current_scan_cfg:  dict                     = {}
        self._calib_timescan:    bool                     = False
        self._scan_start_time:   float                    = 0.0
        self._sl_scan_t0:        float                    = 0.0
        self._sl_cfg_list:       list                     = []
        self._scan_total_pts:    int                      = 0
        self._dir_queue:         list                     = []   # pending direction cfgs
        self._interleaved_2d:    bool                     = False

        # ── Bottom status-bar state (live scan progress) ────────────────────
        self._run_start_time:     float = 0.0   # set once per run, NOT reset per direction
        self._run_scans_total:    int   = 1     # total scan-files this run will produce
        self._run_scans_done:     int   = 0     # scan-files fully completed
        self._scan_first_pt_time: float = 0.0   # time the 1st point of current scan arrived
        self._bar_int_time:       float = 0.1   # integration_time for dead-time calc
        self._bar_last_done:      int   = 0     # last progress(done) seen
        self._bar_last_total:     int   = 1     # last progress(total) seen

        # Only load Cryo setup
        self._setups[CRYO_SETUP] = load_setup(CRYO_SETUP)
        # Surface load problems once the event loop runs (a silently-defaulted
        # setup after an unreadable file would be overwritten on the next save).
        _st = self._setups[CRYO_SETUP].pop("_load_status", "ok")
        if _st.startswith("error"):
            _msg = (f"The saved Cryo configuration could not be read "
                    f"({_st[7:][:150]}).\nThe unreadable file was backed up to "
                    f"~/.config/moke_scan/{CRYO_SETUP}.json.bad — default "
                    f"configs are shown, and saving will overwrite it.")
            QTimer.singleShot(0, lambda m=_msg: QMessageBox.warning(
                self, "Setup configuration", m))

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

        # ── Server sync bar ───────────────────────────────────────────────────
        _srv_bar = QWidget(); _srv_bar.setFixedHeight(34)
        _srv_bar.setObjectName("server_bar")
        _srv_bar.setStyleSheet(
            "#server_bar{background:#0a0a16;border:1px solid #313244;border-radius:6px;}")
        _srv_row = QHBoxLayout(_srv_bar)
        _srv_row.setContentsMargins(8, 4, 8, 4); _srv_row.setSpacing(4)
        _srv_lbl = QLabel("Server:")
        _srv_lbl.setStyleSheet("color:#0080fe;font-size:11px;font-weight:bold;")
        _srv_row.addWidget(_srv_lbl)
        self.server_dir = QLineEdit()
        self.server_dir.setFixedHeight(24)
        self.server_dir.setPlaceholderText("Server sync directory (leave blank to disable)…")
        self.server_dir.setStyleSheet(
            "QLineEdit{background:#1e1e2e;border:1px solid #45475a;border-radius:4px;"
            "padding:2px 6px;color:#a6adc8;font-size:10px;}"
            "QLineEdit:focus{border:1px solid #0080fe;}")
        _srv_row.addWidget(self.server_dir, stretch=1)
        _srv_browse = QPushButton("…")
        _srv_browse.setFixedSize(24, 24)
        _srv_browse.setToolTip("Browse for server sync directory")
        _srv_browse.setStyleSheet(
            "QPushButton{background:#252538;border:1px solid #45475a;border-radius:4px;"
            "padding:0;font-size:11px;color:#cdd6f4;}"
            "QPushButton:hover{background:#313244;}"
            "QPushButton:pressed{background:#252538;}")
        _srv_browse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        _srv_browse.clicked.connect(self._browse_server_dir)
        _srv_row.addWidget(_srv_browse)
        _srv_row.addSpacing(8)
        _sync_btn = QPushButton("↑ Sync")
        _sync_btn.setFixedHeight(24); _sync_btn.setMinimumWidth(66)
        _sync_btn.setToolTip("Sync data to server now")
        _sync_btn.setStyleSheet(
            "QPushButton{background:#0a0a16;border:1px solid #0080fe;border-radius:4px;"
            "color:#0080fe;font-size:11px;font-weight:bold;padding:0 8px;}"
            "QPushButton:hover{background:#131325;}"
            "QPushButton:pressed{background:#0a0a16;}")
        _sync_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        _sync_btn.clicked.connect(self._manual_sync)
        _srv_row.addWidget(_sync_btn)
        main_v.addWidget(_srv_bar)

        # ── Main content ─────────────────────────────────────────────────────
        v_split = QSplitter(Qt.Orientation.Vertical)
        h_split = QSplitter(Qt.Orientation.Horizontal)

        self.cfg_list = ConfigListPanel()
        self.cfg_list.setMinimumWidth(140)
        h_split.addWidget(self.cfg_list)

        center = QWidget(); cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(4)

        self.live_tabs = QTabWidget()

        # 2D Map tab: horizontal splitter so trace and retrace maps sit side-by-side.
        # map2d_retrace is hidden unless an interleaved scan is running.
        map_tab = QWidget(); map_lay = QVBoxLayout(map_tab)
        map_lay.setContentsMargins(2, 4, 2, 0); map_lay.setSpacing(0)
        self._map_split = QSplitter(Qt.Orientation.Horizontal)
        self.map2d         = Live2DWidget()
        self.map2d_retrace = Live2DWidget()
        self.map2d_retrace.hide()
        self._map_split.addWidget(self.map2d)
        self._map_split.addWidget(self.map2d_retrace)
        map_lay.addWidget(self._map_split)
        self.live_tabs.addTab(map_tab, "2D Map")

        plot_tab = QWidget(); plot_lay = QVBoxLayout(plot_tab)
        plot_lay.setContentsMargins(2, 4, 2, 0); plot_lay.setSpacing(0)
        self.plot1d = Live1DWidget(); plot_lay.addWidget(self.plot1d)
        self.live_tabs.addTab(plot_tab, "1D Plot")

        self.calib_panel = CryoCalibrationPanel(self._active_setup,
            sensor_row_factory=lambda **kw: SensorPickerRow(
                self._registry_now(), **kw),
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
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color:#6c7086;font-size:11px;")
        self.status_lbl.setWordWrap(True)
        pr.addWidget(self.status_lbl, stretch=1)
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

        self.bd_cal_panel = BDCalibrationPanel()
        self.bottom_tabs.addTab(self.traj_panel,    "Trajectory")
        self.bottom_tabs.addTab(self.sl_panel,      "Scanlist")
        self.bottom_tabs.addTab(self.bd_cal_panel,  "BD Calibration")
        self.bottom_tabs.addTab(self.data_browser,  "Data Browser")
        self.script_console = ScriptConsolePanel()
        self.bottom_tabs.addTab(self.script_console, "Script")
        self.dev_registry = DeviceRegistryPanel()
        self.bottom_tabs.addTab(self.dev_registry, "Device Registry")
        self.defaults_panel = SetupDefaultsPanel()
        self.bottom_tabs.addTab(self.defaults_panel, "Setup Defaults")
        bw_l.addWidget(self.bottom_tabs, stretch=1)

        v_split.addWidget(bottom_w)
        v_split.setSizes([500, 400])
        v_split.setStretchFactor(0, 1)
        v_split.setStretchFactor(1, 1)
        self._v_split = v_split
        self._split_initialised = False

        main_v.addWidget(v_split)

        # ── Always-visible bottom status bar (live scan progress) ────────────
        self._build_status_bar()

    # ── Bottom status bar ─────────────────────────────────────────────────────

    # ── Status-bar state tint (visible from across the room) ─────────────────
    _SB_TINTS = {
        "idle":    "QStatusBar{background:#181825;border-top:2px solid #313244;}",
        "running": "QStatusBar{background:#16241b;border-top:2px solid #a6e3a1;}",
        "paused":  "QStatusBar{background:#2b2015;border-top:2px solid #fab387;}",
    }

    def _tint_status_bar(self, state: str):
        """Tint the bottom status bar by scan state: green while running,
        peach while paused (manual or auto), neutral when idle."""
        sb = getattr(self, "_sb", None)
        if sb is not None:
            sb.setStyleSheet(self._SB_TINTS.get(state, self._SB_TINTS["idle"]))

    def _build_status_bar(self):
        """Seven-field QStatusBar showing live scan-run progress."""
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._sb = sb
        sb.setStyleSheet(self._SB_TINTS["idle"])
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(8, 0, 8, 0); row.setSpacing(0)

        def _mk_field():
            lbl = QLabel("—")
            lbl.setStyleSheet("color:#cdd6f4;font-size:12px;")
            return lbl

        def _mk_caption(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#a6adc8;font-size:12px;")
            return lbl

        def _mk_sep():
            lbl = QLabel(" │ ")
            lbl.setStyleSheet("color:#45475a;font-size:12px;")
            return lbl

        self._sb_cur     = _mk_field()
        self._sb_scan    = _mk_field()
        self._sb_start   = _mk_field()
        self._sb_elapsed = _mk_field()
        self._sb_runleft = _mk_field()
        self._sb_scanleft= _mk_field()
        self._sb_dead    = _mk_field()
        self._sb_done    = _mk_field()
        fields = [
            # "Current" only moves during a sweep; "—" the rest of the time.
            ("Current: ",   self._sb_cur),
            ("Scan: ",      self._sb_scan),
            ("Start: ",     self._sb_start),
            ("Elapsed: ",   self._sb_elapsed),
            ("Run left: ",  self._sb_runleft),
            ("Scan left: ", self._sb_scanleft),
            ("Dead: ",      self._sb_dead),
            ("Done: ",      self._sb_done),
        ]
        for i, (cap, lbl) in enumerate(fields):
            if i:
                row.addWidget(_mk_sep())
            row.addWidget(_mk_caption(cap)); row.addWidget(lbl)
        row.addStretch()
        sb.addPermanentWidget(container, 1)

        # 1 Hz refresh so Elapsed / Run-left / Scan-left tick between points
        self._sb_timer = QTimer(self)
        self._sb_timer.setInterval(1000)
        self._sb_timer.timeout.connect(self._refresh_status_bar)
        self._sb_timer.start()

    def _refresh_status_bar(self):
        """Recompute and display the seven status-bar fields.

        Cheap no-op while idle (leaves the final frame frozen on completion)."""
        if not self._scan_running:
            return
        now = _time.time()
        done, total = self._bar_last_done, self._bar_last_total
        total = max(1, total)
        scan_elapsed = now - self._scan_start_time if self._scan_start_time else 0.0
        run_elapsed  = now - self._run_start_time  if self._run_start_time  else 0.0

        # Scan-left: warmup-corrected rate (skip the first point's setup overhead)
        if done >= 2 and self._scan_first_pt_time > 0:
            rate = (now - self._scan_first_pt_time) / (done - 1)
            scan_left = rate * (total - done)
        elif done >= 1 and scan_elapsed > 0:
            scan_left = scan_elapsed * (total - done) / done
        else:
            scan_left = 0.0

        # Overall fraction across the whole run (each scan weighted equally)
        frac_in_scan = (done / total) if total else 0.0
        overall_frac = (self._run_scans_done + frac_in_scan) / max(1, self._run_scans_total)
        overall_frac = min(max(overall_frac, 0.0), 1.0)

        # Run-left: proportional on whole-run elapsed (includes inter-scan
        # overhead like field flips / demag / settling that per-point misses)
        if overall_frac > 0.001:
            run_left = run_elapsed * (1 - overall_frac) / overall_frac
        else:
            run_left = 0.0

        # Dead time: current-scan elapsed not spent integrating
        active = done * self._bar_int_time
        dead_pct = (max(0.0, scan_elapsed - active) / scan_elapsed * 100.0
                    ) if scan_elapsed > 0 else 0.0

        done_pct = overall_frac * 100.0
        cur_scan = min(self._run_scans_done + 1, self._run_scans_total)

        self._sb_scan.setText(f"{cur_scan}/{self._run_scans_total}")
        self._sb_elapsed.setText(_sb_fmt(run_elapsed))
        self._sb_runleft.setText(_sb_fmt(run_left))
        self._sb_scanleft.setText(_sb_fmt(scan_left))
        self._sb_dead.setText(f"{dead_pct:.0f}%")
        self._sb_done.setText(f"{done_pct:.0f}%")

    def _status_bar_run_start(self, cfg: dict, n_scans_total: int):
        """Reset status-bar state at the start of a scan run."""
        self._autopause_notified = False   # re-arm the auto-pause popup
        self._run_start_time     = _time.time()
        self._run_scans_done     = 0
        self._run_scans_total    = max(1, int(n_scans_total))
        self._scan_first_pt_time = 0.0
        self._bar_int_time       = float(cfg.get("integration_time", 0.1) or 0.1)
        self._bar_last_done      = 0
        self._bar_last_total     = 1
        from datetime import datetime as _dt
        self._sb_start.setText(_dt.fromtimestamp(self._run_start_time).strftime("%H:%M:%S"))
        self._sb_scan.setText(f"1/{self._run_scans_total}")
        if not self._cs_active:
            self._sb_cur.setText("—")
        for lbl in (self._sb_elapsed, self._sb_runleft, self._sb_scanleft):
            lbl.setText("0s")
        self._sb_dead.setText("0%"); self._sb_done.setText("0%")

    def _status_bar_run_finish(self):
        """Freeze the status bar at 100% when the whole run completes."""
        self._run_scans_done = self._run_scans_total
        self._bar_last_done  = self._bar_last_total
        self._sb_scan.setText(f"{self._run_scans_total}/{self._run_scans_total}")
        self._sb_runleft.setText("0s"); self._sb_scanleft.setText("0s")
        self._sb_done.setText("100%")
        if self._run_start_time:
            self._sb_elapsed.setText(_sb_fmt(_time.time() - self._run_start_time))

    def _status_bar_scan_done(self):
        """One scan-file finished within a multi-scan run; advance the counter.

        Also restamps per-scan timing so the next scan-file's Scan-left /
        Dead-time estimates start fresh (run-level timing is untouched)."""
        self._run_scans_done = min(self._run_scans_done + 1, self._run_scans_total)
        self._scan_first_pt_time = 0.0
        self._scan_start_time    = _time.time()

    def _connect_signals(self):
        # Inline duration estimate in the field-sweep box.  Uses the same
        # per-point model as the status-bar estimate; the ramp/thermalisation
        # wait is not modelled (see _update_estimate), so this is a floor.
        self.traj_panel._seg_list.set_time_estimator(self._field_seg_estimate)
        # Keep the two tabs' hardware panels showing the same values
        self._link_hw_panels()
        self.sl_panel.polarity_changed.connect(self._on_polarity_changed)
        self.sl_panel.cur_sweep.changed.connect(self._on_polarity_changed)
        self.sl_panel.cur_sweep.changed.connect(self._update_estimate)
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
        self.calib_panel.timescan_changed.connect(self._on_calib_timescan_changed)
        self.traj_panel.scan_mode_changed.connect(self._on_scan_mode_changed)
        self._geo_btn_grp.buttonClicked.connect(self._on_geometry_changed)
        self._piezo_btn_grp.buttonClicked.connect(self._on_stage_type_changed)

        self._browser_loaded = False
        self.bottom_tabs.currentChanged.connect(self._on_bottom_tab_changed)

        self.script_console.set_context(
            setup_getter=self._active_setup,
            config_getter=self._build_full_config)

        # ── Metadata bidirectional sync (Trajectory ↔ Scanlist) ──────────────
        self.traj_panel.meta.changed.connect(self._sync_traj_meta_to_sl)
        self.sl_panel.meta.changed.connect(self._sync_sl_meta_to_traj)

        # New-sample popup: offer a fresh BD calibration when the sample ID is
        # edited by hand (programmatic setText — config loads, meta sync — does
        # not emit editingFinished, so only genuine user edits trigger this).
        self.traj_panel.meta.meta_sample.editingFinished.connect(self._on_sample_id_edited)
        self.sl_panel.meta.meta_sample.editingFinished.connect(self._on_sample_id_edited)

        # ── Timing bidirectional sync (Trajectory ↔ Scanlist) ────────────────
        self.traj_panel.int_time.valueChanged.connect(self._sync_traj_timing_to_sl)
        self.traj_panel.settle.valueChanged.connect(self._sync_traj_timing_to_sl)
        self.traj_panel.timeout.valueChanged.connect(self._sync_traj_timing_to_sl)
        self.sl_panel.int_time.valueChanged.connect(self._sync_sl_timing_to_traj)
        self.sl_panel.settle.valueChanged.connect(self._sync_sl_timing_to_traj)
        self.sl_panel.timeout.valueChanged.connect(self._sync_sl_timing_to_traj)

        # ── BD Calibration panel callbacks ────────────────────────────────────
        self.bd_cal_panel.set_callbacks(
            save_cb=self._bd_cal_save,
            load_cb=self._bd_cal_load,
        )
        # "Fit & Import": read a TIME calibration scan from the Dir folder and
        # show the fitted staircase in the 1D plot.
        self.bd_cal_panel.set_fit_context(
            dir_cb=lambda: self.save_dir.text(),
            plot_cb=self._bd_plot_fit,
        )

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
        elif idx == TAB_BD_CALIBRATION:
            self.bd_cal_panel.maybe_prompt(self._active_setup_name)

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
        # ── Shared (setup-level) metadata ────────────────────────────────────
        # Metadata describes the physical sample under test, which is constant
        # across scan types.  Stored once per setup and re-applied here, so
        # switching between a map, a line scan, a field/temperature sweep, etc.
        # keeps "the same sample".  Old setups fall back to the config's copy.
        shared_meta = setup.get("metadata")
        if shared_meta:
            self.traj_panel.meta.load_values(shared_meta)
            self.sl_panel.meta.load_values(shared_meta)
        self._last_sample_id = self.traj_panel.meta.meta_sample.text().strip()
        self.traj_panel.load_monitor_settings(cfg)
        self.right_panel.load_sensors(cfg.get("sensors", DEFAULT_SENSORS))
        self.right_panel.set_display(cfg.get("display_sensor","ZI2 x1"), cfg.get("colormap","RdBu_r"))
        self.right_panel.set_dc_mode(False)   # no DC_HYST in cryo
        self.sl_panel.set_active_name(cfg.get("name","—"))
        self.sl_panel.load_config(cfg)      # polarity control (silent)
        sd = os.path.expanduser(setup.get("save_dir", "~/moke_data"))
        self.save_dir.setText(sd)
        self.server_dir.setText(setup.get("server_sync_dir", ""))
        # BD calibration — load saved values if present, update status
        bd_vals = setup.get("bd_calibration")
        if bd_vals:
            self.bd_cal_panel.load_calibration(bd_vals)
            date_str = setup.get("bd_calibration_date", "")
            self.bd_cal_panel.set_status(
                f"Loaded from setup 'Cryo'"
                + (f" ({date_str})" if date_str else "") + ".")
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
        old.update(self.sl_panel.get_config_partial())   # polarity control
        old["sensors"]        = self.right_panel.get_sensors()
        old["display_sensor"] = self.right_panel.get_display_sensor()
        old["colormap"]       = self.right_panel.get_colormap()
        old["geometry"]       = self._get_current_geometry()
        old["stage_type"]     = self._get_current_stage_type()
        # Metadata is shared across all configs of this setup (same sample) —
        # persist it at setup level so it survives config/scan-type switches.
        self._active_setup()["metadata"] = self.traj_panel.meta.get_values()
        self._active_setup()["save_dir"] = self.save_dir.text().strip()
        self._active_setup()["server_sync_dir"] = self.server_dir.text().strip()
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

        # FIELD covers both the superconducting-magnet field sweep and the
        # temperature sweep.  For both, the per-point cost is dominated by the
        # ramp / thermalisation wait in ScanRunner._wait_not_moving — which the
        # formula below does not model.  Saying "+ moves" only for spatial
        # scans (where the omission is milliseconds) while presenting field and
        # temperature estimates as complete (where it is the whole
        # measurement) is exactly backwards: a temperature sweep shown as
        # "≈ 3 min" can run for hours.
        ramp = 0.0
        if mode == "FIELD":
            is_temp = "temp_start" in cfg
            ramp_key = ("temp_settle_estimate_s" if is_temp
                        else "field_ramp_estimate_s")
            try:
                ramp = max(0.0, float(setup.get(ramp_key, 0.0)))
            except (TypeError, ValueError):
                ramp = 0.0
            what = "thermalisation" if is_temp else "field ramp"
            move_note = ("" if ramp > 0 else
                         f" + {what} per point (not included — "
                         f"set {ramp_key} in the setup to estimate it)")
        else:
            move_note = " + moves" if mode != "TIME" else ""

        def _show(zi_settle=0.0):
            if self._scan_running:
                return
            # Cache for the inline field-segment estimate, which cannot afford
            # its own device read on every spinbox keystroke.
            self._last_zi_settle = zi_settle
            if hasattr(self.traj_panel, "_seg_list"):
                self.traj_panel._seg_list._on_changed()
            parts = []
            if settle    > 0: parts.append(f"{settle:.3g}s settle")
            if ramp      > 0: parts.append(f"{ramp:.3g}s ramp")
            if zi_settle > 0: parts.append(f"{zi_settle:.3g}s ZI")
            parts.append(f"{int_t:.3g}s integ")
            total = n_pts * (settle + ramp + zi_settle + int_t)
            txt = (f"≈ {_fmt(total)}  ({pts} pts × "
                   f"[{' + '.join(parts)}]{move_note})")
            # Current sweep: the whole scanlist runs once per current, with a
            # thermal settle and a refocus in between.  Quoting the per-scan
            # number alone would understate a multi-hour run by an order of
            # magnitude.
            sweep = self.sl_panel.cur_sweep
            if sweep.isChecked():
                n_cur   = len(sweep.currents())
                n_scans = int(self.sl_panel.n_spin.value())
                settle_each = sweep.settle_estimate_s()
                grand = n_cur * (n_scans * total + settle_each)
                txt += (f"   ·   sweep: {n_cur} currents × {n_scans} cycles + "
                        f"{fmt_hms(settle_each)} settle each "
                        f"≈ {_fmt(grand)} total (+ refocus)")
            self.status_lbl.setText(txt)
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

    # ── Metadata bidirectional sync ───────────────────────────────────────────
    def _sync_traj_meta_to_sl(self):
        if self._meta_syncing: return
        self._meta_syncing = True
        try:
            self.sl_panel.meta.load_values(self.traj_panel.meta.get_values())
        finally:
            self._meta_syncing = False

    def _on_sample_id_edited(self):
        """User finished editing the Sample field. If the name actually
        changed, offer to start a fresh BD calibration for the new sample:
        Yes → empty the 6 mV values and jump to the BD Calibration tab
        (the tab's first-open reload prompt is suppressed — it would offer
        the OLD sample's calibration right back)."""
        sender = self.sender()
        new_id = sender.text().strip() if sender is not None else \
            self.traj_panel.meta.meta_sample.text().strip()
        if new_id == self._last_sample_id:
            return
        old_id = self._last_sample_id
        self._last_sample_id = new_id
        if not new_id:
            return
        ans = QMessageBox.question(
            self, "New sample",
            f"Sample changed to '{new_id}'"
            + (f" (was '{old_id}')" if old_id else "")
            + ".\n\nStart a new BD calibration for it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.bd_cal_panel.suppress_prompt(self._active_setup_name)
        self.bd_cal_panel.load_calibration([0.0] * 6)
        self.bd_cal_panel.set_status(
            f"New calibration for sample '{new_id}' — enter the 6 mV values and Save.")
        self.bottom_tabs.setCurrentWidget(self.bd_cal_panel)

    def _sync_sl_meta_to_traj(self):
        if self._meta_syncing: return
        self._meta_syncing = True
        try:
            self.traj_panel.meta.load_values(self.sl_panel.meta.get_values())
        finally:
            self._meta_syncing = False

    # ── Timing bidirectional sync ─────────────────────────────────────────────
    def _sync_traj_timing_to_sl(self):
        if self._timing_syncing: return
        self._timing_syncing = True
        try:
            self.sl_panel.int_time.setValue(self.traj_panel.int_time.value())
            self.sl_panel.settle.setValue(self.traj_panel.settle.value())
            self.sl_panel.timeout.setValue(self.traj_panel.timeout.value())
        finally:
            self._timing_syncing = False

    def _sync_sl_timing_to_traj(self):
        if self._timing_syncing: return
        self._timing_syncing = True
        try:
            self.traj_panel.int_time.setValue(self.sl_panel.int_time.value())
            self.traj_panel.settle.setValue(self.sl_panel.settle.value())
            self.traj_panel.timeout.setValue(self.sl_panel.timeout.value())
        finally:
            self._timing_syncing = False

    def _bd_plot_fit(self, result):
        """Show a BD calibration trace (and its fitted levels) in the 1D plot.

        Skipped while a scan runs — the live plot belongs to the measurement.
        """
        if getattr(self, "_scan_running", False):
            self.status_lbl.setText(
                "Fit done — 1D plot not touched while a scan is running.")
            return
        overlay = result.step_curve() if result.ok else None
        name = os.path.basename(result.path or "")
        self.plot1d.show_static(
            result.t, result.dc,
            xlabel="Time (s)", ylabel="DC (V)",
            title=f"BD calibration — {name}" if name else "BD calibration",
            overlay=overlay, overlay_label="fitted levels")
        try:
            self.live_tabs.setCurrentWidget(self.plot1d.parentWidget())
        except Exception:
            pass

    # ── BD Calibration callbacks ──────────────────────────────────────────────
    def _bd_cal_save(self, vals: list):
        from datetime import datetime as _dt
        date_str = _dt.now().strftime("%Y-%m-%d %H:%M")
        setup = self._active_setup()
        setup["bd_calibration"]      = vals
        setup["bd_calibration_date"] = date_str
        save_setup(CRYO_SETUP, setup)
        self.bd_cal_panel.set_status(f"Saved {date_str} for setup 'Cryo'.")

    def _bd_cal_load(self):
        setup = self._active_setup()
        vals = setup.get("bd_calibration")
        date_str = setup.get("bd_calibration_date", "unknown date")
        return vals, date_str

    def _registry_now(self) -> list:
        """Current device registry — the registry panel once built, else disk."""
        dr = getattr(self, "dev_registry", None)
        return dr.get_registry() if dr is not None else load_registry()

    def _on_registry_changed(self):
        registry = self.dev_registry.get_registry()
        self.right_panel.set_registry(registry)
        self.traj_panel.populate_monitor_combo(registry)
        self.defaults_panel.set_registry(registry)
        # Rebuild the calibration tab's own sensor rows with the new registry
        self.calib_panel.load_timescan_settings(
            self._active_setup().get("calib_timescan", {}))
        self.status_lbl.setText("Device registry saved ✓")

    def _on_calib_timescan_changed(self):
        """Persist the calibration tab's own time-scan settings per setup."""
        setup = self._active_setup()
        setup["calib_timescan"] = self.calib_panel.get_timescan_settings()
        save_setup(self._active_setup_name, setup)

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
        self.calib_panel.load_timescan_settings(
            self._active_setup().get("calib_timescan", {}))
        # ANC300 device — same device regardless of geometry, take from any piezo block
        anc_dev = (setup.get("stage_faraday", {}).get("anc300", {}).get("act1_device", "")
                   or setup.get("stage_voigt", {}).get("anc300", {}).get("act1_device", ""))
        self.calib_panel.set_anc_device(anc_dev)
        # Live-plot "Recent" y-scale window — a per-setup preference set in
        # Setup Defaults (the plot toolbars have no room for another control).
        _rw = int(setup.get("recent_window", 10) or 10)
        self.plot1d.set_recent_window(_rw)
        self.calib_panel.focus_plot.set_recent_window(_rw)
        self.calib_panel.configure_stage(
            piezo_block.get("act1_device", ""), piezo_block.get("act1_attr", "x"),
            piezo_block.get("act2_device", ""), piezo_block.get("act2_attr", "y"),
            piezo_block.get("z_device",    ""), piezo_block.get("z_attr",    "z"),
            piezo_block.get("act1_unit", ""),   piezo_block.get("act2_unit", ""),
            piezo_block.get("z_unit",    ""),
        )

    def _on_polarity_changed(self):
        """Persist a user toggle of Relay/Field flip into the active config.

        Skipped while a scan runs: the running worker holds its own polarity
        settings, so writing a mid-run change to disk would misdescribe the
        measurement in progress.
        """
        if self._scan_running:
            return
        self._save_active_config()

    def _link_hw_panels(self):
        """Mirror the Trajectory and Scanlist hardware panels.

        Both tabs show the same physical Keithley, so their write windows must
        agree — otherwise a current typed on one tab leaves the other showing a
        stale value, and it is not obvious which one the hardware actually has.
        Mirroring moves displayed values only (writes still happen on
        Return/Enter in the panel being edited).  Qt suppresses no-change
        setValue signals, so the cross-connections cannot loop.

        Cryo has no relay, and its field/temperature setpoints live on the
        AttoDRY group which ReadbackWorker already polls live, so only the
        Keithley controls need mirroring.
        """
        a, b = self.traj_panel.hw, self.sl_panel.hw
        for name in ("amp_spin", "freq_spin", "compl_spin"):
            sa, sb = getattr(a, name, None), getattr(b, name, None)
            if sa is None or sb is None:
                continue
            sa.valueChanged.connect(sb.setValue)
            sb.valueChanged.connect(sa.setValue)
        ca, cb = getattr(a, "range_combo", None), getattr(b, "range_combo", None)
        if ca is not None and cb is not None:
            ca.currentIndexChanged.connect(cb.setCurrentIndex)
            cb.currentIndexChanged.connect(ca.setCurrentIndex)

    def _on_scan_mode_changed(self, mode):
        # Temperature sweep uses the standard FIELD engine — no DC mode needed
        self.right_panel.set_dc_mode(False)

    def _browse_save_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Save directory", self.save_dir.text())
        if d: self.save_dir.setText(d)

    def _browse_server_dir(self):
        start = self.server_dir.text().strip() or f"/run/user/{os.getuid()}/gvfs"
        d = QFileDialog.getExistingDirectory(self, "Server sync directory", start)
        if d:
            self.server_dir.setText(d)
            self._active_setup()["server_sync_dir"] = d

    def _manual_sync(self):
        setup = self._active_setup()
        server_path = self.server_dir.text().strip()
        if not server_path:
            self.status_lbl.setText("Server path not set — enter a path above")
            return
        setup["server_sync_dir"] = server_path
        self.status_lbl.setText("Syncing to server…")
        def _done(ok):
            QTimer.singleShot(0, lambda: self.status_lbl.setText(
                "Server sync complete" if ok else "Server sync partial (see log)"))
        sync_setup(self._active_setup_name, setup, done_cb=_done)

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
        # The sweep is checked first: between currents it owns the run while no
        # worker exists at all, so routing on _sl_worker alone would send the
        # abort to the single-scan path and do nothing.
        if self._cs_active or (self._sl_worker and self._sl_worker.isRunning()):
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
        self._tint_status_bar("running" if running else "idle")
        for panel in (self.traj_panel.hw, self.sl_panel.hw):
            if hasattr(panel, 'set_scan_running'):
                panel.set_scan_running(running)

    def _field_seg_estimate(self, total_pts: int):
        """Seconds for `total_pts` field points, or None if not computable."""
        try:
            if total_pts <= 0:
                return None
            int_t  = float(self.traj_panel.int_time.value())
            settle = max(float(self.traj_panel.settle.value()), 0.05)
            zi     = float(getattr(self, "_last_zi_settle", 0.0) or 0.0)
            ramp   = 0.0
            try:
                ramp = max(0.0, float(
                    self._active_setup().get("field_ramp_estimate_s", 0.0)))
            except Exception:
                ramp = 0.0
            return total_pts * (settle + zi + int_t + ramp)
        except Exception:
            return None

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

        # ── BD calibration — injected here (the single build path shared by
        # every start route: single scan, scanlist and the calibration time
        # scan) so the 6 mV λ/2 values reach HDF5 for all scan types.
        bd_panel = getattr(self, "bd_cal_panel", None)
        if bd_panel is not None:
            partial["bd_calibration"] = bd_panel.get_calibration()
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
        if cfg.get("_interleaved_2d"):
            self._scan_data_retrace = {s["label"]: np.full((n_y, n_x), np.nan)
                                       for s in active}
            self._scan_data_retrace[X_TIME] = np.full((n_y, n_x), np.nan)
        else:
            self._scan_data_retrace = {}

    def _setup_live_display(self, cfg, active):
        mode, n_x, n_y = self._scan_dims(cfg)
        if mode == "2D":
            x_arr  = np.linspace(cfg["act1_start"], cfg["act1_stop"], n_x)
            y_arr  = np.linspace(cfg["act2_start"], cfg["act2_stop"], n_y)
            xl     = f"{cfg['act1_label']} ({cfg['act1_unit']})"
            yl     = f"{cfg['act2_label']} ({cfg['act2_unit']})"
            sensor = cfg.get("display_sensor", "")
            cmap   = cfg.get("colormap", "RdBu_r")
            self.map2d.setup(x_arr, y_arr, xl, yl, sensor, cmap)
            if cfg.get("_interleaved_2d"):
                self.map2d_retrace.show()
                self.map2d_retrace.setup(x_arr, y_arr, xl, yl,
                                         sensor + " (retrace)", cmap)
                self._map_split.setSizes([1000, 1000])
            else:
                self.map2d_retrace.hide()
                self.map2d_retrace.clear()
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
        worker.point_retrace.connect(self._on_point_retrace)
        worker.progress.connect(self._on_progress)
        worker.status_msg.connect(self._on_status)
        worker.log_msg.connect(self._log_append)
        worker.scan_done.connect(lambda fn: setattr(self, "_last_fn", fn))
        worker.scan_done_retrace.connect(lambda fn: setattr(self, "_last_fn_retrace", fn))
        worker.error_msg.connect(
            lambda m: self._log_append(f"\n⚠ ERROR:\n{m}", level="error"))
        worker.finished.connect(self._on_worker_finished)
        return worker

    # ── Single scan ──────────────────────────────────────────────────────────
    # Max allowed points per dimension to prevent memory exhaustion.
    # Point-count safety limits now live in core/validation.py (shared with
    # Samba_main); kept here as aliases for anything referencing them.
    _MAX_POINTS_1D = MAX_POINTS_1D
    _MAX_POINTS_2D = MAX_POINTS_2D

    def _validate_scan_config(self, cfg: dict) -> Optional[str]:
        """Validate scan parameters before starting.

        Thin wrapper over the shared core implementation, which Samba_main
        now calls too — one copy, so a check added for one rig protects both.
        Passing the setup enables the optional per-axis soft travel limits.
        """
        return validate_scan_config(cfg, self._active_setup())

    # ── Setup lock helper ─────────────────────────────────────────────────────
    def _acquire_setup_lock(self) -> bool:
        """Take the multi-computer setup lock; warn and return False if busy.

        Used by every start path (single scan, scanlist, calibration time
        scan) — each of them drives the same physical setup, so all of them
        must hold the lock.
        """
        ok, who = acquire_lock(self._active_setup_name)
        if not ok:
            QMessageBox.warning(
                self, "Setup busy",
                f"Setup '{self._active_setup_name}' is already in use:\n{who}\n\n"
                "Abort that scan first, then retry.")
        return ok

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
        if not self._acquire_setup_lock():
            return

        # ── ANM200 temperature-driven scaling ────────────────────────────────
        if cfg.get("stage_type") == "anm200":
            self._apply_anm200_scaling(cfg)

        # BD calibration is injected in _build_full_config() (shared path).

        # ── Build direction queue ─────────────────────────────────────────────
        # Each axis can carry up to 2 [start, stop] directions.
        # For 1D: each direction on the active axis → sequential scans (up to 2).
        # For 2D with one axis having retrace: interleaved per-row/column scan
        #   (trace + retrace built simultaneously, single ScanWorker, two HDF5 files).
        # For 2D with no retrace on either axis: single standard scan.
        scan_x = cfg.get("scan_x", True)
        scan_y = cfg.get("scan_y", False)
        dirs1 = cfg.get("act1_directions", [[cfg.get("act1_start", 0.0), cfg.get("act1_stop", 0.0)]])
        dirs2 = cfg.get("act2_directions", [[cfg.get("act2_start", 0.0), cfg.get("act2_stop", 0.0)]])
        base_name = cfg["name"]

        self._interleaved_2d = False

        if scan_x and scan_y:
            x_has_retrace = len(dirs1) > 1
            y_has_retrace = len(dirs2) > 1
            if x_has_retrace or y_has_retrace:
                # Interleaved 2D: one worker, two simultaneous HDF5 files.
                interleave_axis = "x" if x_has_retrace else "y"
                c = copy.deepcopy(cfg)
                c["act1_start"], c["act1_stop"] = dirs1[0]
                c["act2_start"], c["act2_stop"] = dirs2[0]
                c["name"]              = f"{base_name}_trace"
                c["_interleaved_2d"]   = True
                c["_interleave_axis"]  = interleave_axis
                c["_retrace_name"]     = f"{base_name}_retrace"
                cfgs = [c]
                self._interleaved_2d = True
            else:
                # No retrace — single standard 2D scan
                c = copy.deepcopy(cfg)
                c["act1_start"], c["act1_stop"] = dirs1[0]
                c["act2_start"], c["act2_stop"] = dirs2[0]
                cfgs = [c]
        elif scan_x:
            combos = [(d, None) for d in dirs1]
            use_sfx = len(combos) > 1
            cfgs = []
            for i, (d1, _) in enumerate(combos):
                c = copy.deepcopy(cfg)
                c["act1_start"], c["act1_stop"] = d1
                c["name"] = f"{base_name}_{'trace' if i==0 else 'retrace'}" if use_sfx else base_name
                cfgs.append(c)
        elif scan_y:
            combos = [(None, d) for d in dirs2]
            use_sfx = len(combos) > 1
            cfgs = []
            for i, (_, d2) in enumerate(combos):
                c = copy.deepcopy(cfg)
                c["act2_start"], c["act2_stop"] = d2
                c["name"] = f"{base_name}_{'trace' if i==0 else 'retrace'}" if use_sfx else base_name
                cfgs.append(c)
        else:
            cfgs = [copy.deepcopy(cfg)]   # TIME scan

        first_cfg = cfgs[0]
        self._dir_queue = cfgs[1:]   # remaining directions run after first completes

        self._current_scan_cfg = first_cfg
        self._setup_live_display(first_cfg, active); self._alloc_scan_data(first_cfg, active)
        _, n_x, n_y = self._scan_dims(first_cfg)
        total = n_x * n_y * (2 if self._interleaved_2d else 1)
        self._last_fn_retrace = None
        self._scan_start_time = _time.time(); self._scan_total_pts = total
        # Status bar: first direction + any queued directions = total scan-files.
        # Interleaved-2D produces one file (cfgs has length 1 → _dir_queue empty).
        self._status_bar_run_start(first_cfg, 1 + len(self._dir_queue))
        self.log_text.clear()

        # ── Hardware snapshot (written to HDF5 metadata + lab notebook) ─────
        # Temperature sweep is identified by the presence of "temp_start" —
        # a key that only get_config_partial() adds for temperature sweeps,
        # not for field sweeps.  Checking act1_device == attodry_dev is wrong
        # because temperature sweep configs don't set act1_device at all.
        is_temp_sweep = "temp_start" in first_cfg
        hw_snap = _read_hw_snapshot(setup, first_cfg.get("scan_type", "SPATIAL"),
                                    is_temp_sweep=is_temp_sweep)
        # Temperature sweep flag + start/stop/step for the lab notebook.
        if is_temp_sweep:
            hw_snap["_is_temp_sweep"] = True
            t_start = first_cfg.get("temp_start", 0.0)
            t_stop  = first_cfg.get("temp_stop",  0.0)
            t_pts   = int(first_cfg.get("temp_npts", 1))
            hw_snap["_temp_sweep_start_K"] = t_start
            hw_snap["_temp_sweep_stop_K"]  = t_stop
            hw_snap["_temp_sweep_step_K"]  = (
                (t_stop - t_start) / (t_pts - 1) if t_pts > 1 else "")
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

        # The calibration tab has its own hidden config: points + integration
        # time come from its Time-scan settings group, not the scan config
        # selected in the left panel.
        ts = self.calib_panel.get_timescan_settings()
        cfg["act1_npts"]        = int(ts["npts"])
        cfg["integration_time"] = float(ts["int_time"])
        # The tab's own sensors (its hidden config), not the left panel's
        cal_sensors = ts.get("sensors") or []
        if cal_sensors:
            cfg["sensors"] = cal_sensors

        active = [s for s in cfg["sensors"] if s["enabled"]]
        if not active:
            QMessageBox.warning(
                self, "No sensors",
                "Enable at least one sensor in the Calibration tab's "
                "Time-scan config."); return

        # ── Setup lock ────────────────────────────────────────────────────────
        if not self._acquire_setup_lock():
            return

        n_pts = int(cfg.get("act1_npts", 101))
        self._current_scan_cfg = cfg
        self._calib_timescan = True

        # Plot the sensors marked visible in the right panel; they keep their
        # Y1/Y2 assignment (the calibration plot has a twin right axis).
        plot_sensors = [s for s in active
                        if s.get("plot_visible", True)
                        and s.get("y_axis", s.get("plot_axis", "Y1")) not in ("hidden", "—", "X")]
        self.calib_panel.focus_plot.setup_timescan(
            n_pts, plot_sensors if plot_sensors else active)
        self._setup_live_display(cfg, active)
        self._alloc_scan_data(cfg, active)

        self._worker = self._wire_worker(cfg, setup)
        self._scan_start_time = _time.time()
        self._status_bar_run_start(cfg, 1)   # calibration time scan = one file
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

    def _on_point_retrace(self, ix, iy, x_actual, vals):
        for lbl, v in vals.items():
            if lbl in self._scan_data_retrace:
                self._scan_data_retrace[lbl][iy, ix] = v
        if self._interleaved_2d:
            disp = self.right_panel.get_display_sensor()
            self.map2d_retrace.update_point(ix, iy, vals.get(disp, float("nan")))

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
        worker = self._worker or self._sl_worker or self._cs_settle
        if worker and worker.is_paused():
            from PyQt6.QtWidgets import QStyle
            self.pause_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")
            # Error popup — once per auto-pause event ("AUTO-PAUSED" marker is
            # emitted by every engine auto-pause path; a manual Pause never
            # carries it, so the popup only appears for real failures).
            self._tint_status_bar("paused")
            if "AUTO-PAUSED" in msg and not self._autopause_notified:
                self._autopause_notified = True
                QMessageBox.warning(
                    self, "Measurement paused",
                    f"{msg}\n\nThe scan is holding at the failing point — no "
                    "data has been recorded for it. Fix the device, then "
                    "press Resume to retry the same point (or Abort to stop).")
        elif worker:
            self._autopause_notified = False
            self._tint_status_bar("running")

    def _on_progress(self, done: int, total: int):
        """Record scan progress for the bottom status bar."""
        self._bar_last_done  = done
        self._bar_last_total = total
        if done == 1:
            self._scan_first_pt_time = _time.time()
        self._refresh_status_bar()

    def _on_worker_finished(self):
        # Append to lab notebook for this completed scan direction
        if self._last_fn and self._current_scan_cfg and not getattr(self, '_calib_timescan', False):
            setup = self._active_setup()
            nb = _nb_path(setup.get("notebook_dir", "~/moke_data"), "Cryo")
            entry = dict(self._current_scan_cfg)
            entry["_scan_start_time"] = self._scan_start_time
            entry["_hdf5_path"] = os.path.abspath(self._last_fn)
            append_measurement(nb, entry)

        # If more directions are queued, start the next one without releasing the lock.
        if self._dir_queue:
            # This direction's scan-file is complete — advance the run counter
            # and restamp per-scan timing before the next direction starts.
            self._status_bar_scan_done()
            next_cfg = self._dir_queue.pop(0)
            setup = self._active_setup()
            active = [s for s in next_cfg["sensors"] if s["enabled"]]
            self._current_scan_cfg = next_cfg
            self._setup_live_display(next_cfg, active)
            self._alloc_scan_data(next_cfg, active)
            self._scan_start_time = _time.time()
            self._last_fn = None
            self._worker = self._wire_worker(next_cfg, setup)
            self._worker.start()
            return

        release_lock(self._active_setup_name)
        self._status_bar_run_finish()
        self._scan_running = False; self._set_running(False)
        # Drop the finished worker: _toggle_pause/_on_status pick the target via
        # `self._worker or self._sl_worker`, so a stale finished single-scan
        # worker would swallow Pause clicks during a later scanlist run.
        # (Only in this terminal branch — the _dir_queue branch above re-assigns
        # self._worker for the next direction and returns early.)
        self._worker = None
        self._calib_timescan = False
        self._interleaved_2d = False
        self.map2d_retrace.hide()
        try:
            self.data_browser.refresh()
        except Exception:
            log.debug("Failed to refresh data browser after scan", exc_info=True)
        saved = []
        if self._last_fn:        saved.append(self._last_fn);        self._last_fn = None
        if self._last_fn_retrace: saved.append(self._last_fn_retrace); self._last_fn_retrace = None
        if saved:
            self._log_append("✓ Scan complete — saved " + ", ".join(saved), level="info")
        self._update_estimate()
        _setup = self._active_setup()
        _setup["server_sync_dir"] = self.server_dir.text().strip()
        def _done_sync(ok):
            QTimer.singleShot(0, lambda: self.status_lbl.setText(
                "Server sync complete" if ok else "Server sync partial (see log)"))
        sync_setup(self._active_setup_name, _setup, done_cb=_done_sync)

    def _toggle_pause(self):
        if not self._scan_running: return
        # Between currents a sweep runs a thermal-settle worker, or no worker
        # at all (refocus, phase transitions) — _cs_paused covers those, and is
        # honoured at every phase boundary by _cs_hold().
        worker = self._worker or self._sl_worker or self._cs_settle
        if worker is not None:
            was_paused = worker.is_paused()
            (worker.resume if was_paused else worker.pause)()
        elif self._cs_active:
            was_paused = self._cs_paused
        else:
            return
        if self._cs_active:
            self._cs_paused = not was_paused
        from PyQt6.QtWidgets import QStyle
        if was_paused:
            self.pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.pause_btn.setText("Pause")
            self._tint_status_bar("running")
        else:
            self.pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")
            self._tint_status_bar("paused")

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

        # ── Current sweep — refuse impossible current lists before locking ───
        sweep = self.sl_panel.cur_sweep
        currents = sweep.currents() if sweep.isChecked() else []
        if currents:
            serr = validate_sweep(
                currents, KEITHLEY_RANGES,
                auto_range=sweep.auto_range_cb.isChecked(),
                fixed_range=self.sl_panel.hw.range_combo.currentText())
            if serr:
                QMessageBox.warning(self, "Current sweep", serr)
                self.status_lbl.setText("Scanlist not started — " + serr)
                return
            if sweep.mode() == SETTLE_PLATEAU and not \
                    setup.get("focus_averagein", "").strip():
                QMessageBox.warning(
                    self, "Current sweep",
                    "The settle mode is 'Watch focus signal', but no focus "
                    "sensor is configured for this setup.\n\nSet one in Setup "
                    "Defaults, or switch the sweep to a fixed wait.")
                return

        # ── Setup lock ────────────────────────────────────────────────────────
        if not self._acquire_setup_lock():
            return

        # Scanlist settings are re-read per current in _launch_scanlist,
        # so the auto-name follows the amplitude that was just applied.

        # ── Build per-cycle config list (same direction logic as _start_scan) ──
        scan_x = cfg.get("scan_x", True)
        scan_y = cfg.get("scan_y", False)
        dirs1 = cfg.get("act1_directions",
                        [[cfg.get("act1_start", 0.0), cfg.get("act1_stop", 0.0)]])
        dirs2 = cfg.get("act2_directions",
                        [[cfg.get("act2_start", 0.0), cfg.get("act2_stop", 0.0)]])
        base_name = cfg["name"]

        if scan_x and scan_y:
            x_has_retrace = len(dirs1) > 1
            y_has_retrace = len(dirs2) > 1
            if x_has_retrace or y_has_retrace:
                interleave_axis = "x" if x_has_retrace else "y"
                c = copy.deepcopy(cfg)
                c["act1_start"], c["act1_stop"] = dirs1[0]
                c["act2_start"], c["act2_stop"] = dirs2[0]
                c["name"]             = f"{base_name}_trace"
                c["_interleaved_2d"]  = True
                c["_interleave_axis"] = interleave_axis
                c["_retrace_name"]    = f"{base_name}_retrace"
                cfg_list = [c]
            else:
                c = copy.deepcopy(cfg)
                c["act1_start"], c["act1_stop"] = dirs1[0]
                c["act2_start"], c["act2_stop"] = dirs2[0]
                cfg_list = [c]
        elif scan_x:
            use_sfx = len(dirs1) > 1
            cfg_list = []
            for idx, d1 in enumerate(dirs1):
                c = copy.deepcopy(cfg)
                c["act1_start"], c["act1_stop"] = d1
                if use_sfx:
                    c["name"] = f"{base_name}_{'trace' if idx == 0 else 'retrace'}"
                cfg_list.append(c)
        elif scan_y:
            use_sfx = len(dirs2) > 1
            cfg_list = []
            for idx, d2 in enumerate(dirs2):
                c = copy.deepcopy(cfg)
                c["act2_start"], c["act2_stop"] = d2
                if use_sfx:
                    c["name"] = f"{base_name}_{'trace' if idx == 0 else 'retrace'}"
                cfg_list.append(c)
        else:
            cfg_list = [copy.deepcopy(cfg)]

        # BD calibration is injected in _build_full_config() and carried into
        # each per-cycle cfg by the copy.deepcopy(cfg) above.

        if currents:
            self._cs_begin(cfg_list, currents)
        else:
            self._launch_scanlist(cfg_list, reset_status_bar=True)

    def _launch_scanlist(self, base_list: list, reset_status_bar: bool = True):
        """Start one ScanlistWorker on a copy of `base_list`.

        Called once for a plain scanlist, and once per current during a sweep
        — so every current gets its own hardware snapshot (the amplitude has
        just changed), its own auto-name with the right {I}mA token, and its
        own scanlist .txt.
        """
        setup     = self._active_setup()
        cfg_list  = copy.deepcopy(base_list)
        sl        = self.sl_panel.get_settings()   # re-read: the name follows amp
        first_cfg = cfg_list[0]
        active    = [s for s in first_cfg["sensors"] if s["enabled"]]
        self._sl_cfg_list = cfg_list

        # ── Hardware snapshot (written to HDF5 metadata + lab notebook) ─────
        is_temp_sweep = "temp_start" in first_cfg
        hw_snap = _read_hw_snapshot(setup, first_cfg.get("scan_type", "SPATIAL"),
                                    is_temp_sweep=is_temp_sweep)
        if is_temp_sweep:
            hw_snap["_is_temp_sweep"] = True
            t_start = first_cfg.get("temp_start", 0.0)
            t_stop  = first_cfg.get("temp_stop",  0.0)
            t_pts   = int(first_cfg.get("temp_npts", 1))
            hw_snap["_temp_sweep_start_K"] = t_start
            hw_snap["_temp_sweep_stop_K"]  = t_stop
            hw_snap["_temp_sweep_step_K"]  = (
                (t_stop - t_start) / (t_pts - 1) if t_pts > 1 else "")
        for c in cfg_list:
            c.update(hw_snap)

        self._current_scan_cfg = first_cfg
        self._setup_live_display(first_cfg, active); self._alloc_scan_data(first_cfg, active)

        self._sl_worker = ScanlistWorker(cfg_list, setup, sl["n_scans"], sl["list_name"],
                                         sl["relay_flip"], sl["field_flip"],
                                         setup_name=self._active_setup_name)
        self._sl_worker.point_done.connect(self._on_point)
        self._sl_worker.progress.connect(self._on_progress)
        self._sl_worker.cycle_done.connect(self._on_cycle_done)
        self._sl_worker.scan_done.connect(self._on_sl_scan_done)
        self._sl_worker.status_msg.connect(self._on_status)
        self._sl_worker.log_msg.connect(self._log_append)
        self._sl_worker.all_done.connect(self._on_scanlist_done)
        self._sl_worker.relay_changed.connect(self._on_scanlist_relay_changed)
        self._sl_worker.error_msg.connect(
            lambda m: self._log_append(f"\n⚠ ERROR:\n{m}", level="error"))
        self._sl_worker.finished.connect(self._on_sl_worker_finished)

        self._scan_start_time = _time.time()
        self._sl_scan_t0 = _time.time()
        # Status bar: one scan-file per (cycle × direction).  During a current
        # sweep the whole sweep is one run, started once in _cs_begin, so the
        # per-scanlist reset would wipe the elapsed time on every current.
        if reset_status_bar:
            self._status_bar_run_start(first_cfg, sl["n_scans"] * len(cfg_list))
            self.log_text.clear()
        self._scan_running = True; self._set_running(True)
        self._sl_worker.start()

    # ── Current sweep ────────────────────────────────────────────────────────
    # Mirrors MainWindow._cs_* in Samba_main/samba.py — the two main windows
    # are deliberately independent implementations (see CLAUDE.md §14).  The
    # only real differences are the Keithley setup-key names and that Cryo has
    # no "zero after scan"; keep the two in step when editing either.
    def _cs_begin(self, cfg_list: list, currents: list):
        """Start a current sweep: one scanlist per current, refocusing between.

        The setup lock is already held and is kept for the whole sweep — it is
        released in _cs_finish, not per scanlist.
        """
        self._cs_active    = True
        self._cs_abort     = False
        self._cs_paused    = False
        self._cs_currents  = list(currents)
        self._cs_idx       = 0
        self._cs_base_list = cfg_list

        sl = self.sl_panel.get_settings()
        self._status_bar_run_start(cfg_list[0],
                                   len(currents) * sl["n_scans"] * len(cfg_list))
        self._scan_running = True; self._set_running(True)
        self.log_text.clear()
        self._log_append(
            f"▶ Current sweep — {format_current_list(currents)} "
            f"({len(currents)} scanlists × {sl['n_scans']} cycles)", level="info")
        self._cs_step()

    def _cs_step(self):
        """Run the next current: apply it, settle, refocus, then the scanlist."""
        if self._cs_abort or self._cs_idx >= len(self._cs_currents):
            self._cs_finish(aborted=self._cs_abort)
            return

        def _go():
            mA = self._cs_currents[self._cs_idx]
            self._sb_cur.setText(f"{self._cs_idx + 1}/{len(self._cs_currents)}"
                                 f"  ({mA:.4g} mA)")
            self._log_append(
                f"── Current {self._cs_idx + 1}/{len(self._cs_currents)}: "
                f"{mA:.4g} mA ──", level="info")
            self._cs_apply_current(mA, self._cs_after_current)
        self._cs_hold(_go)

    def _cs_apply_current(self, mA: float, then):
        """Write range + amplitude to the Keithley, then call then(ok, mA, rng).

        Runs off the GUI thread: two TANGO writes to an unresponsive source
        would otherwise freeze the window for up to 20 s.
        """
        setup   = self._active_setup()
        dev     = setup.get("keithley_device", "")
        a_amp   = setup.get("keithley_attr_amplitude") or "amplitude"
        a_range = setup.get("keithley_attr_range") or "range"
        rng     = (pick_keithley_range(mA, KEITHLEY_RANGES)
                   if self.sl_panel.cur_sweep.auto_range_cb.isChecked() else "")
        cur_rng = self.sl_panel.hw.range_combo.currentText()

        def _finish(ok, msgs):
            def _apply():
                for m, lv in msgs:
                    self._log_append(m, level=lv)
                then(ok, mA, rng)
            self._post_to_main.emit(_apply)

        def _work():
            msgs = []
            proxy, cerr = fresh_proxy(dev)
            if cerr and TANGO_AVAILABLE:
                _finish(False, [(f"⚠ Keithley '{dev}' unreachable ({cerr})",
                                 "warning")])
                return
            ok = True
            if rng and rng != cur_rng:
                rerr = safe_write(proxy, a_range, rng)
                ok = ok and not rerr
                msgs.append((f"⚠ Keithley range → {rng} failed: {rerr}", "warning")
                            if rerr else (f"Keithley range → {rng}", "info"))
            # Zero amplitude means "output off" on this source, matching the
            # hardware panel's own amplitude write.
            if abs(mA) < 1e-9:
                try:
                    proxy.command_inout("Off")
                    msgs.append(("Keithley output OFF (amplitude = 0)", "info"))
                except Exception:
                    werr = safe_write(proxy, a_amp, 0.0)
                    ok = ok and not werr
                    msgs.append((f"⚠ Keithley off failed: {werr}", "warning")
                                if werr else ("Keithley amplitude → 0 mA", "info"))
            else:
                try:
                    proxy.command_inout("On")
                except Exception:
                    pass          # already on, or no On command
                werr = safe_write(proxy, a_amp, float(mA))
                ok = ok and not werr
                msgs.append((f"⚠ Keithley amplitude → {mA:.4g} mA failed: {werr}",
                             "warning") if werr else
                            (f"Keithley amplitude → {mA:.4g} mA", "info"))
            _finish(ok, msgs)

        threading.Thread(target=_work, daemon=True, name="cs_apply_current").start()

    def _cs_after_current(self, ok: bool, mA: float, rng: str):
        """Current is set — show it in the panels, then wait for thermalisation."""
        if self._cs_abort:
            self._cs_finish(aborted=True); return
        if not ok:
            # Measuring on regardless would write files whose {I}mA name and
            # whose hardware are different currents — worse than stopping.
            self._cs_auto_pause(
                f"⚠ AUTO-PAUSED — could not set the current source to "
                f"{mA:.4g} mA. Fix the Keithley, then press Resume to retry "
                f"this current.",
                lambda: self._cs_apply_current(mA, self._cs_after_current))
            return
        # Display only: setValue never writes to hardware (Enter does).  This is
        # also what re-stamps the scanlist auto-name with the new {I}mA token.
        self.sl_panel.hw.amp_spin.setValue(float(mA))
        if rng:
            idx = self.sl_panel.hw.range_combo.findText(rng)
            if idx >= 0:
                self.sl_panel.hw.range_combo.setCurrentIndex(idx)

        sweep = self.sl_panel.cur_sweep
        setup = self._active_setup()
        self._cs_settle = ThermalSettleWorker(
            mode=sweep.mode(),
            fixed_min=sweep.fixed_spin.value(),
            fl_dev=setup.get("focus_averagein", "").strip(),
            fl_attr=(setup.get("focus_attr", "") or "Value").strip() or "Value",
            detector=sweep.make_detector(),
            label=f"({self._cs_idx + 1}/{len(self._cs_currents)}, {mA:.4g} mA)")
        self._cs_settle.status_msg.connect(self.status_lbl.setText)
        self._cs_settle.log_msg.connect(self._log_append)
        self._cs_settle.done_.connect(self._cs_settle_done)
        # Live focus trace on the 1D plot so the settling is visible while it
        # happens.  The plot is wiped FIRST, for both modes: otherwise the
        # previous current's trace (or, on the first current, the last scan)
        # stays on screen for the whole settle, which reads as a curve that
        # never returns to zero between currents.
        self._cs_fl_t, self._cs_fl_v = [], []
        self._cs_fl_label = f"{self._cs_idx + 1}/{len(self._cs_currents)}, {mA:.4g} mA"
        try:
            self.plot1d.clear()
        except Exception:
            log.debug("Could not clear the 1D plot for the settle", exc_info=True)
        self._cs_settle.sample.connect(self._cs_on_fl_sample)
        self._cs_settle.start()

    def _cs_on_fl_sample(self, elapsed_s: float, fl: float):
        """Draw the focus signal against time while the sample thermalises.

        show_static drops the scan buffers, which is safe here: the plot is
        re-initialised by _setup_live_display when the scanlist starts.
        """
        self._cs_fl_t.append(float(elapsed_s))
        self._cs_fl_v.append(float(fl))
        if len(self._cs_fl_t) < 2:
            return                       # a single point has nothing to show
        try:
            self.plot1d.show_static(
                self._cs_fl_t, self._cs_fl_v,
                xlabel="Time since current change (s)",
                ylabel="Focus signal",
                title=f"Thermal settle — {self._cs_fl_label}")
            # Anchor the time axis at 0 so the trace visibly grows from the
            # moment the current changed, instead of the axis starting at
            # whenever the first sample happened to land.
            self.plot1d.ax1.set_xlim(left=0.0)
            self.plot1d.canvas.draw_idle()
        except Exception:
            log.debug("Focus-settle plot update failed", exc_info=True)

    def _cs_settle_done(self, reason: str):
        # done_ is emitted from inside run(), so the QThread may not have
        # finished when this handler runs on the GUI thread.  Dropping the last
        # reference there would destroy a still-running QThread; wait() first,
        # which returns immediately because run() has already returned.
        worker, self._cs_settle = self._cs_settle, None
        if worker is not None:
            worker.wait(2000)
        if self._cs_abort or reason == "abort":
            self._cs_finish(aborted=True); return

        def _go():
            if not self.sl_panel.cur_sweep.refocus_cb.isChecked():
                self._launch_scanlist(self._cs_base_list, reset_status_bar=False)
                return
            # Refocus at the focus position of the scan axis (0 = middle of the
            # device); the second axis is parked too when the scan is a 2D map.
            also_y = bool(self._cs_base_list[0].get("scan_y"))
            self._log_append("Refocusing…", level="info")
            # focus_pos=0: the middle of the device on the swept axis.
            if not self.calib_panel.run_autofocus_async(
                    self._cs_focus_done, also_y=also_y, focus_pos=0.0):
                self._log_append(
                    "⚠ Autofocus could not start (no FL sensor configured?) — "
                    "running the scanlist at the current focus", level="warning")
                self._launch_scanlist(self._cs_base_list, reset_status_bar=False)
        self._cs_hold(_go)

    def _cs_focus_done(self, res: dict):
        if self._cs_abort:
            self._cs_finish(aborted=True); return
        if not res.get("ok"):
            self._log_append(f"⚠ Refocus failed: {res.get('msg', '')}",
                             level="warning")
        elif not res.get("reliable"):
            self._log_append(f"⚠ Refocus unreliable: {res.get('msg', '')}",
                             level="warning")
        else:
            self._log_append(
                f"✓ Refocused at Z = {res['z']:.3f} (FL = {res['fl']:.4g})",
                level="info")

        run_it = lambda: self._launch_scanlist(self._cs_base_list,
                                               reset_status_bar=False)
        bad = not (res.get("ok") and res.get("reliable"))
        if bad and self.sl_panel.cur_sweep.pause_bad_cb.isChecked():
            self._cs_auto_pause(
                "⚠ AUTO-PAUSED — the refocus did not find a reliable focus. "
                "Focus by hand on the Calibration tab, then press Resume.",
                run_it)
            return
        self._cs_hold(run_it)

    def _cs_auto_pause(self, msg: str, retry):
        """Hold the sweep and tell the operator why, the same way the scan
        engine's auto-pause does — Resume retries `retry`, Abort ends the run."""
        from PyQt6.QtWidgets import QStyle
        self._log_append(msg, level="warning")
        self.status_lbl.setText(msg)
        self._cs_paused = True
        self.pause_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.pause_btn.setText("Resume")
        self._tint_status_bar("paused")
        if not self._autopause_notified:
            self._autopause_notified = True
            QMessageBox.warning(self, "Current sweep paused", msg)
        self._cs_hold(retry)

    def _cs_hold(self, then):
        """Run `then` once the sweep is neither paused nor aborted.

        Every phase transition goes through this, so a Pause pressed during a
        refocus (which has no worker of its own) still takes effect — at the
        next boundary rather than mid-motion.
        """
        if self._cs_abort:
            self._cs_finish(aborted=True); return
        if not self._cs_paused:
            self._autopause_notified = False
            then(); return
        QTimer.singleShot(200, lambda: self._cs_hold(then))

    def _cs_finish(self, aborted: bool):
        """End the sweep: restore the source and release the lock."""
        self._cs_active = False
        self._cs_paused = False
        sweep = self.sl_panel.cur_sweep
        if sweep.off_at_end_cb.isChecked():
            self._cs_set_output_off()
        else:
            last = (self._cs_currents[min(self._cs_idx, len(self._cs_currents) - 1)]
                    if self._cs_currents else 0.0)
            self._log_append(
                f"⚠ Current source left ON at {last:.4g} mA "
                f"('Output off at end' is unchecked)", level="warning")
        if not aborted:
            self._status_bar_run_finish()
        self._sb_cur.setText("—")
        release_lock(self._active_setup_name)
        self._scan_running = False
        self._set_running(False)
        self._sl_worker = None
        self._log_append(
            "■ Current sweep aborted" if aborted else
            f"✓ Current sweep complete — {len(self._cs_currents)} currents",
            level="warning" if aborted else "info")

    def _cs_set_output_off(self):
        """Turn the current source off at the end of a sweep (background)."""
        setup = self._active_setup()
        dev   = setup.get("keithley_device", "")
        a_amp = setup.get("keithley_attr_amplitude") or "amplitude"

        def _work():
            proxy, cerr = fresh_proxy(dev)
            if cerr and TANGO_AVAILABLE:
                msg = (f"⚠ Could not turn the current source off — "
                       f"'{dev}' unreachable ({cerr})", "warning")
            else:
                try:
                    proxy.command_inout("Off")
                    msg = ("Current source output OFF (sweep finished)", "info")
                except Exception:
                    werr = safe_write(proxy, a_amp, 0.0)
                    msg = ((f"⚠ Current source off failed: {werr}", "warning")
                           if werr else
                           ("Current source amplitude → 0 mA (sweep finished)",
                            "info"))
            self._post_to_main.emit(lambda: (self._log_append(msg[0], level=msg[1]),
                                             self.sl_panel.hw.amp_spin.setValue(0.0)))

        threading.Thread(target=_work, daemon=True, name="cs_output_off").start()

    def _on_sl_scan_done(self, idx: int, fn: str):
        """Per-file callback from ScanlistWorker — updates status bar and
        records a lab-notebook entry for the file just written (each
        scanlist file previously produced no notebook row at all)."""
        self._status_bar_scan_done()
        t_start = self._sl_scan_t0
        self._sl_scan_t0 = _time.time()   # next file starts now
        cfg_list = getattr(self, "_sl_cfg_list", None) or (
            [self._current_scan_cfg] if self._current_scan_cfg else [])
        if not fn or not cfg_list:
            return
        try:
            setup = self._active_setup()
            nb = _nb_path(setup.get("notebook_dir", "~/moke_data"), "Cryo")
            base_cfg = cfg_list[idx % len(cfg_list)]
            entry = dict(base_cfg)
            entry["_scan_start_time"] = t_start
            entry["_hdf5_path"] = os.path.abspath(fn)
            # Mark this row as part of the scanlist (blank for single scans).
            if self._sl_worker is not None:
                entry["_scanlist_name"] = getattr(self._sl_worker, "list_name", "")
            append_measurement(nb, entry)
        except Exception:
            log.debug("Lab notebook append failed for scanlist file", exc_info=True)

    def _on_scanlist_done(self, txt_path):
        # During a current sweep this is one scanlist of several — the run is
        # not over, so the bar must not jump to 100 %.
        if not self._cs_active:
            self._status_bar_run_finish()
        try:
            self.data_browser.refresh()
        except Exception:
            log.debug("Failed to refresh data browser after scanlist", exc_info=True)
        self._log_append(f"✓ Scanlist complete — saved {txt_path}", level="info")
        _setup = self._active_setup()
        _setup["server_sync_dir"] = self.server_dir.text().strip()
        def _done_sync(ok):
            QTimer.singleShot(0, lambda: self.status_lbl.setText(
                "Server sync complete" if ok else "Server sync partial (see log)"))
        sync_setup(self._active_setup_name, _setup, done_cb=_done_sync)

    def _on_sl_worker_finished(self):
        self._sl_worker = None
        if self._cs_active:
            # One current of a sweep is done — the lock stays held and the run
            # keeps going.  _cs_step() ends the sweep if this was the last
            # current or if Abort was pressed.
            self._cs_idx += 1
            self._cs_step()
            return
        release_lock(self._active_setup_name)
        self._set_running(False)
        self._scan_running = False

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
        if not self._scan_running: return
        if self._cs_active:
            # Stop the whole sweep, not just the scanlist in progress.  The
            # phase that is actually running picks this up: a settle worker
            # via abort(), a refocus via _stop_autofocus, a hold via _cs_hold.
            self._cs_abort  = True
            self._cs_paused = False
            if self._cs_settle:
                self._cs_settle.abort()
            try:
                self.calib_panel._stop_autofocus()
            except Exception:
                log.debug("Autofocus abort failed", exc_info=True)
            if not self._sl_worker and not self._cs_settle:
                # Nothing running to notice the flag (mid-refocus or between
                # phases) — _cs_hold/_cs_focus_done will finish the sweep.
                self.status_lbl.setText("Aborting current sweep…")
                return
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
            if (self._interleaved_2d and disp and disp in self._scan_data_retrace
                    and self.map2d_retrace._img is not None):
                self.map2d_retrace.switch_sensor(
                    self._scan_data_retrace[disp], disp + " (retrace)")
        else:
            self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(),
                                     self.right_panel.get_x_key())

    def _on_display_changed(self, sensor, cmap):
        if sensor and sensor in self._scan_data and self.map2d._img is not None:
            self.map2d.switch_sensor(self._scan_data[sensor], sensor)
        self.map2d.set_colormap(cmap)
        if self._interleaved_2d and sensor and sensor in self._scan_data_retrace:
            if self.map2d_retrace._img is not None:
                self.map2d_retrace.switch_sensor(
                    self._scan_data_retrace[sensor], sensor + " (retrace)")
            self.map2d_retrace.set_colormap(cmap)

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
            top = int(self.height() * 0.50)
            self._v_split.setSizes([top, self.height() - top])
            self._split_initialised = True

    def _restore_geometry(self):
        """Restore saved geometry, or open at a sensible preferred size — but
        never larger than the current screen, and never positioned off-display,
        so nothing is clipped on first open (or after a resolution/monitor change)."""
        scr   = QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        s = QSettings("ETH-Intermag", "SambaCryo")
        g = s.value("geometry")
        restored = bool(g) and self.restoreGeometry(bytes(g))
        if not restored:
            self.resize(1360, 920)
        if avail:
            # Clamp size to the usable screen (small margin for window decorations)
            w = min(self.width(),  avail.width()  - 20)
            h = min(self.height(), avail.height() - 60)
            if w < self.width() or h < self.height():
                self.resize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))
            # Pull back on-screen if a saved position lands off the display
            x = min(max(self.x(), avail.left()),
                    max(avail.right()  - self.width(),  avail.left()))
            y = min(max(self.y(), avail.top()),
                    max(avail.bottom() - self.height(), avail.top()))
            if (x, y) != (self.x(), self.y()):
                self.move(x, y)

    def closeEvent(self, ev):
        if self._scan_running:
            r = QMessageBox.question(self, "Scan running", "Abort and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r == QMessageBox.StandardButton.No: ev.ignore(); return
            # 10 s, not 2 s: one point can legitimately take longer than 2 s
            # (lock-in settling + integration, and far longer on a field or
            # temperature point), and abandoning the thread mid-HDF5-write is
            # how a file gets truncated.
            self._cs_active = False    # no further currents after this
            self._cs_abort  = True
            for w in [self._worker, self._sl_worker, self._cs_settle]:
                if w: w.abort(); w.wait(10000)
            # _on_worker_finished may never run once the event loop is tearing
            # down, so release the setup lock here or the rig stays "busy" to
            # every other computer until the 12 h stale-lock takeover.
            release_lock(self._active_setup_name)
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
    """Configure the root logger (rotating file + console).

    Delegates to the shared core implementation, which Samba_main now
    uses as well — one copy of the logging policy for both apps.
    """
    setup_logging("samba_cryo")


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
