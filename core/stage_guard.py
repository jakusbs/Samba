"""
stage_guard.py — Samba v3 (shared core)

Watchdog against UNCOMMANDED stage motion.

Background: a SmarAct axis left in CL_RELATIVE move mode executes an absolute
position write as a *relative* move of that magnitude (the device server's
write_Position converts absolute→relative for STEP and SCAN_RELATIVE but not
for CL_RELATIVE).  The stage then walks away by the size of its own coordinate
on every write.  On the IR setup a +Z excursion of ~100 µm drives the sample
into the objective.

This module holds the *decision* logic only — no Qt, no TANGO — so it can be
unit-tested headless.  The polling and the Stop command live in the panel that
owns the stage configuration.

Design notes
------------
* Trips on two rules: a single abrupt jump between consecutive samples, and a
  slow walk that accumulates past a larger bound.  A runaway that creeps would
  otherwise never exceed the per-sample threshold.
* Commanded motion is not a fault.  Scans, autofocus and jogs announce
  themselves; the guard stands down for those axes until the move settles.
* A trip is **latched**.  Re-arming automatically would let a runaway resume
  the moment the stage stopped, which is exactly when it looks calm.
* The response is Stop, never a position write: commanding a position is what
  misfires in the first place, and in CL_RELATIVE it would add another jump.

This is a mitigation, not an interlock.  It polls over TANGO, so it cannot
outrun a fast runaway — see MAX_CATCH_NOTE below.
"""
from typing import Dict, Optional, Tuple

# Default trip threshold, in micrometres, for one polling interval.
DEFAULT_TRIP_UM = 20.0

# Cumulative uncommanded travel (µm) tolerated since the guard last armed.
# Catches a slow walk whose per-sample steps stay under the jump threshold.
DEFAULT_DRIFT_UM = 40.0

# How long after a commanded move the axis may still be moving, in seconds,
# before the guard re-arms regardless.
DEFAULT_SETTLE_S = 5.0

# Honest bound on what a polling watchdog can do: at poll interval T and stage
# speed v, the stage travels v·T between samples.  At 1 mm/s and T = 0.1 s that
# is 100 µm before the first Stop is even sent.  Hardware limits
# (UnitLimitMin/Max on the Motor) are the only real interlock.
MAX_CATCH_NOTE = ("polling watchdog: worst-case detection distance is "
                  "stage_speed x poll_interval")


def um_per_unit(unit: str) -> float:
    """Micrometres per one unit of *unit* — for converting the µm threshold
    into whatever the axis attribute actually reports.

    Getting this wrong in the safe direction matters: an unknown unit is
    treated as µm, which is what the Samba stage panel already assumes.
    """
    u = (unit or "").strip().lower()
    if u in ("nm", "nanometer", "nanometre"):
        return 1e-3
    if u in ("pm", "picometer", "picometre"):
        return 1e-6
    if u in ("mm", "millimeter", "millimetre"):
        return 1e3
    if u in ("m", "meter", "metre"):
        return 1e6
    return 1.0                      # µm, um, micrometer, or unknown


class Trip:
    """Why the guard fired.  Carries everything a log line or dialog needs."""

    __slots__ = ("axis", "kind", "delta_um", "from_pos", "to_pos", "unit")

    def __init__(self, axis, kind, delta_um, from_pos, to_pos, unit):
        self.axis = axis            # "x" | "y" | "z"
        self.kind = kind            # "jump" | "drift"
        self.delta_um = delta_um
        self.from_pos = from_pos
        self.to_pos = to_pos
        self.unit = unit

    def message(self) -> str:
        what = ("jumped" if self.kind == "jump"
                else "drifted without a commanded move")
        return (f"{self.axis.upper()} axis {what} "
                f"{self.delta_um:+.1f} µm "
                f"({self.from_pos:.3f} → {self.to_pos:.3f} {self.unit}) — "
                f"stage STOPPED")


class _AxisState:
    __slots__ = ("last_pos", "anchor_pos", "allow_until", "target")

    def __init__(self):
        self.last_pos: Optional[float] = None
        self.anchor_pos: Optional[float] = None    # position when last armed
        self.allow_until: float = 0.0              # commanded-move grace
        self.target: Optional[float] = None


class StageGuard:
    """Decides whether observed stage positions constitute a runaway.

    Feed it samples with :meth:`update`; it returns a :class:`Trip` the first
    time motion looks uncommanded, then stays latched until :meth:`reset`.
    """

    def __init__(self, trip_um: float = DEFAULT_TRIP_UM,
                 drift_um: float = DEFAULT_DRIFT_UM,
                 settle_s: float = DEFAULT_SETTLE_S,
                 units: Optional[Dict[str, str]] = None):
        self.trip_um = float(trip_um)
        self.drift_um = float(drift_um)
        self.settle_s = float(settle_s)
        self._units: Dict[str, str] = dict(units or {})
        self._ax: Dict[str, _AxisState] = {}
        self.enabled = True
        self.tripped: Optional[Trip] = None
        self._suspended = False        # scan / autofocus owns the stage

    # ── configuration ────────────────────────────────────────────────────────
    def set_units(self, units: Dict[str, str]):
        self._units = dict(units or {})

    def unit(self, axis: str) -> str:
        return self._units.get(axis, "µm")

    def _to_um(self, axis: str, delta_native: float) -> float:
        return delta_native * um_per_unit(self.unit(axis))

    # ── commanded motion ─────────────────────────────────────────────────────
    def note_commanded_move(self, axis: str, target: Optional[float],
                            now: float):
        """Samba is about to move *axis* — motion there is expected."""
        st = self._ax.setdefault(axis, _AxisState())
        st.allow_until = now + self.settle_s
        st.target = target

    def suspend(self, why: bool = True):
        """Stand down entirely (a scan or autofocus is driving the stage)."""
        self._suspended = bool(why)
        if self._suspended:
            # Forget history so re-arming compares against a fresh anchor
            # rather than the position from before the scan.
            for st in self._ax.values():
                st.last_pos = None
                st.anchor_pos = None

    def reset(self):
        """Clear a latched trip and re-anchor.  Requires a human decision."""
        self.tripped = None
        for st in self._ax.values():
            st.last_pos = None
            st.anchor_pos = None
            st.allow_until = 0.0
            st.target = None

    # ── the decision ─────────────────────────────────────────────────────────
    def update(self, positions: Dict[str, Optional[float]],
               now: float) -> Optional[Trip]:
        """Feed one sample per axis.  Returns a Trip on the transition into
        the tripped state, otherwise None (including while already tripped —
        the caller acts once)."""
        if not self.enabled or self._suspended or self.tripped is not None:
            # Still track positions so the next arm has a sane anchor.
            for axis, pos in positions.items():
                if pos is not None:
                    self._ax.setdefault(axis, _AxisState()).last_pos = pos
            return None

        for axis, pos in positions.items():
            if pos is None:
                continue
            st = self._ax.setdefault(axis, _AxisState())
            prev = st.last_pos
            st.last_pos = pos
            if st.anchor_pos is None:
                st.anchor_pos = pos
            if prev is None:
                continue

            allowed = now < st.allow_until
            if allowed:
                # Expected motion: re-anchor so the settling move is not
                # counted as drift once the allowance lapses.
                st.anchor_pos = pos
                continue

            jump_um = self._to_um(axis, pos - prev)
            if abs(jump_um) > self.trip_um:
                self.tripped = Trip(axis, "jump", jump_um, prev, pos,
                                    self.unit(axis))
                return self.tripped

            drift_um = self._to_um(axis, pos - st.anchor_pos)
            if abs(drift_um) > self.drift_um:
                self.tripped = Trip(axis, "drift", drift_um,
                                    st.anchor_pos, pos, self.unit(axis))
                return self.tripped
        return None
