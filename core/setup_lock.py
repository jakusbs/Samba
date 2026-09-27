"""
setup_lock.py — Client-side setup locking for Samba (shared core)
=================================================================
Provides acquire_lock() / release_lock() that talk to the Setup_lock
TANGO device server (built with Pogo).

The companion SetupLock 2 server has AcquireLease, RenewLease and ReleaseLease
commands plus six compatibility attributes. All workstations should be upgraded
for owner-checked admission; legacy clients remain advisory.

An unavailable optional server preserves the historic fail-open behavior and
is explicitly shown as Unprotected in the acquisition header.

New servers provide atomic owner-checked leases renewed while a run is active.
Legacy servers remain advisory and are identified in the UI. A busy legacy
lock is never automatically stolen based on age alone.

Usage in samba.py:
    from core.setup_lock import acquire_lock, release_lock

    ok, msg = acquire_lock("Green")   # (True, "") or (False, "pc3:412 @ ...")
    release_lock("Green")
"""

import logging
import json
import threading
import uuid
import os
import re
import socket
import time as _time
from datetime import datetime
from typing import Optional, Tuple

log = logging.getLogger(__name__)

try:
    import tango
    TANGO_AVAILABLE = True
except ImportError:
    TANGO_AVAILABLE = False

# ── Configuration ─────────────────────────────────────────────────────────────
LOCK_DEVICE = "hpp-N42/samba/lock"       # adjust to match your TANGO DB

# Kept for old tooling that imports this constant. Age-based takeover is no
# longer used: only the server can expire an owner-checked lease.
STALE_LOCK_HOURS = 12.0

# Map setup name → attribute names on the Pogo device
# NOTE: Tango attribute names are the Python method names (lowercase).
_ATTR_MAP = {
    "Green": ("greenbusy", "greeninfo"),
    "IR":    ("irbusy",    "irinfo"),
    "Cryo":  ("cryobusy",  "cryoinfo"),
}

_STAMP_TIME_FMT = "%Y-%m-%d %H:%M:%S"
_STAMP_TIME_RE  = re.compile(r"@ (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _make_stamp() -> str:
    """Unique holder stamp: hostname:pid @ full date+time."""
    return (f"{socket.gethostname()}:{os.getpid()} "
            f"@ {datetime.now().strftime(_STAMP_TIME_FMT)}")


def _stamp_age_hours(info: str) -> Optional[float]:
    """Age of a lock stamp in hours, or None if the timestamp can't be
    parsed (e.g. an old-format stamp without a date — treated as held)."""
    m = _STAMP_TIME_RE.search(info or "")
    if not m:
        return None
    try:
        t = datetime.strptime(m.group(1), _STAMP_TIME_FMT)
    except ValueError:
        return None
    return (datetime.now() - t).total_seconds() / 3600.0


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


LEASE_SECONDS = 180
RENEW_SECONDS = 30
_held = {}
_health = {}
_guard = threading.RLock()


def lock_status(setup_name):
    """Human-readable protection mode for the acquisition header."""
    with _guard:
        return _health.get(setup_name, (True, "Lock not acquired"))[1]


def lock_health(setup_name):
    """False after renewal failure; the UI must pause before proceeding."""
    with _guard:
        return _health.get(setup_name, (True, "Lock not acquired"))


def _supports_leases(dp):
    try:
        dp.command_query("AcquireLease")
        return True
    except AttributeError:
        return False
    except Exception as exc:
        reasons = [str(getattr(e, "reason", "")) for e in getattr(exc, "args", ())]
        if "API_CommandNotFound" in str(exc) or "API_CommandNotFound" in reasons:
            return False
        raise


def _renew_loop(setup_name, token, stopped):
    while not stopped.wait(RENEW_SECONDS):
        try:
            dp = _get_proxy()
            ok = dp is not None and dp.command_inout("RenewLease", json.dumps(
                {"setup": setup_name, "owner": token, "ttl": LEASE_SECONDS}))
            message = "Lease protected" if ok else "Setup ownership lost — acquisition paused"
        except Exception as exc:
            ok, message = False, f"Lease renewal failed — acquisition paused: {exc}"
        with _guard:
            if _held.get(setup_name, {}).get("token") != token:
                return
            _health[setup_name] = (bool(ok), message)
        if not ok:
            log.error("setup_lock: %s", message)


def acquire_lock(setup_name: str) -> Tuple[bool, str]:
    """Acquire an atomic renewable lease, or visibly degrade on old servers."""
    if setup_name not in _ATTR_MAP:
        return False, f"Unknown setup: {setup_name}"
    with _guard:
        if setup_name in _held:
            return lock_health(setup_name)[0], lock_health(setup_name)[1]
    dp = _get_proxy()
    if dp is None:
        with _guard:
            _health[setup_name] = (True, "Unprotected — lock service unavailable")
        return True, ""
    try:
        if _supports_leases(dp):
            token = f"{_make_stamp()} / {uuid.uuid4().hex}"
            result = json.loads(dp.command_inout("AcquireLease", json.dumps(
                {"setup": setup_name, "owner": token, "ttl": LEASE_SECONDS})))
            if not result.get("acquired"):
                return False, result.get("owner", "another workstation")
            stopped = threading.Event()
            with _guard:
                _held[setup_name] = {"token": token, "lease": True, "stop": stopped}
                _health[setup_name] = (True, "Lease protected")
            threading.Thread(target=_renew_loop, args=(setup_name, token, stopped),
                             daemon=True, name=f"lease-{setup_name}").start()
            return True, ""

        # Backward compatibility is advisory only. Never clear a competing
        # stamp, and never take over a busy legacy lock based solely on age.
        busy_attr, info_attr = _ATTR_MAP[setup_name]
        if dp.read_attribute(busy_attr).value:
            return False, dp.read_attribute(info_attr).value or "another workstation"
        stamp = _make_stamp() + " / " + uuid.uuid4().hex
        dp.write_attribute(info_attr, stamp)
        dp.write_attribute(busy_attr, True)
        _time.sleep(0.05)
        actual = dp.read_attribute(info_attr).value
        if actual != stamp:
            return False, actual or "another workstation"
        with _guard:
            _held[setup_name] = {"token": stamp, "lease": False}
            _health[setup_name] = (True, "Advisory lock — upgrade the lock server")
        log.warning("setup_lock: legacy advisory mode; atomic leases unavailable")
        return True, ""
    except Exception as exc:
        # A reachable service with an uncertain acquisition result is not
        # equivalent to a missing optional service: do not start a second run.
        log.error("setup_lock: acquisition failed: %s", exc)
        return False, f"Lock acquisition failed: {exc}"


def release_lock(setup_name: str):
    """Release only this client's ownership; expired leases need no cleanup."""
    with _guard:
        held = _held.pop(setup_name, None)
    if not held:
        return
    if held.get("stop") is not None:
        held["stop"].set()
    dp = _get_proxy()
    try:
        if dp is None:
            raise RuntimeError("lock service unavailable")
        if held["lease"]:
            dp.command_inout("ReleaseLease", json.dumps(
                {"setup": setup_name, "owner": held["token"]}))
        else:
            busy_attr, info_attr = _ATTR_MAP[setup_name]
            if dp.read_attribute(info_attr).value == held["token"]:
                dp.write_attribute(busy_attr, False)
                # The legacy server clears info together with busy; a second
                # unconditional info write could erase the next owner's stamp.
        with _guard:
            _health[setup_name] = (True, "Lock released")
    except Exception as exc:
        log.warning("setup_lock: release failed: %s", exc)
        with _guard:
            _health[setup_name] = (True, "Release unconfirmed — lease will expire")


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
