#!/usr/bin/env python3
"""
samba.py — Samba v3 — ETH Zürich Intermag Lab
Entry point: MainWindow wires together all modules.

Requirements:  pip install pytango PyQt6 matplotlib h5py numpy
Usage:         export TANGO_HOST=192.168.1.1:10000 && python samba.py
"""
import logging
import sys, os, copy
import numpy as np
from typing import Dict, Optional, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QTabWidget, QTabBar, QTextEdit, QMessageBox, QSplitter,
    QComboBox, QLineEdit, QPushButton, QFileDialog, QButtonGroup, QFrame, QStyle
)
from PyQt6.QtCore import QTimer, QSettings, Qt
from PyQt6.QtGui import QShortcut, QKeySequence, QTextCharFormat, QColor, QTextCursor, QIcon

try:
    import tango
    TANGO_AVAILABLE = True
except ImportError:
    TANGO_AVAILABLE = False

from config  import SETUP_NAMES, X_NATURAL, X_TIME, DEFAULT_SENSORS, load_setup, save_setup, make_default_config
from hardware import get_proxy, safe_read, evict_proxy
from scan    import ScanWorker, ScanlistWorker
from plot_widgets import Live2DWidget, Live1DWidget
from panels  import (ConfigListPanel, RightPanel,
                     TrajectoryPanel, ScanlistPanel, SetupDefaultsPanel)
from data_browser import DataBrowserPanel
from script_console import ScriptConsolePanel
from calibration import CalibrationPanel
from device_registry import DeviceRegistryPanel, load_registry, registry_to_sensors
import play_intro

log = logging.getLogger(__name__)


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
QProgressBar{background:#313244;border:1px solid #45475a;
  border-radius:4px;text-align:center;color:#cdd6f4;}
QProgressBar::chunk{background:#89b4fa;border-radius:3px;}
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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Samba v3 — ETH Zürich")
        self.setMinimumSize(1360, 920)

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

        for n in SETUP_NAMES:
            self._setups[n] = load_setup(n)

        self.setStyleSheet(DARK_STYLE)
        self._build_ui()
        self._connect_signals()
        self._load_active_config()

        self._rb_timer = QTimer(self); self._rb_timer.setInterval(500)
        self._rb_timer.timeout.connect(self._poll_field_readback)
        self._rb_timer.start()
        self._restore_geometry()

    def _active_setup(self) -> dict:
        return self._setups[self._active_setup_name]

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
        self.pbar = QProgressBar(); self.pbar.setFixedHeight(16)
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color:#6c7086;font-size:11px;")
        self.status_lbl.setWordWrap(True)
        pr.addWidget(self.pbar, stretch=1); pr.addWidget(self.status_lbl, stretch=2)
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
        self.bottom_tabs.addTab(self.traj_panel,   "Trajectory")
        self.bottom_tabs.addTab(self.sl_panel,     "Scanlist")
        self.bottom_tabs.addTab(self.data_browser,  "Data Browser")
        self.script_console = ScriptConsolePanel()
        self.bottom_tabs.addTab(self.script_console, "Script")
        self.dev_registry = DeviceRegistryPanel()
        self.bottom_tabs.addTab(self.dev_registry, "Device Registry")
        self.setup_defaults = SetupDefaultsPanel()
        self.bottom_tabs.addTab(self.setup_defaults, "Setup Defaults")
        bw_l.addWidget(self.bottom_tabs, stretch=1)

        v_split.addWidget(bottom_w)
        v_split.setSizes([600, 300])
        v_split.setStretchFactor(0, 1)
        v_split.setStretchFactor(1, 1)
        self._v_split = v_split
        self._split_initialised = False

        main_v.addWidget(v_split)

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
        # Clear plots so stale range never persists across setups
        self.map2d.clear(); self.plot1d.clear()
        # Evict stale SimProxy entries for this setup's devices
        for key in ("magnet_device", "relay_device", "keithley_device"):
            dev = self._active_setup().get(key, "")
            if dev: evict_proxy(dev)
        self._load_active_config()
        # Update calibration panel FL sensor default
        fl_dev = self._active_setup().get("focus_averagein", "")
        if fl_dev: self.calib_panel.set_fl_device(fl_dev)
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
        # Populate all registry-driven combos FIRST so load_config finds the items
        registry = self.dev_registry.get_registry()
        self.traj_panel.populate_monitor_combo(registry)
        self.setup_defaults.set_registry(registry)
        # Load setup defaults and push actuator labels / TR-MOKE device to trajectory
        self.setup_defaults.load(setup)
        self.traj_panel.set_actuator_defaults(
            setup.get("act1_label", "X"), setup.get("act1_unit", "nm"),
            setup.get("act2_label", "Y"), setup.get("act2_unit", "nm"))
        self.traj_panel.set_trmoke_device(setup.get("trmoke_dg645", ""))
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
        # Sync save_dir and setup defaults back into setup
        setup["save_dir"] = self.save_dir.text().strip()
        setup.update(self.setup_defaults.get_defaults())
        self.cfg_list.sync_name(idx, old["name"])
        save_setup(self._active_setup_name, setup)

    def _explicit_save(self):
        self._save_active_config(); self.status_lbl.setText("Config saved ✓")

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
            defaults.get("act1_label", "X"), defaults.get("act1_unit", "nm"),
            defaults.get("act2_label", "Y"), defaults.get("act2_unit", "nm"))
        self.traj_panel.set_trmoke_device(defaults.get("trmoke_dg645", ""))
        # Calibration panel follows the focus sensor
        fl_dev = defaults.get("focus_averagein", "")
        if fl_dev:
            self.calib_panel.set_fl_device(fl_dev)

    def _on_scan_mode_changed(self, mode: str):
        """Called when trajectory panel switches between SPATIAL/FIELD/DC_HYST."""
        self.right_panel.set_dc_mode(mode == "DC_HYST")

    def _browse_save_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Save directory", self.save_dir.text())
        if d: self.save_dir.setText(d)

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
            dg_path = setup.get("trmoke_dg645", "intermag/dg645/1")
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
            elif mode == "FIELD":   xl, xu = "Field", "T"
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
        worker.progress.connect(lambda c, t: self.pbar.setValue(c))
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

        self._current_scan_cfg = cfg
        self._setup_live_display(cfg, active); self._alloc_scan_data(cfg, active)
        _, n_x, n_y = self._scan_dims(cfg)
        # For DC_HYST use cycle count for the progress bar; reset DC accumulators
        if cfg.get("scan_type") == "DC_HYST":
            self.pbar.setMaximum(int(cfg.get("hyst_cycles", 1)))
            self._dc_loop_x = []; self._dc_loop_y = {}; self._last_dc_cycle = 0
            self.traj_panel.reset_dc_monitor()
        else:
            self.pbar.setMaximum(n_x * n_y)
        self.pbar.setValue(0); self.log_text.clear()

        # TR_MOKE is executed as a standard SPATIAL 1D scan — the actuator
        # is the DG645 delay attribute. Store unit factor for x-axis display,
        # then convert scan_type before passing to ScanRunner.
        self._trmoke_x_factor = None
        if cfg.get("scan_type") == "TR_MOKE":
            unit = cfg.get("act1_unit", "ns")
            _factors = {"ps": 1e-12, "ns": 1e-9, "µs": 1e-6}
            self._trmoke_x_factor = 1.0 / _factors.get(unit, 1e-9)
            cfg["scan_type"] = "SPATIAL"

        self._worker = ScanWorker(cfg, setup)
        self._wire_worker(self._worker)
        if cfg.get("scan_type") == "DC_HYST":
            self._worker.dc_loop_ready.connect(self.traj_panel.update_dc_live)

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
        self.pbar.setMaximum(n_pts); self.pbar.setValue(0)

        self._worker = ScanWorker(cfg, setup)
        self._wire_worker(self._worker)

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
            disp = self.right_panel.get_display_sensor()
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
        # Detect auto-pause from ScanRunner and update button state
        if self._worker and self._worker.is_paused():
            self.pause_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")

    def _on_worker_finished(self):
        cfg_type = self._current_scan_cfg.get("scan_type", "") if self._current_scan_cfg else ""
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
            QMessageBox.information(self, "Scan complete", f"Saved:\n{self._last_fn}")
            self._last_fn = None

    def _toggle_pause(self):
        if not self._scan_running or not self._worker: return
        _style = self.style()
        if self._worker.is_paused():
            self._worker.resume()
            self.pause_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.pause_btn.setText("Pause")
        else:
            self._worker.pause()
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

        self._sl_worker = ScanlistWorker(cfg, setup, sl["n_scans"], sl["list_name"],
                                         sl["relay_flip"], sl["field_flip"])
        self._sl_worker.point_done.connect(self._on_point)
        self._sl_worker.progress.connect(lambda c, t: self.pbar.setValue(c))
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
        self._scan_running = True; self._set_running(True); self.log_text.clear()
        self._sl_worker.start()

    def _on_scanlist_done(self, txt_path: str):
        try: self.data_browser.refresh()
        except Exception:
            log.debug("Data browser refresh failed after scanlist", exc_info=True)
        QMessageBox.information(self, "Scanlist complete", f"Saved:\n{txt_path}")

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

    def _abort_scanlist(self):
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
        if sensor and sensor in self._scan_data and self.map2d._img is not None:
            self.map2d.switch_sensor(self._scan_data[sensor], sensor)
        self.map2d.set_colormap(cmap)

    def _poll_field_readback(self):
        # HW panel always reads from the magnet device
        setup    = self._active_setup()
        dev      = setup.get("magnet_device", "")
        fld_attr = setup.get("magnet_field_attr", "field_polar_corr")
        p = get_proxy(dev)
        v, _ = safe_read(p, fld_attr)
        self.traj_panel.hw.update_field_readback(v)
        self.sl_panel.hw.update_field_readback(v)

        if not self._scan_running or not self._current_scan_cfg:
            return
        scan_t = self._current_scan_cfg.get("scan_type", "")

        # AC field scan: read from monitor dropdown device
        if scan_t == "FIELD":
            mon_dev, mon_attr = self.traj_panel.get_monitor_device()
            if mon_dev and mon_attr:
                mp = get_proxy(mon_dev)
                mv, _ = safe_read(mp, mon_attr)
                self.traj_panel.update_field_monitor(mv)
            else:
                # Fallback to magnet field
                self.traj_panel.update_field_monitor(v)

        # DC hyst: poll CycleReadback + Hc from hyst device only
        # (DO NOT poll other devices — the Beckhoff controls the field internally.
        #  The monitor graph is fed by dc_loop_ready signal, not by polling.)
        elif scan_t == "DC_HYST":
            hyst_dev = self._current_scan_cfg.get("hyst_device", "")
            if not hyst_dev:
                return
            hp = get_proxy(hyst_dev)
            cyc_v, _ = safe_read(hp, "CycleReadback")
            if cyc_v is None:
                return
            c_int = int(cyc_v)
            if c_int != self._last_dc_cycle and c_int > 0:
                self._last_dc_cycle = c_int
                hc_v,  _ = safe_read(hp, "Hc")
                hsh_v, _ = safe_read(hp, "Hshift")
                if hc_v is not None:
                    self.traj_panel.update_dc_cycle(
                        c_int,
                        float(hc_v),
                        float(hsh_v) if hsh_v is not None else 0.0)

        # Poll stage positions for calibration tab (only when visible)
        if self.live_tabs.currentWidget() is self.calib_panel:
            info = self.calib_panel.get_axis_info()
            vals = {}
            for axis_key in ("x", "y", "z"):
                dev, attr = info.get(axis_key, ("", ""))
                if dev:
                    p = get_proxy(dev)
                    v, _ = safe_read(p, attr)
                    vals[axis_key] = v
            self.calib_panel.update_positions(vals)

        # Poll TR-MOKE delay readback when TR-MOKE is active
        self.traj_panel.tr_refresh()

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not self._split_initialised and self.height() > 100:
            top = int(self.height() * 0.55)
            bot = self.height() - top
            self._v_split.setSizes([top, bot])
            self._split_initialised = True

    def _restore_geometry(self):
        s = QSettings("ETH-Intermag","SambaV3"); g = s.value("geometry")
        if g: self.restoreGeometry(bytes(g))

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

    update_splash(splash, "Ready!")

    # Show window after splash finishes (3 second minimum display)
    finish_splash(splash, win, min_seconds=3)

    if not TANGO_AVAILABLE:
        QMessageBox.information(win, "Simulation Mode",
            "pytango not installed — running with simulated hardware.\n\n"
            "Install:  pip install pytango\n"
            "Connect:  export TANGO_HOST=192.168.1.1:10000")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
