"""
cryo_monitor.py — Samba Cryo
Rolling-history monitor dialog for the AttoDRY cryostat.

Three-column grid with one subplot per channel:
  Column 1: Temperatures — Sample, VTI, Magnet, Reservoir  (K)     [4 rows]
  Column 2: Pressures    — CryostatIn, CryostatOut          (mbar)  [2 rows]
  Column 3: Heater powers— Sample, VTI, Reservoir            (W)    [3 rows]

Usage:
    dlg = CryoMonitorDialog(attodry_device="hpp-N42/attoDRY/attoDRY", parent=win)
    dlg.show()
"""
import time, collections
import numpy as np

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget
from PyQt6.QtCore import QTimer, Qt

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec

from hardware import get_proxy, safe_read


# ─────────────────────────────────────────────────────────────────────────────
_MAXLEN = 120   # 120 samples × 0.5 s = 60 s window

_TEMP_CHANNELS = [
    ("Temperature",           "Sample T",   "#a6e3a1"),
    ("VtiTemperature",        "VTI T",      "#89b4fa"),
    ("MagnetTemperature",     "Magnet T",   "#fab387"),
    ("ReservoirTemperature",  "Reservoir T","#cba6f7"),
]

_PRES_CHANNELS = [
    ("CryostatInPressure",    "In",   "#89dceb"),
    ("CryostatOutPressure",   "Out",  "#f38ba8"),
]

_HEAT_CHANNELS = [
    ("SampleHeaterPower",     "Sample",    "#a6e3a1"),
    ("VtiHeaterPower",        "VTI",       "#89b4fa"),
    ("ReservoirHeaterPower",  "Reservoir", "#cba6f7"),
]

# Maximum number of rows across all columns (temperatures have 4)
_MAX_ROWS = 4


class CryoMonitorDialog(QDialog):
    def __init__(self, attodry_device: str = "hpp-N42/attoDRY/attoDRY",
                 setup_getter=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cryo Monitor — AttoDRY")
        self.setMinimumSize(1100, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._dev = attodry_device
        self._setup_getter = setup_getter
        self._t0  = time.time()

        # Build channel lists from setup defaults (or hardcoded fallbacks)
        (self._temp_channels,
         self._pres_channels,
         self._heat_channels) = self._build_channel_lists()

        # Rolling buffers: {attr: deque}
        self._time_buf = collections.deque(maxlen=_MAXLEN)
        self._bufs = {}
        for ch_list in (self._temp_channels, self._pres_channels, self._heat_channels):
            for attr, _, _ in ch_list:
                self._bufs[attr] = collections.deque(maxlen=_MAXLEN)

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    # ── Channel-list builder ──────────────────────────────────────────────────
    def _build_channel_lists(self):
        s = self._setup_getter() if self._setup_getter else {}
        temp = [
            (s.get("attodry_attr_temp_rb",        "Temperature"),           "Sample T",   "#a6e3a1"),
            (s.get("attodry_attr_vti_temp",        "VtiTemperature"),        "VTI T",      "#89b4fa"),
            (s.get("attodry_attr_mag_temp",        "MagnetTemperature"),     "Magnet T",   "#fab387"),
            (s.get("attodry_attr_reservoir_temp",  "ReservoirTemperature"),  "Reservoir T","#cba6f7"),
        ]
        pres = [
            (s.get("attodry_attr_pressure_in",  "CryostatInPressure"),  "In",  "#89dceb"),
            (s.get("attodry_attr_pressure_out", "CryostatOutPressure"), "Out", "#f38ba8"),
        ]
        heat = [
            (s.get("attodry_attr_heat_sample",    "SampleHeaterPower"),     "Sample",    "#a6e3a1"),
            (s.get("attodry_attr_heat_vti",       "VtiHeaterPower"),        "VTI",       "#89b4fa"),
            (s.get("attodry_attr_heat_reservoir", "ReservoirHeaterPower"),  "Reservoir", "#cba6f7"),
        ]
        return temp, pres, heat

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # Status row
        hdr = QHBoxLayout()
        self._dev_lbl = QLabel(self._dev)
        self._dev_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        hdr.addWidget(self._dev_lbl)
        hdr.addStretch()
        self._status = QLabel("")
        self._status.setStyleSheet("font-size:10px;")
        hdr.addWidget(self._status)
        root.addLayout(hdr)

        # Matplotlib figure with GridSpec: 3 columns, _MAX_ROWS rows
        self._fig = Figure(figsize=(12, 6), dpi=100, facecolor="#1e1e2e")
        gs = GridSpec(_MAX_ROWS, 3, figure=self._fig, hspace=0.55, wspace=0.30)

        self._channel_axes = []  # list of (ax, attr, label, color, unit)

        # Column 0: Temperatures (4 rows)
        for i, (attr, label, color) in enumerate(self._temp_channels):
            ax = self._fig.add_subplot(gs[i, 0])
            self._channel_axes.append((ax, attr, label, color, "K"))

        # Column 1: Pressures (2 rows)
        for i, (attr, label, color) in enumerate(self._pres_channels):
            ax = self._fig.add_subplot(gs[i, 1])
            self._channel_axes.append((ax, attr, label, color, "mbar"))
        for i in range(len(self._pres_channels), _MAX_ROWS):
            ax = self._fig.add_subplot(gs[i, 1])
            ax.set_visible(False)

        # Column 2: Heater powers (3 rows)
        for i, (attr, label, color) in enumerate(self._heat_channels):
            ax = self._fig.add_subplot(gs[i, 2])
            self._channel_axes.append((ax, attr, label, color, "W"))
        for i in range(len(self._heat_channels), _MAX_ROWS):
            ax = self._fig.add_subplot(gs[i, 2])
            ax.set_visible(False)

        # Style all visible axes and create initial (empty) lines
        self._lines = {}  # attr -> Line2D
        for ax, attr, label, color, unit in self._channel_axes:
            ax.set_facecolor("#12121f")
            ax.set_title(f"{label} ({unit})", color=color, fontsize=8, pad=3)
            ax.tick_params(colors="#aaaacc", labelsize=6)
            for sp in ax.spines.values():
                sp.set_edgecolor("#3a3a5c")
            line, = ax.plot([], [], color=color, linewidth=1.2)
            self._lines[attr] = line

        # Column headers
        self._fig.text(0.19, 0.98, "Temperatures", ha="center", va="top",
                       color="#6c7086", fontsize=10, fontweight="bold")
        self._fig.text(0.50, 0.98, "Pressures", ha="center", va="top",
                       color="#6c7086", fontsize=10, fontweight="bold")
        self._fig.text(0.81, 0.98, "Heater Powers", ha="center", va="top",
                       color="#6c7086", fontsize=10, fontweight="bold")

        self._canvas = FigureCanvas(self._fig)
        root.addWidget(self._canvas, stretch=1)

        # Readback labels row
        rb_row = QHBoxLayout()
        self._rb_labels = {}
        for attr, label, color in self._temp_channels:
            lbl = QLabel(f"{label}: —")
            lbl.setStyleSheet(f"color:{color};font-size:11px;font-weight:bold;"
                              "font-family:'Courier New',monospace;")
            rb_row.addWidget(lbl)
            self._rb_labels[attr] = lbl
        root.addLayout(rb_row)

    # ── Polling ───────────────────────────────────────────────────────────────
    def _poll(self):
        p = get_proxy(self._dev)
        t = time.time() - self._t0
        self._time_buf.append(t)

        ok = True
        for attr, buf in self._bufs.items():
            v, err = safe_read(p, attr)
            if err:
                ok = False
                buf.append(float('nan'))
            else:
                buf.append(float(v) if v is not None else float('nan'))

        # Update readback labels (temperatures only)
        for attr, label, _ in self._temp_channels:
            lbl = self._rb_labels.get(attr)
            if lbl and self._bufs[attr]:
                val = self._bufs[attr][-1]
                if np.isfinite(val):
                    lbl.setText(f"{label}: {val:.2f} K")

        self._status.setStyleSheet(
            "color:#a6e3a1;font-size:10px;" if ok else "color:#f38ba8;font-size:10px;")
        self._status.setText("OK" if ok else "read error")

        self._redraw()

    def _redraw(self):
        if len(self._time_buf) < 2:
            return
        ts = list(self._time_buf)
        x_lo = max(0.0, ts[-1] - 60)
        x_hi = ts[-1] + 1

        for ax, attr, label, color, unit in self._channel_axes:
            vs = list(self._bufs[attr])
            line = self._lines[attr]
            if len(vs) == len(ts):
                line.set_data(ts, vs)
            ax.set_xlim(x_lo, x_hi)
            ax.relim()
            ax.autoscale_view(scalex=False, scaley=True)

        self._fig.subplots_adjust(top=0.93)
        self._canvas.draw_idle()

    def showEvent(self, ev):
        """Resume polling when the dialog becomes visible."""
        super().showEvent(ev)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, ev):
        """Pause polling while the dialog is hidden to avoid unnecessary
        device I/O and CPU overhead from background rendering."""
        super().hideEvent(ev)
        self._timer.stop()

    def closeEvent(self, ev):
        # Stop the timer but do NOT clear the figure — WA_DeleteOnClose=False
        # keeps the dialog alive for re-use, and clearing the figure would
        # destroy all axes/artists so re-opening would show a blank window.
        self._timer.stop()
        super().closeEvent(ev)
