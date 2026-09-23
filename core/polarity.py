"""
polarity.py — the order in which a scanlist visits its two polarity states.

Pure logic only: no Qt, no TANGO, no matplotlib — so it is unit-testable in
CI (same rule as core/current_sweep.py).  The schedule is applied by
ScanlistWorker (core/scan/workers.py); the toggles live in the Scanlist tab's
"Polarity control" group.

A scanlist measures the same config N times while the optical relay and/or
the magnet polarity alternate, and the analysis groups the scans by
relay_sign × sign(field).  Which state each cycle gets is decided here:

    AB     A B A B A B A B      switch on every cycle boundary
    ABBA   A B B A A B B A      switch in pairs

Both orders spend half the list in each state, so the analysis sees the same
two groups either way.  The difference is **drift**: with AB order every A
sits earlier than the B that follows it, so a signal drifting linearly in
time biases the whole A group one way and the B group the other, and the
A−B difference carries the drift in full.  ABBA pairs every A→B step with a
B→A step, so within each quartet a linear drift cancels to first order.

ABBA also **halves the number of field reversals** — the state changes on
every boundary in AB order but only on every other one in ABBA — which on a
superconducting magnet is the difference between one slow ramp per scan and
one per two scans.
"""

# Config values (stored in the scan config as flip_order)
ORDER_AB   = "AB"      # historic behaviour, and the default for old configs
ORDER_ABBA = "ABBA"

FLIP_ORDERS = (ORDER_AB, ORDER_ABBA)


def normalize_order(order) -> str:
    """Return a known order string; anything unrecognised falls back to AB.

    The value arrives from JSON — absent in pre-v12 configs, and editable by
    hand — and a wrong answer here silently changes what the magnet does, so
    an unknown value takes the historic behaviour rather than guessing.
    """
    o = str(order or "").strip().upper()
    return o if o in FLIP_ORDERS else ORDER_AB


def flip_phase(cycle: int, order=ORDER_AB) -> int:
    """Polarity phase of 0-based cycle index `cycle`: 0 = A, 1 = B.

        AB:    0 1 0 1 0 1 0 1
        ABBA:  0 1 1 0 0 1 1 0

    The ABBA form is ``((i + 1) // 2) % 2`` rather than a lookup table so
    that the pattern keeps repeating past the first quartet without the
    A→A boundary at i=3→4 turning into a spurious switch.
    """
    i = int(cycle)
    if normalize_order(order) == ORDER_ABBA:
        return ((i + 1) // 2) % 2
    return i % 2


def switches_before(cycle: int, order=ORDER_AB) -> bool:
    """True when cycle `cycle` must switch state relative to the previous one.

    Cycle 0 never switches: the list starts in whatever state the hardware is
    already in, and that state is what "A" means for this run.
    """
    i = int(cycle)
    if i <= 0:
        return False
    return flip_phase(i, order) != flip_phase(i - 1, order)


def phase_label(phase: int) -> str:
    """'A' or 'B' — for status lines, logs and the scanlist .txt header."""
    return "B" if int(phase) else "A"


def order_preview(order, n: int = 8) -> str:
    """``'A B B A A B B A'`` — the first `n` cycles, for logs and tooltips."""
    return " ".join(phase_label(flip_phase(i, order))
                    for i in range(max(0, int(n))))
