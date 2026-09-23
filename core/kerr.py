"""
kerr.py — Samba v3 (shared core)

Kerr-rotation display conversion: turn a measured voltage into an angle.

The λ/2 (BD) calibration recorded on every scan is six balanced-diode readings
(mV) at micrometer-screw ticks 0, 5, 10, 15, 20, 25.  A straight-line fit of
those against the *optical* angle gives a slope in mV/deg; its reciprocal,
converted to radians and scaled, is the number the analysis calls ``sln``:

    sln [µrad/mV] = (1 / slope [mV/deg]) · π/180 · 1e6

This module owns that arithmetic plus the two things the plots need on top of
it: how to get from a channel's *own* unit (the device registry's "µV", "V", …)
to the mV the slope is expressed in, and when to show nrad instead of µrad.

Deliberately free of Qt, matplotlib and h5py — like core/bd_fit.py and
core/current_sweep.py — so it can be unit-tested in the headless CI
environment (numpy only).  The widgets in core/plot_widgets.py and
core/data_browser.py own the UI; everything numeric lives here.
"""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

# Micrometer-screw tick positions of the λ/2 sweep — the x of the slope fit.
CALIB_TICKS = np.linspace(0.0, 25.0, 6)

# Ticks → mechanical degrees (100 ticks per 4 degrees of screw travel), then
# ×2 because a half-wave plate rotates the polarisation by twice its own angle.
_TICKS_PER_DEG = 100.0 / 4.0
_HWP_FACTOR = 2.0

# Display units.  µrad is the natural scale for a MOKE Kerr rotation; a signal
# being nulled runs well below 1 µrad, where µrad values turn into a column of
# zeros, so the axis switches to nrad.
URAD = "µrad"
NRAD = "nrad"
_DISPLAY_SCALE = {URAD: 1.0, NRAD: 1000.0}

# Switch to nrad below this many µrad …
NRAD_THRESHOLD = 1.0
# … and back to µrad only at this many, so a signal sitting on the boundary
# does not make the axis label flicker between the two every redraw.
URAD_THRESHOLD = 2.0

# Channel units that are a voltage, and what multiplies them into mV.
# Both micro signs in circulation (U+00B5 MICRO SIGN, U+03BC GREEK SMALL MU)
# are normalised to "u" before lookup.
_UNIT_TO_MV: Dict[str, float] = {
    "kv": 1e6,
    "v":  1e3,
    "mv": 1.0,
    "uv": 1e-3,
    "nv": 1e-6,
}


def _normalise_unit(unit) -> str:
    """Lower-cased unit with both micro signs folded onto ASCII "u"."""
    return (str(unit or "").strip().lower()
            .replace("µ", "u")      # MICRO SIGN
            .replace("μ", "u"))     # GREEK SMALL LETTER MU


def unit_to_mV(unit) -> Optional[float]:
    """Factor turning a value in *unit* into mV, or None if it is not a voltage.

    The unit comes from the device registry (live plots) or the HDF5 dataset's
    ``unit`` attribute (data browser), so a channel that is not a voltage —
    a field in mT, a position in nm, a time in s — returns None and is left
    alone rather than being silently scaled by a Kerr calibration.
    """
    return _UNIT_TO_MV.get(_normalise_unit(unit))


def is_voltage_unit(unit) -> bool:
    """True when *unit* is one this module knows how to convert."""
    return unit_to_mV(unit) is not None


def calibration_slope(mv_values: Optional[Sequence[float]]) -> Optional[float]:
    """µrad per mV from the λ/2 sweep, or None when it cannot be determined.

    *mv_values* are the balanced-diode readings (mV) at the first
    ``len(mv_values)`` tick positions — the six numbers of the BD Calibration
    tab, or the ``/data/calibration`` dataset of a scan file.

    **The sign is kept.**  Turning the plate the other way reverses the
    staircase and flips the slope, and 40 of the lab's 54 fittable calibration
    files descend.  Keeping the sign matches ``Analysis/analyze_samba.py``,
    which multiplies the raw signal by this same signed number — a display that
    silently took the magnitude would disagree with the offline analysis about
    which way is positive.
    """
    if mv_values is None:
        return None
    y = np.asarray(mv_values, dtype=float).ravel()
    if y.size < 2 or y.size > CALIB_TICKS.size:
        return None
    if not np.all(np.isfinite(y)) or np.allclose(y, 0.0):
        return None

    # A calibration whose six values are all the same carries no slope.  The
    # fit does not return exactly zero for it — float noise leaves ~1e-19, and
    # 1/that is a factor of 1e19 that would silently rescale every plot — so
    # refuse on the values themselves before dividing.
    span = float(np.ptp(y))
    if span <= 1e-9 * max(float(np.max(np.abs(y))), 1e-12):
        return None

    x_deg = CALIB_TICKS[:y.size] / _TICKS_PER_DEG * _HWP_FACTOR
    try:
        slope = float(np.polyfit(x_deg, y, 1)[0])      # mV/deg
    except Exception:
        return None
    if slope == 0.0 or not np.isfinite(slope):
        return None

    # The signal must actually trend with the tick position.  A set of values
    # that wanders up and back down (a mis-picked run, or the plate turned
    # past the end and back) fits a near-flat line through a large spread, and
    # its reciprocal is meaninglessly large.
    if abs(slope) * float(x_deg[-1] - x_deg[0]) < 0.1 * span:
        return None

    return (1.0 / slope) * np.pi / 180.0 * 1e6


def resolve_display_unit(max_abs_urad: float,
                         current: Optional[str] = None) -> str:
    """Pick µrad or nrad for a signal peaking at *max_abs_urad* µrad.

    *current* is the unit the axis is already showing; passing it applies the
    hysteresis band (NRAD_THRESHOLD … URAD_THRESHOLD) so a live plot does not
    relabel its axis on every redraw while the signal hovers around 1 µrad.
    """
    if max_abs_urad is None or not np.isfinite(max_abs_urad) or max_abs_urad <= 0.0:
        return current or URAD
    if current == NRAD:
        return URAD if max_abs_urad >= URAD_THRESHOLD else NRAD
    if current == URAD:
        return NRAD if max_abs_urad < NRAD_THRESHOLD else URAD
    return NRAD if max_abs_urad < NRAD_THRESHOLD else URAD


def display_scale(display_unit: str) -> float:
    """µrad → *display_unit* multiplier (1 for µrad, 1000 for nrad)."""
    return _DISPLAY_SCALE.get(display_unit, 1.0)


class KerrCalibration:
    """One λ/2 calibration, ready to convert any voltage channel to µrad.

    Construct from the six mV values; ``ok`` says whether they yielded a usable
    slope.  ``factor(unit)`` returns µrad per raw unit for a channel, or None
    when that channel is not a voltage (and so must not be converted).
    """

    __slots__ = ("mv", "sln")

    def __init__(self, mv_values: Optional[Sequence[float]] = None):
        self.mv: List[float] = ([float(v) for v in np.ravel(mv_values)]
                                if mv_values is not None else [])
        self.sln: Optional[float] = calibration_slope(mv_values)

    @property
    def ok(self) -> bool:
        return self.sln is not None

    def factor(self, unit) -> Optional[float]:
        """µrad per one raw *unit*, or None when no conversion applies."""
        if self.sln is None:
            return None
        to_mv = unit_to_mV(unit)
        if to_mv is None:
            return None
        return to_mv * self.sln

    def describe(self) -> str:
        """One line for a tooltip / status message."""
        if self.sln is None:
            return "no usable λ/2 calibration"
        mv = ", ".join(f"{v:.2f}" for v in self.mv)
        return (f"{self.sln:+.4g} µrad/mV  (λ/2 sweep: {mv} mV at ticks "
                f"0, 5, 10, 15, 20, 25)")

    def __repr__(self):
        return f"KerrCalibration(sln={self.sln!r})"


def convert_group(series: Iterable[Tuple[np.ndarray, str]],
                  cal: KerrCalibration,
                  current_unit: Optional[str] = None
                  ) -> Tuple[List[Optional[float]], Optional[str]]:
    """Scale factors for a set of curves that share one axis.

    *series* is an iterable of ``(values, unit)``.  Curves whose unit is not a
    voltage get ``None`` (leave them alone); the rest get one multiplier that
    already includes the µrad→nrad choice, which is made **once for the whole
    group** from the largest converted magnitude so every curve on the axis
    reads in the same unit.

    Returns ``(factors, display_unit)``; ``display_unit`` is None when nothing
    in the group is convertible.
    """
    items = list(series)
    urad_factors: List[Optional[float]] = [cal.factor(u) for _, u in items]
    if not any(f is not None for f in urad_factors):
        return urad_factors, None

    peak = 0.0
    for (vals, _), f in zip(items, urad_factors):
        if f is None or vals is None:
            continue
        arr = np.asarray(vals, dtype=float)
        if arr.size == 0:
            continue
        finite = arr[np.isfinite(arr)]
        if finite.size:
            peak = max(peak, float(np.max(np.abs(finite))) * abs(f))

    unit = resolve_display_unit(peak, current_unit)
    scale = display_scale(unit)
    return [None if f is None else f * scale for f in urad_factors], unit


class KerrDisplayState:
    """Shared on/off state for the µrad display toggle.

    Every plot that can show Kerr rotation holds a pill bound to one of these,
    so flipping the toggle on the 1D plot flips it on the 2D map and in the
    data browser too.  Observers are plain callables — no Qt here, so the
    module stays importable (and testable) headless.
    """

    def __init__(self, enabled: bool = False):
        self._enabled = bool(enabled)
        self._subs: List = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def subscribe(self, cb) -> None:
        """Register ``cb(enabled)``, called on every genuine change."""
        if cb not in self._subs:
            self._subs.append(cb)

    def set(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        for cb in list(self._subs):
            try:
                cb(enabled)
            except Exception:
                # A broken observer (a widget torn down mid-notify) must never
                # take the toggle — or the other plots — down with it.
                pass
