"""
plot_widgets.py — Samba v3
Live matplotlib widgets embedded in PyQt6:
  Live2DWidget — false-colour image map updated point-by-point
  Live1DWidget — dual-Y-axis line plot with live reconfig (no data loss)

v3.2 — Tier 3 polish:
  • Both widgets are now plain QWidget (not QMainWindow) with toolbar + canvas
    in a QVBoxLayout.  Avoids nested-QMainWindow edge cases.
  • Throttled rendering via QTimer (unchanged from v3.1).
"""
import numpy as np
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavToolbar
from matplotlib.figure import Figure

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel
from PyQt6.QtCore import QTimer

from config import LEFT_COLORS, RIGHT_COLORS, X_NATURAL, X_TIME
from plot_interact import ClickReadout, make_fontsize_spin

REDRAW_INTERVAL_MS = 80


# ─────────────────────────────────────────────────────────────────────────────
# Live 2D map
# ─────────────────────────────────────────────────────────────────────────────
class Live2DWidget(QWidget):
    """False-colour image map, updated incrementally as scan points arrive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data  = None
        self._xarr  = self._yarr = None
        self._img   = self._cb   = None
        self._cmap  = "RdBu_r"
        self._sensor = self._xlbl = self._ylbl = ""
        self._dirty = False

        # constrained_layout keeps the axes filling the figure (with the
        # colorbar) across resizes — avoids the map shrinking to a narrow strip.
        self.fig    = Figure(figsize=(6, 5), dpi=100, facecolor="#1e1e2e",
                             constrained_layout=True)
        self.ax     = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.bar    = NavToolbar(self.canvas, None)
        self.bar.setStyleSheet("background:#1e1e2e;color:white;")

        # Toolbar row: nav toolbar + per-view toggles
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(6)
        top.addWidget(self.bar, stretch=1)
        self.autocolor_cb = QCheckBox("Auto color"); self.autocolor_cb.setChecked(True)
        self.autocolor_cb.setToolTip("Rescale the colour range to the data as points arrive.")
        self.autocolor_cb.setStyleSheet("color:#cdd6f4;font-size:10px;")
        self.autocolor_cb.toggled.connect(lambda _: setattr(self, "_dirty", True))
        top.addWidget(self.autocolor_cb)

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.canvas, stretch=1)
        self._style_axes()

        self._timer = QTimer(self)
        self._timer.setInterval(REDRAW_INTERVAL_MS)
        self._timer.timeout.connect(self._throttled_draw)
        self._timer.start()

    def _style_axes(self):
        self.ax.set_facecolor("#12121f")
        self.ax.tick_params(colors="#aaaacc", labelsize=9)
        for sp in self.ax.spines.values():
            sp.set_edgecolor("#3a3a5c")

    def _throttled_draw(self):
        if not self._dirty:
            return
        self._dirty = False
        if self._img is not None and self._data is not None:
            if self.autocolor_cb.isChecked():
                v = self._data[np.isfinite(self._data)]
                if len(v) > 1:
                    lo, hi = v.min(), v.max()
                    if lo == hi: hi = lo + 1e-12
                    self._img.set_clim(lo, hi)
            self._img.set_data(self._data)
        self.canvas.draw_idle()

    def setup(self, x_arr, y_arr, xl: str, yl: str, sensor: str, cmap: str):
        self._xarr = x_arr; self._yarr = y_arr; self._cmap = cmap
        self._sensor = sensor; self._xlbl = xl; self._ylbl = yl
        self._data = np.full((len(y_arr), len(x_arr)), np.nan)
        self._redraw()

    def _redraw(self):
        self.ax.cla(); self._style_axes()
        if self._xarr is None:
            self.canvas.draw_idle(); return
        ext = [self._xarr[0], self._xarr[-1], self._yarr[0], self._yarr[-1]]
        self._img = self.ax.imshow(
            self._data, origin="lower", aspect="auto",
            extent=ext, cmap=self._cmap, interpolation="nearest")
        if self._cb:
            try: self._cb.remove()
            except Exception: pass
        self._cb = self.fig.colorbar(self._img, ax=self.ax)
        self._cb.ax.yaxis.set_tick_params(color="#aaaacc", labelcolor="#aaaacc")
        self.ax.set_xlabel(self._xlbl, color="#aaaacc")
        self.ax.set_ylabel(self._ylbl, color="#aaaacc")
        self.ax.set_title(self._sensor, color="#ccccff", fontsize=10)
        self.canvas.draw_idle()

    def update_point(self, ix: int, iy: int, val: float):
        if self._data is None or self._img is None: return
        self._data[iy, ix] = val
        self._dirty = True

    def switch_sensor(self, new_data: 'np.ndarray', label: str):
        if self._img is None: return
        self._data = new_data.copy(); self._sensor = label
        self.ax.set_title(label, color="#ccccff", fontsize=10)
        self._dirty = True

    def set_colormap(self, cmap: str):
        self._cmap = cmap
        if self._img:
            self._img.set_cmap(cmap); self._dirty = True

    def clear(self):
        self._data = self._xarr = self._yarr = self._img = None
        self._dirty = False
        if self._cb:
            try: self._cb.remove()
            except Exception: pass
            self._cb = None
        self.ax.cla(); self._style_axes(); self.canvas.draw_idle()


# ─────────────────────────────────────────────────────────────────────────────
# Live 1D plot — dual Y axes, live reconfig without data loss
# ─────────────────────────────────────────────────────────────────────────────
class Live1DWidget(QWidget):
    """
    Dual-Y-axis line plot:
      1. alloc()        — allocate data buffers at scan start
      2. apply_config() — rebuild lines from stored data (safe mid-scan)
      3. update_point() — write one data point, defer draw to timer
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._n: int = 0
        self._xd: Optional[np.ndarray]  = None
        self._yd: Dict[str, np.ndarray] = {}
        self._x_key: str = X_NATURAL
        self._x_label_nat: str = ""
        self._lines: Dict[str, Tuple]   = {}
        self._dirty = False
        self._font_pt = 9

        self.fig    = Figure(figsize=(6, 4), dpi=100, facecolor="#1e1e2e")
        self.ax1    = self.fig.add_subplot(111)
        self.ax2    = self.ax1.twinx()
        self.canvas = FigureCanvas(self.fig)
        self.bar    = NavToolbar(self.canvas, None)
        self.bar.setStyleSheet("background:#1e1e2e;color:white;")

        # Toolbar row: nav toolbar + auto-scale toggle + text-size spinbox
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(6)
        top.addWidget(self.bar, stretch=1)
        self.autoscale_cb = QCheckBox("Auto-scale"); self.autoscale_cb.setChecked(True)
        self.autoscale_cb.setToolTip(
            "Rescale axes to the data on every update.\n"
            "Uncheck to keep your zoom/pan during a live scan.")
        self.autoscale_cb.setStyleSheet("color:#cdd6f4;font-size:10px;")
        self.autoscale_cb.toggled.connect(lambda _: setattr(self, "_dirty", True))
        top.addWidget(self.autoscale_cb)
        _tx = QLabel("Text:"); _tx.setStyleSheet("color:#a6adc8;font-size:10px;")
        top.addWidget(_tx)
        self.fs_spin = make_fontsize_spin(self._font_pt, self._on_fontsize)
        top.addWidget(self.fs_spin)

        # Left-click a curve to read off the nearest point's value.
        self._readout = ClickReadout(
            self.canvas, lambda: [self.ax1, self.ax2], lambda: self._font_pt)

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.canvas, stretch=1)
        self._style_axes()

        self._timer = QTimer(self)
        self._timer.setInterval(REDRAW_INTERVAL_MS)
        self._timer.timeout.connect(self._throttled_draw)
        self._timer.start()

    def _style_axes(self):
        self.ax1.set_facecolor("#12121f")
        for ax in [self.ax1, self.ax2]:
            ax.tick_params(colors="#aaaacc", labelsize=self._font_pt)
            for sp in ax.spines.values():
                sp.set_edgecolor("#3a3a5c")
        self.ax1.yaxis.label.set_color("#89b4fa")
        self.ax2.yaxis.label.set_color("#f38ba8")
        # Y2 belongs on the right-hand axis — cla() can reset a twinx back
        # to left-side ticks/label, which then overlap Y1's.
        self.ax2.yaxis.set_label_position("right")
        self.ax2.yaxis.tick_right()
        self.ax1.yaxis.set_label_position("left")
        self.ax1.yaxis.tick_left()

    def _on_fontsize(self, pt: int):
        """User picked a new on-plot text size — restyle and redraw live."""
        self._font_pt = int(pt)
        self._apply_font()
        self._layout()

    def _layout(self):
        """tight_layout (keeps axis titles clear of the tick numbers), then
        reserve a strip above the axes for the legends so they never sit on
        the data.  Legend heights are measured from a real draw, so the
        reserved space follows the current font size and row count."""
        try:
            self.fig.tight_layout()
            legs = [ax.get_legend() for ax in (self.ax1, self.ax2)]
            legs = [l for l in legs if l is not None]
            if legs:
                self.canvas.draw()          # renderer needed for extents
                renderer = self.canvas.get_renderer()
                fig_h = float(self.fig.bbox.height) or 1.0
                h = max(l.get_window_extent(renderer).height
                        for l in legs) / fig_h
                top = self.fig.subplotpars.top
                self.fig.subplots_adjust(top=max(0.4, top - h - 0.01))
        except Exception:
            pass
        self.canvas.draw_idle()

    def _apply_font(self):
        """Push the current font size onto ticks, axis labels and legends."""
        for ax in [self.ax1, self.ax2]:
            ax.tick_params(labelsize=self._font_pt)
            ax.xaxis.label.set_fontsize(self._font_pt)
            ax.yaxis.label.set_fontsize(self._font_pt)
            leg = ax.get_legend()
            if leg is not None:
                for t in leg.get_texts():
                    t.set_fontsize(self._font_pt)

    def _throttled_draw(self):
        if not self._dirty:
            return
        self._dirty = False

        k = self._x_key
        if   k == X_NATURAL: x_arr = self._xd
        elif k == X_TIME:    x_arr = self._yd.get(X_TIME)
        elif k in self._yd:  x_arr = self._yd[k]
        else:                x_arr = self._xd

        for lbl, (line, _) in self._lines.items():
            y = self._yd.get(lbl)
            if y is None: continue
            if x_arr is not None:
                m = np.isfinite(y) & np.isfinite(x_arr)
                if m.any(): line.set_data(x_arr[m], y[m])
            else:
                m = np.isfinite(y)
                if m.any(): line.set_data(np.arange(len(y))[m], y[m])

        # Autoscale (skip entirely when the user has unchecked it, so a manual
        # zoom/pan survives live updates instead of being reset every frame).
        if self.autoscale_cb.isChecked():
            # Manually compute limits — relim() is unreliable on twinx.
            # X-axis is shared between ax1 and ax2, so compute x from all lines.
            all_lines = [(l, ax) for ax in [self.ax1, self.ax2]
                         for l in ax.get_lines()
                         if len(l.get_xdata()) > 0]
            if all_lines:
                all_x = np.concatenate([l.get_xdata() for l, _ in all_lines])
                mx = np.isfinite(all_x)
                if mx.any():
                    xlo, xhi = all_x[mx].min(), all_x[mx].max()
                    pad = max(abs(xhi - xlo) * 0.02, 1e-12)
                    self.ax1.set_xlim(xlo - pad, xhi + pad)
            # Y-limits per axis (independent)
            for ax in [self.ax1, self.ax2]:
                lines = [l for l in ax.get_lines()
                         if len(l.get_ydata()) > 0]
                if not lines: continue
                all_y = np.concatenate([l.get_ydata() for l in lines])
                my = np.isfinite(all_y)
                if my.any():
                    ylo, yhi = all_y[my].min(), all_y[my].max()
                    pad = max(abs(yhi - ylo) * 0.05, 1e-12)
                    ax.set_ylim(ylo - pad, yhi + pad)
        self.canvas.draw_idle()

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def alloc(self, n_pts: int, xl: str, xu: str, all_sensors: List[dict]):
        self._n = n_pts
        self._xd = np.full(n_pts, np.nan)
        self._x_label_nat = f"{xl} ({xu})" if xu else xl
        self._yd = {s["label"]: np.full(n_pts, np.nan) for s in all_sensors}
        self._yd[X_TIME] = np.full(n_pts, np.nan)

    def apply_config(self, sensors_meta: List[dict], x_key: str):
        self._x_key = x_key

        if x_key == X_NATURAL:
            x_arr, x_lbl = self._xd, self._x_label_nat
        elif x_key == X_TIME:
            x_arr, x_lbl = self._yd.get(X_TIME), "Time (s)"
        elif x_key in self._yd:
            x_arr, x_lbl = self._yd[x_key], x_key
        else:
            x_arr, x_lbl = self._xd, self._x_label_nat

        self.ax1.cla(); self.ax2.cla(); self._style_axes()
        self._lines = {}
        if getattr(self, "_readout", None) is not None:
            self._readout.note_axes_cleared()
        self.ax1.set_xlabel(x_lbl or "", color="#aaaacc", fontsize=self._font_pt)

        li = ri = 0
        left_meta:  list = []   # (label, unit, curve color) per Y1 sensor
        right_meta: list = []

        for s in sensors_meta:
            lbl  = s["label"]; axis = s.get("axis", "Y1"); unit = s.get("unit", "")
            if axis == "—" or lbl not in self._yd:
                continue
            if axis == "Y2":
                c  = RIGHT_COLORS[ri % len(RIGHT_COLORS)]; ri += 1; ax = self.ax2
                right_meta.append((lbl, unit, c))
            else:
                c  = LEFT_COLORS[li % len(LEFT_COLORS)];  li += 1; ax = self.ax1
                left_meta.append((lbl, unit, c))
            line, = ax.plot([], [], color=c, linewidth=1.8,
                            label=lbl, marker=".", markersize=4)
            self._lines[lbl] = (line, ax)

        # Axis titles carry the sensor name(s) + unit, not just the unit.
        # A single sensor on an axis colors the title like its curve; with
        # several the title keeps the axis color and the legend maps
        # name → color.
        def _ylabel(entries):
            return ", ".join(f"{l} ({u})" if u else l for l, u, _ in entries)
        if left_meta:
            col = left_meta[0][2] if len(left_meta) == 1 else "#89b4fa"
            self.ax1.set_ylabel(_ylabel(left_meta), color=col,
                                fontsize=self._font_pt)
        if right_meta:
            col = right_meta[0][2] if len(right_meta) == 1 else "#f38ba8"
            self.ax2.set_ylabel(_ylabel(right_meta), color=col,
                                fontsize=self._font_pt)

        self._fill_lines(x_arr)

        # Compute limits — shared x across both axes, independent y per axis
        all_visible = []
        for ax in [self.ax1, self.ax2]:
            labelled  = [l for l in ax.get_lines() if not l.get_label().startswith("_")]
            with_data = [l for l in labelled if len(l.get_xdata()) > 0]
            all_visible.extend((l, ax) for l in with_data)
            if with_data:
                all_y = np.concatenate([l.get_ydata() for l in with_data])
                my = np.isfinite(all_y)
                if my.any():
                    ylo, yhi = all_y[my].min(), all_y[my].max()
                    pad = max(abs(yhi - ylo) * 0.05, 1e-12)
                    ax.set_ylim(ylo - pad, yhi + pad)
            # Legend appears as soon as the axis has any labelled line — even
            # before the first point arrives — so it shows from scan start
            # without needing a manual refresh.  Anchored ABOVE the axes
            # (Y1 left, Y2 right) so it can never sit on the data; _layout()
            # reserves the vertical strip it needs.
            if labelled:
                if ax is self.ax1:
                    loc, anchor = "lower left",  (0.0, 1.005)
                else:
                    loc, anchor = "lower right", (1.0, 1.005)
                ax.legend(
                    loc=loc, bbox_to_anchor=anchor, borderaxespad=0.0,
                    ncol=min(len(labelled), 3),
                    fontsize=self._font_pt, facecolor="#313244",
                    edgecolor="#45475a", labelcolor="#cdd6f4")
        if all_visible:
            all_x = np.concatenate([l.get_xdata() for l, _ in all_visible])
            mx = np.isfinite(all_x)
            if mx.any():
                xlo, xhi = all_x[mx].min(), all_x[mx].max()
                pad = max(abs(xhi - xlo) * 0.02, 1e-12)
                self.ax1.set_xlim(xlo - pad, xhi + pad)

        self._layout()

    def _fill_lines(self, x_arr: Optional[np.ndarray]):
        for lbl, (line, _) in self._lines.items():
            y = self._yd.get(lbl)
            if y is None: continue
            yf = y.flatten()
            if x_arr is not None:
                xf = x_arr.flatten()
                m  = np.isfinite(yf) & np.isfinite(xf)
                if m.any(): line.set_data(xf[m], yf[m])
            else:
                m = np.isfinite(yf)
                if m.any(): line.set_data(np.arange(len(yf))[m], yf[m])

    # ── Live update ───────────────────────────────────────────────────────────
    def update_point(self, ix: int, x_natural: float, vals: dict):
        if self._xd is None: return
        self._xd[ix] = x_natural
        for lbl, v in vals.items():
            if lbl in self._yd:
                self._yd[lbl][ix] = v
        self._dirty = True

    def set_xlabel(self, txt: str):
        self.ax1.set_xlabel(txt, color="#aaaacc", fontsize=self._font_pt)
        self.canvas.draw_idle()

    def clear(self):
        self.ax1.cla(); self.ax2.cla(); self._style_axes()
        self._n = 0; self._xd = None; self._yd = {}; self._lines = {}
        self._dirty = False
        if getattr(self, "_readout", None) is not None:
            self._readout.note_axes_cleared()
        self.canvas.draw_idle()
