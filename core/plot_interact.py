"""
plot_interact.py — small, shared matplotlib interaction helpers.

Two features used by the live 1D plot (core/plot_widgets.py) and the data
browser plot (core/data_browser.py):

  * ClickReadout — left-click anywhere on a line plot to annotate the nearest
    data point (label + x/y value).  Click again to move it; a right-click or
    clear() removes it.  Ignores clicks while a nav toolbar tool (pan/zoom) is
    active so it doesn't fight the toolbar.

  * make_fontsize_spin — a compact QSpinBox for choosing the on-plot text size,
    so operators can read the numbers from across the room during alignment.

Both are deliberately dependency-light (numpy + the canvas that is already
present) and fail-soft: any error in a click handler is swallowed so a stray
click can never crash the UI.
"""
from typing import Callable, List

import numpy as np


class ClickReadout:
    """Annotate the nearest data point to a left-click on a set of axes.

    Parameters
    ----------
    canvas        : the FigureCanvas to listen on.
    axes_getter   : callable returning the list of Axes to search (so callers
                    with twin axes, or axes that are recreated, always get the
                    current ones).
    fontsize_getter : callable returning the annotation font size (points).
    """

    def __init__(self, canvas, axes_getter: Callable[[], List],
                 fontsize_getter: Callable[[], float] = lambda: 9.0):
        self._canvas   = canvas
        self._axes_get = axes_getter
        self._fs_get   = fontsize_getter
        self._ann = None
        self._cid = canvas.mpl_connect("button_press_event", self._on_click)

    # ── event handling ────────────────────────────────────────────────────────
    def _toolbar_active(self) -> bool:
        tb = getattr(self._canvas, "toolbar", None)
        # matplotlib sets toolbar.mode to '' when no pan/zoom tool is engaged
        return bool(tb is not None and getattr(tb, "mode", ""))

    def _on_click(self, event):
        try:
            if event.inaxes is None or self._toolbar_active():
                return
            if event.button != 1:          # right/middle click clears the readout
                self.clear()
                return
            best = None   # (dist2, x, y, color, ax, label)
            for ax in self._axes_get():
                for line in ax.get_lines():
                    xd = np.asarray(line.get_xdata(), dtype=float)
                    yd = np.asarray(line.get_ydata(), dtype=float)
                    if xd.size == 0:
                        continue
                    pts = ax.transData.transform(np.column_stack([xd, yd]))
                    dx = pts[:, 0] - event.x
                    dy = pts[:, 1] - event.y
                    d2 = dx * dx + dy * dy
                    if not np.isfinite(d2).any():
                        continue
                    i = int(np.nanargmin(d2))
                    if best is None or d2[i] < best[0]:
                        best = (d2[i], xd[i], yd[i],
                                line.get_color(), ax, line.get_label())
            if best is not None:
                self._show(*best[1:])
        except Exception:
            pass   # never let a stray click crash the UI

    def _show(self, x, y, color, ax, label):
        self.clear()
        head = "" if (not label or str(label).startswith("_")) else f"{label}\n"
        txt = f"{head}x = {x:.5g}\ny = {y:.5g}"
        self._ann = ax.annotate(
            txt, xy=(x, y), xytext=(10, 10), textcoords="offset points",
            fontsize=self._fs_get(), color="#0b0b12", zorder=1000,
            bbox=dict(boxstyle="round,pad=0.35", fc=color, ec="none", alpha=0.9),
            arrowprops=dict(arrowstyle="->", color=color, lw=1.2))
        self._canvas.draw_idle()

    def clear(self):
        if self._ann is not None:
            try:
                self._ann.remove()
            except Exception:
                pass
            self._ann = None
            self._canvas.draw_idle()

    def note_axes_cleared(self):
        """Call after the owner does ax.cla() — the annotation artist is already
        gone, so just drop the stale reference (no redraw)."""
        self._ann = None


def make_fontsize_spin(default: int = 9, on_change: Callable[[int], None] = None):
    """Return a compact QSpinBox (6–32 pt) for choosing on-plot text size.

    Imported lazily so this module stays import-safe without Qt (e.g. in the
    headless test environment).
    """
    from PyQt6.QtWidgets import QSpinBox
    sp = QSpinBox()
    sp.setRange(6, 32)
    sp.setValue(default)
    sp.setSuffix(" pt")
    sp.setToolTip("On-plot text size (labels, ticks, legend, readout).\n"
                  "Increase to read the plot from further away.")
    sp.setStyleSheet("color:#cdd6f4;font-size:10px;")
    if on_change is not None:
        sp.valueChanged.connect(on_change)
    return sp


def eng_axis(axis):
    """Format a matplotlib Axis with SI engineering notation (24µ, 1.3m, 5k).

    Replaces matplotlib's default scientific offset text ("1e-5" hiding at
    the axis top — a classic misreading source for µV-scale signals).  The
    prefix combines with the unit named in the axis label, so a channel in
    volts reads naturally as "24µ … V" while an already-scaled µV channel
    just shows plain numbers.
    """
    try:
        from matplotlib.ticker import EngFormatter
        axis.set_major_formatter(EngFormatter(unit="", sep=""))
    except Exception:
        pass


def fix_toolbar_icons(bar):
    """Invert the matplotlib nav-toolbar icons for dark backgrounds.

    matplotlib ships dark-gray icons that are nearly invisible on the
    Catppuccin dark toolbar; inverting the RGB channels (alpha preserved)
    turns them light gray.  Fail-soft: any error leaves the icons as-is.
    """
    try:
        from PyQt6.QtGui import QIcon, QImage, QPixmap
        for act in bar.actions():
            icon = act.icon()
            if icon is None or icon.isNull():
                continue
            pm = icon.pixmap(24, 24)
            if pm.isNull():
                continue
            img = pm.toImage()
            img.invertPixels(QImage.InvertMode.InvertRgb)
            act.setIcon(QIcon(QPixmap.fromImage(img)))
    except Exception:
        pass


# ── Light-mode figure export ─────────────────────────────────────────────────
def render_light_figure(fig):
    """Return a light-styled COPY of a dark-theme figure (original untouched).

    White background, dark ink for all text/spines/ticks, and every curve
    colour mapped from the Catppuccin Mocha pastel to its saturated Latte
    counterpart so lines stay readable on white.  Ready for papers/slides.
    Returns (figure, agg_canvas).
    """
    import pickle
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from theme import light_color_for, LIGHT_INK, LIGHT_INK_SOFT

    import matplotlib.colors as mcolors
    fig2 = pickle.loads(pickle.dumps(fig))
    canvas = FigureCanvasAgg(fig2)

    def _map_color(c):
        try:
            return light_color_for(mcolors.to_hex(c))
        except Exception:
            return c

    fig2.patch.set_facecolor("white")
    for ax in fig2.get_axes():
        ax.set_facecolor("white")
        for sp in ax.spines.values():
            sp.set_edgecolor(LIGHT_INK)
        ax.tick_params(colors=LIGHT_INK, which="both")
        for lblset in (ax.get_xticklabels(), ax.get_yticklabels()):
            for t in lblset:
                t.set_color(LIGHT_INK)
        for line in ax.get_lines():
            line.set_color(_map_color(line.get_color()))
        for coll in ax.collections:            # scatter markers etc.
            try:
                coll.set_facecolor([_map_color(c) for c in coll.get_facecolor()])
            except Exception:
                pass
        # Axis titles keep "their curve's colour" semantics via the mapping
        for lab in (ax.xaxis.label, ax.yaxis.label, ax.title):
            c = _map_color(lab.get_color())
            lab.set_color(c if str(c).startswith("#") and c not in
                          ("#aaaacc", "#ccccff", "#6c7086") else LIGHT_INK)
        ax.xaxis.get_offset_text().set_color(LIGHT_INK)
        ax.yaxis.get_offset_text().set_color(LIGHT_INK)
        leg = ax.get_legend()
        if leg is not None:
            frame = leg.get_frame()
            frame.set_facecolor("#f4f4f6"); frame.set_edgecolor("#c8c8d0")
            for t in leg.get_texts():
                t.set_color(LIGHT_INK)
            handles = getattr(leg, "legend_handles",
                              getattr(leg, "legendHandles", []))
            for lh in handles:
                try: lh.set_color(_map_color(lh.get_color()))
                except Exception: pass
        for txt in ax.texts:
            if txt.get_color() in ("#cdd6f4", "#aaaacc", "white"):
                txt.set_color(LIGHT_INK_SOFT)
    return fig2, canvas


def export_figure_light(fig, parent=None):
    """File dialog + save a light-styled copy of *fig* (PNG/PDF/SVG)."""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    path, _ = QFileDialog.getSaveFileName(
        parent, "Export figure (light style)", "",
        "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg)")
    if not path:
        return
    try:
        fig2, canvas = render_light_figure(fig)
        fig2.savefig(path, dpi=200, facecolor="white", bbox_inches="tight")
    except Exception as e:
        QMessageBox.warning(parent, "Export failed", str(e)[:300])


def make_light_export_btn(fig_getter, parent=None):
    """A compact '⬇ Light' button that exports the figure on a white
    background (for papers/slides — the on-screen dark theme prints badly)."""
    from PyQt6.QtWidgets import QPushButton
    btn = QPushButton("⬇ Light")
    btn.setToolTip("Export this plot on a white background\n"
                   "(dark curve colours mapped to print-safe ones).")
    btn.setStyleSheet(
        "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
        "border-radius:4px;padding:2px 8px;font-size:10px;}"
        "QPushButton:hover{background:#45475a;}")
    btn.clicked.connect(lambda: export_figure_light(fig_getter(), parent))
    return btn
