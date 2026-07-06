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
