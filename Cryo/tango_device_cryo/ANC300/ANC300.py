# -*- coding: utf-8 -*-
"""
ANC300
Controls an Attocube ANC300 piezo controller via a Socket TANGO proxy.
Communicates using the ANC300 serial-over-Ethernet ASCII protocol.

Fixes vs. old code:
  - self.ANC.read() → self.ANC.Read() (capital R — TANGO command name)
"""

import time
import re

import tango
from tango import DevState, AttrWriteType
from tango.server import Device, command, attribute, device_property, run

__all__ = ["ANC300", "main"]


class ANC300(Device):
    """
    Attocube ANC300 piezo stepper controller.
    Connects via a Socket device that wraps a serial-over-Ethernet link.
    Frequency (Hz), voltage (V) and mode are sent as ASCII commands.
    Position writes are relative steps (stepu/stepd).

    **Properties:**
    - Proxy: TANGO path to the Socket device connected to the ANC300
    - addr_x/y/z: ANC300 axis address strings
    - password: Authorization password (default '123456')
    """

    Proxy = device_property(dtype='str', default_value='hpp-N42/socket/ANC300',
                            doc="TANGO path to Socket device")
    addr_x = device_property(dtype='str', default_value='4',
                             doc="ANC300 axis address for x")
    addr_y = device_property(dtype='str', default_value='5',
                             doc="ANC300 axis address for y")
    addr_z = device_property(dtype='str', default_value='6',
                             doc="ANC300 axis address for z")
    password = device_property(dtype='str', default_value='123456',
                               doc="ANC300 authorization password")

    def init_device(self):
        Device.init_device(self)
        self._fx = 0.0
        self._fy = 0.0
        self._fz = 0.0
        self._Vx = 0.0
        self._Vy = 0.0
        self._Vz = 0.0
        self._px = 0.0
        self._py = 0.0
        self._pz = 0.0
        self._Gx = False
        self._Gy = False
        self._Gz = False

        self.set_state(DevState.INIT)
        self.ANC = tango.DeviceProxy(self.Proxy)

        # Open connection and authenticate
        self.ANC.Init()
        time.sleep(0.1)
        self.ANC.Read()
        time.sleep(0.1)
        self.ANC.Write(str(self.password))
        time.sleep(0.1)
        readout = self.ANC.Read()
        print(readout)
        if readout == '******\r\nAuthorization success\r\n> ':
            self.set_state(DevState.ON)
        else:
            self.set_state(DevState.OFF)
            self.info_stream('Could not connect to ANC300 using Ethernet...')
            print('Could not connect to ANC300 using Ethernet...')
            return

        # Read all initial values; non-fatal if any individual query fails
        try:
            self._fx = self._getf(self.addr_x)
            self._fy = self._getf(self.addr_y)
            self._fz = self._getf(self.addr_z)
            self._Vx = self._getv(self.addr_x)
            self._Vy = self._getv(self.addr_y)
            self._Vz = self._getv(self.addr_z)
            self._Gx = self._getm(self.addr_x)
            self._Gy = self._getm(self.addr_y)
            self._Gz = self._getm(self.addr_z)
        except Exception as e:
            self.error_stream("ANC300: initial read failed (using defaults): {}".format(e))

    def always_executed_hook(self):
        pass

    # ---- Helpers --------------------------------------------------------

    def _getf(self, addr):
        self.ANC.Write('getf ' + addr)
        time.sleep(0.1)
        readout = self.ANC.Read()
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", readout)
        return float(nums[1]) if len(nums) > 1 else 0.0

    def _getv(self, addr):
        self.ANC.Write('getv ' + addr)
        time.sleep(0.1)
        readout = self.ANC.Read()
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", readout)
        return float(nums[1]) if len(nums) > 1 else 0.0

    def _getm(self, addr):
        self.ANC.Write('getm ' + addr)
        time.sleep(0.1)
        readout = self.ANC.Read()
        return 'gnd' in readout

    # ---- Attributes: frequency ------------------------------------------

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='Hz',
               doc="Step frequency for x axis")
    def fx(self):
        return self._fx

    @fx.write
    def fx(self, value):
        self.ANC.Write('setf ' + self.addr_x + ' ' + str(int(value)))
        self._fx = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='Hz',
               doc="Step frequency for y axis")
    def fy(self):
        return self._fy

    @fy.write
    def fy(self, value):
        self.ANC.Write('setf ' + self.addr_y + ' ' + str(int(value)))
        self._fy = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='Hz',
               doc="Step frequency for z axis")
    def fz(self):
        return self._fz

    @fz.write
    def fz(self, value):
        self.ANC.Write('setf ' + self.addr_z + ' ' + str(int(value)))
        self._fz = value

    # ---- Attributes: voltage --------------------------------------------

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='V',
               doc="Step voltage for x axis")
    def Vx(self):
        return self._Vx

    @Vx.write
    def Vx(self, value):
        self.ANC.Write('setv ' + self.addr_x + ' ' + str(value))
        self._Vx = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='V',
               doc="Step voltage for y axis")
    def Vy(self):
        return self._Vy

    @Vy.write
    def Vy(self, value):
        self.ANC.Write('setv ' + self.addr_y + ' ' + str(value))
        self._Vy = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='V',
               doc="Step voltage for z axis")
    def Vz(self):
        return self._Vz

    @Vz.write
    def Vz(self, value):
        self.ANC.Write('setv ' + self.addr_z + ' ' + str(value))
        self._Vz = value

    # ---- Attributes: position (relative steps) --------------------------

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               doc="X position in steps (write moves relative to current stored position)")
    def px(self):
        return self._px

    @px.write
    def px(self, value):
        steps = int(value - self._px)
        self.ANC.Write('setm ' + self.addr_x + ' stp')
        time.sleep(0.1)
        if steps > 0:
            self.ANC.Write('stepu ' + self.addr_x + ' ' + str(steps))
        else:
            self.ANC.Write('stepd ' + self.addr_x + ' ' + str(abs(steps)))
        self._px = value
        self._Gx = False

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               doc="Y position in steps (write moves relative to current stored position)")
    def py(self):
        return self._py

    @py.write
    def py(self, value):
        steps = int(value - self._py)
        self.ANC.Write('setm ' + self.addr_y + ' stp')
        time.sleep(0.1)
        if steps > 0:
            self.ANC.Write('stepu ' + self.addr_y + ' ' + str(steps))
        else:
            self.ANC.Write('stepd ' + self.addr_y + ' ' + str(abs(steps)))
        self._py = value
        self._Gy = False

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               doc="Z position in steps (write moves relative to current stored position)")
    def pz(self):
        return self._pz

    @pz.write
    def pz(self, value):
        steps = int(value - self._pz)
        self.ANC.Write('setm ' + self.addr_z + ' stp')
        time.sleep(0.1)
        if steps > 0:
            self.ANC.Write('stepu ' + self.addr_z + ' ' + str(steps))
        else:
            self.ANC.Write('stepd ' + self.addr_z + ' ' + str(abs(steps)))
        self._pz = value
        self._Gz = False

    # ---- Attributes: ground ---------------------------------------------

    @attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
               doc="Ground state of x axis (write True to ground)")
    def Gx(self):
        return self._Gx

    @Gx.write
    def Gx(self, value):
        if value:
            self.ANC.Write('setm ' + self.addr_x + ' gnd')
        self._Gx = value

    @attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
               doc="Ground state of y axis (write True to ground)")
    def Gy(self):
        return self._Gy

    @Gy.write
    def Gy(self, value):
        if value:
            self.ANC.Write('setm ' + self.addr_y + ' gnd')
        self._Gy = value

    @attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
               doc="Ground state of z axis (write True to ground)")
    def Gz(self):
        return self._Gz

    @Gz.write
    def Gz(self, value):
        if value:
            self.ANC.Write('setm ' + self.addr_z + ' gnd')
        self._Gz = value

    # ---- Commands -------------------------------------------------------

    @command()
    def Ground(self):
        """Ground all three axes."""
        self.ANC.Write('setm ' + self.addr_x + ' gnd')
        self._Gx = True
        self.ANC.Write('setm ' + self.addr_y + ' gnd')
        self._Gy = True
        self.ANC.Write('setm ' + self.addr_z + ' gnd')
        self._Gz = True


def main(args=None, **kwargs):
    return run((ANC300,), args=args, **kwargs)


if __name__ == '__main__':
    main()
