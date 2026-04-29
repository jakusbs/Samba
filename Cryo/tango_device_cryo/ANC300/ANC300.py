# -*- coding: utf-8 -*-
"""
ANC300
Controls an Attocube ANC300 piezo controller via a direct TCP (Telnet) connection.
No Socket proxy needed — the TCP connection is owned by this device server.
"""

import socket as _socket
import time
import re

import tango
from tango import DevState, AttrWriteType
from tango.server import Device, command, attribute, device_property, run

__all__ = ["ANC300", "main"]


class ANC300(Device):
    """
    Attocube ANC300 piezo stepper controller.
    Connects directly via TCP (Telnet, port 7230) — no Socket proxy required.
    Frequency (Hz), voltage (V) and mode are sent as ASCII commands.
    Position writes are relative steps (stepu/stepd).

    **Properties:**
    - Hostname: IP address of the ANC300
    - Port: TCP port (default 7230)
    - Readtimeout: Read timeout in ms (default 1000)
    - addr_x/y/z: ANC300 axis address strings
    - password: Authorization password (default '123456')
    """

    Hostname    = device_property(dtype='str', mandatory=True,
                                  doc="IP address of the ANC300")
    Port        = device_property(dtype='int', default_value=7230,
                                  doc="TCP port (Telnet, default 7230)")
    Readtimeout = device_property(dtype='int', default_value=1000,
                                  doc="Read timeout in milliseconds")
    addr_x      = device_property(dtype='str', default_value='4',
                                  doc="ANC300 axis address for x")
    addr_y      = device_property(dtype='str', default_value='5',
                                  doc="ANC300 axis address for y")
    addr_z      = device_property(dtype='str', default_value='6',
                                  doc="ANC300 axis address for z")
    password    = device_property(dtype='str', default_value='123456',
                                  doc="ANC300 authorization password")

    def init_device(self):
        Device.init_device(self)
        self._sock = None
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
        try:
            self._connect()
        except Exception as e:
            self.set_state(DevState.FAULT)
            self.set_status("Could not connect to ANC300: {}".format(e))
            return

        # Read initial values; non-fatal if any individual query fails
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

    def delete_device(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def always_executed_hook(self):
        pass

    # =========================================================================
    # TCP connection helpers
    # =========================================================================

    def _connect(self):
        """Open TCP connection, discard Telnet negotiation, authenticate."""
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(self.Readtimeout / 1000.0)
        s.connect((self.Hostname, self.Port))
        self._sock = s

        # Discard Telnet IAC negotiation bytes sent on connect
        time.sleep(0.1)
        try:
            self._sock.recv(4096)
        except _socket.timeout:
            pass

        # Authenticate
        self._send(self.password)
        time.sleep(0.1)
        readout = self._read()
        if 'Authorization success' in readout:
            self.set_state(DevState.ON)
        else:
            self._sock.close()
            self._sock = None
            self.set_state(DevState.FAULT)
            raise RuntimeError("ANC300 authorization failed — check password. Got: {!r}".format(readout))

    def _send(self, cmd):
        """Send a command with CRLF terminator."""
        self._sock.sendall((cmd + '\r\n').encode('utf-8'))

    def _read(self):
        """Read available data from the socket."""
        return self._sock.recv(4096).decode('utf-8', errors='ignore')

    # =========================================================================
    # Protocol helpers
    # =========================================================================

    def _getf(self, addr):
        self._send('getf ' + addr)
        time.sleep(0.1)
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", self._read())
        return float(nums[1]) if len(nums) > 1 else 0.0

    def _getv(self, addr):
        self._send('getv ' + addr)
        time.sleep(0.1)
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", self._read())
        return float(nums[1]) if len(nums) > 1 else 0.0

    def _getm(self, addr):
        self._send('getm ' + addr)
        time.sleep(0.1)
        return 'gnd' in self._read()

    # =========================================================================
    # Attributes: frequency
    # =========================================================================

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='Hz',
               doc="Step frequency for x axis")
    def fx(self):
        return self._fx

    @fx.write
    def fx(self, value):
        self._send('setf ' + self.addr_x + ' ' + str(int(value)))
        self._fx = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='Hz',
               doc="Step frequency for y axis")
    def fy(self):
        return self._fy

    @fy.write
    def fy(self, value):
        self._send('setf ' + self.addr_y + ' ' + str(int(value)))
        self._fy = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='Hz',
               doc="Step frequency for z axis")
    def fz(self):
        return self._fz

    @fz.write
    def fz(self, value):
        self._send('setf ' + self.addr_z + ' ' + str(int(value)))
        self._fz = value

    # =========================================================================
    # Attributes: voltage
    # =========================================================================

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='V',
               doc="Step voltage for x axis")
    def Vx(self):
        return self._Vx

    @Vx.write
    def Vx(self, value):
        self._send('setv ' + self.addr_x + ' ' + str(value))
        self._Vx = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='V',
               doc="Step voltage for y axis")
    def Vy(self):
        return self._Vy

    @Vy.write
    def Vy(self, value):
        self._send('setv ' + self.addr_y + ' ' + str(value))
        self._Vy = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False, unit='V',
               doc="Step voltage for z axis")
    def Vz(self):
        return self._Vz

    @Vz.write
    def Vz(self, value):
        self._send('setv ' + self.addr_z + ' ' + str(value))
        self._Vz = value

    # =========================================================================
    # Attributes: position (relative step counter)
    # =========================================================================

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               doc="X position in steps (relative counter — resets on server restart)")
    def px(self):
        return self._px

    @px.write
    def px(self, value):
        steps = int(value - self._px)
        self._send('setm ' + self.addr_x + ' stp')
        time.sleep(0.1)
        if steps > 0:
            self._send('stepu ' + self.addr_x + ' ' + str(steps))
        elif steps < 0:
            self._send('stepd ' + self.addr_x + ' ' + str(abs(steps)))
        self._px = value
        self._Gx = False

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               doc="Y position in steps (relative counter — resets on server restart)")
    def py(self):
        return self._py

    @py.write
    def py(self, value):
        steps = int(value - self._py)
        self._send('setm ' + self.addr_y + ' stp')
        time.sleep(0.1)
        if steps > 0:
            self._send('stepu ' + self.addr_y + ' ' + str(steps))
        elif steps < 0:
            self._send('stepd ' + self.addr_y + ' ' + str(abs(steps)))
        self._py = value
        self._Gy = False

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               doc="Z position in steps (relative counter — resets on server restart)")
    def pz(self):
        return self._pz

    @pz.write
    def pz(self, value):
        steps = int(value - self._pz)
        self._send('setm ' + self.addr_z + ' stp')
        time.sleep(0.1)
        if steps > 0:
            self._send('stepu ' + self.addr_z + ' ' + str(steps))
        elif steps < 0:
            self._send('stepd ' + self.addr_z + ' ' + str(abs(steps)))
        self._pz = value
        self._Gz = False

    # =========================================================================
    # Attributes: ground
    # =========================================================================

    @attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
               doc="Ground state of x axis")
    def Gx(self):
        return self._Gx

    @Gx.write
    def Gx(self, value):
        self._send('setm ' + self.addr_x + (' gnd' if value else ' stp'))
        self._Gx = value

    @attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
               doc="Ground state of y axis")
    def Gy(self):
        return self._Gy

    @Gy.write
    def Gy(self, value):
        self._send('setm ' + self.addr_y + (' gnd' if value else ' stp'))
        self._Gy = value

    @attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
               doc="Ground state of z axis")
    def Gz(self):
        return self._Gz

    @Gz.write
    def Gz(self, value):
        self._send('setm ' + self.addr_z + (' gnd' if value else ' stp'))
        self._Gz = value

    # =========================================================================
    # Commands
    # =========================================================================

    @command()
    def Ground(self):
        """Ground all three axes. Attempts all axes even if one fails."""
        errors = []
        for addr, flag, name in [
            (self.addr_x, '_Gx', 'x'),
            (self.addr_y, '_Gy', 'y'),
            (self.addr_z, '_Gz', 'z'),
        ]:
            try:
                self._send('setm ' + addr + ' gnd')
                setattr(self, flag, True)
            except Exception as e:
                errors.append('axis {}: {}'.format(name, e))
        if errors:
            tango.Except.throw_exception(
                'Ground failed',
                'Some axes could not be grounded: ' + '; '.join(errors),
                'ANC300::Ground')

    @command()
    def Reconnect(self):
        """Close and reopen the TCP connection to the ANC300."""
        try:
            self._connect()
        except Exception as e:
            self.set_state(DevState.FAULT)
            self.set_status("Reconnect failed: {}".format(e))


def main(args=None, **kwargs):
    return run((ANC300,), args=args, **kwargs)


if __name__ == '__main__':
    main()
