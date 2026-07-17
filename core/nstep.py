"""
nstep.py — keep an N-points spinbox and a Δ-step spinbox consistent.

Both boxes stay visible and editable at all times: editing one derives the
other from the current sweep span.  When the span (start/stop) changes, the
box the user edited last — the "anchor" — keeps its value and the other box
is recomputed.  The default anchor is the STEP SIZE (the step is the
physically meaningful quantity for spatial and field scans), and a
programmatic load resets the anchor back to the step.

The number of points remains the authoritative value handed to the scan
engine: the N box always shows the exact point count the scan will use, so
after typing a step the effective step is the typed one rounded to an
integer point count.

Qt-independent in the sense that any spinbox-like objects with value() /
setValue() / valueChanged work; no PyQt import happens here.
"""


class NStepPair:
    """Bidirectional N ↔ Δ-step coupling for one sweep range.

    Parameters
    ----------
    npts_spin   : integer spinbox holding the number of points (authoritative)
    step_spin   : double spinbox holding the step size
    span_getter : callable returning the signed sweep span (stop − start)
    min_step    : lower clamp used when deriving N from a typed step
    """

    def __init__(self, npts_spin, step_spin, span_getter, min_step=1e-9):
        self._n = npts_spin
        self._s = step_spin
        self._span = span_getter
        self._min_step = float(min_step)
        self._guard = False          # suppress our own handlers, not others'
        self.anchor = "step"         # "step" or "n": preserved on span change
        npts_spin.valueChanged.connect(self._on_n_edited)
        step_spin.valueChanged.connect(self._on_s_edited)

    # ── user edits ────────────────────────────────────────────────────────────
    def _on_n_edited(self, *_):
        if self._guard:
            return
        self.anchor = "n"
        self._derive_step()

    def _on_s_edited(self, *_):
        if self._guard:
            return
        self.anchor = "step"
        self._derive_n()

    # ── derivations (guarded setValue — external listeners still fire) ────────
    def _derive_step(self):
        span = abs(float(self._span()))
        if span <= 0:
            return                   # e.g. time scans with start == stop
        n = max(2, int(self._n.value()))
        self._guard = True
        try:
            self._s.setValue(span / (n - 1))
        finally:
            self._guard = False

    def _derive_n(self):
        span = abs(float(self._span()))
        if span <= 0:
            return
        step = max(self._min_step, float(self._s.value()))
        self._guard = True
        try:
            self._n.setValue(max(2, int(round(span / step)) + 1))
        finally:
            self._guard = False

    # ── programmatic API ──────────────────────────────────────────────────────
    def span_changed(self, *_):
        """Call when start/stop change: keep the anchor, derive the other box."""
        if self._guard:
            return
        if self.anchor == "step":
            self._derive_n()
        else:
            self._derive_step()

    def set_npts(self, n):
        """Config load: set N, derive the step, reset the anchor to the step."""
        self._guard = True
        try:
            self._n.setValue(int(n))
        finally:
            self._guard = False
        self.anchor = "step"
        self._derive_step()

    def set_step(self, step):
        """Config load: set the step, derive N; the step stays the anchor."""
        self._guard = True
        try:
            self._s.setValue(float(step))
        finally:
            self._guard = False
        self.anchor = "step"
        self._derive_n()

    def npts(self) -> int:
        return max(2, int(self._n.value()))
