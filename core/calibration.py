"""
calibration.py — Samba v3
Calibration tab: 1D focus plot + digit-jog stage controls + autofocus.

The digit-jog controls allow precise positioning via per-digit ▲/▼ buttons.
The autofocus routine optimises the Z position by maximising a fluorescence
signal, plotting FL vs Z in real time.
"""
import time, traceback, threading
import numpy as np
from typing import Optional

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavToolbar
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QSplitter,
    QSizePolicy, QDoubleSpinBox, QSpinBox, QCheckBox
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

from plot_interact import ClickReadout, make_fontsize_spin

from hardware import fresh_proxy, is_sim_proxy, get_proxy, safe_read, safe_write


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

    def __init__(self, label: str = "X", unit: str = "µm",
                 n_int: int = 2, n_dec: int = 3, parent=None):
        super().__init__(parent)
        self._label = label; self._unit = unit
        self._n_int = n_int; self._n_dec = n_dec
        self._n_digits = n_int + n_dec
        self._value = 0.0; self._readback = None
        self._digit_labels = []; self._up_btns = []; self._down_btns = []
        self._build_ui()

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
        ulbl = QLabel(self._unit); ulbl.setStyleSheet("color:#6c7086;font-size:11px;")
        grid.addWidget(ulbl, 1, col, Qt.AlignmentFlag.AlignCenter)
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
        u2 = QLabel(self._unit); u2.setStyleSheet("color:#6c7086;font-size:11px;")
        edit_row.addWidget(u2); edit_row.addSpacing(12)
        rb_h = QLabel("readback:"); rb_h.setStyleSheet("color:#6c7086;font-size:10px;")
        edit_row.addWidget(rb_h)
        self._rb_label = QLabel("—")
        self._rb_label.setStyleSheet(
            "color:#a6e3a1;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;")
        edit_row.addWidget(self._rb_label); edit_row.addStretch()
        outer.addLayout(edit_row)
        self._refresh_display()

    def _nudge(self, delta):
        self._value += delta; self._refresh_display(); self.move_requested.emit(self._value)
    def _on_enter(self):
        try:
            self._value = float(self._edit.text().replace(",", ".").strip())
            self._refresh_display(); self.move_requested.emit(self._value)
        except ValueError: pass
    def _refresh_display(self):
        self._sign_lbl.setText("−" if self._value < 0 else "＋")
        fmt = f"{{:0{self._n_int + self._n_dec + 1}.{self._n_dec}f}}"
        digits = fmt.format(abs(self._value)).replace(".", "")
        digits = digits[:self._n_digits].ljust(self._n_digits, "0")
        for i, lbl in enumerate(self._digit_labels): lbl.setText(digits[i])
        if not self._edit.hasFocus():
            self._edit.setText(f"{self._value:.{self._n_dec}f}")
    def set_value(self, v): self._value = v; self._refresh_display()
    def update_readback(self, v):
        self._readback = v
        self._rb_label.setText(f"{v:.3f} µm" if v is not None else "—")
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
        self._font_pt = 9

        # Toolbar row: nav toolbar + text-size spinbox (matches Live1DWidget)
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(6)
        top.addWidget(self.bar, stretch=1)
        _tx = QLabel("Text:"); _tx.setStyleSheet("color:#a6adc8;font-size:10px;")
        top.addWidget(_tx)
        self.fs_spin = make_fontsize_spin(self._font_pt, self._on_fontsize)
        top.addWidget(self.fs_spin)

        # Sensor visibility row (populated per time scan, hidden otherwise):
        # one colored checkbox per plotted sensor — toggles the curve live.
        self._vis_w = QWidget(); self._vis_w.setVisible(False)
        self._vis_lay = QHBoxLayout(self._vis_w)
        self._vis_lay.setContentsMargins(4, 0, 4, 0); self._vis_lay.setSpacing(10)
        _sl = QLabel("Show:"); _sl.setStyleSheet("color:#a6adc8;font-size:10px;")
        self._vis_lay.addWidget(_sl)
        self._vis_lay.addStretch(1)

        # Left-click a curve to read off the nearest point's value.
        self._readout = ClickReadout(
            self.canvas, lambda: [self.ax], lambda: self._font_pt)

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self._vis_w)
        lay.addWidget(self.canvas, stretch=1)
        self._z_data = []; self._fl_data = []
        self._line = None; self._best_dot = None
        self._ts_xd = None; self._ts_yd = {}; self._ts_lines = {}
        self._ts_dirty = False
        self._style()

    def _style(self):
        self.ax.set_facecolor("#12121f")
        self.ax.tick_params(colors="#aaaacc", labelsize=self._font_pt)
        for sp in self.ax.spines.values(): sp.set_edgecolor("#3a3a5c")
        self.ax.set_xlabel("Z position (µm)", color="#aaaacc", fontsize=self._font_pt)
        self.ax.set_ylabel("Focus signal (V)", color="#aaaacc", fontsize=self._font_pt)
        self.ax.set_title("Autofocus", color="#6c7086", fontsize=self._font_pt)

    def _on_fontsize(self, pt: int):
        """User picked a new on-plot text size — restyle and redraw live."""
        self._font_pt = int(pt)
        self.ax.tick_params(labelsize=self._font_pt)
        self.ax.xaxis.label.set_fontsize(self._font_pt)
        self.ax.yaxis.label.set_fontsize(self._font_pt)
        self.ax.title.set_fontsize(self._font_pt)
        leg = self.ax.get_legend()
        if leg is not None:
            for t in leg.get_texts():
                t.set_fontsize(self._font_pt)
        try: self.fig.tight_layout()
        except Exception: pass
        self.canvas.draw_idle()

    def _clear_vis_boxes(self):
        """Remove the per-sensor visibility checkboxes (keep label + stretch)."""
        while self._vis_lay.count() > 2:
            item = self._vis_lay.takeAt(1)
            w = item.widget()
            if w is not None: w.deleteLater()
        self._vis_w.setVisible(False)

    def clear(self):
        self._z_data = []; self._fl_data = []
        self.ax.cla(); self._style()
        self._line = None; self._best_dot = None
        # Also clear time scan state
        self._ts_xd = None; self._ts_yd = {}; self._ts_lines = {}
        self._ts_dirty = False
        self._clear_vis_boxes()
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
        self.ax.relim(); self.ax.autoscale_view()
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
    _TS_COLORS = ['#89b4fa','#74c7ec','#a6e3a1','#f38ba8','#fab387','#cba6f7']

    def setup_timescan(self, n_pts: int, sensors: list):
        """Prepare the plot for a time scan: point index on X, sensor values on Y.

        Every sensor gets a line + a colored visibility checkbox above the
        plot, so what is displayed can be changed while the scan runs.  A
        sensor dict may carry "visible": False to start hidden (data is
        still collected — checking the box later reveals it).
        """
        self.ax.cla()
        self.ax.set_facecolor("#12121f")
        self.ax.tick_params(colors="#aaaacc", labelsize=self._font_pt)
        for sp in self.ax.spines.values(): sp.set_edgecolor("#3a3a5c")
        self.ax.set_xlabel("Point", color="#aaaacc", fontsize=self._font_pt)
        self.ax.set_ylabel("Signal (V)", color="#aaaacc", fontsize=self._font_pt)
        self.ax.set_title("Time scan", color="#6c7086", fontsize=self._font_pt)
        self._line = None; self._best_dot = None
        if getattr(self, "_readout", None) is not None:
            self._readout.note_axes_cleared()
        self._ts_xd = np.full(n_pts, np.nan)
        self._ts_yd = {}; self._ts_lines = {}
        self._clear_vis_boxes()
        for i, s in enumerate(sensors):
            lbl = s["label"]
            c = self._TS_COLORS[i % len(self._TS_COLORS)]
            line, = self.ax.plot([], [], color=c, linewidth=1.5,
                                  marker=".", markersize=4, label=lbl)
            line.set_visible(bool(s.get("visible", True)))
            self._ts_lines[lbl] = line
            self._ts_yd[lbl] = np.full(n_pts, np.nan)
            cb = QCheckBox(lbl); cb.setChecked(line.get_visible())
            cb.setStyleSheet(f"QCheckBox{{color:{c};font-size:10px;spacing:3px;}}")
            cb.toggled.connect(lambda on, l=lbl: self._set_line_visible(l, on))
            self._vis_lay.insertWidget(self._vis_lay.count() - 1, cb)
        self._vis_w.setVisible(bool(sensors))
        self._rebuild_ts_legend()
        self.ax.axhline(0, color="#45475a", linewidth=0.6, linestyle="--")
        self.fig.tight_layout(); self.canvas.draw_idle()
        self._ts_dirty = False
        # Start throttled timer if not running
        if not hasattr(self, '_ts_timer'):
            self._ts_timer = QTimer(self)
            self._ts_timer.setInterval(80)
            self._ts_timer.timeout.connect(self._ts_throttled_draw)
            self._ts_timer.start()

    def _rebuild_ts_legend(self):
        """Legend shows only the currently visible curves."""
        handles = [l for l in self._ts_lines.values() if l.get_visible()]
        leg = self.ax.get_legend()
        if handles:
            self.ax.legend(handles=handles, fontsize=self._font_pt,
                           facecolor="#313244", edgecolor="#45475a",
                           labelcolor="#cdd6f4", loc="best")
        elif leg is not None:
            leg.remove()

    def _set_line_visible(self, lbl: str, on: bool):
        line = self._ts_lines.get(lbl)
        if line is None: return
        line.set_visible(bool(on))
        self._rebuild_ts_legend()
        self._ts_dirty = True
        self.canvas.draw_idle()

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
        # Manual limits — visible curves only, so hiding a large signal
        # rescales the plot onto the ones still shown
        all_lines = [l for l in self._ts_lines.values()
                     if l.get_visible() and len(l.get_xdata()) > 0]
        if all_lines:
            ax = np.concatenate([l.get_xdata() for l in all_lines])
            ay = np.concatenate([l.get_ydata() for l in all_lines])
            mx = np.isfinite(ax); my = np.isfinite(ay)
            if mx.any():
                xlo, xhi = ax[mx].min(), ax[mx].max()
                pad = max(abs(xhi - xlo) * 0.02, 1e-12)
                self.ax.set_xlim(xlo - pad, xhi + pad)
            if my.any():
                ylo, yhi = ay[my].min(), ay[my].max()
                pad = max(abs(yhi - ylo) * 0.05, 1e-12)
                self.ax.set_ylim(ylo - pad, yhi + pad)
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
                 focus_pos: float, dz: float, d_zmax: float, maxtries: int):
        super().__init__()
        self._pos_dev    = positioner_dev
        self._fl_dev     = fl_dev
        self._focus_attr = focus_attr
        self._scan_attr  = scan_attr
        self._focus_pos  = focus_pos
        self._dz         = dz
        self._d_zmax     = d_zmax
        self._maxtries   = maxtries
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
        pos_scan, e = safe_read(p, self._scan_attr)
        if e: pos_scan = 0.0

        if abs(pos0_z) > 100:
            self.error_msg.emit(f"Z = {pos0_z:.1f} µm — too far from focus, aborting")
            return

        self.status_msg.emit(f"Focusing… Z₀={pos0_z:.3f}  scan={pos_scan:.3f}")

        # Move scan axis to focus position
        if abs(self._focus_pos - (pos_scan or 0)) > 0.01:
            safe_write(p, self._scan_attr, self._focus_pos)
            self.status_msg.emit(f"Moved {self._scan_attr} → {self._focus_pos:.3f}")
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
            # Always restore the scan axis, even on error/abort
            if pos_scan is not None:
                safe_write(p, self._scan_attr, pos_scan)
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
        self.status_msg.emit(f"Focus found at Z = {best_z:.3f} µm  "
                             f"(FL = {best_fl:.4g}) — stage moved there")

    _MOVE_SETTLE_S = 0.5   # settle after each Z step
    _FL_TIMEOUT_S  = 2.0   # max wait for the FL device to finish averaging

    def _measure_fl(self, fl_p):
        """Trigger one FL acquisition and read the averaged Value.

        Waits for the device to leave RUNNING (BeckhoffAverage handshake)
        instead of a fixed sleep; falls back gracefully for devices without
        a Start command or state feedback.  Returns (value, err).
        """
        try: fl_p.command_inout("Start")
        except Exception: pass
        t0 = time.time()
        time.sleep(0.05)
        while time.time() - t0 < self._FL_TIMEOUT_S:
            try:
                if str(fl_p.state()) != "RUNNING":
                    break
            except Exception:
                break
            time.sleep(0.02)
        time.sleep(0.05)
        return safe_read(fl_p, "Value")

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
            self.error_msg.emit("No valid FL readings during coarse sweep")
            return None
        best_z, best_fl = max(pts, key=lambda t: t[1])

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

    def __init__(self, setup_getter, config_getter=None, parent=None):
        super().__init__(parent)
        self._setup_getter  = setup_getter
        self._config_getter = config_getter
        self._af_worker = None
        self._stage_cfg: dict = {}   # populated by configure_stage()
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

        self._dev_lbl = QLabel("Device: —")
        self._dev_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        self._dev_lbl.setWordWrap(True)
        ctrl_l.addWidget(self._dev_lbl)

        self.jog_x = DigitJogWidget("X", "µm", n_int=2, n_dec=3)
        self.jog_y = DigitJogWidget("Y", "µm", n_int=2, n_dec=3)
        self.jog_z = DigitJogWidget("Z", "µm", n_int=2, n_dec=3)
        for jog in [self.jog_x, self.jog_y, self.jog_z]:
            ctrl_l.addWidget(jog)

        self.jog_x.move_requested.connect(lambda v: self._move_axis("x", v))
        self.jog_y.move_requested.connect(lambda v: self._move_axis("y", v))
        self.jog_z.move_requested.connect(lambda v: self._move_axis("z", v))

        btn_row = QHBoxLayout(); btn_row.setSpacing(6)
        read_btn = QPushButton("🔄 Read all"); read_btn.clicked.connect(self._read_all)
        btn_row.addWidget(read_btn)
        self.reinit_btn = QPushButton("⟲ Reinitialise")
        self.reinit_btn.setToolTip(
            "Re-initialise the stage motors — the fix for a wedged IR SmarAct "
            "axis after manual use with the hand controller.\n"
            "Sends the stage device's Initialise command (falls back to Init).")
        self.reinit_btn.clicked.connect(self._reinit_stage)
        btn_row.addWidget(self.reinit_btn); btn_row.addStretch()
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

        rl.addWidget(af_grp)

        # ── Time scan settings (the calibration tab's own hidden config) ─────
        # Used by the ▶ Start time scan instead of the scan config selected in
        # the left panel; persisted per setup, never shown in the config list.
        ts_grp = QGroupBox("Time scan (this tab's own settings)")
        ts_l = QGridLayout(ts_grp); ts_l.setSpacing(4)
        ts_l.addWidget(QLabel("Points:"), 0, 0)
        self.ts_npts_spin = QSpinBox()
        self.ts_npts_spin.setRange(2, 1_000_000); self.ts_npts_spin.setValue(300)
        ts_l.addWidget(self.ts_npts_spin, 0, 1)
        ts_l.addWidget(QLabel("Int time:"), 0, 2)
        self.ts_int_spin = QDoubleSpinBox()
        self.ts_int_spin.setRange(0.001, 30.0); self.ts_int_spin.setDecimals(3)
        self.ts_int_spin.setValue(0.1); self.ts_int_spin.setSuffix(" s")
        ts_l.addWidget(self.ts_int_spin, 0, 3)
        ts_grp.setToolTip(
            "Parameters for the calibration time scan (▶ Start while this tab\n"
            "is open). Independent of the scan config selected on the left —\n"
            "saved per setup as a hidden calibration config.")
        self.ts_npts_spin.valueChanged.connect(lambda _: self.timescan_changed.emit())
        self.ts_int_spin.valueChanged.connect(lambda _: self.timescan_changed.emit())
        rl.addWidget(ts_grp)

        splitter.addWidget(right)

        splitter.setSizes([400, 500]); splitter.setStretchFactor(0, 1)
        root.addWidget(splitter)

    # ── Time-scan settings (hidden calibration config) ────────────────────────
    def get_timescan_settings(self) -> dict:
        return {"npts":     int(self.ts_npts_spin.value()),
                "int_time": float(self.ts_int_spin.value())}

    def load_timescan_settings(self, d: dict):
        """Restore the per-setup time-scan settings without re-emitting."""
        for spin, key, default in ((self.ts_npts_spin, "npts", 300),
                                   (self.ts_int_spin, "int_time", 0.1)):
            spin.blockSignals(True)
            try:    spin.setValue(type(default)((d or {}).get(key, default)))
            except Exception: spin.setValue(default)
            finally: spin.blockSignals(False)

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
        self._dev_lbl.setText(
            f"X: {x_dev}/{x_attr}   Y: {y_dev}/{y_attr}   Z: {z_dev}/{z_attr}")
        return {"x": (x_dev, x_attr), "y": (y_dev, y_attr), "z": (z_dev, z_attr)}

    def configure_stage(self, x_dev: str, x_attr: str,
                        y_dev: str, y_attr: str,
                        z_dev: str, z_attr: str):
        """Inject stage device/attribute for each axis from setup defaults.
        Called by the main window on every setup or defaults change."""
        self._stage_cfg = {
            "x": (x_dev, x_attr),
            "y": (y_dev, y_attr),
            "z": (z_dev, z_attr),
        }
        self._dev_lbl.setText(
            f"X: {x_dev or '—'}/{x_attr}   "
            f"Y: {y_dev or '—'}/{y_attr}   "
            f"Z: {z_dev or '—'}/{z_attr}")

    def _move_axis(self, axis_key: str, value_um: float):
        info = self._get_axis_info()
        if axis_key not in info:
            self._set_pos_err(f"No config for '{axis_key}'"); return
        dev, attr = info[axis_key]
        if not dev: self._set_pos_err("No device configured"); return
        p, err = fresh_proxy(dev)
        if err: self._set_pos_err(err); return
        if is_sim_proxy(p): self._set_pos_err("Simulation mode"); return
        err = safe_write(p, attr, value_um)
        if err: self._set_pos_err(f"{attr}: {err[:60]}")
        else:   self._set_pos_ok(f"Sent {attr} = {value_um:.3f} µm")

    def _reinit_stage(self):
        """Re-initialise the stage motors (fixes wedged IR SmarAct axes).
        Calls the stage device's Initialise command; falls back to the
        standard TANGO Init if the server doesn't expose Initialise yet."""
        info = self._get_axis_info()
        dev = info.get("x", ("", ""))[0]
        if not dev:
            self._set_pos_err("No stage device configured"); return
        p, err = fresh_proxy(dev)
        if err: self._set_pos_err(err); return
        if is_sim_proxy(p): self._set_pos_err("Simulation mode"); return
        for cmd in ("Initialise", "Init"):
            try:
                p.command_inout(cmd)
                self._set_pos_ok(f"Stage {cmd} sent to {dev}")
                return
            except Exception as e:
                last = str(e)
        self._set_pos_err(f"Reinitialise failed: {last[:80]}")

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

    def _read_all(self):
        self._refresh_led_state()
        info = self._get_axis_info()
        if not info: self._set_pos_err("No config available"); return
        self._set_pos_ok("Reading…")

        jog_map = {"x": self.jog_x, "y": self.jog_y, "z": self.jog_z}

        def _do():
            results = []
            updates = {}   # axis → value (for GUI jog widgets)
            for key, jog in jog_map.items():
                dev, attr = info.get(key, ("", ""))
                if not dev: results.append(f"{key}: no device"); continue
                p, err = fresh_proxy(dev)
                if err: results.append(f"{key}: {err[:20]}"); continue
                v, e = safe_read(p, attr)
                if e: results.append(f"{key}({attr}): {e[:20]}")
                elif v is not None:
                    updates[key] = v
                    results.append(f"{key}={v:.3f}")

            def _apply():
                for axis, val in updates.items():
                    w = jog_map[axis]
                    w.set_value(val); w.update_readback(val)
                self._set_pos_ok("Read: " + "  ".join(results))

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
        if self._af_worker and self._af_worker.isRunning():
            return
        info = self._get_axis_info()
        if not info: self._set_af_err("No config"); return

        x_dev,  scan_attr = info.get("x", ("", "x"))
        z_dev,  z_attr    = info.get("z", (x_dev, "position0"))
        if not z_dev: z_dev = x_dev   # fall back to X device if Z not separately configured

        fl_dev = self._setup_getter().get("focus_averagein", "").strip()
        if not fl_dev: self._set_af_err("No FL sensor set — configure in Setup Defaults tab"); return

        self.focus_plot.clear()
        self._af_status.setText("")
        self.af_start_btn.setEnabled(False); self.af_stop_btn.setEnabled(True)

        self._af_worker = AutofocusWorker(
            positioner_dev=z_dev, fl_dev=fl_dev,
            focus_attr=z_attr, scan_attr=scan_attr,
            focus_pos=self.focus_pos_spin.value(),
            dz=self.dz_spin.value(),
            d_zmax=self.dzmax_spin.value(),
            maxtries=self.tries_spin.value())
        self._af_worker.point_measured.connect(self.focus_plot.add_point)
        self._af_worker.status_msg.connect(
            lambda m: self._af_status.setText(m))
        self._af_worker.focus_found.connect(self._on_focus_found)
        self._af_worker.error_msg.connect(self._set_af_err)
        self._af_worker.finished_.connect(self._on_af_finished)
        self._af_worker.start()

    def _stop_autofocus(self):
        if self._af_worker: self._af_worker.abort()

    def _on_focus_found(self, z, fl):
        self.focus_plot.mark_best(z, fl)
        self.jog_z.set_value(z); self.jog_z.update_readback(z)

    def _on_af_finished(self):
        self.af_start_btn.setEnabled(True); self.af_stop_btn.setEnabled(False)

    # ── Status helpers ────────────────────────────────────────────────────────
    def _set_pos_ok(self, m):
        self._pos_status.setText(f"✓ {m}"); self._pos_status.setStyleSheet("color:#a6e3a1;font-size:10px;")
    def _set_pos_err(self, m):
        self._pos_status.setText(f"⚠ {m}"); self._pos_status.setStyleSheet("color:#f38ba8;font-size:10px;")
    def _set_af_err(self, m):
        self._af_status.setText(f"⚠ {m}"); self._af_status.setStyleSheet("color:#f38ba8;font-size:10px;")
