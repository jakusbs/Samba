#!/usr/bin/env python3
"""
samba.py — Samba v3 — ETH Zürich Intermag Lab
Entry point: MainWindow wires together all modules.

Requirements:  pip install pytango PyQt6 matplotlib h5py numpy
Usage:         export TANGO_HOST=192.168.1.1:10000 && python samba.py
"""
import logging
import sys, os, copy, threading
import time as _time
import numpy as np

# Ensure repo root is on sys.path so that `import core` resolves correctly,
# regardless of the working directory when the script is launched.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
from typing import Dict, Optional, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTabWidget, QTabBar, QTextEdit, QMessageBox, QSplitter,
    QComboBox, QLineEdit, QPushButton, QFileDialog, QButtonGroup, QFrame, QStyle,
    QStatusBar
)
from PyQt6.QtCore import QTimer, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QShortcut, QKeySequence, QTextCharFormat, QColor, QTextCursor, QIcon

try:
    import tango
    TANGO_AVAILABLE = True
except ImportError:
    TANGO_AVAILABLE = False

from config  import SETUP_NAMES, X_NATURAL, X_TIME, DEFAULT_SENSORS, load_setup, save_setup, make_default_config
from hardware import get_proxy, safe_read, evict_proxy
from scan    import ScanWorker, ScanlistWorker
from lab_notebook import append_measurement, notebook_path as _nb_path
from plot_widgets import Live2DWidget, Live1DWidget
from panels  import (ConfigListPanel, RightPanel,
                     TrajectoryPanel, ScanlistPanel, SetupDefaultsPanel)
from panels.bd_calibration import BDCalibrationPanel
from data_browser import DataBrowserPanel
from script_console import ScriptConsolePanel
from calibration import CalibrationPanel
from device_registry import DeviceRegistryPanel, load_registry, registry_to_sensors
import play_intro

log = logging.getLogger(__name__)

try:
    from setup_lock import acquire_lock, release_lock
except Exception as _e:
    log.warning("setup_lock import failed (%s) — locking disabled", _e)
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

def _read_hw_snapshot(setup: dict, scan_type: str) -> dict:
    """Read key hardware state immediately before a scan starts.

    Returns a dict of hw_* keys that the scan runner writes into HDF5 metadata
    and that the lab notebook records.  All reads are best-effort — a failed
    device read leaves the corresponding key absent (not an error).

    ``scan_type`` controls which keys are suppressed:
      - FIELD: hw_field_mT skipped (field is the swept axis)
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
    k_dev  = setup.get("keithley_device", "")
    for hw_key, attr_key in [
        ("hw_keithley_amplitude_mA",  "keithley_amplitude_attr"),
        ("hw_keithley_frequency_Hz",  "keithley_frequency_attr"),
        ("hw_keithley_range",         "keithley_range_attr"),
        ("hw_keithley_compliance_V",  "keithley_compliance_attr"),
    ]:
        v = _read(k_dev, setup.get(attr_key, ""))
        if v is not None:
            snap[hw_key] = v
    # Keithley output current readback ("I out" in the hardware panel)
    v = _read(k_dev, setup.get("keithley_current_attr", "current"))
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

    # Relay position
    v = _read(setup.get("relay_device", ""), setup.get("relay_attr", ""))
    if v is not None:
        snap["hw_relay_state"] = v

    # Field + magnet current at scan start — skip when field is being swept
    if scan_type != "FIELD":
        v = _read(setup.get("magnet_device", ""), setup.get("magnet_field_attr", ""))
        if v is not None:
            snap["hw_field_mT"] = v
        v = _read(setup.get("magnet_device", ""), setup.get("magnet_current_attr", ""))
        if v is not None:
            snap["hw_magnet_current_A"] = v

    # Stage position at scan start — only relevant for SPATIAL scans
    if scan_type not in ("FIELD", "DC_HYST"):
        v = _read(setup.get("act1_device", ""), setup.get("act1_attr", ""))
        if v is not None:
            snap["hw_act1_pos"] = v
        v = _read(setup.get("act2_device", ""), setup.get("act2_attr", ""))
        if v is not None:
            snap["hw_act2_pos"] = v

    return snap


# ─────────────────────────────────────────────────────────────────────────────
# Stylesheet (catppuccin-mocha)
# ─────────────────────────────────────────────────────────────────────────────
DARK_STYLE = """
QMainWindow,QWidget{background:#1e1e2e;color:#cdd6f4;
  font-family:'Segoe UI',Ubuntu,sans-serif;font-size:12px;}
QGroupBox{border:1px solid #45475a;border-radius:6px;
  margin-top:9px;padding-top:9px;font-weight:bold;color:#89b4fa;}
QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}
QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox{
  background:#313244;border:1px solid #45475a;
  border-radius:4px;padding:3px 6px;color:#cdd6f4;}
QLineEdit:focus,QDoubleSpinBox:focus,QSpinBox:focus{border:1px solid #89b4fa;}
QPushButton{background:#313244;border:1px solid #45475a;
  border-radius:5px;padding:5px 12px;color:#cdd6f4;}
QPushButton:hover{background:#45475a;}
QPushButton:pressed{background:#585b70;}
QPushButton:checked{background:#585b70;border:1px solid #89b4fa;color:#cdd6f4;}
QPushButton#start_btn{background:#a6e3a1;color:#1e1e2e;font-weight:bold;border:none;}
QPushButton#start_btn:hover{background:#94d992;}
QPushButton#abort_btn{background:#f38ba8;color:#1e1e2e;font-weight:bold;border:none;}
QPushButton#abort_btn:hover{background:#e07a97;}
QPushButton#pause_btn{background:#fab387;color:#1e1e2e;font-weight:bold;border:none;}
QPushButton#pause_btn:hover{background:#e8976e;}
QTextEdit{background:#12121f;border:1px solid #313244;
  border-radius:4px;color:#a6e3a1;font-family:'Courier New',monospace;font-size:10px;}
QCheckBox{spacing:6px;}
QCheckBox::indicator{width:14px;height:14px;
  border:1px solid #45475a;border-radius:3px;background:#313244;}
QCheckBox::indicator:checked{background:#89b4fa;}
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

#play_intro.play_intro()
# ─────────────────────────────────────────────────────────────────────────────
# MainWindow
# ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    # General-purpose signal for posting callables to the main thread from
    # background threads. QTimer.singleShot(0, context, lambda) is not reliably
    # delivered in PyQt6 when called from a plain threading.Thread; signals are.
    _post_to_main = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Samba v3 — ETH Zürich")
        # Modest minimum so the window fits smaller laptop screens; the larger
        # *preferred* opening size is applied (screen-clamped) in _restore_geometry.
        self.setMinimumSize(1180, 640)

        self._setups:            Dict[str, dict]          = {}
        self._worker:            Optional[ScanWorker]     = None
        self._sl_worker:         Optional[ScanlistWorker] = None
        self._scan_running:      bool                     = False
        self._scan_data:         Dict[str, np.ndarray]    = {}
        self._last_fn:           Optional[str]            = None
        self._active_setup_name: str                      = "Green"
        # DC hysteresis live-plot accumulators
        self._dc_loop_x:   list = []
        self._dc_loop_y:   Dict[str, list] = {}
        self._last_dc_cycle: int = 0
        self._active_cfg_idx:    int                      = 0
        self._current_scan_cfg:  dict                     = {}
        self._calib_timescan:    bool                     = False
        self._scan_start_time:   float                    = 0.0
        self._sl_scan_t0:        float                    = 0.0
        self._trmoke_x_factor:   Optional[float]          = None
        self._meta_syncing:      bool                     = False
        self._timing_syncing:    bool                     = False

        # ── Bottom status-bar state (live scan progress) ────────────────────
        self._run_start_time:     float = 0.0   # set once per run, NOT reset per direction
        self._run_scans_total:    int   = 1     # total scan-files this run will produce
        self._run_scans_done:     int   = 0     # scan-files fully completed
        self._scan_first_pt_time: float = 0.0   # time the 1st point of current scan arrived
        self._bar_int_time:       float = 0.1   # integration_time for dead-time calc
        self._bar_last_done:      int   = 0     # last progress(done) seen
        self._bar_last_total:     int   = 1     # last progress(total) seen

        for n in SETUP_NAMES:
            self._setups[n] = load_setup(n)

        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._connect_signals()
        self._load_active_config()

        self._rb_timer = QTimer(self); self._rb_timer.setInterval(500)
        self._rb_timer.timeout.connect(self._poll_field_readback)
        self._rb_timer.start()
        self._rb_poll_active = False   # guard: skip tick if previous poll still running

        # Wire cross-thread callable dispatcher
        self._post_to_main.connect(lambda fn: fn())

        # Read hardware panels once after the window is shown.
        # Staggered: traj first, then sl 600ms later, to avoid simultaneous ZI reads.
        QTimer.singleShot(300, self._initial_hw_read)

        self._restore_geometry()

    def _active_setup(self) -> dict:
        return self._setups[self._active_setup_name]

    def _probe_devices(self, status_callback=None):
        """Check critical hardware devices at startup and warn if any are unreachable.

        When *status_callback* is provided probes run in parallel background threads
        and the callback is invoked on the GUI thread as each result arrives.
        Without a callback, probes block and show a QMessageBox for unavailable devices.
        """
        import threading as _threading
        from hardware import fresh_proxy, is_sim_proxy

        # Collect one representative path per device type across all setups
        candidates: dict = {}
        for setup in self._setups.values():
            for key, label in [
                ("act1_device",   "Stage"),
                ("zi_device",     "Lock-in"),
                ("magnet_device", "Magnet"),
                ("keithley_device", "Keithley"),
            ]:
                path = setup.get(key, "").strip()
                if path and label not in candidates:
                    candidates[label] = path

        _PROBE_TIMEOUT = 6.0

        results: dict = {}
        threads: dict = {}
        for name, path in candidates.items():
            def _probe(n=name, p=path):
                results[n] = fresh_proxy(p)
            t = _threading.Thread(target=_probe, daemon=True)
            t.start()
            threads[name] = t

        if status_callback:
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
            for name in threads:
                if name not in reported:
                    results[name] = (None, "connection timed out")
        else:
            for t in threads.values():
                t.join(_PROBE_TIMEOUT)

        unavailable = []
        for name, path in candidates.items():
            proxy, err = results.get(name, (None, "timeout"))
            if err or is_sim_proxy(proxy):
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

        # ── Helper: thin vertical separator ──────────────────────────────────
        def _sep():
            f = QFrame(); f.setFrameShape(QFrame.Shape.VLine)
            f.setFixedWidth(1); f.setFixedHeight(26)
            f.setStyleSheet("background:#313244;border:none;")
            w = QWidget(); wl = QHBoxLayout(w)
            wl.setContentsMargins(8, 0, 8, 0); wl.addWidget(f)
            return w

        # ── 1. Setup selector — exclusive pill buttons ─────────────────────
        self._setup_tab_bar = QTabBar()   # kept for signal compatibility
        self._setup_tab_bar.setVisible(False)
        for sn in SETUP_NAMES:
            self._setup_tab_bar.addTab(sn)
        self._setup_tab_bar.currentChanged.connect(self._action_bar_setup_clicked)

        self._setup_btn_grp = QButtonGroup(self); self._setup_btn_grp.setExclusive(True)
        _SETUP_COLORS = {
            "Green": ("#00a86b", "#1e1e2e"),
            "IR":    ("#ab4b52", "#1e1e2e"),
            "Cryo":  ("#0080fe", "#1e1e2e"),
        }

        for idx, sn in enumerate(SETUP_NAMES):
            fg, bg = _SETUP_COLORS[sn]
            b = QPushButton(sn)   # plain text — no Unicode strikethrough
            b.setCheckable(True); b.setChecked(idx == 0)
            b.setFixedHeight(30); b.setMinimumWidth(52)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            radius = ("border-radius:5px;" if len(SETUP_NAMES) == 1 else
                      "border-top-left-radius:5px;border-bottom-left-radius:5px;"
                      "border-top-right-radius:0;border-bottom-right-radius:0;" if idx == 0 else
                      "border-top-left-radius:0;border-bottom-left-radius:0;"
                      "border-top-right-radius:5px;border-bottom-right-radius:5px;"
                      if idx == len(SETUP_NAMES)-1 else "border-radius:0;")
            b.setStyleSheet(
                f"QPushButton{{background:#252538;border:1px solid #45475a;"
                f"color:#6c7086;font-size:11px;font-weight:bold;padding:0 10px;{radius}}}"
                f"QPushButton:hover{{background:#313244;color:#cdd6f4;}}"
                f"QPushButton:pressed{{background:#313244;color:#cdd6f4;}}"
                f"QPushButton:checked{{background:{fg};color:{bg};border-color:{fg};}}")
            b.clicked.connect(lambda _, i=idx: self._setup_pill_clicked(i))
            self._setup_btn_grp.addButton(b, idx)
            ab.addWidget(b)

        ab.addWidget(_sep())

        # ── 2. Scan control buttons ────────────────────────────────────────
        # Always visible and enabled — _scan_running / _worker guards prevent
        # unwanted actions; no jarring enable/disable state changes.
        _BTN_H = 30

        # Qt built-in standard icons — always available, no theme dependency
        _style = self.style()
        _ico_play  = _style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        _ico_pause = _style.standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        _ico_stop  = _style.standardIcon(QStyle.StandardPixmap.SP_MediaStop)

        self.start_btn = QPushButton()
        self.start_btn.setObjectName("start_btn")
        self.start_btn.setFixedHeight(_BTN_H); self.start_btn.setMinimumWidth(90)
        self.start_btn.setIcon(_ico_play)
        self.start_btn.setText("Start")
        self.start_btn.setStyleSheet(
            "QPushButton{background:#a6e3a1;color:#1e1e2e;font-weight:bold;"
            "border:none;border-radius:5px;padding:0 12px;}"
            "QPushButton:hover{background:#94d992;}"
            "QPushButton:pressed{background:#a6e3a1;}")

        self.pause_btn = QPushButton()
        self.pause_btn.setObjectName("pause_btn")
        self.pause_btn.setFixedHeight(_BTN_H); self.pause_btn.setMinimumWidth(90)
        self.pause_btn.setIcon(_ico_pause)
        self.pause_btn.setText("Pause")
        self.pause_btn.setStyleSheet(
            "QPushButton{background:#fab387;color:#1e1e2e;font-weight:bold;"
            "border:none;border-radius:5px;padding:0 12px;}"
            "QPushButton:hover{background:#e8976e;}"
            "QPushButton:pressed{background:#fab387;}")

        self.abort_btn = QPushButton()
        self.abort_btn.setObjectName("abort_btn")
        self.abort_btn.setFixedHeight(_BTN_H); self.abort_btn.setMinimumWidth(90)
        self.abort_btn.setIcon(_ico_stop)
        self.abort_btn.setText("Abort")
        self.abort_btn.setStyleSheet(
            "QPushButton{background:#f38ba8;color:#1e1e2e;font-weight:bold;"
            "border:none;border-radius:5px;padding:0 12px;}"
            "QPushButton:hover{background:#e07a97;}"
            "QPushButton:pressed{background:#f38ba8;}")

        for b in [self.start_btn, self.pause_btn, self.abort_btn]:
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ab.addWidget(b)
            ab.setSpacing(4)

        ab.addWidget(_sep())

        # ── 3. Save directory ─────────────────────────────────────────────
        ab.addSpacing(4)
        _dir_lbl = QLabel("Dir:")
        _dir_lbl.setStyleSheet("color:#89b4fa;font-size:11px;font-weight:bold;")
        ab.addWidget(_dir_lbl)
        ab.addSpacing(4)
        self.save_dir = QLineEdit(os.path.expanduser("~/moke_data"))
        self.save_dir.setMinimumWidth(180)
        self.save_dir.setFixedHeight(28)
        self.save_dir.setPlaceholderText("Save directory…")
        self.save_dir.setStyleSheet(
            "QLineEdit{background:#313244;border:1px solid #585b70;border-radius:4px;"
            "padding:2px 6px;color:#cdd6f4;font-size:11px;}"
            "QLineEdit:focus{border:1px solid #89b4fa;}")
        ab.addWidget(self.save_dir, stretch=1)
        ab.addSpacing(4)
        browse_btn = QPushButton()
        browse_btn.setFixedSize(28, 28)
        browse_btn.setToolTip("Browse save directory")
        _fi = QIcon.fromTheme("folder-open")
        if not _fi.isNull():
            browse_btn.setIcon(_fi)
        else:
            browse_btn.setText("…")
        browse_btn.setStyleSheet(
            "QPushButton{background:#252538;border:1px solid #45475a;border-radius:4px;padding:0;}"
            "QPushButton:hover{background:#313244;}"
            "QPushButton:pressed{background:#252538;}")
        browse_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        browse_btn.clicked.connect(self._browse_save_dir)
        ab.addWidget(browse_btn)

        main_v.addWidget(action_bar)

        # ── Server sync bar ───────────────────────────────────────────────────
        _srv_bar = QWidget(); _srv_bar.setFixedHeight(34)
        _srv_bar.setObjectName("server_bar")
        _srv_bar.setStyleSheet(
            "#server_bar{background:#0e0e1e;border:1px solid #313244;border-radius:6px;}")
        _srv_row = QHBoxLayout(_srv_bar)
        _srv_row.setContentsMargins(8, 4, 8, 4); _srv_row.setSpacing(4)
        _srv_lbl = QLabel("Server:")
        _srv_lbl.setStyleSheet("color:#89b4fa;font-size:11px;font-weight:bold;")
        _srv_row.addWidget(_srv_lbl)
        self.server_dir = QLineEdit()
        self.server_dir.setFixedHeight(24)
        self.server_dir.setPlaceholderText("Server sync directory (leave blank to disable)…")
        self.server_dir.setStyleSheet(
            "QLineEdit{background:#1e1e2e;border:1px solid #45475a;border-radius:4px;"
            "padding:2px 6px;color:#a6adc8;font-size:10px;}"
            "QLineEdit:focus{border:1px solid #89b4fa;}")
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
            "QPushButton{background:#1e1e2e;border:1px solid #89b4fa;border-radius:4px;"
            "color:#89b4fa;font-size:11px;font-weight:bold;padding:0 8px;}"
            "QPushButton:hover{background:#252538;}"
            "QPushButton:pressed{background:#1e1e2e;}")
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
        for title, widget_attr, widget_cls in [
                ("2D Map", "map2d",  Live2DWidget),
                ("1D Plot","plot1d", Live1DWidget),
        ]:
            tab = QWidget(); lay = QVBoxLayout(tab); lay.setContentsMargins(2, 4, 2, 0)
            w   = widget_cls(); setattr(self, widget_attr, w); lay.addWidget(w)
            self.live_tabs.addTab(tab, title)

        self.calib_panel = CalibrationPanel(self._active_setup,
                                              config_getter=self._build_full_config)
        self.live_tabs.addTab(self.calib_panel, "Calibration")
        self.live_tabs.currentChanged.connect(self._on_live_tab_changed)

        tlog = QWidget(); tlol = QVBoxLayout(tlog); tlol.setContentsMargins(2, 4, 2, 0)
        # Log filter row
        log_hdr = QHBoxLayout(); log_hdr.setSpacing(6)
        log_hdr.addWidget(QLabel("Filter:"))
        self.log_filter = QComboBox()
        self.log_filter.addItems(["All", "Errors only", "Warnings + Errors"])
        self.log_filter.setFixedWidth(140)
        log_hdr.addWidget(self.log_filter)
        log_hdr.addStretch()
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
        h_split.setSizes([215, 760, 360])
        h_split.setStretchFactor(0, 0)   # config list: fixed
        h_split.setStretchFactor(1, 1)   # center: stretches
        h_split.setStretchFactor(2, 0)   # right panel: fixed
        v_split.addWidget(h_split)

        # ── Bottom: bottom tabs only (action bar is at the top now) ──────────
        bottom_w = QWidget(); bw_l = QVBoxLayout(bottom_w)
        bw_l.setContentsMargins(0, 0, 0, 0); bw_l.setSpacing(3)

        # ── Bottom tabs ──────────────────────────────────────────────────────
        self.bottom_tabs = QTabWidget(); self.bottom_tabs.setMinimumHeight(80)
        self.traj_panel  = TrajectoryPanel(self._active_setup)
        self.sl_panel    = ScanlistPanel(self._active_setup)
        self.data_browser = DataBrowserPanel(
            lambda: self._active_setup().get("save_dir", "~/moke_data"))
        self.bd_cal_panel = BDCalibrationPanel()
        self.bottom_tabs.addTab(self.traj_panel,   "Trajectory")
        self.bottom_tabs.addTab(self.sl_panel,     "Scanlist")
        self.bottom_tabs.addTab(self.bd_cal_panel, "BD Calibration")
        self.bottom_tabs.addTab(self.data_browser,  "Data Browser")
        self.script_console = ScriptConsolePanel()
        self.bottom_tabs.addTab(self.script_console, "Script")
        self.dev_registry = DeviceRegistryPanel()
        self.bottom_tabs.addTab(self.dev_registry, "Device Registry")
        self.setup_defaults = SetupDefaultsPanel()
        self.bottom_tabs.addTab(self.setup_defaults, "Setup Defaults")
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
    def _build_status_bar(self):
        """Seven-field QStatusBar showing live scan-run progress."""
        sb = QStatusBar()
        self.setStatusBar(sb)
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

        self._sb_scan    = _mk_field()
        self._sb_start   = _mk_field()
        self._sb_elapsed = _mk_field()
        self._sb_runleft = _mk_field()
        self._sb_scanleft= _mk_field()
        self._sb_dead    = _mk_field()
        self._sb_done    = _mk_field()
        fields = [
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
        self.cfg_list.load_setups(self._setups)
        self.cfg_list.config_selected.connect(self._on_config_selected)
        self.cfg_list.new_config_requested.connect(self._on_new_config)
        self.cfg_list.config_deleted.connect( self._on_config_deleted)
        self.cfg_list.config_renamed.connect( self._on_config_renamed)
        self.cfg_list.save_requested.connect( self._explicit_save)
        self.cfg_list.setup_tabs.currentChanged.connect(self._on_setup_changed)

        # ── Action bar buttons ────────────────────────────────────────────────
        self.start_btn.clicked.connect(self._unified_start)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.abort_btn.clicked.connect(self._unified_abort)

        self.right_panel.refresh_requested.connect(  self._refresh_plot)
        self.right_panel.display_changed.connect(    self._on_display_changed)
        self.right_panel.x_axis_changed.connect(     self._on_x_axis_changed)
        self.right_panel.plot_config_changed.connect(self._on_plot_config_changed)
        # When registry is saved, update the right panel's registry reference
        self.dev_registry.registry_changed.connect(self._on_registry_changed)
        # When setup defaults are edited, save them and update trajectory labels
        self.setup_defaults.defaults_changed.connect(self._on_defaults_changed)
        # When scan mode changes, swap right panel between normal/DC
        self.traj_panel.scan_mode_changed.connect(self._on_scan_mode_changed)

        # Lazy-load data browser when its tab is first selected
        self._browser_loaded = False
        self.bottom_tabs.currentChanged.connect(self._on_bottom_tab_changed)

        # Wire script console into the application
        self.script_console.set_context(
            setup_getter=self._active_setup,
            config_getter=self._build_full_config)

        # ── Metadata bidirectional sync (Trajectory ↔ Scanlist) ──────────────
        self.traj_panel.meta.changed.connect(self._sync_traj_meta_to_sl)
        self.sl_panel.meta.changed.connect(self._sync_sl_meta_to_traj)

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

        # ── Keyboard shortcuts (F5 only — no accidental abort/pause) ──────────
        QShortcut(QKeySequence("F5"), self, activated=self._unified_start)

    # ── Bottom tab handling ──────────────────────────────────────────────────
    def _on_bottom_tab_changed(self, idx):
        current = self.bottom_tabs.currentWidget()
        # Data Browser — refresh on first visit
        if current is self.data_browser and not self._browser_loaded:
            self.data_browser.refresh()
            self._browser_loaded = True
        # Refresh HW panel values when switching to Trajectory or Scanlist
        if current is self.traj_panel:
            self.traj_panel.hw.refresh()
        elif current is self.sl_panel:
            self.sl_panel.hw.refresh()
        # BD Calibration — prompt once per setup per session
        elif current is self.bd_cal_panel:
            self.bd_cal_panel.maybe_prompt(self._active_setup_name)

    def _on_live_tab_changed(self, _idx):
        if self.live_tabs.currentWidget() is self.calib_panel:
            self.calib_panel._read_all()

    # ── Config management ─────────────────────────────────────────────────────
    def _on_setup_changed(self, idx):
        self._save_active_config()
        self._active_setup_name = SETUP_NAMES[idx]
        self._active_cfg_idx    = self._active_setup().get("active_idx", 0)
        # Sync hidden QTabBar and pill buttons
        self._setup_tab_bar.blockSignals(True)
        self._setup_tab_bar.setCurrentIndex(idx)
        self._setup_tab_bar.blockSignals(False)
        btn = self._setup_btn_grp.button(idx)
        if btn:
            btn.blockSignals(True); btn.setChecked(True); btn.blockSignals(False)
        # Clear plots so stale range never persists across setups.
        # Skip the clear if a scan is running — worker signals are still live
        # and clearing would destroy the active plot buffers.
        if not self._scan_running:
            self.map2d.clear(); self.plot1d.clear()
        # Evict stale SimProxy entries for this setup's devices
        for key in ("magnet_device", "relay_device", "keithley_device"):
            dev = self._active_setup().get(key, "")
            if dev: evict_proxy(dev)
        self._load_active_config()
        # Update setup defaults panel
        self.setup_defaults.load(self._active_setup())
        # Refresh data browser if it was already loaded
        if self._browser_loaded:
            self.data_browser.refresh()

    def _setup_pill_clicked(self, idx: int):
        """Called when a setup pill button is clicked — sync the hidden QTabBar."""
        self._setup_tab_bar.blockSignals(True)
        self._setup_tab_bar.setCurrentIndex(idx)
        self._setup_tab_bar.blockSignals(False)
        self._action_bar_setup_clicked(idx)   # reuse existing handler

    def _action_bar_setup_clicked(self, idx):
        """Called when a setup tab (Green/IR/Cryo) is clicked in the action bar."""
        self.cfg_list.setup_tabs.setCurrentIndex(idx)  # triggers _on_setup_changed

    def _on_new_config(self):
        """Create a blank new config: SPATIAL scan along X, no sensors loaded."""
        self._save_active_config()
        new_cfg = make_default_config("new_scan")
        new_cfg["sensors"] = []       # empty device window
        new_cfg["scan_type"] = "SPATIAL"
        new_cfg["scan_x"]    = True
        new_cfg["scan_y"]    = False
        self._active_setup()["configs"].append(new_cfg)
        new_idx = len(self._active_setup()["configs"]) - 1
        self._active_cfg_idx = new_idx
        self._active_setup()["active_idx"] = new_idx
        self.cfg_list.add_item(new_cfg["name"])
        save_setup(self._active_setup_name, self._active_setup())

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
            save_setup(self._active_setup_name, self._active_setup()); return
        self._save_active_config()
        self._active_cfg_idx = idx
        self._active_setup()["active_idx"] = idx
        save_setup(self._active_setup_name, self._active_setup())
        self._load_active_config()

    def _on_config_deleted(self, idx: int):
        configs = self._active_setup()["configs"]
        if len(configs) <= 1: return
        configs.pop(idx); self.cfg_list.remove_item(idx)
        new_idx = min(idx, len(configs) - 1)
        self._active_cfg_idx = new_idx
        self._active_setup()["active_idx"] = new_idx
        save_setup(self._active_setup_name, self._active_setup())
        self._load_active_config()

    def _on_config_renamed(self, idx: int, name: str):
        configs = self._active_setup()["configs"]
        if 0 <= idx < len(configs):
            configs[idx]["name"] = name
            self.cfg_list.rename_item(idx, name)
            save_setup(self._active_setup_name, self._active_setup())

    def _load_active_config(self):
        setup = self._active_setup()
        configs = setup.get("configs", [])
        if not configs: return
        idx = min(self._active_cfg_idx, len(configs)-1); cfg = configs[idx]
        # Populate all registry-driven combos FIRST so load_config finds the items.
        # preserve=False so load_monitor_settings below controls the selection,
        # not a stale carry-over from the previous setup.
        registry = self.dev_registry.get_registry()
        self.traj_panel.populate_monitor_combo(registry, preserve=False)
        self.setup_defaults.set_registry(registry)
        # Load setup defaults and push actuator labels / TR-MOKE device to trajectory
        self.setup_defaults.load(setup)
        self.traj_panel.set_actuator_defaults(
            setup.get("act1_device", ""), setup.get("act1_attr", "x"),
            setup.get("act1_label",  "X"), setup.get("act1_unit", "nm"),
            setup.get("act2_device", ""), setup.get("act2_attr", "y"),
            setup.get("act2_label",  "Y"), setup.get("act2_unit", "nm"))
        self.traj_panel.set_trmoke_device(setup.get("trmoke_dg645", ""))
        self.traj_panel.set_rtv40_device(setup.get("rtv40_device", ""))
        self.calib_panel.set_fl_device(setup.get("focus_averagein", ""))
        self.calib_panel.configure_stage(
            setup.get("act1_device", ""), setup.get("act1_attr", "x"),
            setup.get("act2_device", ""), setup.get("act2_attr", "y"),
            setup.get("z_device",    ""), setup.get("z_attr",    "position0"),
        )
        # Now load config values into all widgets
        self.traj_panel.load_config(cfg)
        self.traj_panel.load_monitor_settings(cfg)
        self.right_panel.load_sensors(cfg.get("sensors", DEFAULT_SENSORS))
        self.right_panel.set_display(cfg.get("display_sensor","ZI2 x1"), cfg.get("colormap","RdBu_r"))
        hyst_chs = cfg.get("hyst_channels", [])
        if hyst_chs:
            self.right_panel.load_dc_channels(hyst_chs)
        self.right_panel.set_dc_mode(cfg.get("scan_type") == "DC_HYST")
        self.sl_panel.set_active_name(cfg.get("name","—"))
        sd = os.path.expanduser(setup.get("save_dir", "~/moke_data"))
        self.save_dir.setText(sd)
        self.server_dir.setText(setup.get("server_sync_dir", ""))
        # BD calibration — load saved values if present, update status
        bd_vals = setup.get("bd_calibration")
        if bd_vals:
            self.bd_cal_panel.load_calibration(bd_vals)
            date_str = setup.get("bd_calibration_date", "")
            self.bd_cal_panel.set_status(
                f"Loaded from setup '{self._active_setup_name}'"
                + (f" ({date_str})" if date_str else "") + ".")

    def _save_active_config(self):
        setup   = self._active_setup()
        configs = setup.get("configs", [])
        if not configs: return
        idx = min(self._active_cfg_idx, len(configs)-1)
        old = configs[idx]; old.update(self.traj_panel.get_config_partial())
        old["sensors"]        = self.right_panel.get_sensors()
        old["display_sensor"] = self.right_panel.get_display_sensor()
        old["colormap"]       = self.right_panel.get_colormap()
        old["hyst_channels"]  = self.right_panel.get_dc_channels()
        # Sync save_dir, server_sync_dir, and setup defaults back into setup
        setup["save_dir"] = self.save_dir.text().strip()
        setup["server_sync_dir"] = self.server_dir.text().strip()
        setup.update(self.setup_defaults.get_defaults())
        self.cfg_list.sync_name(idx, old["name"])
        save_setup(self._active_setup_name, setup)
        self._update_estimate()

    def _update_estimate(self):
        """Show a breakdown pre-scan time estimate in status_lbl when idle.

        The ZI settling read is done in a background thread so the GUI is
        never blocked.  The label is updated twice: immediately (zi_settle=0)
        and again once the device responds.
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

        if mode == "DC_HYST":
            int_t  = float(cfg.get("hyst_int_time", 2.0))
            npts   = int(cfg.get("hyst_npts", 100))
            cycles = int(cfg.get("hyst_cycles", 1))
            total  = int_t * 2 * cycles
            self.status_lbl.setText(
                f"≈ {_fmt(total)}  (2 × {int_t:.3g}s/half-loop × {cycles} cycle(s), {npts} pts/half)")
            return

        int_t  = float(cfg.get("integration_time", 0.1))
        settle = float(cfg.get("settle_time", 0.05))
        if mode == "FIELD":   settle = max(settle, 0.05)
        elif mode == "TIME":  settle = 0.0
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

        # Show immediately without ZI settling (no I/O)
        _show(0.0)

        # Then read ZI settling in background and refresh label
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
        self._save_active_config(); self.status_lbl.setText("Config saved ✓")

    # ── Metadata bidirectional sync ───────────────────────────────────────────
    def _sync_traj_meta_to_sl(self):
        if self._meta_syncing: return
        self._meta_syncing = True
        try:
            self.sl_panel.meta.load_values(self.traj_panel.meta.get_values())
        finally:
            self._meta_syncing = False

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

    # ── BD Calibration callbacks ──────────────────────────────────────────────
    def _bd_cal_save(self, vals: list):
        from datetime import datetime as _dt
        date_str = _dt.now().strftime("%Y-%m-%d %H:%M")
        setup = self._active_setup()
        setup["bd_calibration"]      = vals
        setup["bd_calibration_date"] = date_str
        save_setup(self._active_setup_name, setup)
        self.bd_cal_panel.set_status(f"Saved {date_str} for setup '{self._active_setup_name}'.")

    def _bd_cal_load(self):
        setup = self._active_setup()
        vals = setup.get("bd_calibration")
        date_str = setup.get("bd_calibration_date", "unknown date")
        return vals, date_str

    def _on_registry_changed(self):
        """Called when the Device Registry is saved — update the right panel and monitor."""
        registry = self.dev_registry.get_registry()
        self.right_panel.set_registry(registry)
        self.traj_panel.populate_monitor_combo(registry)
        self.setup_defaults.set_registry(registry)
        self.status_lbl.setText("Device registry saved ✓")

    def _on_defaults_changed(self):
        """Called when Setup Defaults are edited — save to setup dict immediately."""
        defaults = self.setup_defaults.get_defaults()
        self._active_setup().update(defaults)
        save_setup(self._active_setup_name, self._active_setup())
        # Push updated labels and TR-MOKE device to trajectory panel
        self.traj_panel.set_actuator_defaults(
            defaults.get("act1_device", ""), defaults.get("act1_attr", "x"),
            defaults.get("act1_label",  "X"), defaults.get("act1_unit", "nm"),
            defaults.get("act2_device", ""), defaults.get("act2_attr", "y"),
            defaults.get("act2_label",  "Y"), defaults.get("act2_unit", "nm"))
        self.traj_panel.set_trmoke_device(defaults.get("trmoke_dg645", ""))
        self.traj_panel.set_rtv40_device(defaults.get("rtv40_device", ""))
        self.calib_panel.set_fl_device(defaults.get("focus_averagein", ""))
        self.calib_panel.configure_stage(
            defaults.get("act1_device", ""), defaults.get("act1_attr", "x"),
            defaults.get("act2_device", ""), defaults.get("act2_attr", "y"),
            defaults.get("z_device",    ""), defaults.get("z_attr",    "position0"),
        )

    def _on_scan_mode_changed(self, mode: str):
        """Called when trajectory panel switches between SPATIAL/FIELD/DC_HYST."""
        self.right_panel.set_dc_mode(mode == "DC_HYST")

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

    def _unified_start(self):
        """Start scan or scanlist depending on which bottom tab is active.
        If the calibration tab is visible, start a time scan plotting there."""
        if self._scan_running: return
        # Sync save_dir from action bar into setup
        self._active_setup()["save_dir"] = self.save_dir.text().strip()
        self.traj_panel._save_dir = self.save_dir.text().strip()
        # Check if calibration tab is active → start time scan there
        if self.live_tabs.currentWidget() is self.calib_panel:
            self._start_calib_timescan()
            return
        # Check if scanlist tab is active
        if self.bottom_tabs.currentWidget() is self.sl_panel:
            self._start_scanlist()
        else:
            self._start_scan()

    def _unified_abort(self):
        """Abort whichever is running — single scan or scanlist."""
        if self._sl_worker:
            self._abort_scanlist()
        else:
            self._abort_scan()

    def _set_running(self, running: bool):
        """Buttons stay always enabled — guards in click handlers prevent misuse."""
        if not running:
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
        setup    = self._active_setup()
        configs  = setup.get("configs", [])
        partial["name"]           = configs[self._active_cfg_idx]["name"] if configs else "scan"
        partial["sensors"]        = self.right_panel.get_sensors()
        partial["display_sensor"] = self.right_panel.get_display_sensor()
        partial["colormap"]       = self.right_panel.get_colormap()

        # ── Inject device paths from Setup Defaults ───────────────────────────
        scan_type = partial.get("scan_type", "SPATIAL")
        if scan_type == "TR_MOKE":
            # TR-MOKE: act1 is the DG645 delay channel
            dg_path = setup.get("trmoke_dg645", "hpp-N42/delay/DG645")
            partial["act1_device"]  = dg_path
            partial["trmoke_dg645"] = dg_path
        else:
            # Spatial / Field / Time: stage device+attr always from setup defaults
            partial["act1_device"] = setup.get("act1_device", "")
            partial["act1_attr"]   = setup.get("act1_attr",   "x")
            partial["act2_device"] = setup.get("act2_device", "")
            partial["act2_attr"]   = setup.get("act2_attr",   "y")
        # DC hyst channels live in the right panel now
        if partial.get("scan_type") == "DC_HYST":
            dc_sensors = self.right_panel.get_dc_channels()
            # Map SensorPickerRow format to scan engine format (needs "attr" key)
            hyst_chs = []
            for s in dc_sensors:
                ch = dict(s)
                ch["attr"] = ch.get("attribute", ch.get("attr", ""))
                hyst_chs.append(ch)
            partial["hyst_channels"] = hyst_chs
            # Use device from first enabled channel if hyst_device not set
            if not partial.get("hyst_device"):
                for ch in hyst_chs:
                    if ch.get("enabled") and ch.get("device"):
                        partial["hyst_device"] = ch["device"]
                        break
        return partial

    # ── Scan geometry helpers ─────────────────────────────────────────────────
    def _scan_dims(self, cfg) -> Tuple[str, int, int]:
        """Return (mode, n_x, n_y).  mode: '2D'|'1D'|'1D_Y'|'FIELD'|'TIME'"""
        st = cfg.get("scan_type","SPATIAL")
        sx = cfg.get("scan_x",  True)
        sy = cfg.get("scan_y",  False)
        if st == "DC_HYST":       return "DC_HYST", int(cfg.get("hyst_npts", 100)) * 2, 1
        if st == "FIELD":         return "FIELD",  int(cfg.get("field_npts",101)), 1
        if st == "TR_MOKE":       return "1D",     int(cfg.get("act1_npts",101)), 1
        if sx and sy:             return "2D",     int(cfg.get("act1_npts",51)),   int(cfg.get("act2_npts",51))
        if sy and not sx:         return "1D_Y",   int(cfg.get("act2_npts",51)),   1
        if not sx and not sy:     return "TIME",   int(cfg.get("act1_npts",101)),  1
        return "1D",                               int(cfg.get("act1_npts",51)),   1

    def _hyst_active(self, cfg: dict) -> list:
        """Return sensor-like dicts for enabled DC hyst channels."""
        chs = cfg.get("hyst_channels", [])
        return [
            {"label": c.get("label", "?"),
             "attribute": c.get("attribute", c.get("attr", "")),
             "device": c.get("device", cfg.get("hyst_device", "")),
             "unit": c.get("unit", "V"),
             "y_axis": c.get("y_axis", "Y1"),
             "plot_axis": c.get("plot_axis", c.get("y_axis", "Y1")),
             "plot_visible": c.get("plot_visible", True),
             "enabled": True}
            for c in chs
            if c.get("enabled", True)
        ]

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
            self.live_tabs.setCurrentIndex(0)
        else:
            if   mode == "DC_HYST": xl, xu = "Field", "mT"
            elif mode == "FIELD":   xl, xu = cfg.get("field_x_label", "Field"), cfg.get("field_x_unit", "mT")
            elif mode == "1D_Y":    xl, xu = cfg["act2_label"], cfg["act2_unit"]
            elif mode == "TIME":    xl, xu = "Time", "s"
            else:                   xl, xu = cfg["act1_label"], cfg["act1_unit"]
            self.plot1d.alloc(n_x, xl, xu, active)
            # For DC_HYST build sensor meta from hyst_channels directly,
            # bypassing the right_panel registry (which knows nothing about result arrays).
            if mode == "DC_HYST":
                self.plot1d.apply_config(self._dc_sensors_meta(cfg), X_NATURAL)
                if not self._calib_timescan:
                    self.live_tabs.setCurrentIndex(1)
                x_opts = [(X_NATURAL, "Field (mT)")]
                for s in active: x_opts.append((s["label"], s["label"]))
                self.right_panel.set_x_options(x_opts)
            else:
                self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(),
                                         self.right_panel.get_x_key())
                if not self._calib_timescan:
                    self.live_tabs.setCurrentIndex(1)
                x_opts = [(X_NATURAL, f"{xl} ({xu})" if xu else xl), (X_TIME, "Time (s)")]
                for s in active: x_opts.append((s["label"], s["label"]))
                self.right_panel.set_x_options(x_opts)

    # ── Scan helpers (shared logic) ─────────────────────────────────────────
    def _prepare_scan(self, cfg: dict) -> Optional[list]:
        """Resolve active sensors from cfg.  Returns the active list, or None
        if validation fails (warning dialog already shown)."""
        if cfg.get("scan_type") == "DC_HYST":
            active = self._hyst_active(cfg)
            if not active:
                QMessageBox.warning(self, "No channels",
                                    "Enable at least one DC hyst channel.")
                return None
        else:
            active = [s for s in cfg["sensors"] if s["enabled"]]
            if not active:
                QMessageBox.warning(self, "No sensors",
                                    "Enable at least one sensor.")
                return None
        return active

    def _wire_worker(self, worker: ScanWorker):
        """Connect the standard signals shared by all scan workers."""
        worker.point_done.connect(self._on_point)
        worker.progress.connect(self._on_progress)
        worker.status_msg.connect(self._on_status)
        worker.log_msg.connect(self._log_append)
        worker.scan_done.connect(lambda fn: setattr(self, "_last_fn", fn))
        worker.error_msg.connect(
            lambda m: self._log_append(f"\n⚠ ERROR:\n{m}", level="error"))
        worker.finished.connect(self._on_worker_finished)

    # ── Single scan ───────────────────────────────────────────────────────────
    def _start_scan(self):
        if self._scan_running: return
        self._calib_timescan = False
        self._save_active_config()
        cfg = self._build_full_config(); setup = self._active_setup()

        active = self._prepare_scan(cfg)
        if active is None: return

        # ── Setup lock ────────────────────────────────────────────────────────
        ok, who = acquire_lock(self._active_setup_name)
        if not ok:
            QMessageBox.warning(
                self, "Setup busy",
                f"Setup '{self._active_setup_name}' is already in use:\n{who}\n\n"
                "Abort that scan first, then retry.")
            return

        self._current_scan_cfg = cfg
        self._setup_live_display(cfg, active); self._alloc_scan_data(cfg, active)
        # DC_HYST: reset DC live-plot accumulators
        if cfg.get("scan_type") == "DC_HYST":
            self._dc_loop_x = []; self._dc_loop_y = {}; self._last_dc_cycle = 0
            self.traj_panel.reset_dc_monitor()
        self.log_text.clear()

        # TR_MOKE is executed as a standard SPATIAL 1D scan — the actuator
        # is the DG645 delay attribute. Store unit factor for x-axis display,
        # then convert scan_type before passing to ScanRunner.
        self._trmoke_x_factor = None
        if cfg.get("scan_type") == "TR_MOKE":
            unit = cfg.get("act1_unit", "ns")
            _factors = {"ps": 1e-12, "ns": 1e-9, "µs": 1e-6}
            self._trmoke_x_factor = 1.0 / _factors.get(unit, 1e-9)
            cfg["scan_type"] = "SPATIAL"
            if cfg.get("rtv40_sync_enabled"):
                cfg["rtv40_device"] = setup.get("rtv40_device", "")
                if cfg.get("rtv40_device"):
                    # Pre-scan check: will any scan point push the width out of range?
                    base_ns  = float(cfg.get("rtv40_base_width_ns", 1.0))
                    start_s  = float(cfg.get("act1_start", 0.0))
                    stop_s   = float(cfg.get("act1_stop",  0.0))
                    total_ns = (stop_s - start_s) * 1e9
                    min_w = base_ns - max(0.0, total_ns)
                    max_w = base_ns - min(0.0, total_ns)
                    issues = []
                    if max_w > 20.0:
                        issues.append(f"Max pulse width {max_w:.2f} ns > 20 ns hardware limit.")
                    if min_w < 0.3:
                        issues.append(f"Min pulse width {min_w:.2f} ns < 0.3 ns hardware limit.")
                    if issues:
                        reply = QMessageBox.question(
                            self, "RTV40 width out of range",
                            "RTV40 sync width will exceed hardware limits:\n\n"
                            + "\n".join(issues)
                            + "\n\nWidth will be clamped to [0.3, 20.0] ns. Continue?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                        if reply != QMessageBox.StandardButton.Yes:
                            release_lock(self._active_setup_name)
                            return
                    # Check trigger source — External (1) required for DG645-gated operation
                    try:
                        _rp, _rp_err = get_proxy(cfg["rtv40_device"])
                        if not _rp_err:
                            trig, _terr = safe_read(_rp, "TriggerSource")
                            if trig is not None and int(trig) != 1:
                                _src_names = {0: "Off", 1: "External", 2: "Internal"}
                                reply = QMessageBox.question(
                                    self, "RTV40 trigger check",
                                    f"RTV40 TriggerSource is "
                                    f"'{_src_names.get(int(trig), trig)}' (not External).\n"
                                    "For TR-MOKE, External trigger is typically required.\n\n"
                                    "Continue anyway?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                                if reply != QMessageBox.StandardButton.Yes:
                                    release_lock(self._active_setup_name)
                                    return
                    except Exception:
                        pass  # fail-open: device unreachable, proceed

        # ── BD calibration — injected into cfg for HDF5 storage ─────────────
        cfg["bd_calibration"] = self.bd_cal_panel.get_calibration()

        # ── Hardware snapshot (written to HDF5 metadata + lab notebook) ─────
        cfg.update(_read_hw_snapshot(setup, cfg.get("scan_type", "SPATIAL")))

        self._worker = ScanWorker(cfg, setup)
        self._wire_worker(self._worker)
        if cfg.get("scan_type") == "DC_HYST":
            self._worker.dc_loop_ready.connect(self.traj_panel.update_dc_live)

        self._scan_start_time = _time.time()
        # A single Start produces exactly one scan-file (TR-MOKE, DC_HYST,
        # trace/retrace and interleaved-2D are all one file here — Samba_main
        # has no _dir_queue).
        self._status_bar_run_start(cfg, 1)
        self._scan_running = True; self._set_running(True); self._last_fn = None
        self._worker.start()

    # ── Calibration time scan ─────────────────────────────────────────────────
    def _start_calib_timescan(self):
        """Start a time scan (no movement) plotting into the calibration tab."""
        if self._scan_running: return
        self._save_active_config()
        self._trmoke_x_factor = None
        cfg   = self._build_full_config()
        setup = self._active_setup()

        # Force time scan: no axis movement
        cfg["scan_type"] = "SPATIAL"
        cfg["scan_x"] = False
        cfg["scan_y"] = False

        active = [s for s in cfg["sensors"] if s["enabled"]]
        if not active:
            QMessageBox.warning(self, "No sensors",
                                "Enable at least one sensor."); return

        n_pts = int(cfg.get("act1_npts", 101))
        self._current_scan_cfg = cfg
        self._calib_timescan = True

        # For the calibration plot, only show sensors that aren't hidden
        plot_sensors = [s for s in active
                        if s.get("plot_visible", True)
                        and s.get("y_axis", s.get("plot_axis", "Y1")) not in ("hidden", "—", "X")]
        self.calib_panel.focus_plot.setup_timescan(n_pts, plot_sensors if plot_sensors else active)

        # Also set up the main live display for the Log tab
        self._setup_live_display(cfg, active)
        self._alloc_scan_data(cfg, active)

        self._worker = ScanWorker(cfg, setup)
        self._wire_worker(self._worker)

        self._scan_start_time = _time.time()
        self._status_bar_run_start(cfg, 1)   # calibration time scan = one file
        self._scan_running = True; self._set_running(True); self._last_fn = None
        self._worker.start()

    def _on_point(self, ix, iy, x_actual, vals):
        # TR-MOKE: convert x from seconds to display unit (ns/ps/µs)
        if self._trmoke_x_factor is not None:
            x_actual = x_actual * self._trmoke_x_factor
        for lbl, v in vals.items():
            if lbl in self._scan_data: self._scan_data[lbl][iy, ix] = v
        mode, _, __ = self._scan_dims(self._current_scan_cfg)
        if mode == "2D":
            # Use the scan config's display sensor while running so a
            # temporary setup switch can't redirect updates to the wrong sensor.
            disp = (self._current_scan_cfg.get("display_sensor", "")
                    or self.right_panel.get_display_sensor())
            self.map2d.update_point(ix, iy, vals.get(disp, float("nan")))
        else:
            self.plot1d.update_point(ix, x_actual, vals)
        # Also feed calibration plot if a calib time scan is running
        if getattr(self, '_calib_timescan', False):
            self.calib_panel.focus_plot.update_timescan_point(ix, x_actual, vals)

    def _log_append(self, msg: str, level: str = "auto"):
        """
        Append a message to the log with color-coding.
        level: "info", "warning", "error", or "auto" (detect from content).
        Respects the log filter dropdown.
        """
        if level == "auto":
            ml = msg.lower()
            if "⚠" in msg or "error" in ml or "traceback" in ml:
                level = "error"
            elif "warning" in ml or "mismatch" in ml or "auto-pause" in ml:
                level = "warning"
            else:
                level = "info"

        # Apply filter
        filt = self.log_filter.currentIndex()  # 0=All, 1=Errors, 2=Warn+Err
        if filt == 1 and level != "error":
            return
        if filt == 2 and level == "info":
            return

        colors = {"info": "#a6e3a1", "warning": "#fab387", "error": "#f38ba8"}
        color = colors.get(level, "#a6e3a1")

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(msg + "\n", fmt)
        self.log_text.setTextCursor(cursor)

    def _on_status(self, msg):
        self.status_lbl.setText(msg); self._log_append(msg)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum())
        # Detect auto-pause from ScanRunner (single scan or scanlist) and update button
        _active_worker = self._worker or self._sl_worker
        if _active_worker and _active_worker.is_paused():
            self.pause_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")

    def _on_progress(self, done: int, total: int):
        """Record scan progress for the bottom status bar."""
        self._bar_last_done  = done
        self._bar_last_total = total
        if done == 1:
            self._scan_first_pt_time = _time.time()
        self._refresh_status_bar()

    def _on_worker_finished(self):
        cfg_type = self._current_scan_cfg.get("scan_type", "") if self._current_scan_cfg else ""
        release_lock(self._active_setup_name)
        self._status_bar_run_finish()
        self._scan_running = False; self._set_running(False)
        self._calib_timescan = False
        # Auto-zero the field after every DC hysteresis scan
        if cfg_type == "DC_HYST":
            self._log_append("DC hyst complete — auto-zeroing field…", level="info")
            self.traj_panel.hw.demagnetize()
        try: self.data_browser.refresh()
        except Exception:
            log.debug("Data browser refresh failed after scan", exc_info=True)
        if self._last_fn:
            # Lab notebook
            setup = self._active_setup()
            nb = _nb_path(setup.get("notebook_dir", "~/moke_data"),
                          self._active_setup_name)
            if self._current_scan_cfg:
                entry = dict(self._current_scan_cfg)
                entry["_scan_start_time"] = self._scan_start_time
                entry["_hdf5_path"] = os.path.abspath(self._last_fn)
                # Strip Cryo-only keys that may linger in migrated configs
                for _k in ("geometry", "stage_type",
                           "hw_temperature_K",
                           "_temp_sweep_start_K", "_temp_sweep_stop_K", "_temp_sweep_step_K"):
                    entry.pop(_k, None)
                append_measurement(nb, entry)
            self._log_append(f"✓ Scan complete — saved {self._last_fn}", level="info")
            self._last_fn = None
        self._update_estimate()
        _setup = self._active_setup()
        _setup["server_sync_dir"] = self.server_dir.text().strip()
        def _done_sync(ok):
            QTimer.singleShot(0, lambda: self.status_lbl.setText(
                "Server sync complete" if ok else "Server sync partial (see log)"))
        sync_setup(self._active_setup_name, _setup, done_cb=_done_sync)

    def _toggle_pause(self):
        if not self._scan_running: return
        worker = self._worker or self._sl_worker
        if not worker: return
        _style = self.style()
        if worker.is_paused():
            worker.resume()
            self.pause_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.pause_btn.setText("Pause")
        else:
            worker.pause()
            self.pause_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")

    def _abort_scan(self):
        if not self._scan_running: return
        if self._worker: self._worker.abort()
        self.status_lbl.setText("Aborting…")

    # ── Scanlist ──────────────────────────────────────────────────────────────
    def _start_scanlist(self):
        if self._scan_running: return
        self._save_active_config()
        cfg = self._build_full_config(); setup = self._active_setup()

        active = self._prepare_scan(cfg)
        if active is None: return

        self._current_scan_cfg = cfg; sl = self.sl_panel.get_settings()
        self._setup_live_display(cfg, active); self._alloc_scan_data(cfg, active)

        # ── BD calibration — injected into cfg for HDF5 storage ──────────────
        cfg["bd_calibration"] = self.bd_cal_panel.get_calibration()

        # ── Hardware snapshot (written to HDF5 metadata + lab notebook) ─────
        cfg.update(_read_hw_snapshot(setup, cfg.get("scan_type", "SPATIAL")))

        self._sl_worker = ScanlistWorker(cfg, setup, sl["n_scans"], sl["list_name"],
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

        # Status bar: one scan-file per (cycle × direction).
        self._status_bar_run_start(cfg, sl["n_scans"] * len(self._sl_worker.cfg_list))
        self._sl_scan_t0 = _time.time()
        self._scan_running = True; self._set_running(True); self.log_text.clear()
        self._sl_worker.start()

    def _on_sl_scan_done(self, idx: int, fn: str):
        """Per-file callback from ScanlistWorker — updates status bar and
        records a lab-notebook entry for the file just written (each
        scanlist file previously produced no notebook row at all)."""
        self._status_bar_scan_done()
        t_start = self._sl_scan_t0
        self._sl_scan_t0 = _time.time()   # next file starts now
        if not fn or not self._current_scan_cfg:
            return
        try:
            setup = self._active_setup()
            nb = _nb_path(setup.get("notebook_dir", "~/moke_data"),
                          self._active_setup_name)
            entry = dict(self._current_scan_cfg)
            entry["_scan_start_time"] = t_start
            entry["_hdf5_path"] = os.path.abspath(fn)
            for _k in ("geometry", "stage_type",
                       "hw_temperature_K",
                       "_temp_sweep_start_K", "_temp_sweep_stop_K", "_temp_sweep_step_K"):
                entry.pop(_k, None)
            append_measurement(nb, entry)
        except Exception:
            log.debug("Lab notebook append failed for scanlist file", exc_info=True)

    def _on_scanlist_done(self, txt_path: str):
        self._status_bar_run_finish()
        try: self.data_browser.refresh()
        except Exception:
            log.debug("Data browser refresh failed after scanlist", exc_info=True)
        self._log_append(f"✓ Scanlist complete — saved {txt_path}", level="info")
        _setup = self._active_setup()
        _setup["server_sync_dir"] = self.server_dir.text().strip()
        def _done_sync(ok):
            QTimer.singleShot(0, lambda: self.status_lbl.setText(
                "Server sync complete" if ok else "Server sync partial (see log)"))
        sync_setup(self._active_setup_name, _setup, done_cb=_done_sync)

    def _on_scanlist_relay_changed(self, state: int):
        """Update relay label in both HW panels when the scanlist worker flips the relay."""
        for hw in (self.traj_panel.hw, self.sl_panel.hw):
            hw.set_relay_state(state)

    def _on_cycle_done(self, cycle_idx: int):
        cfg  = self._current_scan_cfg
        mode, _, __ = self._scan_dims(cfg)
        if mode == "2D": return
        if mode == "DC_HYST":
            active = self._hyst_active(cfg)
        else:
            active = [s for s in cfg["sensors"] if s["enabled"]]
        self._alloc_scan_data(cfg, active); self._setup_live_display(cfg, active)

    def _on_sl_worker_finished(self):
        self._set_running(False)
        self._scan_running = False
        self._sl_worker = None

    def _abort_scanlist(self):
        if not self._scan_running: return
        if self._sl_worker: self._sl_worker.abort()
        self.status_lbl.setText("Aborting scanlist…")

    # ── Plot helpers ──────────────────────────────────────────────────────────
    def _dc_sensors_meta(self, cfg: dict) -> list:
        """Sensor meta list suitable for plot1d.apply_config, from hyst_channels.
        Respects the axis dropdown: hidden channels get axis='—' so they're
        recorded but not plotted."""
        result = []
        for s in self._hyst_active(cfg):
            axis = s.get("plot_axis", s.get("y_axis", "Y1"))
            if axis in ("hidden", "X"):
                axis = "—"
            result.append({"label": s["label"], "axis": axis, "unit": s.get("unit", "V")})
        return result

    def _on_plot_config_changed(self):
        """Visibility or axis changed — rebuild lines immediately, no data loss."""
        if not self._current_scan_cfg: return
        mode, _, __ = self._scan_dims(self._current_scan_cfg)
        if mode == "2D": return
        if mode == "DC_HYST":
            self.plot1d.apply_config(
                self._dc_sensors_meta(self._current_scan_cfg), X_NATURAL)
        else:
            self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(),
                                     self.right_panel.get_x_key())

    def _on_x_axis_changed(self, key: str, label: str):
        if not self._current_scan_cfg: return
        mode, _, __ = self._scan_dims(self._current_scan_cfg)
        if mode == "2D": return
        if mode == "DC_HYST":
            self.plot1d.apply_config(
                self._dc_sensors_meta(self._current_scan_cfg), X_NATURAL)
        else:
            self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(), key)

    def _refresh_plot(self):
        cfg  = self._current_scan_cfg or self._build_full_config()
        mode, _, __ = self._scan_dims(cfg)
        if mode == "2D":
            disp = self.right_panel.get_display_sensor()
            if disp and disp in self._scan_data and self.map2d._img is not None:
                self.map2d.switch_sensor(self._scan_data[disp], disp)
        elif mode == "DC_HYST":
            self.plot1d.apply_config(self._dc_sensors_meta(cfg), X_NATURAL)
        else:
            self.plot1d.apply_config(self.right_panel.get_plot_sensors_meta(),
                                     self.right_panel.get_x_key())

    def _on_display_changed(self, sensor: str, cmap: str):
        # Redirect the running scan's incoming points to the newly-selected
        # sensor too — otherwise only the already-acquired data would switch
        # and new points would keep filling the previous (frozen) sensor.
        if sensor and self._current_scan_cfg:
            self._current_scan_cfg["display_sensor"] = sensor
        if sensor and sensor in self._scan_data and self.map2d._img is not None:
            self.map2d.switch_sensor(self._scan_data[sensor], sensor)
        self.map2d.set_colormap(cmap)

    def _poll_field_readback(self):
        """All TANGO I/O runs in a background thread; GUI updates posted back
        via QTimer.singleShot so the Qt event loop is never blocked."""
        if self._rb_poll_active:
            return  # previous poll still in flight — skip this tick
        self._rb_poll_active = True

        # ── Snapshot GUI state on the main thread (no I/O) ────────────────
        setup    = self._active_setup()
        mag_dev  = setup.get("magnet_device", "")
        fld_attr = setup.get("magnet_field_attr", "field_polar_corr")
        scan_t   = (self._current_scan_cfg.get("scan_type", "")
                    if self._scan_running and self._current_scan_cfg else "")
        last_dc  = self._last_dc_cycle

        mon_dev, mon_attr = "", ""
        if scan_t == "FIELD":
            mon_dev, mon_attr = self.traj_panel.get_monitor_device()

        hyst_dev = ""
        if scan_t == "DC_HYST":
            hyst_dev = self._current_scan_cfg.get("hyst_device", "")

        calib_active = (self.live_tabs.currentWidget() is self.calib_panel)
        calib_axes: dict = {}
        if calib_active:
            info = self.calib_panel.get_axis_info()
            calib_axes = {k: info.get(k, ("", "")) for k in ("x", "y", "z")}

        # ── All device reads off the GUI thread ────────────────────────────
        def _do():
            try:
                res: dict = {}

                # Field readback
                p = get_proxy(mag_dev)
                v, _ = safe_read(p, fld_attr)
                res["field"] = v

                # FIELD scan: monitor device (or fallback to field value)
                if scan_t == "FIELD":
                    if mon_dev and mon_attr:
                        mp = get_proxy(mon_dev)
                        mv, _ = safe_read(mp, mon_attr)
                        res["monitor"] = mv
                    else:
                        res["monitor"] = v

                # DC_HYST: poll CycleReadback + optional Hc/Hshift
                if hyst_dev:
                    hp = get_proxy(hyst_dev)
                    cyc_v, _ = safe_read(hp, "CycleReadback")
                    res["dc_cycle"] = cyc_v
                    if cyc_v is not None:
                        c_int = int(cyc_v)
                        if c_int != last_dc and c_int > 0:
                            hc_v,  _ = safe_read(hp, "Hc")
                            hsh_v, _ = safe_read(hp, "Hshift")
                            res["hc"]  = hc_v
                            res["hsh"] = hsh_v

                # Calibration tab: stage positions
                if calib_axes:
                    pos: dict = {}
                    for k, (dev, attr) in calib_axes.items():
                        if dev:
                            px = get_proxy(dev)
                            av, _ = safe_read(px, attr)
                            pos[k] = av
                    res["positions"] = pos

                self._post_to_main.emit(lambda: self._apply_field_poll(res, scan_t))
            finally:
                self._rb_poll_active = False

        threading.Thread(target=_do, daemon=True).start()

        # tr_refresh is lightweight (just updates a label from a cached value)
        self.traj_panel.tr_refresh()

    def _apply_field_poll(self, res: dict, scan_t: str):
        """Apply poll results to widgets (always called on the main thread)."""
        v = res.get("field")
        self.traj_panel.hw.update_field_readback(v)
        self.sl_panel.hw.update_field_readback(v)

        if "monitor" in res:
            self.traj_panel.update_field_monitor(res["monitor"])

        if "dc_cycle" in res:
            cyc_v = res["dc_cycle"]
            if cyc_v is not None:
                c_int = int(cyc_v)
                if c_int != self._last_dc_cycle and c_int > 0:
                    self._last_dc_cycle = c_int
                    hc_v  = res.get("hc")
                    hsh_v = res.get("hsh")
                    if hc_v is not None:
                        self.traj_panel.update_dc_cycle(
                            c_int, float(hc_v),
                            float(hsh_v) if hsh_v is not None else 0.0)

        if "positions" in res:
            self.calib_panel.update_positions(res["positions"])

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def _initial_hw_read(self):
        """Read hardware panels once on startup. Staggered to avoid simultaneous ZI reads."""
        self.traj_panel.hw.refresh()
        QTimer.singleShot(800, self.sl_panel.hw.refresh)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not self._split_initialised and self.height() > 100:
            top = int(self.height() * 0.50)
            bot = self.height() - top
            self._v_split.setSizes([top, bot])
            self._split_initialised = True

    def _restore_geometry(self):
        """Restore saved geometry, or open at a sensible preferred size — but
        never larger than the current screen, and never positioned off-display,
        so nothing is clipped on first open (or after a resolution/monitor change)."""
        scr   = QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        s = QSettings("ETH-Intermag", "SambaV3"); g = s.value("geometry")
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
            for w in [self._worker, self._sl_worker]:
                if w: w.abort(); w.wait(2000)
        self._save_active_config()
        QSettings("ETH-Intermag","SambaV3").setValue("geometry", self.saveGeometry())
        ev.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    # ── Set WM_CLASS *before* QApplication (critical for GNOME taskbar icon) ──
    import platform
    if platform.system() == "Linux":
        # This makes GNOME match our window to our .desktop file
        os.environ.setdefault("RESOURCE_NAME", "samba")
        # Also set via Xlib if possible
        try:
            import ctypes
            x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
            x11.XSetClassHint  # just check it exists
        except Exception:
            pass

    app = QApplication(["samba"])  # argv[0] sets WM_CLASS on X11
    app.setApplicationName("Samba")
    app.setOrganizationName("ETH Zürich - Intermag")
    app.setDesktopFileName("samba")  # matches samba.desktop

    # ── App icon ──
    _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samba_icon_256.png")
    _app_icon = None
    if os.path.exists(_icon_path):
        _app_icon = QIcon(_icon_path)
        app.setWindowIcon(_app_icon)

    # ── Splash screen ──
    from play_intro import show_splash, update_splash, finish_splash
    splash = show_splash(app)

    update_splash(splash, "Loading configuration…")
    if not TANGO_AVAILABLE:
        update_splash(splash, "pytango not found — simulation mode")

    update_splash(splash, "Building main window…")
    win = MainWindow()

    # Set icon directly on the window (needed for Spyder / some Linux WMs)
    if _app_icon:
        win.setWindowIcon(_app_icon)

    if TANGO_AVAILABLE:
        win._probe_devices(status_callback=lambda msg: update_splash(splash, msg))

    update_splash(splash, "Ready!")

    # Show window after splash; probe runs above so 3 s covers short probe times too
    finish_splash(splash, win, min_seconds=3)

    if not TANGO_AVAILABLE:
        QMessageBox.information(win, "Simulation Mode",
            "pytango not installed — running with simulated hardware.\n\n"
            "Install:  pip install pytango\n"
            "Connect:  export TANGO_HOST=192.168.1.1:10000")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
