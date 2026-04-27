# -*- coding: utf-8 -*-
"""
AttoDRY
Connects to the AttoDRY2100 cryostat via UDP to a Windows PC running
AttoDRY2100 software. A daemon listener thread polls the state every 0.2 s.

Fix vs. old code:
  - Base class updated to LatestDeviceImpl
  - Added self.setField and self.setTemp so AttoDRYCheck can read them
"""

import socket
import time
import re
import threading
import sys
from collections import OrderedDict

import PyTango
from AttoDRYThreadDaemon import AttoDRYThread
from AttoDRYCheck import AttoDRYCheck

__all__ = ["AttoDRY", "AttoDRYClass", "main"]


class AttoDRY(PyTango.LatestDeviceImpl):
    """
    Server connecting to the AttoDRY2100 via UDP.
    Uses a daemon listener thread that sends 'Read' every 0.2 s and
    parses the 'ReadA...N' response packet to update all attributes.
    """

    def __init__(self, cl, name):
        PyTango.LatestDeviceImpl.__init__(self, cl, name)
        self.debug_stream("In __init__()")
        AttoDRY.init_device(self)
        self.set_state(PyTango.DevState.STANDBY)
        self.Connect()
        self.Start()

    def delete_device(self):
        self.debug_stream("In delete_device()")

    def init_device(self):
        self.debug_stream("In init_device()")
        self.get_device_properties(self.get_device_class())

        self.on = False
        self._cache_lock = threading.Lock()

        # Setpoint cache — used by AttoDRYCheck to detect convergence
        self.setField = 0.0
        self.setTemp  = 0.0

        # Attribute cache
        self.attr_MagneticField_read        = 0.0
        self.attr_Temperature_read          = 0.0
        self.attr_CryostatInPressure_read   = 0.0
        self.attr_CryostatOutPressure_read  = 0.0
        self.attr_MagnetTemperature_read    = 0.0
        self.attr_ReservoirTemperature_read = 0.0
        self.attr_VtiTemperature_read       = 0.0
        self.attr_SampleHeaterPower_read    = 0.0
        self.attr_ReservoirHeaterPower_read = 0.0
        self.attr_VtiHeaterPower_read       = 0.0

        self.attr_toggleMagneticFieldControl_read   = False
        self.attr_toggleFulltemperatureControl_read = False
        self.attr_togglePersistentMode_read         = False

        # Internal state mirrors
        self.is_controlling_field        = 0
        self.is_controlling_temperature  = 0
        self.is_persistent_mode_set      = 0
        self.current_magnetic_field      = 0.0
        self.sample_temperature          = 0.0
        self.vti_temperature             = 0.0
        self.magnet_temperature          = 0.0
        self.reservoir_temperature       = 0.0
        self.cryostat_out_pressure       = 0.0
        self.cryostat_in_pressure        = 0.0
        self.reservoir_heater_power      = 0.0
        self.vti_heater_power            = 0.0
        self.sample_heater_power         = 0.0

    def always_executed_hook(self):
        pass

    # ---- Attribute read/write methods -----------------------------------

    def read_MagneticField(self, attr):
        attr.set_value(self.attr_MagneticField_read)

    def write_MagneticField(self, attr):
        data = attr.get_write_value()
        self.setField = data
        if hasattr(self, 'thread') and self.thread.is_alive():
            self.thread.stop()
            self.thread.join(timeout=1.0)
        cmd = "W001:" + str(data)
        self.s.sendto(cmd.encode('utf-8'), self.server)
        self.thread = AttoDRYCheck(self)
        self.thread.start()

    def read_Temperature(self, attr):
        attr.set_value(self.attr_Temperature_read)

    def write_Temperature(self, attr):
        data = attr.get_write_value()
        self.setTemp = data
        if hasattr(self, 'thread') and self.thread.is_alive():
            self.thread.stop()
            self.thread.join(timeout=1.0)
        cmd = "W002:" + str(data)
        self.s.sendto(cmd.encode('utf-8'), self.server)
        self.thread = AttoDRYCheck(self)
        self.thread.start()

    def read_toggleMagneticFieldControl(self, attr):
        attr.set_value(self.attr_toggleMagneticFieldControl_read)

    def write_toggleMagneticFieldControl(self, attr):
        self.s.sendto('W003'.encode('utf-8'), self.server)

    def read_toggleFulltemperatureControl(self, attr):
        attr.set_value(self.attr_toggleFulltemperatureControl_read)

    def write_toggleFulltemperatureControl(self, attr):
        self.s.sendto('W004'.encode('utf-8'), self.server)

    def read_togglePersistentMode(self, attr):
        attr.set_value(self.attr_togglePersistentMode_read)

    def write_togglePersistentMode(self, attr):
        self.s.sendto('W005'.encode('utf-8'), self.server)

    def read_VtiTemperature(self, attr):
        attr.set_value(self.attr_VtiTemperature_read)

    def read_MagnetTemperature(self, attr):
        attr.set_value(self.attr_MagnetTemperature_read)

    def read_ReservoirTemperature(self, attr):
        attr.set_value(self.attr_ReservoirTemperature_read)

    def read_CryostatOutPressure(self, attr):
        attr.set_value(self.attr_CryostatOutPressure_read)

    def read_CryostatInPressure(self, attr):
        attr.set_value(self.attr_CryostatInPressure_read)

    def read_ReservoirHeaterPower(self, attr):
        attr.set_value(self.attr_ReservoirHeaterPower_read)

    def read_VtiHeaterPower(self, attr):
        attr.set_value(self.attr_VtiHeaterPower_read)

    def read_SampleHeaterPower(self, attr):
        attr.set_value(self.attr_SampleHeaterPower_read)

    def read_attr_hardware(self, data):
        pass

    # ---- Command methods ------------------------------------------------

    def Connect(self):
        """Open UDP socket and handshake with the Windows computer."""
        self.host   = self.LocalIP
        self.port   = int(self.LocalPort)
        self.server = (self.AttoIP, int(self.AttoPort))

        # Stop the daemon before closing the socket it uses, to avoid
        # recvfrom errors on the old socket and port-in-use on rebind.
        if hasattr(self, 'listener') and self.listener.is_alive():
            self.listener.stop()
            self.listener.join(timeout=2.0)
        if hasattr(self, 's'):
            try:
                self.s.close()
            except Exception:
                pass

        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.settimeout(5.0)
        self.s.bind((self.host, self.port))

        try:
            self.s.sendto('start'.encode('utf-8'), self.server)
            self.s.sendto('ON'.encode('utf-8'), self.server)
            data, addr = self.s.recvfrom(1024)
            data = data.decode('utf-8')
        except socket.timeout:
            self.info_stream('Timeout waiting for AttoDRY Windows PC response.')
            self.on = False
            self.set_state(PyTango.DevState.OFF)
            return

        if data == 'ON':
            self.set_state(PyTango.DevState.ON)
            time.sleep(0.1)
            self.on = True
        else:
            self.info_stream('Could not connect to the Windows computer...')
            print('Could not connect to the Windows computer...')
            self.on = False
            self.set_state(PyTango.DevState.OFF)

    def Disconnect(self):
        """Send OFF and close the UDP socket."""
        try:
            self.s.sendto('OFF'.encode('utf-8'), self.server)
        except Exception:
            pass
        self.set_state(PyTango.DevState.OFF)
        self.on = False
        self.info_stream('Disconnected from Windows computer...')
        self.s.close()

    def Start(self):
        """Start (or verify) the daemon listener thread."""
        if hasattr(self, 'listener') and self.listener.is_alive():
            self.info_stream("Listener already running.")
            return
        self.listener = AttoDRYThread(self)
        self.listener.start()
        self.info_stream("Listener thread started.")


class AttoDRYClass(PyTango.DeviceClass):

    class_property_list = {}

    device_property_list = {
        'AttoIP':    [PyTango.DevString, 'IP of the Windows PC running AttoDRY2100 software', ["192.168.1.8"]],
        'AttoPort':  [PyTango.DevDouble, 'UDP port on the Windows PC', [11000]],
        'LocalIP':   [PyTango.DevString, 'IP address of the local network interface to bind to', ["192.168.1.7"]],
        'LocalPort': [PyTango.DevLong,   'UDP port to bind on this machine', [11005]],
    }

    cmd_list = {
        'Connect':    [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'Disconnect': [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'Start':      [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
    }

    attr_list = OrderedDict([
        ('toggleMagneticFieldControl',   [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('togglePersistentMode',         [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('MagneticField',                [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('toggleFulltemperatureControl', [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('Temperature',                  [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('VtiTemperature',               [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('MagnetTemperature',            [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('ReservoirTemperature',         [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('CryostatOutPressure',          [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('CryostatInPressure',           [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('SampleHeaterPower',            [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('VtiHeaterPower',               [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('ReservoirHeaterPower',         [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
    ])


def main():
    try:
        py = PyTango.Util(sys.argv)
        py.add_class(AttoDRYClass, AttoDRY, 'AttoDRY')
        U = PyTango.Util.instance()
        U.server_init()
        U.server_run()
    except PyTango.DevFailed as e:
        print('-------> Received a DevFailed exception:', e)
    except Exception as e:
        print('-------> An unforeseen exception occured....', e)


if __name__ == '__main__':
    main()
