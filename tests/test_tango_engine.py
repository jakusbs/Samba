"""The scan engine's acquisition handshake against a REAL pytango device.

`tango.test_context.DeviceTestContext` serves a minimal lock-in-like device
in a child process with no TANGO database and no hardware, and hands back a
genuine `DeviceProxy` — so what is exercised here is the real client API
(asynchronous command dispatch, reply collection, state polling, attribute
reads), not a Python fake of it.  Skipped when pytango is not installed.

Run from the repo root:  python -m pytest tests/ -q
"""
import os
import sys
import threading
import time
import types

import numpy as np
import pytest

tango = pytest.importorskip("tango")
from tango import AttrWriteType, DevState                       # noqa: E402
from tango.server import Device, attribute, command             # noqa: E402
from tango.test_context import DeviceTestContext                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# runner.py imports PyQt6 (unused here) and the app-level `config` module.
try:
    import PyQt6  # noqa: F401
except ImportError:                                             # pragma: no cover
    from unittest.mock import MagicMock
    _qt = MagicMock()
    _qt.QtCore.QThread = object
    _qt.QtCore.pyqtSignal = lambda *a, **kw: None
    sys.modules["PyQt6"] = _qt
    sys.modules["PyQt6.QtCore"] = _qt.QtCore
_cfg = types.ModuleType("config")
_cfg.MAX_RETRIES, _cfg.RETRY_DELAY, _cfg.X_TIME = 2, 0.005, "_time_"
sys.modules.setdefault("config", _cfg)
for _p in (os.path.join(ROOT, "core", "scan"), os.path.join(ROOT, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hardware                                                 # noqa: E402  (the real module)
from runner import ScanRunner                                   # noqa: E402

ALL_ASYNCH = tango.asyn_req_type.ALL_ASYNCH


class FakeLockin(Device):
    """Start → RUNNING → (integrationtime) → ON, like the ZI servers.

    `FailNextStart(True)` makes the next Start raise server-side while the
    device stays ON — the DoubleInBeckhoffAverage failure mode (its PLC write
    happens before the state change).
    """

    def init_device(self):
        super().init_device()
        self._it = 0.02
        self._x = 0.0
        self._n = 0
        self._fail = False
        self._setp = 0.0
        self.set_state(DevState.ON)

    integrationtime = attribute(dtype=float, access=AttrWriteType.READ_WRITE)

    def read_integrationtime(self):
        return self._it

    def write_integrationtime(self, v):
        self._it = float(v)

    setpoint = attribute(dtype=float, access=AttrWriteType.READ_WRITE)

    def read_setpoint(self):
        return self._setp

    def write_setpoint(self, v):
        self._setp = float(v)

    x1 = attribute(dtype=float)

    def read_x1(self):
        return self._x

    y1 = attribute(dtype=float)

    def read_y1(self):
        return -self._x

    @command(dtype_in=bool)
    def FailNextStart(self, flag):
        self._fail = bool(flag)

    @command
    def Start(self):
        if self._fail:
            tango.Except.throw_exception(
                "Not_ready", "Cannot Start in state FAULT", "Start")
        if self.get_state() != DevState.ON:
            return
        self.set_state(DevState.RUNNING)

        def _run():
            time.sleep(self._it)
            self._n += 1
            self._x = float(self._n)
            self.set_state(DevState.ON)

        threading.Thread(target=_run, daemon=True).start()


@pytest.fixture(scope="module")
def proxy():
    try:
        ctx = DeviceTestContext(FakeLockin, process=True, timeout=30)
        p = ctx.__enter__()
    except Exception as e:                                      # pragma: no cover
        pytest.skip(f"cannot start an in-process device server here: {e}")
    try:
        yield p
    finally:
        ctx.__exit__(None, None, None)


def _engine(proxy):
    name = proxy.name()
    r = ScanRunner({"move_timeout": 5.0, "sensors": []}, {})
    r._trigger_consec_fails = {name: 0}
    devp = {name: proxy}
    dev_sensors = {name: [{"attribute": "x1", "label": "L x1"},
                          {"attribute": "y1", "label": "L y1"}]}
    trig = {name: "Start"}
    return r, name, devp, dev_sensors, trig


def _acquire(r, devp, dev_sensors, trig, int_time=0.02, move_timeout=5.0):
    return r._do_acquire(devp, dev_sensors, trig, int_time, time.time(),
                         {DevState.RUNNING, "RUNNING"},
                         {"move_timeout": move_timeout}, 15000, lambda m: None)


def test_handshake_against_real_device_collects_every_reply(proxy):
    r, name, devp, dev_sensors, trig = _engine(proxy)
    before = proxy.pending_asynch_call(ALL_ASYNCH)
    last = None
    for _ in range(20):
        vals, _t, ok = _acquire(r, devp, dev_sensors, trig)
        assert ok
        assert last is None or vals["L x1"] == last + 1   # a FRESH value each point
        last = vals["L x1"]
    # Un-collected replies live in the process-wide ApiUtil table (~2.2 KB
    # each, never freed when the proxy goes); the engine must leave none.
    assert proxy.pending_asynch_call(ALL_ASYNCH) == before == 0


def test_start_rejected_server_side_fails_the_point(proxy):
    """Start raises while the device stays ON: the exception only exists in
    the asynchronous reply.  The point must fail (NaN), not pass with the
    previous point's values."""
    r, name, devp, dev_sensors, trig = _engine(proxy)
    vals, _t, ok = _acquire(r, devp, dev_sensors, trig)
    assert ok
    good = vals["L x1"]
    proxy.FailNextStart(True)
    try:
        vals, _t, ok = _acquire(r, devp, dev_sensors, trig)
    finally:
        proxy.FailNextStart(False)
    assert not ok
    assert np.isnan(vals["L x1"]) and np.isnan(vals["L y1"])
    assert r._last_bad_devs == [name]
    assert proxy.pending_asynch_call(ALL_ASYNCH) == 0
    # …and the next point recovers with a fresh value.
    vals, _t, ok = _acquire(r, devp, dev_sensors, trig)
    assert ok and vals["L x1"] == good + 1


def test_integration_longer_than_stage_timeout_completes(proxy):
    r, name, devp, dev_sensors, trig = _engine(proxy)
    proxy.integrationtime = 0.3
    try:
        vals, _t, ok = _acquire(r, devp, dev_sensors, trig,
                                int_time=0.3, move_timeout=0.05)
    finally:
        proxy.integrationtime = 0.02
    assert ok and np.isfinite(vals["L x1"])


def test_probe_alive_answers_for_a_live_device(proxy):
    assert hardware.probe_alive(proxy, timeout_s=2.0) is None


def test_numpy_scalar_setpoint_is_accepted_by_this_pytango(proxy):
    """np.linspace yields numpy scalars; the FIELD path coerces to float
    because one lab pytango rejected them.  Record what this one does."""
    assert hardware.safe_write(proxy, "setpoint", np.float64(1.25)) is None
    assert proxy.setpoint == 1.25
