"""In-process TANGO device fakes for tests (`tango.test_context`).

Each is a real `tango.server.Device`, served without a database and
without hardware by `DeviceTestContext`, and reached through a genuine
`DeviceProxy` — so a test exercises the real client API (asynchronous
dispatch, reply collection, state polling, attribute reads), not a Python
stand-in for it.  Import requires pytango.
"""
import threading
import time

import tango
from tango import AttrWriteType, DevState
from tango.server import Device, attribute, command


class FakeLockin(Device):
    """Start → RUNNING → (integrationtime) → ON, like the ZI servers.

    `FailNextStart(True)` makes the next Start raise server-side while the
    device stays ON — the DoubleInBeckhoffAverage failure mode (its PLC
    write happens before the state change).  Every completed acquisition
    increments x1 so a test can tell a fresh value from a stale one.
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


class FakeRelay(Device):
    """PyRelais-like: `switchvar` (odd → ON, even → OFF/STANDBY).

    As on the real server, EVERY write actuates the relay whatever the
    value; `actuations` counts them so a test can assert that a scanlist
    only switches when the scheduled state changes.
    """

    def init_device(self):
        super().init_device()
        self._sv = 0
        self._n = 0
        self.set_state(DevState.STANDBY)

    switchvar = attribute(dtype=int, access=AttrWriteType.READ_WRITE)

    def read_switchvar(self):
        return self._sv

    def write_switchvar(self, v):
        self._n += 1
        self._sv = int(v)
        self.set_state(DevState.ON if self._sv % 2 else DevState.STANDBY)

    actuations = attribute(dtype=int)

    def read_actuations(self):
        return self._n
