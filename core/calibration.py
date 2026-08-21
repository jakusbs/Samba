"""
calibration.py — Samba v3
Calibration tab: 1D focus plot + digit-jog stage controls + autofocus.

The digit-jog controls allow precise positioning via per-digit ▲/▼ buttons.
The autofocus routine optimises the Z position by maximising a fluorescence
signal, plotting FL vs Z in real time.
"""
import logging
import time, traceback, threading
import numpy as np
from typing import Optional

log = logging.getLogger(__name__)

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavToolbar
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QSplitter,
    QSizePolicy, QDoubleSpinBox, QSpinBox, QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

from plot_interact import (ClickReadout, make_fontsize_spin, eng_axis,
                           fix_toolbar_icons, make_light_export_btn,
                           set_multicolor_ylabel, make_scale_pills,
                           recent_symmetric_ylim, SCALE_RECENT,
                           RECENT_WINDOW)
from theme import PLOT_LEFT_COLORS, PLOT_RIGHT_COLORS

from hardware import (fresh_proxy, is_sim_proxy, get_proxy, safe_read,
                      safe_write, trigger_and_read)




class _NoScrollDoubleSpinBox(QDoubleSpinBox):
    """Spinbox that ignores the wheel — a stray scroll over the panel must not
    change a safety threshold.  Module-local by the convention already used in
    core/bd_calibration.py and core/current_sweep_ui.py (core/ cannot import
    the apps' panels/_widgets)."""
    def wheelEvent(self, e):
        e.ignore()


# ─────────────────────────────────────────────────────────────────────────────
# DigitJogWidget — one axis with per-digit ▲/▼ buttons + editable field
# ─────────────────────────────────────────────────────────────────────────────
class DigitJogWidget(QWidget):
    """Single-axis digit-jog control. Clicking ▲/▼ sends move immediately.
    Text field supports Enter to move."""
    move_requested = pyqtSignal(float)

    _BTN_STYLE = (
        "QPushButton{background:#313244;color:#89b4fa;border:1px solid #45475a;"
        "border-radius:2px;font-size:10px;font-weight:bold;padding:0;}"
        "QPushButton:hover{background:#45475a;}"
        "QPushButton:pressed{background:#585b70;}")
    _BTN_DOWN_STYLE = (
        "QPushButton{background:#313244;color:#f38ba8;border:1px solid #45475a;"
        "border-radius:2px;font-size:10px;font-weight:bold;padding:0;}"
        "QPushButton:hover{background:#45475a;}"
        "QPushButton:pressed{background:#585b70;}")
    _DIGIT_STYLE = (
        "QLabel{color:#cdd6f4;font-family:'Courier New',monospace;"
        "font-size:16px;font-weight:bold;background:#181825;"
        "border:1px solid #313244;border-radius:3px;"
        "padding:2px 4px;min-width:16px;qproperty-alignment:'AlignCenter';}")
    _EDIT_STYLE = (
        "QLineEdit{background:#181825;border:1px solid #45475a;border-radius:4px;"
        "color:#cdd6f4;font-family:'Courier New',monospace;font-size:13px;"
        "font-weight:bold;padding:3px 6px;}"
        "QLineEdit:focus{border:1px solid #89b4fa;}")
    _EDIT_STYLE_UNKNOWN = (
        "QLineEdit{background:#181825;border:1px solid #45475a;border-radius:4px;"
        "color:#6c7086;font-family:'Courier New',monospace;font-size:13px;"
        "font-weight:bold;padding:3px 6px;}")

    def __init__(self, label: str = "X", unit: str = "µm",
                 n_int: int = 2, n_dec: int = 3, parent=None):
        super().__init__(parent)
        self._label = label; self._unit = unit
        self._n_int = n_int; self._n_dec = n_dec
        self._n_digits = n_int + n_dec
        self._value = 0.0; self._readback = None
        # A jog sends an ABSOLUTE target, so the widget must never hold a
        # number whose frame of reference it cannot vouch for.  Start
        # unknown: nothing can be sent until a position has been read back.
        self._known = False
        self._digit_labels = []; self._up_btns = []; self._down_btns = []
        self._build_ui()
        self._apply_known()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2); outer.setSpacing(3)

        # Row 1: digit-jog grid
        jog_row = QHBoxLayout(); jog_row.setSpacing(4)
        self._sign_lbl = QLabel("＋")
        self._sign_lbl.setFixedSize(22, 20)
        self._sign_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sign_lbl.setStyleSheet(
            "color:#cdd6f4;font-size:16px;font-weight:bold;background:#181825;"
            "border:1px solid #313244;border-radius:3px;")

        grid = QGridLayout(); grid.setSpacing(1); grid.setContentsMargins(0, 0, 0, 0)
        col = 0
        grid.addWidget(self._sign_lbl, 1, col, Qt.AlignmentFlag.AlignCenter); col += 1
        for i in range(self._n_digits):
            if i == self._n_int:
                dot = QLabel("."); dot.setFixedWidth(8)
                dot.setStyleSheet("color:#6c7086;font-size:16px;font-weight:bold;")
                dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(dot, 1, col, Qt.AlignmentFlag.AlignCenter); col += 1
            power = self._n_int - 1 - i
            up = QPushButton("▲"); up.setFixedSize(22, 16)
            up.setStyleSheet(self._BTN_STYLE); up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            up.clicked.connect(lambda _, p=power: self._nudge(10 ** p))
            grid.addWidget(up, 0, col, Qt.AlignmentFlag.AlignCenter); self._up_btns.append(up)
            d = QLabel("0"); d.setStyleSheet(self._DIGIT_STYLE)
            d.setAlignment(Qt.AlignmentFlag.AlignCenter); d.setFixedSize(22, 26)
            grid.addWidget(d, 1, col, Qt.AlignmentFlag.AlignCenter); self._digit_labels.append(d)
            dn = QPushButton("▼"); dn.setFixedSize(22, 16)
            dn.setStyleSheet(self._BTN_DOWN_STYLE); dn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            dn.clicked.connect(lambda _, p=power: self._nudge(-(10 ** p)))
            grid.addWidget(dn, 2, col, Qt.AlignmentFlag.AlignCenter); self._down_btns.append(dn)
            col += 1
        self._unit_lbl1 = QLabel(self._unit)
        self._unit_lbl1.setStyleSheet("color:#6c7086;font-size:11px;")
        grid.addWidget(self._unit_lbl1, 1, col, Qt.AlignmentFlag.AlignCenter)
        jog_row.addLayout(grid); jog_row.addStretch()
        outer.addLayout(jog_row)

        # Row 2: editable target + readback
        edit_row = QHBoxLayout(); edit_row.setSpacing(6)
        ax = QLabel(self._label); ax.setFixedWidth(16)
        ax.setStyleSheet("color:#89b4fa;font-weight:bold;font-size:13px;")
        ax.setAlignment(Qt.AlignmentFlag.AlignCenter); edit_row.addWidget(ax)
        self._edit = QLineEdit("0.000"); self._edit.setStyleSheet(self._EDIT_STYLE)
        self._edit.setFixedWidth(110); self._edit.returnPressed.connect(self._on_enter)
        edit_row.addWidget(self._edit)
        self._unit_lbl2 = QLabel(self._unit)
        self._unit_lbl2.setStyleSheet("color:#6c7086;font-size:11px;")
        edit_row.addWidget(self._unit_lbl2); edit_row.addSpacing(12)
        rb_h = QLabel("readback:"); rb_h.setStyleSheet("color:#6c7086;font-size:10px;")
        edit_row.addWidget(rb_h)
        self._rb_label = QLabel("—")
        self._rb_label.setStyleSheet(
            "color:#a6e3a1;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;")
        edit_row.addWidget(self._rb_label); edit_row.addStretch()
        outer.addLayout(edit_row)
        self._refresh_display()

    def set_unit(self, unit: str):
        """Relabel the axis unit (values are never rescaled — see
        CalibrationPanel.configure_stage)."""
        self._unit = unit or self._unit
        for lbl in (getattr(self, '_unit_lbl1', None),
                    getattr(self, '_unit_lbl2', None)):
            if lbl is not None:
                lbl.setText(self._unit)

    def unit(self) -> str:
        return self._unit

    def _nudge(self, delta):
        if not self._known:
            return
        self._value += delta; self._refresh_display(); self.move_requested.emit(self._value)
    def _on_enter(self):
        if not self._known:
            return
        try:
            self._value = float(self._edit.text().replace(",", ".").strip())
            self._refresh_display(); self.move_requested.emit(self._value)
        except ValueError: pass
    def _refresh_display(self):
        if not self._known:
            self._sign_lbl.setText("?")
            for lbl in self._digit_labels: lbl.setText("-")
            if not self._edit.hasFocus():
                self._edit.setText("—")
            return
        self._sign_lbl.setText("−" if self._value < 0 else "＋")
        fmt = f"{{:0{self._n_int + self._n_dec + 1}.{self._n_dec}f}}"
        digits = fmt.format(abs(self._value)).replace(".", "")
        digits = digits[:self._n_digits].ljust(self._n_digits, "0")
        for i, lbl in enumerate(self._digit_labels): lbl.setText(digits[i])
        if not self._edit.hasFocus():
            self._edit.setText(f"{self._value:.{self._n_dec}f}")

    # ── Known / unknown position ─────────────────────────────────────────────
    # The jog controls send an absolute target.  A box still showing the value
    # from before a failed read — or from before a re-zero, which redefines
    # the whole coordinate frame — would move the stage by that entire stale
    # offset on the next arrow click.  Blanking the display is not enough:
    # the widget refuses to emit anything until a real position is read back.
    def is_known(self) -> bool:
        return self._known

    def set_unknown(self, reason: str = ""):
        self._known = False
        self._readback = None
        self._rb_label.setText("—")
        self._apply_known(reason)

    def _apply_known(self, reason: str = ""):
        for b in self._up_btns + self._down_btns:
            b.setEnabled(self._known)
        self._edit.setEnabled(self._known)
        self._edit.setStyleSheet(
            self._EDIT_STYLE if self._known else self._EDIT_STYLE_UNKNOWN)
        if self._known:
            self.setToolTip("")
        else:
            tip = (f"{self._label} position unknown — jogging is disabled.\n"
                   "Press 🔄 Read all to re-read the stage.")
            if reason:
                tip += f"\n({reason})"
            self.setToolTip(tip)
        self._refresh_display()

    def set_value(self, v):
        self._value = float(v); self._known = True; self._apply_known()
    def update_readback(self, v):
        self._readback = v
        self._rb_label.setText(f"{v:.3f} {self._unit}" if v is not None else "—")
    def get_value(self): return self._value


# ─────────────────────────────────────────────────────────────────────────────
# FocusPlotWidget — 1D plot showing FL signal vs Z position
# ─────────────────────────────────────────────────────────────────────────────
class FocusPlotWidget(QWidget):
    """1D matplotlib plot for autofocus: shows fluorescence vs Z."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=(5, 4), dpi=90, facecolor="#1e1e2e")
        self.ax  = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        self.bar = NavToolbar(self.canvas, None)
        self.bar.setStyleSheet("background:#1e1e2e;color:white;")
        fix_toolbar_icons(self.bar)
        self._font_pt = 9

        # Toolbar row: nav toolbar + text-size spinbox (matches Live1DWidget)
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(6)
        top.addWidget(self.bar, stretch=1)
        top.addWidget(make_light_export_btn(lambda: self.fig, self))
        # Y-scale mode: Full (all data) or Recent (±max|y| of the last N pts)
        self._recent_window = RECENT_WINDOW
        self._scale_w, self._scale_mode = make_scale_pills(
            self._on_scale_mode, self)
        top.addWidget(self._scale_w)
        _tx = QLabel("Text:"); _tx.setStyleSheet("color:#a6adc8;font-size:10px;")
        top.addWidget(_tx)
        self.fs_spin = make_fontsize_spin(self._font_pt, self._on_fontsize)
        top.addWidget(self.fs_spin)

        # Left-click a curve to read off the nearest point's value.
        self._readout = ClickReadout(
            self.canvas,
            lambda: [a for a in (self.ax, self._ts_ax2) if a is not None],
            lambda: self._font_pt)

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.canvas, stretch=1)
        self._z_data = []; self._fl_data = []
        self._line = None; self._best_dot = None
        self._ts_xd = None; self._ts_yd = {}; self._ts_lines = {}
        self._ts_ax2 = None      # right (Y2) axis, created per time scan
        self._ts_dirty = False
        self._style()

    def _style(self):
        self.ax.set_facecolor("#12121f")
        self.ax.tick_params(colors="#aaaacc", labelsize=self._font_pt)
        for sp in self.ax.spines.values(): sp.set_edgecolor("#3a3a5c")
        self.ax.set_xlabel("Z position (µm)", color="#aaaacc", fontsize=self._font_pt)
        self.ax.set_ylabel("Focus signal (V)", color="#aaaacc", fontsize=self._font_pt)
        self.ax.set_title("Autofocus", color="#6c7086", fontsize=self._font_pt)
        # SI engineering ticks (24µ, 1.3m) instead of a 1e-5 offset at the top
        eng_axis(self.ax.yaxis)

    def set_recent_window(self, n: int):
        """Trailing-point count used by the Recent y-scale mode.

        Set from Setup Defaults (`recent_window`); the plot redraws with the
        new window on the next update.
        """
        try:
            self._recent_window = max(2, int(n))
        except (TypeError, ValueError):
            return
        self._dirty = True
        self._ts_dirty = True        # time-scan mode redraws on its next tick

    def _on_scale_mode(self):
        """Y-scale pill changed — re-apply the limits immediately."""
        self._ts_dirty = True        # time scan picks it up on the next tick
        self._rescale_focus_y()      # autofocus curve rescales right away
        self.canvas.draw_idle()

    def _rescale_focus_y(self):
        """Apply the current y-scale mode to the autofocus curve.

        X always follows the full data range; only the y rule changes.
        """
        if self._line is None:
            return
        self.ax.relim(); self.ax.autoscale_view()
        if self._scale_mode() == SCALE_RECENT:
            lim = recent_symmetric_ylim([self._fl_data],
                                        window=self._recent_window)
            if lim is not None:
                self.ax.set_ylim(*lim)

    def _on_fontsize(self, pt: int):
        """User picked a new on-plot text size — restyle and redraw live."""
        self._font_pt = int(pt)
        axes = [a for a in (self.ax, self._ts_ax2) if a is not None]
        for ax in axes:
            ax.tick_params(labelsize=self._font_pt)
            ax.xaxis.label.set_fontsize(self._font_pt)
            ax.yaxis.label.set_fontsize(self._font_pt)
            ax.title.set_fontsize(self._font_pt)
            leg = ax.get_legend()
            if leg is not None:
                for t in leg.get_texts():
                    t.set_fontsize(self._font_pt)
        try: self.fig.tight_layout()
        except Exception: pass
        self.canvas.draw_idle()

    def _drop_ts_ax2(self):
        """Remove the right (Y2) time-scan axis if it exists."""
        if self._ts_ax2 is not None:
            try: self._ts_ax2.remove()
            except Exception: pass
            self._ts_ax2 = None

    def clear(self):
        self._z_data = []; self._fl_data = []
        self._drop_ts_ax2()
        self.ax.cla(); self._style()
        self._line = None; self._best_dot = None
        # Also clear time scan state
        self._ts_xd = None; self._ts_yd = {}; self._ts_lines = {}
        self._ts_dirty = False
        if getattr(self, "_readout", None) is not None:
            self._readout.note_axes_cleared()
        self.canvas.draw_idle()

    def add_point(self, z: float, fl: float):
        self._z_data.append(z); self._fl_data.append(fl)
        if self._line is None:
            self._line, = self.ax.plot(self._z_data, self._fl_data,
                                        color="#89b4fa", linewidth=1.5,
                                        marker=".", markersize=5)
        else:
            self._line.set_data(self._z_data, self._fl_data)
        self._rescale_focus_y()
        self.canvas.draw_idle()

    def mark_best(self, z: float, fl: float):
        """Mark the best focus position with a green dot."""
        if self._best_dot is not None:
            try: self._best_dot.remove()
            except Exception: pass
        self._best_dot = self.ax.scatter([z], [fl], s=100, c="#a6e3a1",
                                          marker="*", zorder=10)
        self.canvas.draw_idle()

    # ── Time scan mode ────────────────────────────────────────────────────────
    # Same validated palettes as the main 1D plot (see core/theme.py)
    _TS_LEFT_COLORS  = PLOT_LEFT_COLORS
    _TS_RIGHT_COLORS = PLOT_RIGHT_COLORS

    def setup_timescan(self, n_pts: int, sensors: list):
        """Prepare the plot for a time scan: point index on X, sensor values on Y.

        Sensors keep their Y1/Y2 assignment from the sensor panel: Y1 curves
        go on the left axis, Y2 curves on a right twin axis with its own
        scale — so a large signal (focus diode) and a small one (balanced
        diode) are both visible instead of the small one flattening out.
        """
        self._drop_ts_ax2()
        self.ax.cla()
        self.ax.set_facecolor("#12121f")
        self.ax.tick_params(colors="#aaaacc", labelsize=self._font_pt)
        for sp in self.ax.spines.values(): sp.set_edgecolor("#3a3a5c")
        self.ax.set_xlabel("Point", color="#aaaacc", fontsize=self._font_pt)
        self.ax.set_title("Time scan", color="#6c7086", fontsize=self._font_pt)
        eng_axis(self.ax.yaxis)
        self._line = None; self._best_dot = None
        if getattr(self, "_readout", None) is not None:
            self._readout.note_axes_cleared()
        self._ts_xd = np.full(n_pts, np.nan)
        self._ts_yd = {}; self._ts_lines = {}

        y2_sensors = [s for s in sensors
                      if s.get("y_axis", s.get("plot_axis", "Y1")) == "Y2"]
        if y2_sensors:
            self._ts_ax2 = self.ax.twinx()
            self._ts_ax2.tick_params(colors="#aaaacc", labelsize=self._font_pt)
            for sp in self._ts_ax2.spines.values(): sp.set_edgecolor("#3a3a5c")
            self._ts_ax2.yaxis.set_label_position("right")
            self._ts_ax2.yaxis.tick_right()
            eng_axis(self._ts_ax2.yaxis)

        li = ri = 0
        left_meta, right_meta = [], []   # (label, unit, color)
        for s in sensors:
            lbl  = s["label"]; unit = s.get("unit", "")
            if s in y2_sensors:
                c = self._TS_RIGHT_COLORS[ri % len(self._TS_RIGHT_COLORS)]; ri += 1
                ax = self._ts_ax2
                right_meta.append((lbl, unit, c))
            else:
                c = self._TS_LEFT_COLORS[li % len(self._TS_LEFT_COLORS)]; li += 1
                ax = self.ax
                left_meta.append((lbl, unit, c))
            line, = ax.plot([], [], color=c, linewidth=1.5,
                            marker=".", markersize=4, label=lbl)
            self._ts_lines[lbl] = line
            self._ts_yd[lbl] = np.full(n_pts, np.nan)

        # Each sensor's name in the axis title takes its curve's color
        set_multicolor_ylabel(self.ax, left_meta, "#89b4fa", self._font_pt)
        if self._ts_ax2 is not None:
            set_multicolor_ylabel(self._ts_ax2, right_meta, "#f38ba8",
                                  self._font_pt)

        # Combined legend (both axes) on the topmost axes
        handles = list(self._ts_lines.values())
        if handles:
            leg_ax = self._ts_ax2 if self._ts_ax2 is not None else self.ax
            leg_ax.legend(handles=handles, fontsize=self._font_pt,
                          facecolor="#313244", edgecolor="#45475a",
                          labelcolor="#cdd6f4", loc="best")
        self.ax.axhline(0, color="#45475a", linewidth=0.6, linestyle="--")
        self.fig.tight_layout(); self.canvas.draw_idle()
        self._ts_dirty = False
        # Start throttled timer if not running
        if not hasattr(self, '_ts_timer'):
            self._ts_timer = QTimer(self)
            self._ts_timer.setInterval(80)
            self._ts_timer.timeout.connect(self._ts_throttled_draw)
            self._ts_timer.start()

    def update_timescan_point(self, ix: int, x_val: float, vals: dict):
        """Update one point in the time scan plot."""
        if self._ts_xd is None: return
        self._ts_xd[ix] = x_val
        for lbl, v in vals.items():
            if lbl in self._ts_yd:
                self._ts_yd[lbl][ix] = v
        self._ts_dirty = True

    def _ts_throttled_draw(self):
        if not self._ts_dirty: return
        self._ts_dirty = False
        x = self._ts_xd
        if x is None: return
        for lbl, line in self._ts_lines.items():
            y = self._ts_yd.get(lbl)
            if y is None: continue
            m = np.isfinite(x) & np.isfinite(y)
            if m.any(): line.set_data(x[m], y[m])
        # Manual limits — X shared across both axes, Y independent per axis
        # (relim() is unreliable with twinx, and the axhline(0) reference
        # line must not enter the limit computation)
        all_lines = [l for l in self._ts_lines.values()
                     if len(l.get_xdata()) > 0]
        if all_lines:
            ax_x = np.concatenate([l.get_xdata() for l in all_lines])
            mx = np.isfinite(ax_x)
            if mx.any():
                xlo, xhi = ax_x[mx].min(), ax_x[mx].max()
                pad = max(abs(xhi - xlo) * 0.02, 1e-12)
                self.ax.set_xlim(xlo - pad, xhi + pad)
            recent = self._scale_mode() == SCALE_RECENT
            for axis in (self.ax, self._ts_ax2):
                if axis is None: continue
                ys = [l.get_ydata() for l in all_lines if l.axes is axis]
                if not ys: continue
                if recent:
                    lim = recent_symmetric_ylim(ys, window=self._recent_window)
                    if lim is not None:
                        axis.set_ylim(*lim)
                    continue
                ay = np.concatenate(ys)
                my = np.isfinite(ay)
                if my.any():
                    ylo, yhi = ay[my].min(), ay[my].max()
                    pad = max(abs(yhi - ylo) * 0.05, 1e-12)
                    axis.set_ylim(ylo - pad, yhi + pad)
        self.canvas.draw_idle()


# ─────────────────────────────────────────────────────────────────────────────
# AutofocusWorker — runs autofocus routine in a background thread
# ─────────────────────────────────────────────────────────────────────────────
class AutofocusWorker(QThread):
    point_measured = pyqtSignal(float, float)   # z_pos, fl_value
    status_msg     = pyqtSignal(str)
    focus_found    = pyqtSignal(float, float)   # best_z, best_fl
    error_msg      = pyqtSignal(str)
    finished_      = pyqtSignal()

    def __init__(self, positioner_dev: str, fl_dev: str,
                 focus_attr: str, scan_attr: str,
                 focus_pos: float, dz: float, d_zmax: float, maxtries: int,
                 fl_attr: str = "Value", z_limits=None, z_unit: str = "µm",
                 scan_attr2: str = "", focus_pos2: float = 0.0):
        super().__init__()
        self._pos_dev    = positioner_dev
        self._fl_dev     = fl_dev
        # NOTE: `focus_attr` is the Z-AXIS attribute (historic name).  The FL
        # sensor's attribute is `fl_attr` — the setup key also called
        # "focus_attr", which is why the two are easy to confuse.
        self._focus_attr = focus_attr
        self._scan_attr  = scan_attr
        self._focus_pos  = focus_pos
        # Optional second in-plane axis, parked at focus_pos2 for the duration
        # and restored afterwards.  A 2D map ends at the last raster point, so
        # focusing with only X re-centred would measure at the edge of the map
        # in Y.  Empty = single-axis behaviour (what the ▶ button uses).
        self._scan_attr2 = (scan_attr2 or "").strip()
        self._focus_pos2 = focus_pos2
        self._dz         = dz
        self._d_zmax     = d_zmax
        self._maxtries   = maxtries
        self._fl_attr    = (fl_attr or "Value").strip() or "Value"
        self._z_limits   = z_limits
        self._z_unit     = z_unit or "µm"
        self._fl_ok      = 0
        self._fl_fail    = 0
        self._abort      = False

    def abort(self): self._abort = True

    def run(self):
        try:
            self._run_autofocus()
        except Exception:
            self.error_msg.emit(traceback.format_exc())
        finally:
            self.finished_.emit()

    def _run_autofocus(self):
        p, err = fresh_proxy(self._pos_dev)
        if err:
            self.error_msg.emit(f"Positioner: {err}"); return
        fl_p, err = fresh_proxy(self._fl_dev)
        if err:
            self.error_msg.emit(f"FL sensor: {err}"); return

        # Read current positions
        pos0_z, e = safe_read(p, self._focus_attr)
        if e or pos0_z is None:
            self.error_msg.emit(f"Cannot read Z: {e}"); return
        # A failed read must NOT fall back to 0.0: that made the code believe
        # the axis was already at the focus position, skip the move, and then
        # "restore" the axis to 0 after the sweep — the exact opposite of what
        # is wanted.  None means "unknown": park it anyway, and leave it there
        # rather than driving it to an invented position.
        pos_scan, e = safe_read(p, self._scan_attr)
        if e or pos_scan is None:
            pos_scan = None
            self.status_msg.emit(
                f"⚠ Cannot read {self._scan_attr} ({e}) — parking it at the "
                f"focus position anyway and leaving it there")
        pos_scan2 = None
        if self._scan_attr2:
            pos_scan2, e2 = safe_read(p, self._scan_attr2)
            if e2: pos_scan2 = None

        # Sweep bounds must stay inside the configured travel, when the setup
        # defines any.  (Replaces a hardcoded `abs(z) > 100` test that assumed
        # a µm axis whose origin sits at focus, and so refused to run on any
        # stage with a different coordinate origin.)
        if self._z_limits is not None:
            lo, hi = self._z_limits
            s_lo, s_hi = pos0_z - self._d_zmax, pos0_z + self._d_zmax
            if s_lo < lo or s_hi > hi:
                self.error_msg.emit(
                    f"Sweep {s_lo:.3f} … {s_hi:.3f} {self._z_unit} leaves the "
                    f"configured Z travel [{lo:g}, {hi:g}] {self._z_unit} — "
                    "reduce Max range or move closer to focus first")
                return

        _scan_now = "?" if pos_scan is None else f"{pos_scan:.3f}"
        self.status_msg.emit(f"Focusing… Z₀={pos0_z:.3f}  scan={_scan_now}")

        # Park the scan axis (and the optional second in-plane axis) at the
        # focus position, so the sweep measures the middle of the device.
        moved = (self._park(p, self._scan_attr, self._focus_pos, pos_scan) |
                 self._park(p, self._scan_attr2, self._focus_pos2, pos_scan2))
        if moved:
            time.sleep(1)

        # Sweep-based autofocus: coarse sweep over the full ±range, fine
        # sweep around the coarse peak, parabolic refinement, then move to
        # the best Z.  (Replaces the old hill-climb, which always started
        # downward and — when the intensity change per step stayed below its
        # noise threshold — never reversed direction, so it just crawled
        # down and left the stage at the last position instead of the best.)
        try:
            best = self._sweep_focus(p, fl_p, pos0_z)
        finally:
            # Always restore the scan axes, even on error/abort
            if pos_scan is not None:
                safe_write(p, self._scan_attr, pos_scan)
            if self._scan_attr2 and pos_scan2 is not None:
                safe_write(p, self._scan_attr2, pos_scan2)
            if pos_scan is not None or pos_scan2 is not None:
                time.sleep(0.5)

        if best is None:
            # Aborted or no valid FL data — return Z to where we started
            safe_write(p, self._focus_attr, float(pos0_z))
            self.status_msg.emit("Autofocus stopped — returned to Z₀ "
                                 f"({pos0_z:.3f} µm)")
            return
        best_z, best_fl = best

        # Move to the found focus (the old code never did this final move)
        safe_write(p, self._focus_attr, float(best_z))
        time.sleep(self._MOVE_SETTLE_S)
        fl_conf, e = self._measure_fl(fl_p)
        if e is None and fl_conf is not None:
            self.point_measured.emit(best_z, fl_conf)
            best_fl = fl_conf

        self.focus_found.emit(best_z, best_fl)
        q = getattr(self, "_quality", None) or {}
        base = (f"Focus found at Z = {best_z:.3f} {self._z_unit}  "
                f"(FL = {best_fl:.4g}) — stage moved there")
        if q:
            snr = q.get("snr", float("inf"))
            snr_s = "∞" if snr == float("inf") else f"{snr:.1f}"
            base += f"  ·  peak/noise = {snr_s}"
        if self._fl_fail:
            base += f"  ·  {self._fl_fail} failed FL read(s)"
        if q and not q.get("ok", True):
            # Same wording either way would hide the difference between a real
            # peak and an endpoint or a noise excursion.
            self.status_msg.emit(f"⚠ {base}  —  UNRELIABLE: {q.get('msg', '')}")
        else:
            self.status_msg.emit(base)

    _POS_TOL = 0.01        # "already there" tolerance, in the axis' own unit

    def _park(self, p, attr: str, target: float, current) -> bool:
        """Move one in-plane axis to `target`.  True if a move was sent.

        `current` is None when the position could not be read — park anyway
        rather than assuming the axis is already in place.  A failed write is
        reported instead of being swallowed: silently skipping the move is how
        a refocus ends up measuring the edge of the device instead of its
        middle, with nothing in the log to say so.
        """
        if not attr:
            return False
        if current is not None and abs(target - current) <= self._POS_TOL:
            return False
        werr = safe_write(p, attr, float(target))
        if werr:
            self.status_msg.emit(
                f"⚠ Could not move {attr} → {target:.3f}: {werr}")
            return False
        self.status_msg.emit(f"{attr} → {target:.3f} for focus")
        return True

    _MOVE_SETTLE_S = 0.5   # settle after each Z step
    _FL_TIMEOUT_S  = 2.0   # max wait for the FL device to finish averaging

    def _measure_fl(self, fl_p):
        """Trigger one FL acquisition and read the averaged Value.

        The handshake itself lives in hardware.trigger_and_read (shared with
        the thermal-settle monitor); this wrapper only keeps the ok/fail
        counters that the reliability report quotes.  Returns (value, err).
        """
        val, err = trigger_and_read(fl_p, self._fl_attr,
                                    wait_s=self._FL_TIMEOUT_S)
        if err or val is None:
            self._fl_fail += 1
        else:
            self._fl_ok += 1
        return val, err

    def _sweep_z(self, p, fl_p, z_values):
        """Move through z_values in order, measuring FL at each.

        Emits point_measured per point; skips failed reads.  Returns a list
        of (z, fl) or None if aborted.
        """
        out = []
        for z in z_values:
            if self._abort:
                return None
            safe_write(p, self._focus_attr, float(z))
            time.sleep(self._MOVE_SETTLE_S)
            fl, e = self._measure_fl(fl_p)
            if e or fl is None:
                continue
            self.point_measured.emit(float(z), float(fl))
            out.append((float(z), float(fl)))
        return out

    def _assess(self, pts, best_z, best_fl) -> dict:
        """Judge whether the coarse sweep actually found a focus peak.

        Returns {'ok', 'prominence', 'noise', 'snr', 'at_edge', 'msg'}.

        Without this the caller cannot distinguish three very different
        outcomes that all end in "Focus found at Z = …": a real peak; a
        monotonic curve, meaning the range never bracketed focus and the
        answer is just the endpoint; and a flat noisy curve, where the answer
        is the largest noise excursion.
        """
        z = np.array([t[0] for t in pts], dtype=float)
        y = np.array([t[1] for t in pts], dtype=float)
        out = {"ok": True, "prominence": 0.0, "noise": 0.0, "snr": float("inf"),
               "at_edge": False, "msg": ""}
        if y.size < 3:
            out["msg"] = f"only {y.size} valid point(s)"
            out["ok"] = False
            return out
        # Noise proxy: median |first difference| is robust to the peak itself.
        noise = float(np.median(np.abs(np.diff(y)))) or 0.0
        prominence = float(best_fl - np.median(y))
        out["noise"] = noise
        out["prominence"] = prominence
        out["snr"] = prominence / noise if noise > 0 else float("inf")
        # Peak on the first or last swept point → the range did not bracket it
        i_best = int(np.argmin(np.abs(z - best_z)))
        out["at_edge"] = i_best in (0, y.size - 1)
        problems = []
        if out["at_edge"]:
            problems.append("peak is at the edge of the sweep — "
                            "focus is probably outside Max range")
        if noise > 0 and out["snr"] < 3.0:
            problems.append(f"peak is only {out['snr']:.1f}× the noise")
        if problems:
            out["ok"] = False
            out["msg"] = "; ".join(problems)
        return out

    def _sweep_focus(self, p, fl_p, z0):
        """Coarse sweep ± d_zmax → fine sweep around the peak → parabola.

        Returns (best_z, best_fl) or None on abort / no valid data.
        The coarse sweep is capped at `maxtries` points — when the full
        range needs more, the step widens and the fine sweep recovers the
        resolution around the peak.
        """
        lo, hi = z0 - self._d_zmax, z0 + self._d_zmax

        n = int(round(2 * self._d_zmax / max(self._dz, 1e-9))) + 1
        n = max(5, min(n, max(5, self._maxtries)))
        coarse = np.linspace(lo, hi, n)
        step = coarse[1] - coarse[0]

        self.status_msg.emit(f"Coarse sweep: {n} pts, "
                             f"{lo:.3f} → {hi:.3f} µm (step {step:.3f})")
        pts = self._sweep_z(p, fl_p, coarse)
        if pts is None:
            return None
        if not pts:
            self.error_msg.emit(
                f"No valid FL readings during coarse sweep — "
                f"0/{self._fl_ok + self._fl_fail} reads of "
                f"'{self._fl_attr}' on {self._fl_dev} succeeded. "
                "Check the FL sensor device and attribute in Setup Defaults.")
            return None
        best_z, best_fl = max(pts, key=lambda t: t[1])
        self._quality = self._assess(pts, best_z, best_fl)

        # Fine sweep: ± one coarse step around the peak (clamped to range)
        flo = max(lo, best_z - step)
        fhi = min(hi, best_z + step)
        fine = np.linspace(flo, fhi, 9)
        self.status_msg.emit(f"Fine sweep around {best_z:.3f} µm "
                             f"({flo:.3f} → {fhi:.3f})")
        fpts = self._sweep_z(p, fl_p, fine)
        if fpts is None:
            return None
        if fpts:
            fz  = np.array([t[0] for t in fpts])
            ffl = np.array([t[1] for t in fpts])
            i = int(np.argmax(ffl))
            if ffl[i] > best_fl:
                best_z, best_fl = float(fz[i]), float(ffl[i])
            # Parabolic vertex through the max and its neighbours for
            # sub-step accuracy (only when curvature is a real maximum)
            if 0 < i < len(fz) - 1:
                x1, x2, x3 = fz[i - 1], fz[i], fz[i + 1]
                y1, y2, y3 = ffl[i - 1], ffl[i], ffl[i + 1]
                denom = (x1 - x2) * (x1 - x3) * (x2 - x3)
                if abs(denom) > 1e-12:
                    a = (x3 * (y2 - y1) + x2 * (y1 - y3)
                         + x1 * (y3 - y2)) / denom
                    b = (x3 * x3 * (y1 - y2) + x2 * x2 * (y3 - y1)
                         + x1 * x1 * (y2 - y3)) / denom
                    if a < 0:
                        zv = -b / (2 * a)
                        if x1 <= zv <= x3:
                            best_z = float(zv)
        return best_z, best_fl


# ─────────────────────────────────────────────────────────────────────────────
# CalibrationPanel — 1D focus plot + digit jog + autofocus
# ─────────────────────────────────────────────────────────────────────────────
class CalibrationPanel(QWidget):
    """Calibration tab: 1D focus plot, digit-jog stage controls, autofocus."""

    # Cross-thread GUI marshal: background reader threads emit a callable,
    # the queued connection runs it on the GUI thread.  (More reliable than
    # QTimer.singleShot(0, …) from a plain Python thread, whose delivery is
    # Qt/PyQt-version dependent.)
    _gui_apply = pyqtSignal(object)
    # Emitted when the tab's own time-scan settings are edited (persist them)
    timescan_changed = pyqtSignal()

    def __init__(self, setup_getter, config_getter=None, parent=None,
                 sensor_row_factory=None):
        """sensor_row_factory: callable(device_name=, channel_attr=, axis=,
        enabled=) returning the app's SensorPickerRow — gives the calibration
        tab its own sensor selection, independent of the scan config."""
        super().__init__(parent)
        self._setup_getter  = setup_getter
        self._config_getter = config_getter
        self._af_worker = None
        self._af_result: Optional[dict] = None   # last focus_found payload
        self._af_on_done = None                  # run_autofocus_async callback
        self._af_last_msg = ""
        self._stage_cfg: dict = {}   # populated by configure_stage()
        self._sensor_row_factory = sensor_row_factory
        self._ts_sensor_rows: list = []
        self._ts_loading = False
        self._gui_apply.connect(lambda fn: fn())

        root = QHBoxLayout(self); root.setContentsMargins(4, 4, 4, 4); root.setSpacing(6)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: 1D focus plot ───────────────────────────────────────────────
        self.focus_plot = FocusPlotWidget()
        self.focus_plot.setMinimumSize(320, 300)
        splitter.addWidget(self.focus_plot)

        # ── Right: jog controls + autofocus (side by side) ─────────────────
        right = QWidget(); rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)

        # ── Column 1: Stage positioning ───────────────────────────────────
        ctrl_grp = QGroupBox("Stage positioning")
        ctrl_l = QVBoxLayout(ctrl_grp); ctrl_l.setSpacing(4)
        ctrl_l.setContentsMargins(8, 8, 8, 8)

        # The per-axis device/attribute line used to sit here.  It cost a row
        # of height on a tab that is short of it, and the same information is
        # on the Setup Defaults tab; it survives as the group's tooltip.
        self._stage_grp = ctrl_grp
        ctrl_grp.setToolTip("Axis devices are set on the Setup Defaults tab")


        self.jog_x = DigitJogWidget("X", "µm", n_int=2, n_dec=3)
        self.jog_y = DigitJogWidget("Y", "µm", n_int=2, n_dec=3)
        self.jog_z = DigitJogWidget("Z", "µm", n_int=2, n_dec=3)
        for jog in [self.jog_x, self.jog_y, self.jog_z]:
            ctrl_l.addWidget(jog)

        self.jog_x.move_requested.connect(lambda v: self._move_axis("x", v))
        self.jog_y.move_requested.connect(lambda v: self._move_axis("y", v))
        self.jog_z.move_requested.connect(lambda v: self._move_axis("z", v))

        btn_row = QHBoxLayout(); btn_row.setSpacing(6)
        read_btn = QPushButton("🔄 Read all")
        # Via a lambda, not directly: clicked(bool) would land in _read_all's
        # `note` parameter.
        read_btn.clicked.connect(lambda: self._read_all())
        btn_row.addWidget(read_btn)
        self.reinit_btn = QPushButton("⟲ Reinitialise")
        self.reinit_btn.setToolTip(
            "Re-initialise the stage motors — the fix for a wedged IR SmarAct "
            "axis after manual use with the hand controller.\n"
            "Sends the stage device's Initialise command (falls back to Init).")
        self.reinit_btn.clicked.connect(self._reinit_stage)
        btn_row.addWidget(self.reinit_btn)
        # ⊘ Zero here — MCS2 only: shown when the stage device exposes both
        # the SetZero and Initialise commands (the SmarActMCS2Stage
        # signature), so the Green setup's old Smaract server and the Cryo
        # Attocube stages never see it.  Defines the CURRENT position as 0
        # without any movement (SmarAct SetOffset), unlike a referencing Home.
        self.home_btn = QPushButton("⊘ Zero here")
        self.home_btn.setToolTip(
            "Define the current stage position as 0 on all three axes.\n"
            "No movement — this just re-labels where you are as the origin\n"
            "(SmarAct SetOffset). Redefines the coordinate frame, so saved\n"
            "positions shift accordingly.")
        self.home_btn.clicked.connect(self._home_stage)
        self.home_btn.setVisible(False)
        btn_row.addWidget(self.home_btn)
        btn_row.addStretch()
        ctrl_l.addLayout(btn_row)

        # LED lights (green = LED1, IR = LED2) — only shown when a Lights device
        # is configured for the setup (set via set_lights_device()).  The On/Off
        # buttons act as a toggle pair: the active state is highlighted (On →
        # green, Off → red), the inactive one stays grey, so you can see at a
        # glance whether each LED is on.
        self._lights_dev = ""
        self._led_state = {1: None, 2: None}   # None = unknown, True = on, False = off
        self._led_btns  = {}                    # led → (on_btn, off_btn)
        self.led_grp = QGroupBox("LEDs")
        led_l = QHBoxLayout(self.led_grp); led_l.setSpacing(4)
        led_l.setContentsMargins(8, 6, 8, 6)
        for led in (1, 2):
            led_l.addWidget(QLabel(f"{led}:"))
            on_btn  = QPushButton("On")
            off_btn = QPushButton("Off")
            on_btn.clicked.connect(lambda _=False, n=led:  self._led(n, True))
            off_btn.clicked.connect(lambda _=False, n=led: self._led(n, False))
            led_l.addWidget(on_btn); led_l.addWidget(off_btn)
            if led == 1:
                led_l.addSpacing(10)
            self._led_btns[led] = (on_btn, off_btn)
            self._style_led(led)
        self.led_grp.setVisible(False)
        ctrl_l.addWidget(self.led_grp)

        self._pos_status = QLabel("")
        self._pos_status.setWordWrap(True); self._pos_status.setStyleSheet("font-size:10px;")
        ctrl_l.addWidget(self._pos_status)
        self._right_layout = rl   # exposed so subclasses can append extra columns
        rl.addWidget(ctrl_grp)

        # Autofocus
        af_grp = QGroupBox("Autofocus")
        af_l = QGridLayout(af_grp); af_l.setSpacing(4)
        af_l.setContentsMargins(8, 8, 8, 8)

        af_l.addWidget(QLabel("FL sensor:"), 0, 0)
        self.fl_dev_lbl = QLineEdit()
        self.fl_dev_lbl.setReadOnly(True)
        self.fl_dev_lbl.setPlaceholderText("— set in Setup Defaults —")
        self.fl_dev_lbl.setStyleSheet(
            "background:#1e1e2e;color:#6c7086;border:1px solid #313244;"
            "border-radius:4px;padding:2px 4px;font-size:10px;")
        af_l.addWidget(self.fl_dev_lbl, 0, 1, 1, 2)

        af_l.addWidget(QLabel("Focus pos:"), 1, 0)
        self.focus_pos_spin = QDoubleSpinBox()
        self.focus_pos_spin.setRange(-1e6, 1e6); self.focus_pos_spin.setDecimals(3)
        self.focus_pos_spin.setValue(0.0); self.focus_pos_spin.setSuffix(" µm")
        af_l.addWidget(self.focus_pos_spin, 1, 1, 1, 2)

        af_l.addWidget(QLabel("Step (dz):"), 2, 0)
        self.dz_spin = QDoubleSpinBox()
        self.dz_spin.setRange(0.001, 10); self.dz_spin.setDecimals(3)
        self.dz_spin.setValue(0.1); self.dz_spin.setSuffix(" µm")
        af_l.addWidget(self.dz_spin, 2, 1)

        af_l.addWidget(QLabel("Max range:"), 3, 0)
        self.dzmax_spin = QDoubleSpinBox()
        self.dzmax_spin.setRange(0.1, 50); self.dzmax_spin.setDecimals(1)
        self.dzmax_spin.setValue(2.0); self.dzmax_spin.setSuffix(" µm")
        af_l.addWidget(self.dzmax_spin, 3, 1)

        af_l.addWidget(QLabel("Max points:"), 4, 0)
        self.tries_spin = QSpinBox()
        self.tries_spin.setRange(5, 200); self.tries_spin.setValue(20)
        self.tries_spin.setToolTip(
            "Point budget for the coarse sweep over ±max range.\n"
            "If the range needs more points than this at the given step,\n"
            "the coarse step widens — the fine sweep around the peak\n"
            "recovers the resolution.")
        af_l.addWidget(self.tries_spin, 4, 1)

        af_btn_row = QHBoxLayout()
        self.af_start_btn = QPushButton("▶  Autofocus")
        self.af_start_btn.setObjectName("start_btn"); self.af_start_btn.setFixedHeight(30)
        self.af_start_btn.clicked.connect(self._start_autofocus)
        self.af_stop_btn = QPushButton("■  Stop")
        self.af_stop_btn.setObjectName("abort_btn"); self.af_stop_btn.setFixedHeight(30)
        self.af_stop_btn.setEnabled(False)
        self.af_stop_btn.clicked.connect(self._stop_autofocus)
        af_btn_row.addWidget(self.af_start_btn); af_btn_row.addWidget(self.af_stop_btn)
        af_l.addLayout(af_btn_row, 5, 0, 1, 3)

        self._af_status = QLabel("")
        self._af_status.setWordWrap(True); self._af_status.setStyleSheet("font-size:10px;")
        af_l.addWidget(self._af_status, 6, 0, 1, 3)

        # ── Column 2: Autofocus on top, Time scan settings underneath ───────
        # (one vertical column, so together they take the height of the
        # Stage-positioning column instead of adding a third column)
        col2 = QWidget()
        c2 = QVBoxLayout(col2); c2.setContentsMargins(0, 0, 0, 0); c2.setSpacing(6)
        c2.addWidget(af_grp)

        # ── Time scan settings (the calibration tab's own hidden config) ─────
        # Used by the ▶ Start time scan instead of the scan config selected in
        # the left panel; persisted per setup, never shown in the config list.
        ts_grp = QGroupBox("Time scan (this tab's own config)")
        ts_v = QVBoxLayout(ts_grp); ts_v.setSpacing(4)
        ts_v.setContentsMargins(8, 8, 8, 8)
        ts_l = QGridLayout(); ts_l.setSpacing(4)
        ts_l.addWidget(QLabel("Points:"), 0, 0)
        self.ts_npts_spin = QSpinBox()
        self.ts_npts_spin.setRange(2, 1_000_000); self.ts_npts_spin.setValue(300)
        ts_l.addWidget(self.ts_npts_spin, 0, 1)
        ts_l.addWidget(QLabel("Int time:"), 0, 2)
        self.ts_int_spin = QDoubleSpinBox()
        self.ts_int_spin.setRange(0.001, 30.0); self.ts_int_spin.setDecimals(3)
        self.ts_int_spin.setValue(0.1); self.ts_int_spin.setSuffix(" s")
        ts_l.addWidget(self.ts_int_spin, 0, 3)
        ts_v.addLayout(ts_l)
        ts_grp.setToolTip(
            "The calibration tab's own scan config (points, integration time,\n"
            "sensors) — used by ▶ Start while this tab is open. Independent of\n"
            "the scan config selected on the left; saved per setup, never\n"
            "shown in the config list.")
        self.ts_npts_spin.valueChanged.connect(lambda _: self._ts_emit_changed())
        self.ts_int_spin.valueChanged.connect(lambda _: self._ts_emit_changed())

        # Its own sensors (device/channel/axis picker rows from the app)
        sens_hdr = QHBoxLayout(); sens_hdr.setSpacing(4)
        _sens_lbl = QLabel("Sensors:")
        _sens_lbl.setStyleSheet("color:#a6adc8;font-size:10px;")
        self.ts_add_btn = QPushButton("＋")
        self.ts_add_btn.setFixedSize(24, 20)
        self.ts_add_btn.setToolTip("Add a sensor to the calibration time scan")
        self.ts_add_btn.clicked.connect(self._ts_add_sensor_row)
        sens_hdr.addWidget(_sens_lbl); sens_hdr.addStretch(1)
        sens_hdr.addWidget(self.ts_add_btn)
        ts_v.addLayout(sens_hdr)
        self._ts_rows_lay = QVBoxLayout(); self._ts_rows_lay.setSpacing(2)
        ts_v.addLayout(self._ts_rows_lay)
        ts_v.addStretch(1)
        if self._sensor_row_factory is None:
            _sens_lbl.setVisible(False); self.ts_add_btn.setVisible(False)

        c2.addWidget(ts_grp, stretch=1)
        rl.addWidget(col2)

        splitter.addWidget(right)

        splitter.setSizes([400, 500]); splitter.setStretchFactor(0, 1)
        root.addWidget(splitter)

    # ── Time-scan settings (hidden calibration config) ────────────────────────
    _TS_MAX_SENSORS = 6

    def _ts_emit_changed(self):
        if not self._ts_loading:
            self.timescan_changed.emit()

    def _ts_make_row(self, device_name: str = "", channel_attr: str = "",
                     axis: str = "Y1", enabled: bool = True):
        row = self._sensor_row_factory(device_name=device_name,
                                       channel_attr=channel_attr,
                                       axis=axis, enabled=enabled)
        row.changed.connect(self._ts_emit_changed)
        row.delete_requested.connect(lambda r=row: self._ts_remove_row(r))
        self._ts_sensor_rows.append(row)
        self._ts_rows_lay.addWidget(row)
        return row

    def _ts_add_sensor_row(self):
        if (self._sensor_row_factory is None
                or len(self._ts_sensor_rows) >= self._TS_MAX_SENSORS):
            return
        self._ts_make_row()
        self._ts_emit_changed()

    def _ts_remove_row(self, row):
        if row in self._ts_sensor_rows:
            self._ts_sensor_rows.remove(row)
            row.setParent(None); row.deleteLater()
            self._ts_emit_changed()

    def get_timescan_sensors(self) -> list:
        """Sensor dicts (scan-engine format) from the tab's own picker rows."""
        return [r.get() for r in self._ts_sensor_rows]

    def get_timescan_settings(self) -> dict:
        return {"npts":     int(self.ts_npts_spin.value()),
                "int_time": float(self.ts_int_spin.value()),
                "sensors":  self.get_timescan_sensors()}

    def load_timescan_settings(self, d: dict):
        """Restore the per-setup time-scan settings without re-emitting."""
        for spin, key, default in ((self.ts_npts_spin, "npts", 300),
                                   (self.ts_int_spin, "int_time", 0.1)):
            spin.blockSignals(True)
            try:    spin.setValue(type(default)((d or {}).get(key, default)))
            except Exception: spin.setValue(default)
            finally: spin.blockSignals(False)
        if self._sensor_row_factory is None:
            return
        self._ts_loading = True
        try:
            for r in list(self._ts_sensor_rows):
                self._ts_sensor_rows.remove(r)
                r.setParent(None); r.deleteLater()
            for s in ((d or {}).get("sensors") or [])[:self._TS_MAX_SENSORS]:
                self._ts_make_row(
                    s.get("device_name", ""), s.get("channel_attr", ""),
                    s.get("plot_axis", s.get("y_axis", "Y1")),
                    bool(s.get("enabled", True)))
            if not self._ts_sensor_rows:
                self._ts_make_row()   # start with one row on fresh setups
        finally:
            self._ts_loading = False

    # ── Axis info from config ─────────────────────────────────────────────────
    def _get_axis_info(self) -> dict:
        if self._stage_cfg:
            return dict(self._stage_cfg)
        # Fallback: derive from scan config (used when configure_stage() was never called)
        s = self._setup_getter()
        configs = s.get("configs", [])
        idx = s.get("active_idx", 0)
        if not configs: return {}
        cfg = configs[min(idx, len(configs) - 1)]
        x_dev  = cfg.get("act1_device", "")
        x_attr = cfg.get("act1_attr", "x")
        y_dev  = cfg.get("act2_device", x_dev)
        y_attr = cfg.get("act2_attr", "y")
        z_dev  = s.get("z_device", x_dev)
        z_attr = s.get("z_attr", cfg.get("z_attr", "position0"))
        self._set_axis_tooltip(x_dev, x_attr, y_dev, y_attr, z_dev, z_attr)
        return {"x": (x_dev, x_attr), "y": (y_dev, y_attr), "z": (z_dev, z_attr)}

    def configure_stage(self, x_dev: str, x_attr: str,
                        y_dev: str, y_attr: str,
                        z_dev: str, z_attr: str,
                        x_unit: str = "", y_unit: str = "", z_unit: str = ""):
        """Inject stage device/attribute/unit for each axis from setup defaults.
        Called by the main window on every setup or defaults change.

        Units matter: this tab writes raw values straight to the device
        attribute, so every number here is in the axis' own unit.  They used to
        be labelled "µm" unconditionally, which is a lie on a stage configured
        in nm or in steps.  Units are display-only — no value is rescaled — so
        an existing, working setup is unaffected apart from correct labels.
        """
        self._stage_cfg = {
            "x": (x_dev, x_attr),
            "y": (y_dev, y_attr),
            "z": (z_dev, z_attr),
        }
        self._stage_units = {
            "x": (x_unit or "").strip() or self._DEFAULT_UNIT,
            "y": (y_unit or "").strip() or self._DEFAULT_UNIT,
            "z": (z_unit or "").strip() or self._DEFAULT_UNIT,
        }
        self._apply_axis_units()
        self._set_axis_tooltip(x_dev, x_attr, y_dev, y_attr, z_dev, z_attr)
        self._probe_home_support(x_dev)

    def _set_axis_tooltip(self, x_dev, x_attr, y_dev, y_attr, z_dev, z_attr):
        """Axis devices live in the group tooltip — the visible line was
        dropped to give the tab back a row of height."""
        grp = getattr(self, "_stage_grp", None)
        if grp is None:
            return
        grp.setToolTip(f"X: {x_dev or '—'}/{x_attr}\n"
                       f"Y: {y_dev or '—'}/{y_attr}\n"
                       f"Z: {z_dev or '—'}/{z_attr}\n"
                       "(set on the Setup Defaults tab)")

    _DEFAULT_UNIT = "µm"

    def _axis_unit(self, axis_key: str) -> str:
        return getattr(self, "_stage_units", {}).get(axis_key, self._DEFAULT_UNIT)

    def _apply_axis_units(self):
        """Push the configured units onto the jog widgets and autofocus spins."""
        for key, jog in (("x", getattr(self, "jog_x", None)),
                         ("y", getattr(self, "jog_y", None)),
                         ("z", getattr(self, "jog_z", None))):
            if jog is not None and hasattr(jog, "set_unit"):
                try:
                    jog.set_unit(self._axis_unit(key))
                except Exception:
                    pass
        zu = self._axis_unit("z")
        # focus_pos_spin is a position on the SCAN axis, not on Z.
        fp = getattr(self, "focus_pos_spin", None)
        if fp is not None:
            try:
                fp.setSuffix(f" {self._axis_unit('x')}")
            except Exception:
                pass
        for spin in (getattr(self, "dz_spin", None),
                     getattr(self, "dzmax_spin", None)):
            if spin is not None:
                try:
                    spin.setSuffix(f" {zu}")
                except Exception:
                    pass

    # Setup-key prefix carrying each axis' optional soft travel limits
    # (act1_min/act1_max, act2_min/act2_max, z_min/z_max) — the same keys
    # core/validation.py already checks before a scan.  Applying them here
    # too closes the gap that let the jog buttons drive past a limit the
    # scan engine would have refused.
    _LIMIT_PREFIX = {"x": "act1", "y": "act2", "z": "z"}

    def _axis_limits(self, axis_key: str):
        """(min, max) soft travel limits for an axis, or None when unset."""
        pfx = self._LIMIT_PREFIX.get(axis_key)
        if not pfx:
            return None
        try:
            s = self._setup_getter() or {}
        except Exception:
            return None
        lo, hi = s.get(f"{pfx}_min"), s.get(f"{pfx}_max")
        if lo is None or hi is None:
            return None
        try:
            lo, hi = float(lo), float(hi)
        except (TypeError, ValueError):
            return None
        return (lo, hi) if hi > lo else None
    def _move_axis(self, axis_key: str, value_um: float):
        info = self._get_axis_info()
        if axis_key not in info:
            self._set_pos_err(f"No config for '{axis_key}'"); return
        dev, attr = info[axis_key]
        if not dev: self._set_pos_err("No device configured"); return
        unit = self._axis_unit(axis_key)

        # Never send an absolute target derived from a position we could not
        # read.  The jog widgets already refuse to emit in that state; this is
        # the choke point every programmatic caller passes through too.
        jog = {"x": getattr(self, "jog_x", None),
               "y": getattr(self, "jog_y", None),
               "z": getattr(self, "jog_z", None)}.get(axis_key)
        if jog is not None and not jog.is_known():
            self._set_pos_err(
                f"{axis_key.upper()} position unknown — press 🔄 Read all "
                "before moving")
            return

        # Refuse, don't clamp: silently moving somewhere other than the
        # requested position is how a "small" jog ends up against the
        # objective with nothing in the log to say so.
        lim = self._axis_limits(axis_key)
        if lim is not None and not (lim[0] <= value_um <= lim[1]):
            self._set_pos_err(
                f"Move refused — {axis_key.upper()} target {value_um:.3f} {unit} "
                f"is outside the travel limits [{lim[0]:g}, {lim[1]:g}] {unit} "
                "(Setup Defaults → Stage Actuators)")
            return

        p, err = fresh_proxy(dev)
        if err: self._set_pos_err(err); return
        if is_sim_proxy(p): self._set_pos_err("Simulation mode"); return
        err = safe_write(p, attr, value_um)
        if err: self._set_pos_err(f"{attr}: {err[:60]}")
        else:   self._set_pos_ok(f"Sent {attr} = {value_um:.3f} {unit}")

    def _reinit_stage(self):
        """Re-initialise the stage motors (fixes wedged IR SmarAct axes).

        Calls the stage device's Initialise command; falls back to the
        standard TANGO Init if the server doesn't expose Initialise yet.

        Runs in a background thread with a long client timeout: Initialise
        re-inits three motors at up to 30 s each, which the default 3 s TANGO
        client timeout would abandon half way — reporting a failure while the
        server carried on, and freezing the GUI until it gave up.

        Afterwards every axis is marked *unknown* and re-read.  An Init can
        change what the position numbers mean (the motor server resets its
        unit conversion in init_device), so any target still displayed from
        before the reinit is not a position in the same frame any more.
        """
        info = self._get_axis_info()
        dev = info.get("x", ("", ""))[0]
        if not dev:
            self._set_pos_err("No stage device configured"); return
        for jog in (self.jog_x, self.jog_y, self.jog_z):
            jog.set_unknown("stage re-initialised")
        self.reinit_btn.setEnabled(False)
        self._set_pos_ok("Re-initialising stage…")

        def _do():
            last = ""
            used = ""
            try:
                p, err = fresh_proxy(dev)
                if err:
                    raise RuntimeError(err)
                if is_sim_proxy(p):
                    raise RuntimeError("Simulation mode")
                p.set_timeout_millis(120000)      # 3 axes × 30 s + slack
                for cmd in ("Initialise", "Init"):
                    try:
                        p.command_inout(cmd)
                        used = cmd
                        break
                    except Exception as e:
                        last = str(e)
            except Exception as e:
                last = str(e)

            def _done():
                self.reinit_btn.setEnabled(True)
                if not used:
                    self._set_pos_err(f"Reinitialise failed: {last[:100]}")
                    self._read_all()
                elif used == "Init":
                    # The plain Init path does not restore the motors' unit
                    # conversion or travel limits — say so rather than let it
                    # look like a clean recovery.
                    self._read_all(
                        f"⚠ Stage Init sent to {dev} — this server predates the "
                        "Initialise command, so unit conversion and travel "
                        "limits were NOT restored. Update the stage server.",
                        note_is_error=True)
                else:
                    self._read_all(f"Stage {used} sent to {dev}")
            self._gui_apply.emit(_done)

        threading.Thread(target=_do, daemon=True).start()

    def _probe_home_support(self, dev: str):
        """Show the ⊘ Zero-here button only for the MCS2 stage server.

        It needs both SetZero and Initialise — that pair is the stage
        server's signature.  Probed in a background thread so an unreachable
        device can't freeze the GUI; anything else (old Green Smaract
        server, Cryo Attocube stages, sim mode) keeps the button hidden.
        """
        self.home_btn.setVisible(False)
        if not dev:
            return

        def _do():
            show = False
            try:
                p, err = fresh_proxy(dev)
                if not err and not is_sim_proxy(p):
                    cmds = {str(c).lower() for c in p.get_command_list()}
                    show = "setzero" in cmds and "initialise" in cmds
            except Exception:
                pass
            self._gui_apply.emit(lambda s=show: self.home_btn.setVisible(s))

        threading.Thread(target=_do, daemon=True).start()

    def _home_stage(self):
        """Define the current stage position as 0 on all axes (SetZero).

        No movement — the SmarActMCS2Stage SetZero command re-labels the
        current position as the origin (SmarAct SetOffset), unlike a
        referencing Home.  A confirmation dialog is still shown because it
        redefines the coordinate frame (saved positions shift).  Runs in a
        background thread; positions are re-read afterwards.
        """
        info = self._get_axis_info()
        dev = info.get("x", ("", ""))[0]
        if not dev:
            self._set_pos_err("No stage device configured"); return
        ans = QMessageBox.question(
            self, "Set current position as zero?",
            "Define the current position of all three axes as 0?\n\n"
            "No movement occurs — this re-labels where the stage is now as\n"
            "the origin.  It redefines the coordinate frame, so previously\n"
            "saved positions will shift accordingly.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.home_btn.setEnabled(False)
        # The zero redefines the coordinate frame, so every displayed target
        # becomes meaningless the instant the command is sent — including if
        # the follow-up read fails.  Blank them first, restore from the read.
        for jog in (self.jog_x, self.jog_y, self.jog_z):
            jog.set_unknown("coordinate frame re-zeroed")
        self._set_pos_ok("Setting current position as zero…")

        def _do():
            try:
                p, err = fresh_proxy(dev)
                if err:
                    raise RuntimeError(err)
                if is_sim_proxy(p):
                    raise RuntimeError("Simulation mode")
                p.set_timeout_millis(20000)
                p.command_inout("SetZero")
                def _ok():
                    self.home_btn.setEnabled(True)
                    self._set_pos_ok("Current position defined as 0 (no movement)")
                    self._read_all()
                self._gui_apply.emit(_ok)
            except Exception as e:
                msg = str(e)
                def _err():
                    self.home_btn.setEnabled(True)
                    self._set_pos_err(f"Set-zero failed: {msg[:120]}")
                    # SetZero attempts all three axes and collects errors, so a
                    # failure can still mean some axes were re-zeroed.  Re-read
                    # rather than leave the frame ambiguous.
                    self._read_all()
                self._gui_apply.emit(_err)

        threading.Thread(target=_do, daemon=True).start()

    # ── LED lights ────────────────────────────────────────────────────────────
    def set_lights_device(self, path: str):
        """Configure the Lights TANGO device path and show/hide the LED row."""
        self._lights_dev = (path or "").strip()
        self.led_grp.setVisible(bool(self._lights_dev))
        if self._lights_dev:
            self._refresh_led_state()

    def _refresh_led_state(self):
        """Read the actual LED states from the Lights device (led1/led2 attrs)
        in a background thread and recolour the buttons. Servers predating the
        read-back attributes (or unreachable devices) leave the state unknown
        (grey); the reason is shown in the status line so a grey pair is
        diagnosable instead of silent."""
        dev = self._lights_dev
        if not dev:
            return

        def _do():
            states = {}
            fail = ""
            try:
                p, err = fresh_proxy(dev)
                if err or is_sim_proxy(p):
                    fail = err or "simulation proxy"
                else:
                    for led, attr in ((1, "led1"), (2, "led2")):
                        v, e = safe_read(p, attr)
                        if not e and v is not None:
                            states[led] = bool(v)
                        elif not fail:
                            fail = e or f"{attr} returned nothing"
            except Exception as exc:
                fail = str(exc)

            def _apply():
                for led, on in states.items():
                    self._led_state[led] = on
                    self._style_led(led)
                tip = ""
                if not states and fail:
                    tip = (f"State read failed: {str(fail)[:120]}\n"
                           "(old Lights server without led1/led2, or device "
                           "unreachable)")
                    self._set_pos_err(
                        f"LED state unavailable ({str(fail)[:60]})")
                for on_btn, off_btn in self._led_btns.values():
                    on_btn.setToolTip(tip); off_btn.setToolTip(tip)

            self._gui_apply.emit(_apply)

        threading.Thread(target=_do, daemon=True).start()

    _LED_ON_STYLE  = ("QPushButton{background:#a6e3a1;color:#11111b;"
                      "border:1px solid #45475a;border-radius:4px;padding:2px 8px;"
                      "font-weight:bold;}")
    _LED_OFF_STYLE = ("QPushButton{background:#f38ba8;color:#11111b;"
                      "border:1px solid #45475a;border-radius:4px;padding:2px 8px;"
                      "font-weight:bold;}")
    _LED_IDLE_STYLE = ("QPushButton{background:#313244;color:#cdd6f4;"
                       "border:1px solid #45475a;border-radius:4px;padding:2px 8px;}"
                       "QPushButton:hover{background:#45475a;}")

    def _style_led(self, led: int):
        """Highlight whichever of the On/Off pair matches the LED's last state."""
        on_btn, off_btn = self._led_btns[led]
        state = self._led_state[led]
        on_btn.setStyleSheet(self._LED_ON_STYLE  if state is True  else self._LED_IDLE_STYLE)
        off_btn.setStyleSheet(self._LED_OFF_STYLE if state is False else self._LED_IDLE_STYLE)

    def _led(self, led: int, on: bool):
        cmd = f"LED{led}{'ON' if on else 'OFF'}"
        if not self._lights_dev:
            self._set_pos_err("No Lights device configured"); return
        p, err = fresh_proxy(self._lights_dev)
        if err: self._set_pos_err(err); return
        if is_sim_proxy(p): self._set_pos_err("Simulation mode"); return
        try:
            p.command_inout(cmd)
            self._led_state[led] = on
            self._style_led(led)
            self._set_pos_ok(f"{cmd} → {self._lights_dev}")
        except Exception as e:
            self._set_pos_err(f"{cmd} failed: {str(e)[:70]}")

    def _read_all(self, note: str = "", note_is_error: bool = False):
        """Re-read all three axis positions.

        `note` is carried through to the final status line.  Callers that
        re-read straight after another operation (stop, reinit, re-zero) have
        something to say that matters more than the positions — without this
        the read's own "Read: x=… y=… z=…" would overwrite it a moment later.
        """
        self._refresh_led_state()
        info = self._get_axis_info()
        if not info: self._set_pos_err("No config available"); return
        self._set_pos_ok(note or "Reading…")

        jog_map = {"x": self.jog_x, "y": self.jog_y, "z": self.jog_z}

        def _do():
            results = []
            updates = {}   # axis → value (for GUI jog widgets)
            failed  = {}   # axis → why the position could not be read
            for key, jog in jog_map.items():
                dev, attr = info.get(key, ("", ""))
                if not dev:
                    results.append(f"{key}: no device")
                    failed[key] = "no device configured"
                    continue
                p, err = fresh_proxy(dev)
                if err:
                    results.append(f"{key}: {err[:20]}")
                    failed[key] = err
                    continue
                v, e = safe_read(p, attr)
                if e:
                    results.append(f"{key}({attr}): {e[:20]}")
                    failed[key] = e
                elif v is None:
                    results.append(f"{key}({attr}): no value")
                    failed[key] = "device returned no value"
                else:
                    updates[key] = v
                    results.append(f"{key}={v:.3f}")

            def _apply():
                for axis, val in updates.items():
                    w = jog_map[axis]
                    w.set_value(val); w.update_readback(val)
                # An axis whose position could not be read must NOT keep the
                # value it was showing: that number belongs to a frame we can
                # no longer vouch for, and one arrow click would send it back
                # as an absolute target.  Blank it and block jogging instead.
                for axis, why in failed.items():
                    jog_map[axis].set_unknown(str(why)[:120])
                msg = "Read: " + "  ".join(results)
                if failed:
                    msg += ("  —  jogging disabled for "
                            + ", ".join(sorted(a.upper() for a in failed)))
                if note:
                    msg = f"{note}  —  {msg}"
                if failed or note_is_error:
                    self._set_pos_err(msg)
                else:
                    self._set_pos_ok(msg)

            self._gui_apply.emit(_apply)

        threading.Thread(target=_do, daemon=True).start()

    def get_axis_info(self) -> dict:
        return self._get_axis_info()

    def update_positions(self, axis_values: dict):
        x = axis_values.get("x"); y = axis_values.get("y"); z = axis_values.get("z")
        if x is not None: self.jog_x.update_readback(x)
        if y is not None: self.jog_y.update_readback(y)
        if z is not None: self.jog_z.update_readback(z)

    def set_fl_device(self, dev: str):
        """Update the read-only FL sensor display (called on setup change)."""
        self.fl_dev_lbl.setText(dev or "(set in Setup Defaults tab)")

    # ── Autofocus ─────────────────────────────────────────────────────────────
    def _start_autofocus(self):
        """▶ button handler: this tab's focus-position spinbox, X only."""
        self._launch_autofocus(focus_pos=self.focus_pos_spin.value())

    def run_autofocus_async(self, on_done,
                            focus_pos: Optional[float] = None,
                            focus_pos_y: Optional[float] = None) -> bool:
        """Run the ▶ autofocus programmatically and report the outcome.

        Used by the current sweep to refocus between currents; the plot, the
        status line and the button states behave exactly as for a manual run.
        `focus_pos` / `focus_pos_y` override the tab's focus-position
        spinbox for the X and Y axes.  The scanlist passes the Refocus
        box's position, so "the middle of the device" is one setting next to
        the measurement rather than a spinbox on another tab that the
        operator may have left somewhere else.  Pass None for an axis the
        scan does not move: it is then left where it is.
        `on_done` receives a dict:

            {"ok": bool,        # a focus was found at all
             "reliable": bool,  # the peak passed the quality assessment
             "z", "fl": float | None,
             "msg": str}

        Returns False — without ever calling on_done — if it could not start,
        so the caller can decide what to do about a missing FL sensor.
        """
        return self._launch_autofocus(on_done=on_done, focus_pos=focus_pos,
                                      focus_pos_y=focus_pos_y)

    def _launch_autofocus(self, on_done=None,
                          focus_pos: Optional[float] = None,
                          focus_pos_y: Optional[float] = None) -> bool:
        if self._af_worker and self._af_worker.isRunning():
            return False
        info = self._get_axis_info()
        if not info: self._set_af_err("No config"); return False

        x_dev,  scan_attr = info.get("x", ("", "x"))
        y_dev,  y_attr    = info.get("y", ("", ""))
        z_dev,  z_attr    = info.get("z", (x_dev, "position0"))
        if not z_dev: z_dev = x_dev   # fall back to X device if Z not separately configured

        fl_dev = self._setup_getter().get("focus_averagein", "").strip()
        if not fl_dev:
            self._set_af_err("No FL sensor set — configure in Setup Defaults tab")
            return False

        # The worker drives every in-plane axis through the positioner proxy it
        # opens for Z, so a second axis is only offered when it lives on that
        # same device (true for SmarAct x/y/z and for the Attocube scanner).
        # None means "leave that axis alone" — a scan that does not move an
        # axis has no meaningful focus position for it.  A Y target is only
        # honoured when that axis lives on the same device the worker opens
        # for Z (true for SmarAct x/y/z and the Attocube scanner), because
        # the worker drives every in-plane axis through that one proxy.
        want_x = focus_pos is not None
        want_y = focus_pos_y is not None
        if not want_x:
            scan_attr = ""
        scan_attr2 = y_attr if (want_y and y_attr and y_dev == z_dev) else ""

        self._af_result   = None
        self._af_on_done  = on_done
        self.focus_plot.clear()
        self._af_status.setText("")
        self.af_start_btn.setEnabled(False); self.af_stop_btn.setEnabled(True)

        _setup = self._setup_getter()
        # The FL sensor's ATTRIBUTE is configurable in Setup Defaults; it used
        # to be ignored and hardcoded to "Value", so any non-Beckhoff focus
        # sensor silently produced zero valid readings.
        fl_attr = (_setup.get("focus_attr", "") or "Value").strip() or "Value"
        # Optional soft travel limits, same keys as core/validation.py.  These
        # replace the old hardcoded `abs(z) > 100` check, which assumed both a
        # µm axis and a stage whose origin happens to sit near focus.
        z_lim = None
        try:
            _lo, _hi = _setup.get("z_min"), _setup.get("z_max")
            if _lo is not None and _hi is not None and float(_hi) > float(_lo):
                z_lim = (float(_lo), float(_hi))
        except (TypeError, ValueError):
            z_lim = None

        self._af_worker = AutofocusWorker(
            positioner_dev=z_dev, fl_dev=fl_dev,
            focus_attr=z_attr, scan_attr=scan_attr,
            focus_pos=(float(focus_pos) if want_x else 0.0),
            dz=self.dz_spin.value(),
            d_zmax=self.dzmax_spin.value(),
            maxtries=self.tries_spin.value(),
            fl_attr=fl_attr, z_limits=z_lim,
            z_unit=self._axis_unit("z"),
            scan_attr2=scan_attr2,
            focus_pos2=(float(focus_pos_y) if want_y else 0.0))
        self._af_worker.point_measured.connect(self.focus_plot.add_point)
        self._af_worker.status_msg.connect(self._on_af_status)
        self._af_worker.focus_found.connect(self._on_focus_found)
        self._af_worker.error_msg.connect(self._set_af_err)
        self._af_worker.finished_.connect(self._on_af_finished)
        # Autofocus drives Z itself; its sweep would otherwise read as a fault.
        self._af_worker.start()
        return True

    def _stop_autofocus(self):
        if self._af_worker: self._af_worker.abort()

    def _on_af_status(self, m: str):
        self._af_status.setText(m)
        self._af_last_msg = m

    def _on_focus_found(self, z, fl):
        self.focus_plot.mark_best(z, fl)
        self.jog_z.set_value(z); self.jog_z.update_readback(z)
        self._af_result = {"z": float(z), "fl": float(fl)}

    def _on_af_finished(self):
        self.af_start_btn.setEnabled(True); self.af_stop_btn.setEnabled(False)
        # Re-arm first: the completion-callback path below returns early
        # when no callback is registered, which would skip this.
        cb, self._af_on_done = getattr(self, "_af_on_done", None), None
        if cb is None:
            return
        res = getattr(self, "_af_result", None)
        # focus_found only fires on success, so its absence IS the failure
        # signal; the quality dict distinguishes a real peak from an endpoint
        # or a noise excursion (see AutofocusWorker._assess).
        quality = getattr(self._af_worker, "_quality", None) or {}
        out = {"ok": res is not None,
               "reliable": bool(res is not None and quality.get("ok", True)),
               "z":  res["z"]  if res else None,
               "fl": res["fl"] if res else None,
               "msg": getattr(self, "_af_last_msg", "") or ""}
        try:
            cb(out)
        except Exception:
            log.debug("Autofocus completion callback failed", exc_info=True)

    # ── Status helpers ────────────────────────────────────────────────────────
    def _set_pos_ok(self, m):
        self._pos_status.setText(f"✓ {m}"); self._pos_status.setStyleSheet("color:#a6e3a1;font-size:10px;")
    def _set_pos_err(self, m):
        self._pos_status.setText(f"⚠ {m}"); self._pos_status.setStyleSheet("color:#f38ba8;font-size:10px;")
    def _set_af_err(self, m):
        self._af_status.setText(f"⚠ {m}"); self._af_status.setStyleSheet("color:#f38ba8;font-size:10px;")
