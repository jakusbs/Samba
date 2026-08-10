"""
bd_fit.py — Samba v3 (shared core)

Step-fitting for the λ/2 (BD) plate calibration.

A calibration measurement is a TIME scan in which the operator steps the λ/2
plate through its tick positions, holding at each one.  The DC channel is
therefore a staircase: flat plateaus separated by fast transitions.  This
module turns such a trace into the six plateau levels the BD Calibration tab
wants, without any Qt or matplotlib dependency, so it can be unit-tested in
the headless CI environment (numpy + h5py only).

The panel (core/bd_calibration.py) owns the UI; everything numeric lives here.
"""
import os
import re
from typing import List, Optional, Tuple

import numpy as np

# Number of λ/2 tick positions the calibration records (ticks 0,5,10,15,20,25)
N_LEVELS = 6

# Fraction of each plateau, at its END, that is averaged into the level.  The
# operator has just stopped turning the plate there, so the tail is the
# settled part; the head still contains the approach.
TAIL_FRAC = 0.25

# A plateau shorter than this many samples is treated as part of a transition
# rather than a real hold.
MIN_PLATEAU_PTS = 3

# Two neighbouring plateaus closer together than this fraction of the typical
# step are treated as one hold that got split by a glitch.
SPLIT_MERGE_FRAC = 0.15

# How uneven the six steps may be before the run is rejected, as a coefficient
# of variation.  Turning the plate by a fixed tick gives a nearly constant
# change: measured across the real calibration files on the lab machine,
# genuine sweeps come out at 1-8 % while traces that are really flat (and get
# carved into six look-alike plateaus) land at 31-49 %.
MAX_SPACING_CV = 0.20

# The step between ticks must also be large compared with the noise.  A flat
# trace with a slow drift can produce six evenly spaced "levels" a few tens of
# µV apart, which would pass the uniformity test alone.  Real sweeps measure
# 250-420 sigma per step; the flat ones, 0.5-9.
MIN_STEP_SIGMA = 20.0

# Calibration TIME-scan filenames look like
#   103813_TIME_W_15_2e_calibration.h5
# i.e. HHMMSS, the TIME scan marker, then a name ending in "calibration".
TIME_CAL_RE = re.compile(r"^\d{6}_TIME_.*calibration\.h5$", re.IGNORECASE)


def is_time_calibration(path: str) -> bool:
    """True if *path*'s basename is a TIME …_calibration.h5 file."""
    return bool(TIME_CAL_RE.match(os.path.basename(path or "")))


def latest_h5(folder: str) -> Optional[str]:
    """Newest *.h5 in *folder* by modification time, or None."""
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.lower().endswith(".h5")]
    except OSError:
        return None
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        return None
    return max(files, key=lambda f: os.path.getmtime(f))


def load_dc_time(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Read (time [s], DC [V]) from a Samba TIME-scan HDF5 file.

    Only finite samples present in BOTH channels are returned, in time order.
    Raises ValueError with a readable message if the file lacks the channels.
    """
    import h5py                      # local: keeps import cost off the UI path
    with h5py.File(path, "r") as f:
        data = f.get("data")
        if data is None:
            raise ValueError("no /data group — not a Samba scan file")
        if "DC" not in data:
            raise ValueError("no DC channel in this file "
                             f"(has: {', '.join(sorted(data.keys()))})")
        if "time" not in data:
            raise ValueError("no time axis — not a TIME scan")
        dc = np.asarray(data["DC"][:], dtype=float).ravel()
        t = np.asarray(data["time"][:], dtype=float).ravel()
    n = min(t.size, dc.size)
    t, dc = t[:n], dc[:n]
    m = np.isfinite(t) & np.isfinite(dc)
    t, dc = t[m], dc[m]
    order = np.argsort(t)
    return t[order], dc[order]


class Plateau:
    """One flat hold in the staircase."""

    __slots__ = ("i0", "i1", "t0", "t1", "value")

    def __init__(self, i0: int, i1: int, t0: float, t1: float, value: float):
        self.i0, self.i1 = i0, i1        # inclusive sample index range
        self.t0, self.t1 = t0, t1        # seconds
        self.value = value               # volts

    def __repr__(self):
        return (f"Plateau({self.t0:.2f}-{self.t1:.2f}s, "
                f"{self.value*1000:.4g} mV, n={self.i1 - self.i0 + 1})")


def _noise_sigma(dc: np.ndarray) -> float:
    """Per-sample noise σ from the MAD of the differences (σ_diff = √2·σ).

    The MAD is used rather than a std because the step edges cannot inflate
    it — a std would be dragged up by the very transitions being measured.
    """
    if dc.size < 2:
        return 0.0
    d = np.diff(dc)
    mad = float(np.median(np.abs(d - float(np.median(d)))))
    return (1.4826 * mad) / np.sqrt(2.0)


def detect_plateaus(t: np.ndarray, dc: np.ndarray,
                    min_pts: int = MIN_PLATEAU_PTS,
                    tail_frac: float = TAIL_FRAC) -> List[Plateau]:
    """Split a staircase trace into its flat sections.

    A plateau is found by **flatness over a window**, not by per-sample jumps.
    That distinction matters: the operator turns the λ/2 plate by hand, so a
    transition is a ramp spread over many samples.  Its per-sample change is
    the step height divided by the ramp length, which for a realistic turn
    falls *below* the noise floor — a difference-threshold detector then walks
    straight through the transition and merges several ticks into one plateau.
    A window that straddles the ramp, by contrast, always shows a large spread.

    The noise scale comes from a median-absolute-deviation of the sample
    differences, which the step edges cannot inflate (a plain std would be
    dragged up by the very transitions we are trying to see).
    """
    n = dc.size
    if t.size < 2 or n != t.size:
        return []

    sigma = _noise_sigma(dc)
    if sigma <= 0.0:                    # perfectly clean (synthetic) trace
        sigma = max(float(np.max(dc) - np.min(dc)), 1e-15) * 1e-4

    # Window: long enough to straddle a ramp, short enough to fit inside the
    # shortest realistic hold.  Odd, so it has a well-defined centre sample.
    w = int(np.clip(n // 100, 5, 101))
    if w % 2 == 0:
        w += 1
    if n < w:
        w = max(3, (n // 2) * 2 - 1)
    if n < w or w < 3:
        return []

    # Flat where the peak-to-peak spread across the window is consistent with
    # noise alone.  The expected range of w normal samples grows like
    # √(2·ln w), so the threshold tracks the window length instead of being a
    # fixed multiple that is too tight for long windows.
    win = np.lib.stride_tricks.sliding_window_view(dc, w)
    spread = win.max(axis=1) - win.min(axis=1)
    thresh = sigma * (2.0 + 2.0 * np.sqrt(2.0 * np.log(w)))

    flat = np.zeros(n, dtype=bool)
    # Attribute each window's verdict to its centre sample.  This erodes each
    # plateau by w//2 at both ends, which is harmless — better, it keeps the
    # ramp shoulders out of the averaged tail.
    flat[w // 2: w // 2 + spread.size] = spread < thresh

    # A genuine hold is at least as long as the window used to judge it.
    # Without this, the middle of a very slow ramp — where the window spread
    # briefly drops below threshold — is emitted as a run of tiny "plateaus".
    need = max(min_pts, w)

    plateaus: List[Plateau] = []
    i = 0
    while i < n:
        if not flat[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flat[j + 1]:
            j += 1
        if (j - i + 1) >= need:
            seg = dc[i:j + 1]
            k = max(1, int(round(seg.size * tail_frac)))
            plateaus.append(Plateau(i, j, float(t[i]), float(t[j]),
                                    float(np.mean(seg[-k:]))))
        i = j + 1
    return plateaus


class FitResult:
    """Outcome of a calibration fit.

    ``ok`` is the only thing callers should branch on; ``reason`` explains a
    failure in words suitable for a dialog.
    """

    __slots__ = ("ok", "reason", "levels_V", "selected", "all_plateaus",
                 "t", "dc", "path", "spacing_cv", "mean_step_V")

    def __init__(self, ok, reason, levels_V, selected, all_plateaus,
                 t, dc, path="", spacing_cv=float("nan"), mean_step_V=0.0):
        self.ok = ok
        self.reason = reason
        self.levels_V = levels_V          # list[float], time-ordered
        self.selected = selected          # list[Plateau], time-ordered
        self.all_plateaus = all_plateaus  # list[Plateau], every one found
        self.t, self.dc = t, dc           # the raw trace (for plotting)
        self.path = path
        self.spacing_cv = spacing_cv      # step uniformity of the chosen run
        self.mean_step_V = mean_step_V    # mean step between ticks [V]

    @property
    def levels_mV(self) -> List[float]:
        """The values the calibration boxes want (V → mV)."""
        return [v * 1000.0 for v in self.levels_V]

    def step_curve(self) -> Tuple[np.ndarray, np.ndarray]:
        """(x, y) tracing the fitted level across each selected plateau.

        Returned as one polyline per plateau joined by NaN, so a single
        plot call draws the fitted levels without connecting across the
        gaps between holds.
        """
        xs: List[float] = []
        ys: List[float] = []
        for p in self.selected:
            xs.extend([p.t0, p.t1, np.nan])
            ys.extend([p.value, p.value, np.nan])
        return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


def merge_split_holds(plateaus: List[Plateau],
                      factor: float = SPLIT_MERGE_FRAC) -> List[Plateau]:
    """Rejoin a single hold that got split into look-alike fragments.

    A brief disturbance mid-hold (someone nudging the table, a readout
    glitch) breaks one plateau into two at almost the same level.  Left
    alone, that shifts every later plateau in the consecutive-run search by
    one.  Fragments are recognised by being far closer together than the
    typical step between neighbouring plateaus; the merged level is taken
    from the later fragment, whose tail is the most settled.
    """
    if len(plateaus) < 2:
        return list(plateaus)
    diffs = np.abs(np.diff([p.value for p in plateaus]))
    med = float(np.median(diffs))
    if med <= 0.0:
        return list(plateaus)
    thr = factor * med
    out = [plateaus[0]]
    for p in plateaus[1:]:
        q = out[-1]
        if abs(p.value - q.value) < thr:
            out[-1] = Plateau(q.i0, p.i1, q.t0, p.t1, p.value)
        else:
            out.append(p)
    return out


def _spacing_cv(values: List[float]) -> Tuple[float, float]:
    """(coefficient of variation, mean step) of successive differences.

    A perfectly even staircase gives 0.  Sign changes push the mean towards
    zero and the CV up, so a run that turns around scores badly without
    needing a separate monotonicity rule.
    """
    d = np.diff(np.asarray(values, dtype=float))
    if d.size == 0:
        return float("inf"), 0.0
    mean = float(np.mean(d))
    if abs(mean) < 1e-15:
        return float("inf"), mean
    return float(np.std(d)) / abs(mean), mean


def fit_calibration(t: np.ndarray, dc: np.ndarray, path: str = "",
                    n_levels: int = N_LEVELS,
                    tail_frac: float = TAIL_FRAC) -> FitResult:
    """Fit *n_levels* plateau levels out of a staircase DC trace.

    A recording usually contains more holds than tick positions: the operator
    parks the plate before starting and again after finishing, and those
    holds sit at arbitrary levels.  The tick positions are instead recognised
    by being **consecutive in time** and **evenly spaced in value** — turning
    the plate by a fixed tick increment changes the signal by a nearly
    constant amount.  So every run of *n_levels* neighbouring plateaus is
    scored by how uniform its steps are, and the most uniform run wins.

    (An earlier rule kept the plateaus closest to 0 V.  On real data that
    picks the pre- and post-sweep parking holds and drops genuine ticks —
    the sweep is not centred on zero.)
    """
    found = detect_plateaus(t, dc, tail_frac=tail_frac)
    plateaus = merge_split_holds(found)
    if len(plateaus) < n_levels:
        return FitResult(
            False,
            f"found only {len(plateaus)} plateau(s), need {n_levels}",
            [], [], found, t, dc, path)

    # Fragmentation guard.  When the transitions are as slow as the holds
    # there is no staircase to speak of, and the detector emits a long run of
    # look-alike plateaus carved out of the ramps.  Picking six of those would
    # return plausible-looking but meaningless numbers, so refuse instead —
    # a wrong calibration is far worse than no calibration.
    if len(plateaus) > 4 * n_levels:
        return FitResult(
            False,
            f"found {len(plateaus)} plateaus — too fragmented to be a "
            f"{n_levels}-step staircase (transitions as slow as the holds?)",
            [], [], found, t, dc, path)

    best_cv, best_step, best = float("inf"), 0.0, None
    for i in range(len(plateaus) - n_levels + 1):
        win = plateaus[i:i + n_levels]
        cv, mean = _spacing_cv([p.value for p in win])
        if cv < best_cv:
            best_cv, best_step, best = cv, mean, win

    if best is None or not np.isfinite(best_cv):
        return FitResult(False,
                         "no run of evenly spaced plateaus found",
                         [], [], found, t, dc, path)
    sigma = _noise_sigma(dc)
    if sigma > 0.0 and abs(best_step) < MIN_STEP_SIGMA * sigma:
        return FitResult(
            False,
            f"the steps between plateaus ({abs(best_step)*1000:.3g} mV) are "
            f"comparable to the noise ({sigma*1000:.3g} mV) — the signal is "
            "essentially flat, not a λ/2 staircase",
            [], [], found, t, dc, path)
    if best_cv > MAX_SPACING_CV:
        return FitResult(
            False,
            f"the {n_levels} best consecutive plateaus are unevenly spaced "
            f"(steps vary by {best_cv*100:.0f} %, mean {best_step*1000:+.2f} mV)"
            " — this does not look like a λ/2 sweep",
            [], [], found, t, dc, path)

    return FitResult(True, "", [p.value for p in best], list(best),
                     found, t, dc, path, spacing_cv=best_cv,
                     mean_step_V=best_step)


def fit_file(path: str, n_levels: int = N_LEVELS,
             tail_frac: float = TAIL_FRAC) -> FitResult:
    """Load a TIME-scan file and fit it.  Raises only on unreadable files."""
    t, dc = load_dc_time(path)
    return fit_calibration(t, dc, path=path, n_levels=n_levels,
                           tail_frac=tail_frac)
