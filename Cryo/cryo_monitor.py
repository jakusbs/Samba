"""
cryo_monitor.py — Samba Cryo  (v2)
Comprehensive AttoDRY monitor and control panel.

Left panel  (~430 px, scrollable):
  • Status flags (LED badges for 7 boolean states)
  • Numeric readbacks (all 15 channels)
  • Field Control  — setpoint, Mag Ctrl toggle, Persistent Mode toggle, Sweep to Zero
  • Temperature Control — setpoint, Full Temp Ctrl toggle, Sample Temp Ctrl, Go to Base T
  • Sample Exchange — status, Start Exchange, Cancel
  • System — Startup/Shutdown, Clear Error
  • Message log

Right panel (flexible):
  • Time-window selector (1 min → 3 hr)
  • 4 stacked rolling plots: Temperatures (K) / Pressures (mbar) /
    Heater Powers (W) / Turbopump (Hz)

Architecture:
  QTimer (500 ms) → daemon thread reads all attrs via read_attributes() →
  _data_ready signal → _apply() updates UI on main thread.
"""
import time
import collections
import threading

from PyQt6.QtWidgets import (
    QDialog, QSplitter, QScrollArea, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QPushButton, QDoubleSpinBox,
    QAbstractSpinBox, QTextEdit, QComboBox,
)
from PyQt6.QtCore import QTimer, Qt, pyqtSignal

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from hardware import get_proxy, fresh_proxy, safe_write


# ── Constants ──────────────────────────────────────────────────────────────────
_POLL_MS      = 500
_MAX_WINDOW_S = 3 * 3600                                    # 3 hours
_MAXLEN       = int(_MAX_WINDOW_S * 1000 / _POLL_MS) + 10  # ~21 610 samples

_WINDOWS = [
    ("1 min",  60),
    ("5 min",  300),
    ("30 min", 1800),
    ("1 hr",   3600),
    ("3 hr",   10800),
]

# All attributes to batch-read every poll cycle
_ALL_ATTRS = [
    "MagneticField", "MagneticFieldSetpoint",
    "Temperature", "UserTemperature",
    "VtiTemperature", "MagnetTemperature",
    "Stage40KTemperature", "ReservoirTemperature",
    "CryostatInPressure", "CryostatOutPressure", "DumpPressure",
    "SampleHeaterPower", "VtiHeaterPower", "ReservoirHeaterPower",
    "TurbopumpFrequency",
    "toggleMagneticFieldControl",
    "toggleFulltemperatureControl",
    "togglePersistentMode",
    "GoingToBaseTemperature", "SampleExchangeInProgress",
    "SampleReadyToExchange", "ZeroingField",
    "Pumping", "SystemRunning", "SampleHeaterOn",
    "ErrorStatus", "ErrorMessage", "ActionMessage",
]

# ── Plot channel definitions ───────────────────────────────────────────────────
_TEMP_LINES = [
    ("Temperature",          "Sample",    "#a6e3a1"),
    ("VtiTemperature",       "VTI",       "#89b4fa"),
    ("MagnetTemperature",    "Magnet",    "#fab387"),
    ("Stage40KTemperature",  "40K Stage", "#f9e2af"),
    ("ReservoirTemperature", "Reservoir", "#cba6f7"),
]
_PRES_LINES = [
    ("CryostatInPressure",  "Cryo In",  "#89dceb"),
    ("CryostatOutPressure", "Cryo Out", "#f38ba8"),
    ("DumpPressure",        "Dump",     "#94e2d5"),
]
_HEAT_LINES = [
    ("SampleHeaterPower",    "Sample",    "#a6e3a1"),
    ("VtiHeaterPower",       "VTI",       "#89b4fa"),
    ("ReservoirHeaterPower", "Reservoir", "#cba6f7"),
]
_TURBO_LINES = [
    ("TurbopumpFrequency", "Turbopump", "#f9e2af"),
]
_PLOT_GROUPS = [
    ("Temperature (K)",  _TEMP_LINES),
    ("Pressure (mbar)",  _PRES_LINES),
    ("Heater Power (W)", _HEAT_LINES),
    ("Turbopump (Hz)",   _TURBO_LINES),
]

# ── Boolean flag badges ────────────────────────────────────────────────────────
_FLAGS = [
    ("SystemRunning",            "System Running",       "#a6e3a1"),
    ("Pumping",                  "Pumping",              "#89b4fa"),
    ("GoingToBaseTemperature",   "Going to Base T",      "#f9e2af"),
    ("ZeroingField",             "Zeroing Field",        "#fab387"),
    ("SampleExchangeInProgress", "Exchange In Progress", "#89dceb"),
    ("SampleReadyToExchange",    "Ready to Exchange",    "#a6e3a1"),
    ("SampleHeaterOn",           "Sample Heater On",     "#f38ba8"),
]

# ── Numeric readback rows: (attr, label, color, unit, decimals) ───────────────
_READINGS = [
    ("MagneticField",        "Field:",          "#89b4fa", "T",    4),
    ("MagneticFieldSetpoint","Field SP:",        "#6c7086", "T",    4),
    ("Temperature",          "Sample T:",        "#a6e3a1", "K",    3),
    ("UserTemperature",      "T Setpoint:",      "#6c7086", "K",    3),
    ("VtiTemperature",       "VTI T:",           "#89b4fa", "K",    3),
    ("MagnetTemperature",    "Magnet T:",        "#fab387", "K",    3),
    ("Stage40KTemperature",  "40K Stage T:",     "#f9e2af", "K",    3),
    ("ReservoirTemperature", "Reservoir T:",     "#cba6f7", "K",    3),
    ("CryostatInPressure",   "Cryo In P:",       "#89dceb", "mbar", 4),
    ("CryostatOutPressure",  "Cryo Out P:",      "#f38ba8", "mbar", 4),
    ("DumpPressure",         "Dump P:",          "#94e2d5", "mbar", 4),
    ("SampleHeaterPower",    "Sample Htr:",      "#a6e3a1", "W",    4),
    ("VtiHeaterPower",       "VTI Htr:",         "#89b4fa", "W",    4),
    ("ReservoirHeaterPower", "Res. Htr:",        "#cba6f7", "W",    4),
    ("TurbopumpFrequency",   "Turbopump:",       "#f9e2af", "Hz",   1),
]

# ── CSS constants ──────────────────────────────────────────────────────────────
_GRP = (
    "QGroupBox{{color:{col};font-weight:bold;border:1px solid #45475a;"
    "border-radius:4px;margin-top:8px;padding-top:2px;}}"
    "QGroupBox::title{{subcontrol-origin:margin;left:8px;padding:0 4px;}}"
)
_TOGGLE = (
    "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
    "border-radius:4px;padding:4px 8px;font-size:11px;}"
    "QPushButton:checked{background:#a6e3a1;color:#1e1e2e;font-weight:bold;"
    "border:1px solid #a6e3a1;}"
    "QPushButton:hover{border:1px solid #89b4fa;}"
)
_ACTION = (
    "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
    "border-radius:4px;padding:4px 8px;font-size:11px;}"
    "QPushButton:hover{background:#45475a;border:1px solid #89b4fa;}"
    "QPushButton:pressed{background:#1e1e2e;}"
)
_DANGER = (
    "QPushButton{background:#313244;color:#f38ba8;border:1px solid #f38ba8;"
    "border-radius:4px;padding:4px 8px;font-size:11px;}"
    "QPushButton:hover{background:#45475a;}"
    "QPushButton:pressed{background:#1e1e2e;}"
)


# ── LED badge ──────────────────────────────────────────────────────────────────
class _LED(QLabel):
    _ON  = ("color:#1e1e2e;background:{col};border-radius:5px;"
            "font-size:10px;padding:2px 6px;font-weight:bold;")
    _OFF = ("color:#585b70;background:#313244;border-radius:5px;"
            "font-size:10px;padding:2px 6px;")

    def __init__(self, text: str, color: str = "#a6e3a1"):
        super().__init__(text)
        self._color = color
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(118)
        self.setState(False)

    def setState(self, on: bool):
        self.setStyleSheet(
            self._ON.format(col=self._color) if on else self._OFF)


# ── Main dialog ────────────────────────────────────────────────────────────────
class CryoMonitorDialog(QDialog):
    _data_ready = pyqtSignal(dict)
    _cmd_status = pyqtSignal(bool, str)   # (ok, message)

    def __init__(self, attodry_device: str = "hpp-N42/attoDRY/attoDRY",
                 setup_getter=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AttoDRY Monitor & Control")
        self.setMinimumSize(1300, 740)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._dev = attodry_device
        self._setup_getter = setup_getter
        self._t0 = time.time()
        self._polling = False
        self._last_msg_key = None

        # Rolling time-series buffers
        self._time_buf: collections.deque = collections.deque(maxlen=_MAXLEN)
        self._plot_bufs: dict = {}
        for _, channels in _PLOT_GROUPS:
            for attr, _, _ in channels:
                self._plot_bufs[attr] = collections.deque(maxlen=_MAXLEN)

        # Toggle button registry — populated by _build_field_ctrl / _build_temp_ctrl
        self._toggle_btns: list = []

        self._build_ui()

        self._data_ready.connect(self._apply)
        self._cmd_status.connect(self._show_cmd_status)

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._schedule_poll)
        self._timer.start()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(4)

        # Top status bar
        top = QHBoxLayout()
        self._dev_lbl = QLabel(self._dev)
        self._dev_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        top.addWidget(self._dev_lbl)
        top.addStretch()
        self._conn_dot = QLabel("●")
        self._conn_dot.setStyleSheet("color:#6c7086;font-size:16px;padding:0 4px;")
        top.addWidget(self._conn_dot)
        self._conn_lbl = QLabel("—")
        self._conn_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        top.addWidget(self._conn_lbl)
        root.addLayout(top)

        # Main horizontal splitter
        spl = QSplitter(Qt.Orientation.Horizontal)
        spl.setStyleSheet("QSplitter::handle{background:#45475a;width:2px;}")
        root.addWidget(spl, stretch=1)

        # ── Left scroll panel ──────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(432)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:#1e1e2e;}")
        left_w = QWidget()
        left_w.setStyleSheet("background:#1e1e2e;")
        lv = QVBoxLayout(left_w)
        lv.setSpacing(5)
        lv.setContentsMargins(4, 2, 4, 4)
        scroll.setWidget(left_w)
        spl.addWidget(scroll)

        self._build_flags(lv)
        self._build_readings(lv)
        self._build_field_ctrl(lv)
        self._build_temp_ctrl(lv)
        self._build_exchange(lv)
        self._build_system(lv)
        self._build_log(lv)
        lv.addStretch()

        # ── Right plots panel ──────────────────────────────────────────────
        right_w = QWidget()
        rv = QVBoxLayout(right_w)
        rv.setContentsMargins(4, 2, 4, 4)
        rv.setSpacing(4)
        spl.addWidget(right_w)
        spl.setSizes([432, 868])

        win_row = QHBoxLayout()
        win_row.addWidget(QLabel("Time window:"))
        self._win_combo = QComboBox()
        for label, _ in _WINDOWS:
            self._win_combo.addItem(label)
        self._win_combo.setCurrentIndex(1)   # 5 min default
        self._win_combo.setFixedWidth(80)
        self._win_combo.currentIndexChanged.connect(self._redraw)
        win_row.addWidget(self._win_combo)
        win_row.addStretch()
        rv.addLayout(win_row)

        self._build_plots(rv)

    def _grp(self, title: str, color: str = "#cba6f7") -> QGroupBox:
        g = QGroupBox(title)
        g.setStyleSheet(_GRP.format(col=color))
        return g

    def _build_flags(self, parent):
        grp = self._grp("Status Flags")
        g = QGridLayout(grp)
        g.setSpacing(4)
        g.setContentsMargins(6, 12, 6, 6)
        self._leds: dict = {}
        for i, (attr, label, color) in enumerate(_FLAGS):
            led = _LED(label, color)
            self._leds[attr] = led
            g.addWidget(led, i // 2, i % 2)
        parent.addWidget(grp)

    def _build_readings(self, parent):
        grp = self._grp("Readings")
        g = QGridLayout(grp)
        g.setSpacing(2)
        g.setContentsMargins(6, 12, 6, 6)
        g.setColumnStretch(1, 1)
        _RB = ("color:{col};font-family:'Courier New',monospace;"
               "font-weight:bold;font-size:12px;")
        self._rb: dict = {}
        for row, (attr, label, color, unit, dec) in enumerate(_READINGS):
            g.addWidget(QLabel(label), row, 0)
            lbl = QLabel(f"—  {unit}")
            lbl.setStyleSheet(_RB.format(col=color))
            lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            g.addWidget(lbl, row, 1)
            self._rb[attr] = (lbl, unit, dec)
        parent.addWidget(grp)

    def _build_field_ctrl(self, parent):
        grp = self._grp("Field Control", "#89b4fa")
        g = QGridLayout(grp)
        g.setSpacing(4)
        g.setContentsMargins(6, 12, 6, 6)

        g.addWidget(QLabel("Setpoint (T):"), 0, 0)
        self._field_sp = QDoubleSpinBox()
        self._field_sp.setRange(-9.0, 9.0)
        self._field_sp.setDecimals(4)
        self._field_sp.setSuffix(" T")
        self._field_sp.setFixedWidth(112)
        self._field_sp.setStepType(
            QAbstractSpinBox.StepType.AdaptiveDecimalStepType)
        g.addWidget(self._field_sp, 0, 1)
        btn_apply = QPushButton("Apply")
        btn_apply.setFixedWidth(54)
        btn_apply.setStyleSheet(_ACTION)
        btn_apply.clicked.connect(self._write_field)
        g.addWidget(btn_apply, 0, 2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_mag_ctrl = QPushButton("Mag Ctrl")
        self.btn_mag_ctrl.setCheckable(True)
        self.btn_mag_ctrl.setStyleSheet(_TOGGLE)
        self.btn_mag_ctrl.setToolTip(
            "Toggle active magnetic field control.\n"
            "ON (green): magnet ramps to the field setpoint.\n"
            "OFF: magnet holds current field without regulation.")
        self.btn_mag_ctrl.clicked.connect(self._toggle_mag_ctrl)
        btn_row.addWidget(self.btn_mag_ctrl)

        self.btn_persist = QPushButton("Persistent")
        self.btn_persist.setCheckable(True)
        self.btn_persist.setStyleSheet(_TOGGLE)
        self.btn_persist.setToolTip(
            "Toggle persistent mode.\n"
            "ON (green): field held without current (lower heat load).\n"
            "OFF: active drive required before changing field.")
        self.btn_persist.clicked.connect(self._toggle_persist)
        btn_row.addWidget(self.btn_persist)
        g.addLayout(btn_row, 1, 0, 1, 3)

        btn_zero = QPushButton("Sweep to Zero")
        btn_zero.setStyleSheet(_ACTION)
        btn_zero.clicked.connect(lambda: self._send_cmd("SweepFieldToZero"))
        g.addWidget(btn_zero, 2, 0, 1, 3)

        self._toggle_btns.extend([
            ("toggleMagneticFieldControl", self.btn_mag_ctrl),
            ("togglePersistentMode",       self.btn_persist),
        ])
        parent.addWidget(grp)

    def _build_temp_ctrl(self, parent):
        grp = self._grp("Temperature Control", "#a6e3a1")
        g = QGridLayout(grp)
        g.setSpacing(4)
        g.setContentsMargins(6, 12, 6, 6)

        g.addWidget(QLabel("Setpoint (K):"), 0, 0)
        self._temp_sp = QDoubleSpinBox()
        self._temp_sp.setRange(0.0, 400.0)
        self._temp_sp.setDecimals(2)
        self._temp_sp.setSuffix(" K")
        self._temp_sp.setFixedWidth(112)
        self._temp_sp.setStepType(
            QAbstractSpinBox.StepType.AdaptiveDecimalStepType)
        g.addWidget(self._temp_sp, 0, 1)
        btn_apply = QPushButton("Apply")
        btn_apply.setFixedWidth(54)
        btn_apply.setStyleSheet(_ACTION)
        btn_apply.clicked.connect(self._write_temp)
        g.addWidget(btn_apply, 0, 2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_temp_ctrl = QPushButton("Full Temp Ctrl")
        self.btn_temp_ctrl.setCheckable(True)
        self.btn_temp_ctrl.setStyleSheet(_TOGGLE)
        self.btn_temp_ctrl.setToolTip(
            "Toggle full temperature control (PID).\n"
            "ON (green): temperature actively regulated to setpoint.\n"
            "OFF: heaters off, temperature drifts freely.")
        self.btn_temp_ctrl.clicked.connect(self._toggle_temp_ctrl)
        btn_row.addWidget(self.btn_temp_ctrl)

        btn_stc = QPushButton("Sample Temp Ctrl")
        btn_stc.setStyleSheet(_ACTION)
        btn_stc.setToolTip(
            "Toggle sample-only temperature control\n"
            "(independent of VTI heater loop).")
        btn_stc.clicked.connect(
            lambda: self._send_cmd("toggleSampleTemperatureControl"))
        btn_row.addWidget(btn_stc)
        g.addLayout(btn_row, 1, 0, 1, 3)

        self._toggle_btns.append(
            ("toggleFulltemperatureControl", self.btn_temp_ctrl))

        btn_base = QPushButton("Go to Base Temperature")
        btn_base.setStyleSheet(_ACTION)
        btn_base.clicked.connect(lambda: self._send_cmd("GoToBaseTemperature"))
        g.addWidget(btn_base, 2, 0, 1, 3)

        parent.addWidget(grp)

    def _build_exchange(self, parent):
        grp = self._grp("Sample Exchange", "#89dceb")
        g = QGridLayout(grp)
        g.setSpacing(4)
        g.setContentsMargins(6, 12, 6, 6)

        self._exchange_lbl = QLabel("—")
        self._exchange_lbl.setStyleSheet(
            "color:#89dceb;font-family:'Courier New',monospace;font-size:11px;")
        self._exchange_lbl.setWordWrap(True)
        g.addWidget(self._exchange_lbl, 0, 0, 1, 2)

        btn_start = QPushButton("Start Exchange")
        btn_start.setStyleSheet(_ACTION)
        btn_start.clicked.connect(lambda: self._send_cmd("StartSampleExchange"))
        g.addWidget(btn_start, 1, 0)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(_DANGER)
        btn_cancel.clicked.connect(lambda: self._send_cmd("Cancel"))
        g.addWidget(btn_cancel, 1, 1)

        parent.addWidget(grp)

    def _build_system(self, parent):
        grp = self._grp("System", "#fab387")
        h = QHBoxLayout(grp)
        h.setSpacing(4)
        h.setContentsMargins(6, 12, 6, 6)

        btn_ss = QPushButton("Startup / Shutdown")
        btn_ss.setStyleSheet(_ACTION)
        btn_ss.setToolTip("Toggle the AttoDRY startup / shutdown sequence.")
        btn_ss.clicked.connect(lambda: self._send_cmd("toggleStartUpShutdown"))
        h.addWidget(btn_ss)

        btn_err = QPushButton("Clear Error")
        btn_err.setStyleSheet(_DANGER)
        btn_err.setToolTip("Clear the current error condition on the AttoDRY.")
        btn_err.clicked.connect(lambda: self._send_cmd("LowerError"))
        h.addWidget(btn_err)

        parent.addWidget(grp)

    def _build_log(self, parent):
        grp = self._grp("Messages", "#6c7086")
        v = QVBoxLayout(grp)
        v.setContentsMargins(6, 12, 6, 6)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(95)
        self._log.setStyleSheet(
            "background:#12121f;color:#cdd6f4;"
            "font-family:'Courier New',monospace;font-size:10px;border:none;")
        v.addWidget(self._log)
        parent.addWidget(grp)

    def _build_plots(self, parent):
        self._fig = Figure(figsize=(10, 8), dpi=90, facecolor="#1e1e2e")
        n = len(_PLOT_GROUPS)
        self._plot_lines: dict = {}
        self._axes = []

        for i, (title, channels) in enumerate(_PLOT_GROUPS):
            sharex = self._axes[0] if i > 0 else None
            ax = self._fig.add_subplot(n, 1, i + 1, sharex=sharex)
            ax.set_facecolor("#12121f")
            ax.set_title(title, color="#a6adc8", fontsize=9, pad=2, loc="left")
            ax.tick_params(colors="#aaaacc", labelsize=7)
            for sp in ax.spines.values():
                sp.set_edgecolor("#3a3a5c")
            ax.grid(color="#2a2a3c", linewidth=0.5, linestyle="--", alpha=0.7)
            if i < n - 1:
                ax.tick_params(labelbottom=False)

            legend_lines = []
            for attr, label, color in channels:
                ln, = ax.plot([], [], color=color, linewidth=1.2, label=label)
                self._plot_lines[attr] = ln
                legend_lines.append(ln)

            if len(channels) > 1:
                ax.legend(fontsize=7, loc="upper left",
                          facecolor="#1e1e2e", edgecolor="#45475a",
                          labelcolor="#cdd6f4")
            self._axes.append(ax)

        self._axes[-1].set_xlabel("Elapsed (s)", color="#aaaacc", fontsize=8)
        self._fig.tight_layout(pad=1.2, h_pad=0.8)

        self._canvas = FigureCanvas(self._fig)
        parent.addWidget(self._canvas, stretch=1)

    # ── Polling ────────────────────────────────────────────────────────────────

    def _schedule_poll(self):
        if self._polling:
            return
        self._polling = True
        threading.Thread(target=self._poll_thread, daemon=True).start()

    def _poll_thread(self):
        t = time.time() - self._t0
        data: dict = {"_t": t, "_ok": False}
        try:
            p = get_proxy(self._dev)
            results = p.read_attributes(_ALL_ATTRS)
            data["_ok"] = True
            for r in results:
                try:
                    if not r.has_failed:
                        data[r.name] = r.value
                except Exception:
                    pass
        except Exception as e:
            data["_error"] = str(e)[:120]
        finally:
            self._polling = False
        self._data_ready.emit(data)

    # ── Apply (runs on main thread via signal) ─────────────────────────────────

    def _apply(self, data: dict):
        ok = data.get("_ok", False)

        # Connection indicator
        if ok:
            self._conn_dot.setStyleSheet(
                "color:#a6e3a1;font-size:16px;padding:0 4px;")
            self._conn_lbl.setStyleSheet("color:#a6e3a1;font-size:10px;")
            self._conn_lbl.setText("Connected")
        else:
            self._conn_dot.setStyleSheet(
                "color:#f38ba8;font-size:16px;padding:0 4px;")
            self._conn_lbl.setStyleSheet("color:#f38ba8;font-size:10px;")
            self._conn_lbl.setText(data.get("_error", "Error")[:60])

        # Boolean LED flags
        for attr, _, _ in _FLAGS:
            val = data.get(attr)
            if val is not None and attr in self._leds:
                self._leds[attr].setState(bool(val))

        # Numeric readbacks
        for attr, (lbl, unit, dec) in self._rb.items():
            val = data.get(attr)
            if val is not None:
                try:
                    lbl.setText(f"{float(val):.{dec}f}  {unit}")
                except (ValueError, TypeError):
                    lbl.setText(f"{val}  {unit}")

        # Toggle button states — update without triggering click handlers
        for attr, btn in self._toggle_btns:
            val = data.get(attr)
            if val is not None:
                btn.blockSignals(True)
                btn.setChecked(bool(val))
                btn.blockSignals(False)

        # Sample exchange status
        in_prog = bool(data.get("SampleExchangeInProgress", False))
        ready   = bool(data.get("SampleReadyToExchange",    False))
        if ready:
            self._exchange_lbl.setText("Sample ready to exchange")
        elif in_prog:
            self._exchange_lbl.setText("Exchange in progress…")
        else:
            self._exchange_lbl.setText("—")

        # Message log — only append when content changes
        err_status = data.get("ErrorStatus", 0)
        err_msg    = str(data.get("ErrorMessage",  "") or "")
        act_msg    = str(data.get("ActionMessage", "") or "")
        key = (err_status, err_msg, act_msg)
        if key != self._last_msg_key:
            self._last_msg_key = key
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S")
            if act_msg:
                self._log.append(
                    f'<span style="color:#89b4fa;">[{ts}] {act_msg}</span>')
            if err_msg and err_status:
                self._log.append(
                    f'<span style="color:#f38ba8;">[{ts}] '
                    f'Err {err_status}: {err_msg}</span>')
            sb = self._log.verticalScrollBar()
            sb.setValue(sb.maximum())

        # Feed time-series buffers
        t = data.get("_t", 0.0)
        if t > 0:
            self._time_buf.append(t)
            for attr, buf in self._plot_bufs.items():
                val = data.get(attr)
                buf.append(float(val) if val is not None else float("nan"))

        self._redraw()

    def _redraw(self):
        if len(self._time_buf) < 2:
            return
        ts = list(self._time_buf)
        window_s = _WINDOWS[self._win_combo.currentIndex()][1]
        x_hi = ts[-1] + 1.0
        x_lo = max(0.0, x_hi - window_s)

        for attr, line in self._plot_lines.items():
            vs = list(self._plot_bufs[attr])
            if len(vs) == len(ts):
                line.set_data(ts, vs)

        for ax in self._axes:
            ax.set_xlim(x_lo, x_hi)
            ax.relim()
            ax.autoscale_view(scalex=False, scaley=True)

        self._canvas.draw_idle()

    # ── Control methods ────────────────────────────────────────────────────────

    def _write_field(self):
        val = self._field_sp.value()

        def _do():
            p, err = fresh_proxy(self._dev)
            if err:
                self._cmd_status.emit(False, err)
                return
            e = safe_write(p, "MagneticField", val)
            if e:
                self._cmd_status.emit(False, e[:80])
            else:
                self._cmd_status.emit(True, f"Field setpoint → {val:.4f} T")

        threading.Thread(target=_do, daemon=True).start()

    def _write_temp(self):
        val = self._temp_sp.value()

        def _do():
            p, err = fresh_proxy(self._dev)
            if err:
                self._cmd_status.emit(False, err)
                return
            e = safe_write(p, "Temperature", val)
            if e:
                self._cmd_status.emit(False, e[:80])
            else:
                self._cmd_status.emit(True, f"T setpoint → {val:.2f} K")

        threading.Thread(target=_do, daemon=True).start()

    def _write_bool_attr(self, attr: str, val: bool, label: str):
        # The AttoDRY toggle attributes ignore the written value and always
        # send the toggle command to hardware, so any write triggers a toggle.
        def _do():
            p, err = fresh_proxy(self._dev)
            if err:
                self._cmd_status.emit(False, err)
                return
            e = safe_write(p, attr, val)
            if e:
                self._cmd_status.emit(False, e[:80])
            else:
                self._cmd_status.emit(True, f"{label} toggled")

        threading.Thread(target=_do, daemon=True).start()

    def _toggle_mag_ctrl(self):
        self._write_bool_attr(
            "toggleMagneticFieldControl",
            self.btn_mag_ctrl.isChecked(), "Mag Ctrl")

    def _toggle_temp_ctrl(self):
        self._write_bool_attr(
            "toggleFulltemperatureControl",
            self.btn_temp_ctrl.isChecked(), "Full Temp Ctrl")

    def _toggle_persist(self):
        self._write_bool_attr(
            "togglePersistentMode",
            self.btn_persist.isChecked(), "Persistent Mode")

    def _send_cmd(self, cmd_name: str):
        def _do():
            p, err = fresh_proxy(self._dev)
            if err:
                self._cmd_status.emit(False, err)
                return
            try:
                p.command_inout(cmd_name)
                self._cmd_status.emit(True, cmd_name)
            except Exception as e:
                self._cmd_status.emit(False, str(e)[:80])

        threading.Thread(target=_do, daemon=True).start()

    def _show_cmd_status(self, ok: bool, msg: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        color  = "#a6e3a1" if ok else "#f38ba8"
        prefix = "✓" if ok else "⚠"
        self._log.append(
            f'<span style="color:{color};">[{ts}] {prefix} {msg}</span>')
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Qt events ──────────────────────────────────────────────────────────────

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._timer.stop()

    def closeEvent(self, ev):
        self._timer.stop()
        super().closeEvent(ev)
