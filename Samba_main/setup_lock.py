"""
setup_lock.py — Client-side setup locking for Samba
====================================================
Provides acquire_lock() / release_lock() that talk to the Setup_lock
TANGO device server (built with Pogo).

The server just has 6 attributes:
  GreenBusy, IrBusy, CryoBusy     (DevBoolean, READ_WRITE)
  GreenInfo,  IrInfo,  CryoInfo   (DevString,  READ_WRITE)

No custom commands needed — we read/write the attributes directly.

If the server is unreachable, locks are silently skipped (fail-open)
so Samba always works even without the lock server running.

Usage in samba.py:
    from setup_lock import acquire_lock, release_lock

    ok, msg = acquire_lock("Green")   # (True, "") or (False, "pc3 @ 14:02:31")
    release_lock("Green")
"""

import logging
import os
import socket
import time as _time
from datetime import datetime
from typing import Tuple

log = logging.getLogger(__name__)

try:
    import tango
    TANGO_AVAILABLE = True
except ImportError:
    TANGO_AVAILABLE = False

# ── Configuration ─────────────────────────────────────────────────────────────
LOCK_DEVICE = "hpp-N42/samba/lock"       # adjust to match your TANGO DB

# Map setup name → attribute names on the Pogo device
# NOTE: Tango attribute names are the Python method names (lowercase).
_ATTR_MAP = {
    "Green": ("greenbusy", "greeninfo"),
    "IR":    ("irbusy",    "irinfo"),
    "Cryo":  ("cryobusy",  "cryoinfo"),
}


def _get_proxy():
    """Return a DeviceProxy to the lock server, or None if unavailable."""
    if not TANGO_AVAILABLE:
        log.warning("setup_lock: tango not available")
        return None
    try:
        dp = tango.DeviceProxy(LOCK_DEVICE)
        dp.set_timeout_millis(1000)
        dp.ping()
        return dp
    except Exception as e:
        log.warning("setup_lock: cannot reach %s (%s) — locking skipped", LOCK_DEVICE, e)
        return None


def acquire_lock(setup_name: str) -> Tuple[bool, str]:
    """
    Try to lock *setup_name* (Green / IR / Cryo).

    Returns:
        (True, "")              — lock acquired
        (False, "<who has it>") — already locked by someone else
        (True, "")              — lock server unreachable (fail-open)
    """
    dp = _get_proxy()
    if dp is None:
        return True, ""

    busy_attr, info_attr = _ATTR_MAP.get(setup_name, (None, None))
    if busy_attr is None:
        return True, ""

    try:
        # Check if already locked
        if dp.read_attribute(busy_attr).value:
            info = dp.read_attribute(info_attr).value
            return False, info or "unknown"

        # Acquire: write info first, then flip busy.
        # Include pid so the stamp is unique even on the same host.
        stamp = (f"{socket.gethostname()}:{os.getpid()} "
                 f"@ {datetime.now().strftime('%H:%M:%S')}")
        dp.write_attribute(info_attr, stamp)
        dp.write_attribute(busy_attr, True)

        # Verify we won the race: wait briefly and re-read the info attribute.
        # If another client wrote its own stamp in the same window, we lost.
        _time.sleep(0.05)
        actual = dp.read_attribute(info_attr).value
        if actual != stamp:
            # Another client snuck in — release and report who has it.
            try:
                dp.write_attribute(busy_attr, False)
                dp.write_attribute(info_attr, "")
            except Exception:
                pass
            return False, actual or "unknown"

        log.info("setup_lock: acquired '%s' as %s", setup_name, stamp)
        return True, ""
    except Exception as e:
        log.warning("setup_lock: acquire failed for '%s' (%s) — proceeding anyway", setup_name, e)
        return True, ""


def release_lock(setup_name: str):
    """Release the lock for *setup_name*.  Silently ignores errors."""
    dp = _get_proxy()
    if dp is None:
        return

    busy_attr, info_attr = _ATTR_MAP.get(setup_name, (None, None))
    if busy_attr is None:
        return

    try:
        dp.write_attribute(busy_attr, False)
        dp.write_attribute(info_attr, "")
        log.info("setup_lock: released '%s'", setup_name)
    except Exception as e:
        log.warning("setup_lock: release failed for '%s' (%s)", setup_name, e)


def check_lock(setup_name: str) -> Tuple[bool, str]:
    """
    Check if a setup is currently locked (without acquiring).

    Returns:
        (True, "<info>")  — busy
        (False, "")       — free or server unreachable
    """
    dp = _get_proxy()
    if dp is None:
        return False, ""

    busy_attr, info_attr = _ATTR_MAP.get(setup_name, (None, None))
    if busy_attr is None:
        return False, ""

    try:
        if dp.read_attribute(busy_attr).value:
            info = dp.read_attribute(info_attr).value
            return True, info or "unknown"
        return False, ""
    except Exception:
        return False, ""
