"""
validation.py — Samba (shared core)

Pre-scan sanity checks on a fully-built scan config.

Ported from Cryo (which had `_validate_scan_config`; Samba_main had nothing)
so both applications refuse the same nonsensical scans, and extended with
per-axis soft travel limits.

Checks are intentionally conservative: only values that would cause an
immediate problem — a hang, an out-of-memory allocation, nonsensical geometry,
or driving a stage past its configured travel.  Anything a physicist might
legitimately want stays allowed.
"""
from typing import Optional

MAX_POINTS_1D = 10_000
MAX_POINTS_2D = 500_000     # 1000×500 ≈ a generous upper bound for spatial maps
MAX_CYCLES    = 10_000      # DC-hyst cycles; the PLC loop cannot be paused


def _limits(setup: dict, pfx: str):
    """Return (min, max) soft travel limits for axis `pfx`, or None if unset.

    Limits are optional per-setup keys (`act1_min` / `act1_max`, …).  A setup
    that has never defined them behaves exactly as before.
    """
    lo = setup.get(f"{pfx}_min")
    hi = setup.get(f"{pfx}_max")
    if lo is None or hi is None:
        return None
    try:
        lo, hi = float(lo), float(hi)
    except (TypeError, ValueError):
        return None
    if hi <= lo:
        return None
    return lo, hi


def validate_scan_config(cfg: dict, setup: Optional[dict] = None) -> Optional[str]:
    """Validate scan parameters before starting.

    Returns an error string if the config is invalid, or None if it is OK.
    """
    setup = setup or {}
    scan_type = cfg.get("scan_type", "SPATIAL")

    if scan_type in ("SPATIAL", "TR_MOKE"):
        n_x = int(cfg.get("act1_npts", 1))
        n_y = int(cfg.get("act2_npts", 1))
        scan_2d = cfg.get("scan_x", True) and cfg.get("scan_y", False)

        if n_x < 1:
            return f"X points must be ≥ 1 (got {n_x})."
        if n_y < 1:
            return f"Y points must be ≥ 1 (got {n_y})."
        if n_x > MAX_POINTS_1D:
            return (f"X points ({n_x:,}) exceeds the safety limit of "
                    f"{MAX_POINTS_1D:,}.")
        total = n_x * n_y if scan_2d else n_x
        if total > MAX_POINTS_2D:
            return (f"Total scan points ({total:,}) = {n_x}×{n_y} exceeds the "
                    f"safety limit of {MAX_POINTS_2D:,}.\n"
                    "Reduce n_pts or scan range.")

        # Soft travel limits — a mistyped stop position is the cheapest way to
        # drive a stage into the sample holder.  TR-MOKE sweeps a delay
        # generator, not a stage, so it is exempt.
        if scan_type == "SPATIAL":
            for pfx, scan_key, axis in (("act1", "scan_x", "X"),
                                        ("act2", "scan_y", "Y")):
                if not cfg.get(scan_key):
                    continue
                lim = _limits(setup, pfx)
                if lim is None:
                    continue
                lo, hi = lim
                unit = cfg.get(f"{pfx}_unit", "")
                for edge in ("start", "stop"):
                    try:
                        v = float(cfg.get(f"{pfx}_{edge}", 0.0))
                    except (TypeError, ValueError):
                        continue
                    if not (lo <= v <= hi):
                        return (f"{axis} {edge} = {v:g} {unit} is outside the "
                                f"configured travel limits "
                                f"[{lo:g}, {hi:g}] {unit}.\n"
                                "Fix the range, or adjust the limits in "
                                "Setup Defaults.")

    elif scan_type == "FIELD":
        segs = cfg.get("field_segments", []) or []
        total_field_pts = sum(int(s[2]) for s in segs if len(s) >= 3)
        if total_field_pts < 2:
            return "Field scan requires at least 2 points."
        if total_field_pts > MAX_POINTS_1D:
            return (f"Field scan points ({total_field_pts:,}) exceeds the "
                    f"safety limit of {MAX_POINTS_1D:,}.")

    elif scan_type == "DC_HYST":
        # The PLC runs the loop autonomously and cannot be paused once
        # started, so a mistyped cycle count is only escapable by aborting.
        n_half = int(cfg.get("hyst_npts", 100))
        cycles = int(cfg.get("hyst_cycles", 1))
        if n_half < 2:
            return f"DC hysteresis needs at least 2 points per half loop (got {n_half})."
        if n_half > MAX_POINTS_1D:
            return (f"DC hysteresis points per half loop ({n_half:,}) exceeds "
                    f"the safety limit of {MAX_POINTS_1D:,}.")
        if cycles < 1:
            return f"DC hysteresis needs at least 1 cycle (got {cycles})."
        if cycles > MAX_CYCLES:
            return (f"DC hysteresis cycles ({cycles:,}) exceeds the safety "
                    f"limit of {MAX_CYCLES:,}.")

    elif scan_type == "TIME":
        n_t = int(cfg.get("act1_npts", 1))
        if n_t < 1:
            return "Time scan requires at least 1 point."
        if n_t > MAX_POINTS_1D:
            return (f"Time scan points ({n_t:,}) exceeds the safety limit of "
                    f"{MAX_POINTS_1D:,}.")

    integ = float(cfg.get("integration_time", 0.1))
    if integ <= 0:
        return f"Integration time must be > 0 (got {integ})."

    return None     # all OK
