# -*- coding: utf-8 -*-
"""
ANM200
Controls Attocube ANM200 piezo DC motors via three DoubleOutBeckhoff proxies.
x, y, z positions are stored internally; writes send scaled voltages to the
Beckhoff DAC outputs. Scaling converts voltage [V] to distance [µm].
"""

import tango
from tango import DevState, AttrWriteType
from tango.server import Device, attribute, device_property, run

__all__ = ["ANM200", "main"]


class ANM200(Device):
    """
    Moves piezo motors by applying ±10 V through three Beckhoff AOC channels.
    Positions are tracked in scaled units (e.g. µm); actual hardware output
    is voltage = position × scaling.

    **Properties:**
    - Socketx/y/z: TANGO paths to the DoubleOutBeckhoff devices for each axis
    """

    Socketx = device_property(dtype='str', default_value='hpp-n42/beckhoff/AOC1',
                              doc="TANGO path to DoubleOutBeckhoff for x axis")
    Sockety = device_property(dtype='str', default_value='hpp-n42/beckhoff/AOC2',
                              doc="TANGO path to DoubleOutBeckhoff for y axis")
    Socketz = device_property(dtype='str', default_value='hpp-n42/beckhoff/AOC3',
                              doc="TANGO path to DoubleOutBeckhoff for z axis")

    def init_device(self):
        Device.init_device(self)
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0
        self._scaling = 1.0
        self.Sx = tango.DeviceProxy(self.Socketx)
        self.Sy = tango.DeviceProxy(self.Sockety)
        self.Sz = tango.DeviceProxy(self.Socketz)
        self.set_state(DevState.ON)

    def always_executed_hook(self):
        pass

    # ---- Attributes -----------------------------------------------------

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False,
               doc="X position in scaled units")
    def x(self):
        return self._x / self._scaling if self._scaling != 0 else 0.0

    @x.write
    def x(self, value):
        voltage = value * self._scaling
        self.Sx.write_attribute('Value', voltage)
        self._x = voltage

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False,
               doc="Y position in scaled units")
    def y(self):
        return self._y / self._scaling if self._scaling != 0 else 0.0

    @y.write
    def y(self, value):
        voltage = value * self._scaling
        self.Sy.write_attribute('Value', voltage)
        self._y = voltage

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               doc="Z position in scaled units")
    def z(self):
        return self._z / self._scaling if self._scaling != 0 else 0.0

    @z.write
    def z(self, value):
        voltage = value * self._scaling
        self.Sz.write_attribute('Value', voltage)
        self._z = voltage

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=True,
               doc="Scaling factor: distance/Voltage [µm/V]")
    def scaling(self):
        return self._scaling

    @scaling.write
    def scaling(self, value):
        self._scaling = value


def main(args=None, **kwargs):
    return run((ANM200,), args=args, **kwargs)


if __name__ == '__main__':
    main()
