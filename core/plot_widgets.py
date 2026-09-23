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
from plot_interact import (ClickReadout, make_fontsize_spin, eng_axis,
                           fix_toolbar_icons, make_light_export_btn,
                           set_multicolor_ylabel, make_scale_pills,
                           recent_symmetric_ylim, SCALE_RECENT,
                           RECENT_WINDOW, make_kerr_pill, set_kerr_pill)
from theme import DIVERGING_CMAPS
import kerr

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
        # Kerr-rotation display.  _data stays in the sensor's own raw unit;
        # the conversion is applied only where the image is drawn, so toggling
        # it costs a redraw and never touches the recorded values.
        self._units: Dict[str, str] = {}      # sensor label -> registry unit
        self._kerr_cal   = kerr.KerrCalibration()
        self._kerr_state = None
        self._kerr_unit: Optional[str] = None  # µrad / nrad currently shown

        # constrained_layout keeps the axes filling the figure (with the
        # colorbar) across resizes — avoids the map shrinking to a narrow strip.
        self.fig    = Figure(figsize=(6, 5), dpi=100, facecolor="#1e1e2e",
                             constrained_layout=True)
        self.ax     = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.bar    = NavToolbar(self.canvas, None)
        self.bar.setStyleSheet("background:#1e1e2e;color:white;")
        fix_toolbar_icons(self.bar)

        # Toolbar row: nav toolbar + per-view toggles
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(6)
        top.addWidget(self.bar, stretch=1)
        top.addWidget(make_light_export_btn(lambda: self.fig, self))
        self.autocolor_cb = QCheckBox("Auto color"); self.autocolor_cb.setChecked(True)
        self.autocolor_cb.setToolTip("Rescale the colour range to the data as points arrive.")
        self.autocolor_cb.setStyleSheet("color:#cdd6f4;font-size:10px;")
        self.autocolor_cb.toggled.connect(lambda _: setattr(self, "_dirty", True))
        top.addWidget(self.autocolor_cb)
        self.kerr_btn = make_kerr_pill(self._on_kerr_clicked, self)
        top.addWidget(self.kerr_btn)
        self._refresh_kerr_btn()

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

    # ── Kerr-rotation display ────────────────────────────────────────────────
    def set_sensor_units(self, units: Dict[str, str]):
        """Registry unit per sensor label — what the Kerr conversion reads."""
        self._units = dict(units or {})
        self._dirty = True

    def set_kerr_calibration(self, mv_values):
        """λ/2 calibration (six mV values) behind the θ pill."""
        self._kerr_cal = kerr.KerrCalibration(mv_values)
        self._refresh_kerr_btn()
        self._redraw_if_ready()

    def set_kerr_state(self, state):
        """Bind to the shared on/off state so every plot toggles together."""
        self._kerr_state = state
        if state is not None:
            state.subscribe(self._on_kerr_state)
        self._refresh_kerr_btn()

    def _on_kerr_clicked(self, checked: bool):
        if self._kerr_state is not None:
            self._kerr_state.set(checked)
        else:
            self._on_kerr_state(checked)

    def _on_kerr_state(self, enabled: bool):
        set_kerr_pill(self.kerr_btn, enabled)
        self._kerr_unit = None          # re-pick µrad/nrad for the new view
        self._redraw_if_ready()

    def _refresh_kerr_btn(self):
        ok = self._kerr_cal.ok
        self.kerr_btn.setEnabled(ok)
        enabled = bool(self._kerr_state.enabled) if self._kerr_state else False
        set_kerr_pill(self.kerr_btn, ok and enabled)
        self.kerr_btn.setToolTip(
            "Show the map as Kerr rotation instead of the raw voltage,\n"
            f"using the λ/2 calibration: {self._kerr_cal.describe()}."
            if ok else
            "Needs a λ/2 (BD) calibration — enter or fit one in the "
            "BD Calibration tab.")

    def _kerr_factor(self) -> Optional[float]:
        """Display multiplier for the shown sensor, or None when off."""
        if self._kerr_state is None or not self._kerr_state.enabled:
            return None
        if self._data is None:
            return None
        factors, unit = kerr.convert_group(
            [(self._data, self._units.get(self._sensor, ""))],
            self._kerr_cal, self._kerr_unit)
        self._kerr_unit = unit
        return factors[0]

    def _display_data(self) -> Tuple[Optional[np.ndarray], str]:
        """(array to draw, title) — converted to µrad/nrad when the pill is on."""
        f = self._kerr_factor()
        if f is None or self._data is None:
            return self._data, self._sensor
        title = (f"{self._sensor} ({self._kerr_unit})" if self._sensor
                 else str(self._kerr_unit))
        return self._data * f, title

    def _redraw_if_ready(self):
        if self._img is not None:
            self._dirty = True

    def _throttled_draw(self):
        if not self._dirty:
            return
        self._dirty = False
        if self._img is not None and self._data is not None:
            shown, title = self._display_data()
            if self.autocolor_cb.isChecked():
                v = shown[np.isfinite(shown)]
                if len(v) > 1:
                    lo, hi = v.min(), v.max()
                    if lo == hi: hi = lo + 1e-12
                    # Diverging colormap + signed data → centre the colour
                    # range on zero so the neutral midpoint means "no signal"
                    if self._cmap in DIVERGING_CMAPS and lo < 0.0 < hi:
                        m = max(abs(lo), abs(hi))
                        lo, hi = -m, m
                    self._img.set_clim(lo, hi)
            self._img.set_data(shown)
            if self.ax.get_title() != title:
                self.ax.set_title(title, color="#ccccff", fontsize=10)
        self.canvas.draw_idle()

    def setup(self, x_arr, y_arr, xl: str, yl: str, sensor: str, cmap: str):
        self._xarr = x_arr; self._yarr = y_arr; self._cmap = cmap
        self._sensor = sensor; self._xlbl = xl; self._ylbl = yl
        self._data = np.full((len(y_arr), len(x_arr)), np.nan)
        self._kerr_unit = None
        self._redraw()

    def _redraw(self):
        self.ax.cla(); self._style_axes()
        if self._xarr is None:
            self.canvas.draw_idle(); return
        ext = [self._xarr[0], self._xarr[-1], self._yarr[0], self._yarr[-1]]
        shown, title = self._display_data()
        self._img = self.ax.imshow(
            shown, origin="lower", aspect="auto",
            extent=ext, cmap=self._cmap, interpolation="nearest")
        if self._cb:
            try: self._cb.remove()
            except Exception: pass
        self._cb = self.fig.colorbar(self._img, ax=self.ax)
        self._cb.ax.yaxis.set_tick_params(color="#aaaacc", labelcolor="#aaaacc")
        eng_axis(self._cb.ax.yaxis)
        self.ax.set_xlabel(self._xlbl, color="#aaaacc")
        self.ax.set_ylabel(self._ylbl, color="#aaaacc")
        self.ax.set_title(title, color="#ccccff", fontsize=10)
        self.canvas.draw_idle()

    def update_point(self, ix: int, iy: int, val: float):
        if self._data is None or self._img is None: return
        self._data[iy, ix] = val
        self._dirty = True

    def switch_sensor(self, new_data: 'np.ndarray', label: str):
        if self._img is None: return
        self._data = new_data.copy(); self._sensor = label
        self._kerr_unit = None           # a different channel, different scale
        shown, title = self._display_data()
        self.ax.set_title(title, color="#ccccff", fontsize=10)
        self._dirty = True

    def set_colormap(self, cmap: str):
        self._cmap = cmap
        if self._img:
            self._img.set_cmap(cmap); self._dirty = True

    def clear(self):
        self._data = self._xarr = self._yarr = self._img = None
        self._dirty = False
        self._kerr_unit = None
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

    # The legend keeps this fixed size regardless of the Text spinbox — a
    # scaled-up legend ate half the plot; identity only needs to be legible.
    _LEGEND_PT = 9

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
        # Kerr-rotation display.  _yd keeps the raw readings in the channel's
        # own unit; the conversion is applied where the lines are drawn, so
        # the toggle never touches the buffers or the recorded data.
        self._units: Dict[str, str] = {}      # sensor label -> registry unit
        self._left_meta:  list = []           # (label, unit, colour) per axis,
        self._right_meta: list = []           # kept so labels can be redrawn
        self._kerr_cal   = kerr.KerrCalibration()
        self._kerr_state = None
        # Display unit (µrad/nrad) currently shown per scope — kept between
        # redraws so resolve_display_unit()'s hysteresis has something to hold
        # on to and a signal near 1 µrad cannot flicker the axis label.
        self._kerr_unit: Dict[str, Optional[str]] = {}

        self.fig    = Figure(figsize=(6, 4), dpi=100, facecolor="#1e1e2e")
        self.ax1    = self.fig.add_subplot(111)
        self.ax2    = self.ax1.twinx()
        self.canvas = FigureCanvas(self.fig)
        self.bar    = NavToolbar(self.canvas, None)
        self.bar.setStyleSheet("background:#1e1e2e;color:white;")
        fix_toolbar_icons(self.bar)

        # Toolbar row: nav toolbar + auto-scale toggle + text-size spinbox
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(6)
        top.addWidget(self.bar, stretch=1)
        top.addWidget(make_light_export_btn(lambda: self.fig, self))
        # Y-scale mode: Full (all data) or Recent (±max|y| of the last N pts)
        # Trailing-point count for the Recent y-scale mode; comes from
        # Setup Defaults so it is configurable without spending toolbar
        # space on a control nobody adjusts mid-measurement.
        self._recent_window = RECENT_WINDOW
        self._scale_w, self._scale_mode = make_scale_pills(
            lambda: setattr(self, "_dirty", True), self)
        top.addWidget(self._scale_w)
        self.kerr_btn = make_kerr_pill(self._on_kerr_clicked, self)
        top.addWidget(self.kerr_btn)
        self._refresh_kerr_btn()
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

        # Debounced re-layout on resize: the legend strip is reserved as a
        # figure fraction, so it must be recomputed when the canvas changes
        # size or the empty space above the plot grows with the window.
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(150)
        self._relayout_timer.timeout.connect(self._layout)
        self.canvas.mpl_connect(
            "resize_event", lambda ev: self._relayout_timer.start())

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
        # SI engineering ticks (24µ, 1.3m) instead of a 1e-5 offset at the top
        eng_axis(self.ax1.yaxis)
        eng_axis(self.ax2.yaxis)

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

    # ── Kerr-rotation display ────────────────────────────────────────────────
    def set_kerr_calibration(self, mv_values):
        """λ/2 calibration (six mV values) behind the θ pill."""
        self._kerr_cal = kerr.KerrCalibration(mv_values)
        self._refresh_kerr_btn()
        self._refresh_labels()
        self._dirty = True

    def set_kerr_state(self, state):
        """Bind to the shared on/off state so every plot toggles together."""
        self._kerr_state = state
        if state is not None:
            state.subscribe(self._on_kerr_state)
        self._refresh_kerr_btn()

    def _on_kerr_clicked(self, checked: bool):
        if self._kerr_state is not None:
            self._kerr_state.set(checked)
        else:
            self._on_kerr_state(checked)

    def _on_kerr_state(self, enabled: bool):
        set_kerr_pill(self.kerr_btn, enabled)
        self._kerr_unit = {}            # re-pick µrad/nrad for the new view
        self._refresh_labels()
        self._dirty = True

    def _refresh_kerr_btn(self):
        ok = self._kerr_cal.ok
        self.kerr_btn.setEnabled(ok)
        enabled = bool(self._kerr_state.enabled) if self._kerr_state else False
        set_kerr_pill(self.kerr_btn, ok and enabled)
        self.kerr_btn.setToolTip(
            "Show voltage channels as Kerr rotation instead of the raw\n"
            f"reading, using the λ/2 calibration: {self._kerr_cal.describe()}.\n"
            "Channels that are not a voltage (field, position, time) are\n"
            "left alone.  Recorded data is unaffected."
            if ok else
            "Needs a λ/2 (BD) calibration — enter or fit one in the "
            "BD Calibration tab.")

    def _kerr_active(self) -> bool:
        return bool(self._kerr_state is not None and self._kerr_state.enabled
                    and self._kerr_cal.ok)

    def _kerr_scaling(self) -> Tuple[Dict[str, float], Dict[str, Optional[str]]]:
        """Per-curve display multipliers, and the unit each scope shows.

        Scopes are the two y-axes and — when the x-axis plots a sensor rather
        than the actuator or time — the x-axis, each picking its own µrad/nrad
        so a large DC channel on Y1 cannot force a nulled lock-in signal on Y2
        into unreadable numbers.  Non-voltage channels get no entry and are
        drawn exactly as recorded.
        """
        if not self._kerr_active():
            return {}, {}

        factors: Dict[str, float] = {}
        units: Dict[str, Optional[str]] = {}
        scopes = [("ax1", [l for l, (_, ax) in self._lines.items() if ax is self.ax1]),
                  ("ax2", [l for l, (_, ax) in self._lines.items() if ax is self.ax2])]
        xk = self._x_key
        if xk not in (X_NATURAL, X_TIME) and xk in self._yd:
            scopes.append(("x", [xk]))

        for scope, labels in scopes:
            if not labels:
                continue
            series = [(self._yd.get(l), self._units.get(l, "")) for l in labels]
            fs, unit = kerr.convert_group(series, self._kerr_cal,
                                          self._kerr_unit.get(scope))
            units[scope] = unit
            self._kerr_unit[scope] = unit
            for lbl, f in zip(labels, fs):
                if f is not None:
                    factors[lbl] = f
        return factors, units

    def _display_unit(self, label: str, scope: str,
                      factors: Dict[str, float],
                      units: Dict[str, Optional[str]]) -> str:
        """Unit to print for *label* — the Kerr unit when it was converted."""
        if label in factors and units.get(scope):
            return str(units[scope])
        return self._units.get(label, "")

    def _refresh_labels(self):
        """Redraw the axis titles (and the x title) for the current unit."""
        if not self._lines and not self._left_meta and not self._right_meta:
            return
        factors, units = self._kerr_scaling()
        left = [(lbl, self._display_unit(lbl, "ax1", factors, units), c)
                for lbl, _u, c in self._left_meta]
        right = [(lbl, self._display_unit(lbl, "ax2", factors, units), c)
                 for lbl, _u, c in self._right_meta]
        set_multicolor_ylabel(self.ax1, left, "#89b4fa", self._font_pt)
        set_multicolor_ylabel(self.ax2, right, "#f38ba8", self._font_pt)
        xk = self._x_key
        if xk in factors:
            self.ax1.set_xlabel(f"{xk} ({units.get('x')})",
                                color="#aaaacc", fontsize=self._font_pt)
        elif xk not in (X_NATURAL, X_TIME) and xk in self._yd:
            self.ax1.set_xlabel(xk, color="#aaaacc", fontsize=self._font_pt)
        self.canvas.draw_idle()

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
                # Fill the figure to the top: axes + legend + a small pad —
                # tight_layout's own generous top margin would otherwise
                # leave a dead band above the legend.
                pad = 8.0 / fig_h
                self.fig.subplots_adjust(top=max(0.4, 1.0 - h - pad))
        except Exception:
            pass
        self.canvas.draw_idle()

    def _apply_font(self):
        """Push the current font size onto ticks, axis labels and legends."""
        for ax in [self.ax1, self.ax2]:
            ax.tick_params(labelsize=self._font_pt)
            ax.xaxis.label.set_fontsize(self._font_pt)
            ax.yaxis.label.set_fontsize(self._font_pt)
            # legend deliberately NOT scaled — fixed at _LEGEND_PT

    def _throttled_draw(self):
        if not self._dirty:
            return
        self._dirty = False

        k = self._x_key
        if   k == X_NATURAL: x_arr = self._xd
        elif k == X_TIME:    x_arr = self._yd.get(X_TIME)
        elif k in self._yd:  x_arr = self._yd[k]
        else:                x_arr = self._xd

        before = dict(self._kerr_unit)
        factors, units = self._kerr_scaling()
        if x_arr is not None and k in factors:
            x_arr = x_arr * factors[k]

        for lbl, (line, _) in self._lines.items():
            y = self._yd.get(lbl)
            if y is None: continue
            f = factors.get(lbl)
            if f is not None:
                y = y * f
            if x_arr is not None:
                m = np.isfinite(y) & np.isfinite(x_arr)
                if m.any(): line.set_data(x_arr[m], y[m])
            else:
                m = np.isfinite(y)
                if m.any(): line.set_data(np.arange(len(y))[m], y[m])

        # The µrad→nrad choice can change as the signal grows; relabel only
        # when it actually did, so the common redraw stays cheap.
        if self._kerr_unit != before:
            self._refresh_labels()

        # Autoscale.  X always follows the full data range; only the y-scale
        # rule changes with the Full/Recent pill.
        recent = self._scale_mode() == SCALE_RECENT
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
            if recent:
                lim = recent_symmetric_ylim([l.get_ydata() for l in lines],
                                            window=self._recent_window)
                if lim is not None:
                    ax.set_ylim(*lim)
                continue
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
        # Units come from the device registry via the sensor list — they decide
        # which channels the Kerr conversion may touch.
        self._units = {s["label"]: s.get("unit", "") for s in all_sensors}
        self._kerr_unit = {}

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
            self._units.setdefault(lbl, unit)
            if unit:
                self._units[lbl] = unit
            if axis == "Y2":
                c  = RIGHT_COLORS[ri % len(RIGHT_COLORS)]; ri += 1; ax = self.ax2
                right_meta.append((lbl, unit, c))
            else:
                c  = LEFT_COLORS[li % len(LEFT_COLORS)];  li += 1; ax = self.ax1
                left_meta.append((lbl, unit, c))
            line, = ax.plot([], [], color=c, linewidth=1.8,
                            label=lbl, marker=".", markersize=4)
            self._lines[lbl] = (line, ax)

        self._left_meta, self._right_meta = left_meta, right_meta
        factors, units = self._kerr_scaling()

        # Axis titles carry the sensor name(s) + unit, not just the unit —
        # and each sensor's name is drawn in its curve's color, so a shared
        # axis stays readable at a glance.  A converted channel prints the
        # Kerr unit it is actually drawn in.
        set_multicolor_ylabel(
            self.ax1, [(l, self._display_unit(l, "ax1", factors, units), c)
                       for l, _u, c in left_meta], "#89b4fa", self._font_pt)
        set_multicolor_ylabel(
            self.ax2, [(l, self._display_unit(l, "ax2", factors, units), c)
                       for l, _u, c in right_meta], "#f38ba8", self._font_pt)
        if x_key in factors:
            # Plotting one sensor against another: the x-axis is a converted
            # channel too, so say which unit it is now in.
            self.ax1.set_xlabel(f"{x_lbl} ({units.get('x')})",
                                color="#aaaacc", fontsize=self._font_pt)
            if x_arr is not None:
                x_arr = x_arr * factors[x_key]

        self._fill_lines(x_arr, factors)

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
                    loc, anchor = "lower left",  (0.0, 1.002)
                else:
                    loc, anchor = "lower right", (1.0, 1.002)
                ax.legend(
                    loc=loc, bbox_to_anchor=anchor, borderaxespad=0.0,
                    ncol=min(len(labelled), 3),
                    fontsize=self._LEGEND_PT, facecolor="#313244",
                    edgecolor="#45475a", labelcolor="#cdd6f4",
                    borderpad=0.3, labelspacing=0.3, handlelength=1.4,
                    handletextpad=0.5, columnspacing=1.0)
        if all_visible:
            all_x = np.concatenate([l.get_xdata() for l, _ in all_visible])
            mx = np.isfinite(all_x)
            if mx.any():
                xlo, xhi = all_x[mx].min(), all_x[mx].max()
                pad = max(abs(xhi - xlo) * 0.02, 1e-12)
                self.ax1.set_xlim(xlo - pad, xhi + pad)

        self._layout()

    def _fill_lines(self, x_arr: Optional[np.ndarray],
                    factors: Optional[Dict[str, float]] = None):
        factors = factors or {}
        for lbl, (line, _) in self._lines.items():
            y = self._yd.get(lbl)
            if y is None: continue
            f = factors.get(lbl)
            yf = (y * f).flatten() if f is not None else y.flatten()
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

    def show_static(self, x, y, xlabel: str = "", ylabel: str = "",
                    title: str = "", overlay=None, overlay_label: str = "fit"):
        """Draw a one-off trace (plus an optional overlay) instead of scan data.

        Used by the BD-calibration "Fit & Import" button to show the DC
        staircase with the fitted levels on top.  The scan buffers are dropped,
        so the next alloc()/apply_config() at scan start takes over cleanly —
        this view is transient by design and is replaced when a scan begins.
        """
        self.ax1.cla(); self.ax2.cla(); self._style_axes()
        self._n = 0; self._xd = None; self._yd = {}; self._lines = {}
        self._left_meta = []; self._right_meta = []; self._kerr_unit = {}
        self._dirty = False
        if getattr(self, "_readout", None) is not None:
            self._readout.note_axes_cleared()

        self.ax1.plot(np.asarray(x, dtype=float), np.asarray(y, dtype=float),
                      color=LEFT_COLORS[0], linewidth=1.0, label=ylabel or "data")
        if overlay is not None:
            ox, oy = overlay
            self.ax1.plot(np.asarray(ox, dtype=float),
                          np.asarray(oy, dtype=float),
                          color=RIGHT_COLORS[0], linewidth=2.4,
                          solid_capstyle="butt", label=overlay_label)
        self.ax1.set_xlabel(xlabel, color="#aaaacc", fontsize=self._font_pt)
        self.ax1.set_ylabel(ylabel, color=LEFT_COLORS[0], fontsize=self._font_pt)
        if title:
            self.ax1.set_title(title, color="#6c7086", fontsize=self._font_pt)
        self.ax1.legend(fontsize=self._LEGEND_PT, facecolor="#313244",
                        edgecolor="#45475a", labelcolor="#cdd6f4", loc="best")
        self.ax2.set_yticks([])
        self._layout()

    def clear(self):
        self.ax1.cla(); self.ax2.cla(); self._style_axes()
        self._n = 0; self._xd = None; self._yd = {}; self._lines = {}
        self._left_meta = []; self._right_meta = []; self._kerr_unit = {}
        self._dirty = False
        if getattr(self, "_readout", None) is not None:
            self._readout.note_axes_cleared()
        self.canvas.draw_idle()
