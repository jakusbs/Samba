"""
hardware.py — Samba v3
TANGO proxy management: SimProxy fallback, caching, fresh connections,
and safe read/write helpers.
"""
import logging
import threading
import numpy as np
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

try:
    import tango
    TANGO_AVAILABLE = True
except ImportError:
    TANGO_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# SimProxy — fallback when TANGO is unavailable or a device is unreachable
# ─────────────────────────────────────────────────────────────────────────────
class SimProxy:
    """Drop-in replacement for tango.DeviceProxy in simulation mode."""
    def __init__(self, path: str = ""):
        self._v: Dict = {
            "current_polar": 0.0, "current_longitudinal": 0.0,
            "current": 0.0, "amplitude": 1.0, "switchvar": 0,
            "frequency": 100.0, "range": "20mA",
            "x": 0.0, "y": 0.0,
            "integrationtime": 0.1,
        }
        self._running_until: float = 0.0   # time.time() when integration ends

    def state(self):
        import time as _t
        if _t.time() < self._running_until:
            return "RUNNING"
        return None

    def write_attribute(self, attr: str, val):
        self._v[attr.lower()] = val

    def read_attribute(self, attr: str):
        class FA:
            def __init__(s, v, name=""): s.value = v; s.name = name
        attr_l = attr.lower()
        for fa in ("field_polar_corr", "field_longitudinal_corr"):
            if fa in attr_l:
                cur = self._v.get("current_polar", 0) + self._v.get("current_longitudinal", 0)
                return FA(cur * 0.15 + np.random.normal(0, 5e-4), attr)
        if attr_l in self._v:
            return FA(self._v[attr_l], attr)
        x = self._v.get("x", 0.0); y = self._v.get("y", 0.0)
        r = np.sqrt(x**2 + y**2) / 5000
        return FA(np.exp(-r**2 / 2) + np.random.normal(0, 3e-3), attr)

    def read_attributes(self, attrs: list):
        """Batch read — returns list of attribute-value objects."""
        return [self.read_attribute(a) for a in attrs]

    def command_inout(self, cmd: str, arg=None):
        import time as _t
        if cmd.lower() == "start":
            integ = self._v.get("integrationtime", 0.1)
            self._running_until = _t.time() + integ
        return None

    def command_inout_asynch(self, cmd: str, arg=None):
        """Non-blocking trigger — fires command immediately, returns a request id."""
        self.command_inout(cmd, arg)
        return 0

    def command_inout_reply(self, req_id, timeout_ms=0):
        """Collect reply for async command (no-op in sim)."""
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Proxy cache (thread-safe: accessed from main thread + QThread workers)
# ─────────────────────────────────────────────────────────────────────────────
_pcache: Dict[str, object] = {}
_pcache_lock = threading.Lock()


def get_proxy(path: str) -> object:
    """
    Return a cached proxy — used during scans where speed matters.
    Falls back to SimProxy on first connection failure and caches that too.
    """
    if not path or not path.strip():
        return SimProxy(path)
    path = path.strip()
    with _pcache_lock:
        if path not in _pcache:
            try:
                _pcache[path] = tango.DeviceProxy(path) if TANGO_AVAILABLE else SimProxy(path)
            except Exception:
                _pcache[path] = SimProxy(path)
        return _pcache[path]


def fresh_proxy(path: str) -> Tuple[object, Optional[str]]:
    """
    Always create a brand-new DeviceProxy, bypassing the cache.

    Used by interactive hardware-panel operations so that a SimProxy cached
    at startup never silently intercepts writes to real devices.
    On success the cache is updated so subsequent scan reads also benefit.

    Returns (proxy, error_string_or_None).
    """
    if not path or not path.strip():
        return SimProxy(path), "No device path configured"
    path = path.strip()
    if not TANGO_AVAILABLE:
        return SimProxy(path), "pytango not installed (simulation mode)"
    try:
        p = tango.DeviceProxy(path)
        with _pcache_lock:
            _pcache[path] = p          # update cache with live proxy
        return p, None
    except Exception as e:
        return SimProxy(path), str(e)


def evict_proxy(path: str):
    """Remove a device path from the cache (used when switching setups)."""
    if path:
        with _pcache_lock:
            _pcache.pop(path, None)


def is_sim_proxy(proxy) -> bool:
    return isinstance(proxy, SimProxy)


# ─────────────────────────────────────────────────────────────────────────────
# Safe read / write helpers
# ─────────────────────────────────────────────────────────────────────────────
def safe_write(proxy, attr: str, val) -> Optional[str]:
    """Write an attribute; return None on success or the error string."""
    try:
        proxy.write_attribute(attr, val)
        return None
    except Exception as e:
        return str(e)


def safe_read(proxy, attr: str) -> Tuple[Optional[float], Optional[str]]:
    """Read a numeric attribute; return (value, None) or (None, error).
    Handles both scalar and array-type returns (e.g. SmarAct positions)."""
    try:
        raw = proxy.read_attribute(attr).value
        v = float(raw[0]) if hasattr(raw, "__len__") else float(raw)
        return v, None
    except Exception as e:
        return None, str(e)


def safe_read_str(proxy, attr: str) -> Tuple[Optional[str], Optional[str]]:
    """Read a string attribute; return (value, None) or (None, error)."""
    try:
        return str(proxy.read_attribute(attr).value), None
    except Exception as e:
        return None, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Device reconnection (for socket-based instruments like Keithley)
# ─────────────────────────────────────────────────────────────────────────────
def reconnect_device(path: str, max_retries: int = 3,
                     delay: float = 2.0) -> Tuple[object, Optional[str]]:
    """
    Attempt to reconnect a Tango device that has lost its socket connection.

    Handles the Socket→PyKeithley chain:
      1. Read the device's SocketProxy property (if it exists)
      2. Call Init on the Socket device (closes old TCP, reopens fresh)
      3. Wait for OS to fully tear down old TCP (EALREADY / TIME_WAIT)
      4. If Socket still in OFF, retry Init
      5. Call Init on the PyKeithley device (re-creates its proxy to Socket)
      6. Verify by reading state()

    Returns (proxy, error_string_or_None).
    """
    import time as _t

    if not path or not path.strip():
        return SimProxy(path), "No device path"
    path = path.strip()
    if not TANGO_AVAILABLE:
        return SimProxy(path), "pytango not installed"

    evict_proxy(path)

    for attempt in range(max_retries):
        try:
            p = tango.DeviceProxy(path)

            # ── Step 1: Find the underlying Socket device ────────────────
            socket_path = ""
            try:
                db = tango.Database()
                prop = db.get_device_property(path, ["SocketProxy"])
                socket_path = prop["SocketProxy"][0] if prop.get("SocketProxy") else ""
            except Exception:
                pass

            # ── Step 2: Reconnect the Socket (Init works in any state) ───
            if socket_path:
                try:
                    sock_p = tango.DeviceProxy(socket_path)
                    # Init calls delete_device() → deletes old ClientSocket
                    # then init_device() → creates new ClientSocket(host, port)
                    sock_p.command_inout("Init")
                except Exception:
                    pass

                # Wait for OS to tear down old TCP connection
                # (errno 114 = EALREADY means connect() is still in progress)
                _t.sleep(delay)

                # Check if Socket came back ON
                for retry in range(3):
                    try:
                        sock_state = sock_p.state()
                        if sock_state == tango.DevState.ON:
                            break
                        # Still OFF — the TCP connect likely got EALREADY.
                        # Wait longer and try Init again.
                        _t.sleep(delay)
                        sock_p.command_inout("Init")
                        _t.sleep(delay)
                    except Exception:
                        _t.sleep(delay)

            # ── Step 3: Reinitialise the PyKeithley device ───────────────
            try:
                p.command_inout("Init")
            except Exception:
                pass

            _t.sleep(1.0)

            # ── Step 4: Verify ───────────────────────────────────────────
            try:
                state = p.state()
                if state == tango.DevState.ON:
                    with _pcache_lock:
                        _pcache[path] = p
                    return p, None
                elif state == tango.DevState.FAULT:
                    if attempt < max_retries - 1:
                        _t.sleep(delay)
                        continue
                    with _pcache_lock:
                        _pcache[path] = p
                    return p, f"Device in FAULT after {max_retries} attempts"
                else:
                    with _pcache_lock:
                        _pcache[path] = p
                    return p, None
            except Exception as e:
                if attempt < max_retries - 1:
                    _t.sleep(delay)
                    continue
                with _pcache_lock:
                    _pcache[path] = p
                return p, f"Cannot read state: {e}"

        except Exception as e:
            if attempt < max_retries - 1:
                _t.sleep(delay)
                continue
            return SimProxy(path), f"Connection failed: {e}"

    return SimProxy(path), "Max retries exceeded"


# ─────────────────────────────────────────────────────────────────────────────
# Demagnetization — alternating-decay routine to bring magnet to zero
# ─────────────────────────────────────────────────────────────────────────────
def demagnetize_magnet(proxy, attr: str,
                       log_fn=None, n_steps: int = 20,
                       start_A: float = 2,
                       decay: float = 0.80,
                       delay_s: float = 0.10):
    """
    Demagnetize by writing an alternating, geometrically decaying current.

    Sequence (n_steps=20, start=1 A, decay=0.80):
      +1.000, −0.800, +0.640, −0.512, … → 0.000 A

    Parameters
    ----------
    proxy  : Tango DeviceProxy (or SimProxy)
    attr   : current attribute name (e.g. "current_polar")
    log_fn : callable(str) for status messages, or None
    """
    import time as _t
    if log_fn is None:
        log_fn = lambda m: None
    log_fn("Demagnetization started")
    sign = 1
    amp  = start_A
    for step in range(n_steps):
        val = sign * amp
        err = safe_write(proxy, attr, val)
        if err:
            log_fn(f"⚠ demag step {step}: {err}")
            break
        log_fn(f"  demag {step+1}/{n_steps}: {val:+.4f} A")
        _t.sleep(delay_s)
        sign = -sign
        amp  *= decay
    # Final: set exactly 0
    err = safe_write(proxy, attr, 0.0)
    if err:
        log_fn(f"⚠ demag final zero: {err}")
    else:
        log_fn("Demagnetization done — current set to 0.000 A")
