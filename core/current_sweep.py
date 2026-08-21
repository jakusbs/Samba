"""
current_sweep.py — repeat a whole scanlist at several excitation currents.

Pure logic only: no Qt, no TANGO, no matplotlib — so it is unit-testable in
CI (same rule as core/bd_fit.py).  The widgets, the settle QThread and the
sequencer live in core/current_sweep_ui.py.

A current sweep runs, for every current in the list:

    set current → wait for the sample to thermalise → refocus → scanlist

One ScanlistWorker per current, so each current produces its own scanlist
.txt, its own filenames (the auto-name already carries the {I}mA token), its
own hardware snapshot and its own lab-notebook rows.  That is also exactly
what the analysis expects — import_analyze_both() takes one scanlist and one
current, and writes into <sample>/<current>mA <date>/.
"""
import math
import re
from typing import List, Optional, Sequence, Tuple

# Settle strategies (stored in the scan config as cursweep_settle_mode)
SETTLE_FIXED   = "fixed"     # wait a set time, then refocus
SETTLE_PLATEAU = "plateau"   # watch the focus signal until it stops drifting

SETTLE_MODES = (SETTLE_FIXED, SETTLE_PLATEAU)

MAX_CURRENTS = 64   # guards against a mistyped N turning into a week-long run

# Scan-config defaults.  One definition shared by both apps' config.py, the
# schema migration and CurrentSweepGroup.load_values(), so the three cannot
# drift apart.  Disabled by default: the Start button runs one plain scanlist
# exactly as before unless the operator turns the sweep on.
CURRENT_SWEEP_DEFAULTS = {
    "cursweep_enabled":         False,
    "cursweep_start_mA":        5.0,
    "cursweep_stop_mA":         15.0,
    "cursweep_npts":            3,
    "cursweep_step_mA":         5.0,
    "cursweep_settle_mode":     SETTLE_FIXED,
    "cursweep_fixed_min":       10.0,    # minutes
    "cursweep_plateau_min":     3.0,     # minutes — see PlateauDetector
    "cursweep_plateau_max":     20.0,    # minutes
    "cursweep_drift_pct_min":   0.5,     # %/min of the focus signal
    "cursweep_pause_bad_focus": False,
    "cursweep_auto_range":      True,
    "cursweep_output_off_end":  True,
}

# Autofocus, shared by the sweep and by plain scanlists.  One switch: when
# refocus_enabled is off nothing refocuses automatically at all.  The interval
# governs boundaries WITHIN a scanlist; a current change always refocuses,
# because the current step is what causes the drift.  Both paths share one
# "last focus" timestamp so they never autofocus twice in a row.
REFOCUS_DEFAULTS = {
    "refocus_enabled":   False,
    "refocus_every_min": 30.0,
    "refocus_x":         0.0,   # in the X axis' own unit
    "refocus_y":         0.0,   # in the Y axis' own unit
}


def refocus_due(last_focus_t, now, interval_min) -> bool:
    """True when a periodic refocus is owed at a scan boundary.

    `last_focus_t` is None before the first autofocus of a run, which counts
    as due; a non-positive interval disables the periodic refocus entirely
    (the per-current one is independent of it).
    """
    try:
        interval = float(interval_min) * 60.0
    except (TypeError, ValueError):
        return False
    if interval <= 0:
        return False
    if last_focus_t is None:
        return True
    return (float(now) - float(last_focus_t)) >= interval


# ─────────────────────────────────────────────────────────────────────────────
# Current list
# ─────────────────────────────────────────────────────────────────────────────
def build_current_list(start_mA: float, stop_mA: float, npts: int) -> List[float]:
    """Evenly spaced currents from start to stop, inclusive.

    Runs in the order given: start > stop simply sweeps downward.  Values are
    rounded to 4 decimals, matching the amplitude spinbox — an unrounded
    8.333333333 would also make the {I}mA filename token unstable.
    """
    n = max(1, int(npts))
    if n == 1:
        return [round(float(start_mA), 4)]
    step = (float(stop_mA) - float(start_mA)) / (n - 1)
    return [round(float(start_mA) + i * step, 4) for i in range(n)]


def format_current_list(currents: Sequence[float], max_shown: int = 6) -> str:
    """Compact human summary, e.g. '5, 7.5, 10, 12.5, 15 mA'."""
    if not currents:
        return "—"
    def _f(v):
        return f"{v:.4g}"
    if len(currents) <= max_shown:
        body = ", ".join(_f(v) for v in currents)
    else:
        head = ", ".join(_f(v) for v in currents[:max_shown - 1])
        body = f"{head}, … , {_f(currents[-1])}"
    return f"{body} mA"


# ─────────────────────────────────────────────────────────────────────────────
# Keithley range selection
# ─────────────────────────────────────────────────────────────────────────────
_RANGE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([munµ]?)\s*A\s*$", re.IGNORECASE)

_RANGE_SCALE_mA = {"": 1000.0, "m": 1.0, "u": 1e-3, "µ": 1e-3, "n": 1e-6}


def parse_range_mA(text: str) -> Optional[float]:
    """'20mA' → 20.0, '100 mA' → 100.0, '1A' → 1000.0.  None if unparseable."""
    m = _RANGE_RE.match(str(text or ""))
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    scale = _RANGE_SCALE_mA.get(m.group(2).lower())
    if scale is None:
        return None
    return value * scale


def pick_keithley_range(current_mA: float, ranges: Sequence[str]) -> Optional[str]:
    """Smallest offered range that can source |current_mA|.

    The source clips to the selected range, so a sweep crossing 2 → 20 mA has
    to move the range with it.  Returns None when nothing fits (the caller
    refuses the sweep) or when no range string could be parsed.
    """
    need = abs(float(current_mA))
    fits = []
    for r in ranges:
        v = parse_range_mA(r)
        if v is not None and v + 1e-9 >= need:
            fits.append((v, r))
    if not fits:
        return None
    fits.sort(key=lambda t: t[0])
    return fits[0][1]


def validate_sweep(currents: Sequence[float],
                   ranges: Sequence[str],
                   auto_range: bool = True,
                   fixed_range: str = "") -> Optional[str]:
    """Return an error string if this sweep cannot be run, else None."""
    if not currents:
        return "The current sweep list is empty."
    if len(currents) > MAX_CURRENTS:
        return (f"{len(currents)} currents requested — the limit is "
                f"{MAX_CURRENTS}.  Reduce N or narrow the range.")
    if auto_range:
        bad = [c for c in currents if pick_keithley_range(c, ranges) is None]
        if bad:
            biggest = max(abs(float(c)) for c in bad)
            top = max((parse_range_mA(r) or 0.0) for r in ranges) if ranges else 0.0
            return (f"{format_current_list(sorted(set(bad)))} exceeds the largest "
                    f"Keithley range ({top:g} mA).  Highest requested: {biggest:g} mA.")
    else:
        limit = parse_range_mA(fixed_range)
        if limit is None:
            return f"Cannot interpret the Keithley range '{fixed_range}'."
        over = [c for c in currents if abs(float(c)) > limit + 1e-9]
        if over:
            return (f"{format_current_list(sorted(set(over)))} exceeds the selected "
                    f"{fixed_range} range.  Enable auto-range or pick a larger one.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Plateau detection
# ─────────────────────────────────────────────────────────────────────────────
class PlateauDetector:
    """Decide when a drifting signal has stopped changing.

    Feed it (t, value) samples of the focus diode while the sample warms up
    to the new current; state() reports the drift rate over the trailing
    `window_s` seconds as a percentage of the signal magnitude per minute.

    "Settled" requires all of:
      * `min_wait_s` elapsed since the first sample.  This bound is NOT
        optional: right after a refocus the FL signal sits on its maximum,
        where dFL/dz ≈ 0, so the first minutes of drift barely move it and an
        unbounded detector would report a plateau while the stage is still
        running.
      * a full `window_s` of samples (at least `min_samples` of them).
      * |rate| <= `tol_pct_per_min`.

    At `max_wait_s` it gives up and reports settled with reason "timeout", so
    a noisy or dead FL signal costs a bounded wait rather than a stalled run.
    """

    def __init__(self, window_s: float = 60.0,
                 tol_pct_per_min: float = 0.5,
                 min_wait_s: float = 180.0,
                 max_wait_s: float = 1200.0,
                 min_samples: int = 5):
        self.window_s       = max(1.0, float(window_s))
        self.tol            = abs(float(tol_pct_per_min))
        self.min_wait_s     = max(0.0, float(min_wait_s))
        self.max_wait_s     = max(self.min_wait_s, float(max_wait_s))
        self.min_samples    = max(3, int(min_samples))
        self._t: List[float] = []
        self._v: List[float] = []

    # ── input ────────────────────────────────────────────────────────────────
    def add(self, t: float, value: float) -> bool:
        """Append one sample.  Non-finite readings are dropped (returns False)."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(v):
            return False
        self._t.append(float(t))
        self._v.append(v)
        return True

    @property
    def t0(self) -> Optional[float]:
        return self._t[0] if self._t else None

    def history(self) -> Tuple[List[float], List[float]]:
        return list(self._t), list(self._v)

    # ── evaluation ───────────────────────────────────────────────────────────
    def _window(self, now: float):
        cutoff = now - self.window_s
        idx = [i for i, t in enumerate(self._t) if t >= cutoff]
        return [self._t[i] for i in idx], [self._v[i] for i in idx]

    def rate_pct_per_min(self, now: float) -> Optional[float]:
        """Least-squares drift over the trailing window, in %/min.

        A slope fit rather than first-vs-last: with a 5 s poll the window
        holds ~12 samples and a single noisy endpoint would otherwise decide
        the verdict.
        """
        tw, vw = self._window(now)
        if len(tw) < self.min_samples:
            return None
        if (tw[-1] - tw[0]) < 0.5 * self.window_s:
            return None
        n = len(tw)
        mt = sum(tw) / n
        mv = sum(vw) / n
        sxx = sum((t - mt) ** 2 for t in tw)
        if sxx <= 0:
            return None
        sxy = sum((t - mt) * (v - mv) for t, v in zip(tw, vw))
        slope = sxy / sxx                       # value units per second
        # Normalise by the signal magnitude so the tolerance is unit-free.
        # An FL diode reads a positive intensity well away from zero; if it
        # really is ~0 the sensor is broken, and the max-wait bound below is
        # what ends the wait rather than a meaningless percentage.
        scale = max(abs(mv), 1e-12)
        return slope * 60.0 / scale * 100.0

    def state(self, now: float) -> dict:
        """Verdict dict: settled, reason, rate, elapsed, remaining_min_wait."""
        if not self._t:
            return {"settled": False, "reason": "no data", "rate": None,
                    "elapsed": 0.0, "min_wait_left": self.min_wait_s}
        elapsed = now - self._t[0]
        rate = self.rate_pct_per_min(now)
        if elapsed >= self.max_wait_s:
            return {"settled": True, "reason": "timeout", "rate": rate,
                    "elapsed": elapsed, "min_wait_left": 0.0}
        min_left = max(0.0, self.min_wait_s - elapsed)
        if min_left > 0:
            return {"settled": False, "reason": "minimum wait", "rate": rate,
                    "elapsed": elapsed, "min_wait_left": min_left}
        if rate is None:
            return {"settled": False, "reason": "collecting", "rate": None,
                    "elapsed": elapsed, "min_wait_left": 0.0}
        if abs(rate) <= self.tol:
            return {"settled": True, "reason": "plateau", "rate": rate,
                    "elapsed": elapsed, "min_wait_left": 0.0}
        return {"settled": False, "reason": "drifting", "rate": rate,
                "elapsed": elapsed, "min_wait_left": 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# Timing estimate
# ─────────────────────────────────────────────────────────────────────────────
def fmt_hms(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def settle_estimate_s(mode: str, fixed_min: float, plateau_min_min: float,
                      plateau_max_min: float) -> float:
    """Per-current settle time used by the pre-scan estimate.

    Plateau mode is bounded but not predictable, so the midpoint of its
    min/max window is used — the estimate says so rather than pretending the
    minimum is what will happen.
    """
    if mode == SETTLE_PLATEAU:
        return 60.0 * (float(plateau_min_min) + float(plateau_max_min)) / 2.0
    return 60.0 * float(fixed_min)
