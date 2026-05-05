# -*- coding: utf-8 -*-
"""
PyKeithley2
Controls a second Keithley 6221 current source via a Socket TANGO device.
Identical to PyKeithley but uses trigger line 3 for the phase marker.
"""

import time

import tango
from tango import DevState, AttrWriteType
from tango.server import Device, command, attribute, device_property, run

__all__ = ["PyKeithley2", "main"]


class PyKeithley2(Device):
    """
    Keithley 6221 current source controller (unit 2).
    Current is set in mA; the device converts to A for the instrument.
    Compliance voltage is in V. Sine-wave phase marker uses trigger line 3.

    **Properties:**
    - SocketProxy: TANGO path to the Socket device connected to this Keithley
    """

    SocketProxy = device_property(
        dtype='str',
        default_value='hpp-n42/socket/keithley6221_2',
        doc="TANGO path to the Socket device for this Keithley"
    )

    def init_device(self):
        Device.init_device(self)
        self._current = 0.0
        self._compliance = 0.0
        self._autorange = False
        self._amplitude = 0.0
        self._frequency = 0.0
        self._range = ""
        self.keithley = tango.DeviceProxy(self.SocketProxy)
        # Recover from any state left by a previous session (instrument keeps
        # running waveforms across TCP disconnects). ABOR is safe in IDLE state.
        try:
            self.keithley.WriteLine('SOUR:WAVE:ABOR')
            self.keithley.WriteLine('OUTP OFF')
        except Exception:
            pass
        self.set_state(DevState.ON)

    def always_executed_hook(self):
        pass

    # ---- Attributes -----------------------------------------------------

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=True,
               unit='mA', doc="DC output current in mA (clamped to ±105 mA)")
    def current(self):
        return self._current

    @current.write
    def current(self, value):
        if abs(value) > 105.0:
            value = 105.0 if value > 0 else -105.0
        self.keithley.WriteLine('CURR ' + str(value / 1000.0))
        self._current = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=True,
               unit='V', doc="Compliance voltage in V (0.1–105 V)")
    def compliance(self):
        return self._compliance

    @compliance.write
    def compliance(self, value):
        if value < 0.1:
            value = 0.1
        if value > 105.0:
            value = 105.0
        self.keithley.WriteLine('CURR:COMP ' + str(value))
        self._compliance = value

    @attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=True,
               doc="Enable automatic current range selection")
    def autorange(self):
        return self._autorange

    @autorange.write
    def autorange(self, value):
        if value:
            self.keithley.WriteLine('CURR:RANGE:AUTO ON')
        else:
            self.keithley.WriteLine('CURR:RANGE:AUTO OFF')
        self._autorange = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               unit='mA', doc="Sine-wave amplitude in mA")
    def amplitude(self):
        return self._amplitude

    @amplitude.write
    def amplitude(self, value):
        self._amplitude = value
        self.WAVEOFF()
        self.SINEWAVE()

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=True,
               unit='Hz', doc="Sine-wave frequency in Hz")
    def frequency(self):
        return self._frequency

    @frequency.write
    def frequency(self, value):
        self._frequency = value

    @attribute(dtype=str, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=True,
               doc="Current range string: '0.0002mA','0.002mA','0.02mA','0.2mA','2mA','20mA','100mA'")
    def range(self):
        return self._range

    @range.write
    def range(self, value):
        range_map = {
            '0.0002mA': '1e-7',
            '0.002mA':  '1e-6',
            '0.02mA':   '1e-5',
            '0.2mA':    '1e-4',
            '2mA':      '1e-3',
            '20mA':     '12e-3',
            '100mA':    '25e-3',
        }
        if value in range_map:
            self.keithley.WriteLine('CURR:RANGE ' + range_map[value])
        self._range = value

    # ---- Commands -------------------------------------------------------

    @command()
    def ON(self):
        """Enable the current output."""
        self.keithley.WriteLine('OUTP ON')

    @command()
    def OFF(self):
        """Disable the current output."""
        self.keithley.WriteLine('OUTP OFF')

    @command()
    def SINEWAVE(self):
        """Configure and start sine-wave output with trigger on line 3."""
        self.keithley.WriteLine('SOUR:WAVE:ABOR')         # ensure IDLE state
        self.keithley.WriteLine('SOUR:WAVE:FUNC SIN')
        self.keithley.WriteLine('SOUR:WAVE:FREQ ' + str(self._frequency))
        self.keithley.WriteLine('SOUR:WAVE:AMPL ' + str(self._amplitude / 1000.0))
        self.keithley.WriteLine('SOUR:WAVE:PMAR:STAT ON')
        self.keithley.WriteLine('SOUR:WAVE:PMAR 180')
        self.keithley.WriteLine('SOUR:WAVE:PMAR:OLIN 3')
        self.keithley.WriteLine('SOUR:WAVE:DUR:TIME INF')
        self.keithley.WriteLine('SOUR:WAVE:RANG FIX')
        time.sleep(0.1)
        self.keithley.WriteLine('SOUR:WAVE:ARM')
        self.keithley.WriteLine('SOUR:WAVE:INIT')

    @command()
    def WAVEOFF(self):
        """Abort the current sine-wave output."""
        self.keithley.WriteLine('SOUR:WAVE:ABOR')


def main(args=None, **kwargs):
    return run((PyKeithley2,), args=args, **kwargs)


if __name__ == '__main__':
    main()
