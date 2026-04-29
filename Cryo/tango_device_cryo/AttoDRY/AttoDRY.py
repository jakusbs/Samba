# -*- coding: utf-8 -*-
"""
AttoDRY
Connects to the AttoDRY2100 cryostat via UDP to a Windows PC running
AttoDRY2100 software. A daemon listener thread polls the state every 0.2 s.
"""

import socket
import time
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
    Daemon thread sends 'Read' every 0.2 s and parses the CSV reply packet.

    UDP packet: "Read:<f0>,...,<f24>|<error_status>|<error_message>|<action_message>"
      0  isControllingField          9  getCryostatInPressure
      1  isControllingTemperature   10  getDumpPressure
      2  isPersistentModeSet        11  getReservoirHeaterPower
      3  getMagneticField           12  getVtiHeaterPower
      4  getSampleTemperature       13  getSampleHeaterPower
      5  getVtiTemperature          14  getMagneticFieldSetPoint
      6  get4KStageTemperature      15  getUserTemperature
      7  get40KStageTemperature     16  getTurbopumpFrequency
      8  getReservoirTemperature    17  getCryostatOutPressure
                                    18  isGoingToBaseTemperature
                                    19  isSampleExchangeInProgress
                                    20  isSampleReadyToExchange
                                    21  isZeroingField
                                    22  isPumping
                                    23  isSystemRunning
                                    24  isSampleHeaterOn
    string fields (after |): getAttodryErrorStatus, getAttodryErrorMessage, getActionMessage
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

        # Setpoint cache — read by AttoDRYCheck to detect convergence
        self.setField = 0.0
        self.setTemp  = 0.0

        # ── Magnetic field ────────────────────────────────────────────────
        self.attr_MagneticField_read        = 0.0
        self.attr_MagneticFieldSetpoint_read = 0.0

        # ── Temperatures ──────────────────────────────────────────────────
        self.attr_Temperature_read          = 0.0   # sample (setpoint R/W)
        self.attr_UserTemperature_read      = 0.0   # temperature setpoint readback
        self.attr_VtiTemperature_read       = 0.0
        self.attr_MagnetTemperature_read    = 0.0   # 4 K stage
        self.attr_Stage40KTemperature_read  = 0.0   # 40 K stage
        self.attr_ReservoirTemperature_read = 0.0

        # ── Pressures ─────────────────────────────────────────────────────
        self.attr_CryostatInPressure_read   = 0.0
        self.attr_CryostatOutPressure_read  = 0.0
        self.attr_DumpPressure_read         = 0.0

        # ── Heater powers ─────────────────────────────────────────────────
        self.attr_SampleHeaterPower_read    = 0.0
        self.attr_VtiHeaterPower_read       = 0.0
        self.attr_ReservoirHeaterPower_read = 0.0

        # ── Diagnostics ───────────────────────────────────────────────────
        self.attr_TurbopumpFrequency_read   = 0.0

        # ── Control toggles ───────────────────────────────────────────────
        self.attr_toggleMagneticFieldControl_read   = False
        self.attr_toggleFulltemperatureControl_read = False
        self.attr_togglePersistentMode_read         = False

        # ── Status flags ──────────────────────────────────────────────────
        self.attr_GoingToBaseTemperature_read   = False
        self.attr_SampleExchangeInProgress_read = False
        self.attr_SampleReadyToExchange_read    = False
        self.attr_ZeroingField_read             = False
        self.attr_Pumping_read                  = False
        self.attr_SystemRunning_read            = False
        self.attr_SampleHeaterOn_read           = False

        # ── Error / status messages ───────────────────────────────────────
        self.attr_ErrorStatus_read   = 0
        self.attr_ErrorMessage_read  = ''
        self.attr_ActionMessage_read = ''

        # Internal mirrors used by AttoDRYCheck
        self.current_magnetic_field     = 0.0
        self.sample_temperature         = 0.0

    def always_executed_hook(self):
        pass

    # =========================================================================
    # Magnetic field
    # =========================================================================

    def read_MagneticField(self, attr):
        attr.set_value(self.attr_MagneticField_read)

    def write_MagneticField(self, attr):
        data = attr.get_write_value()
        self.setField = data
        if hasattr(self, 'thread') and self.thread.is_alive():
            self.thread.stop()
            self.thread.join(timeout=1.0)
        self.s.sendto(('W001:' + str(data)).encode('utf-8'), self.server)
        self.thread = AttoDRYCheck(self)
        self.thread.start()

    def read_MagneticFieldSetpoint(self, attr):
        attr.set_value(self.attr_MagneticFieldSetpoint_read)

    # =========================================================================
    # Temperatures
    # =========================================================================

    def read_Temperature(self, attr):
        attr.set_value(self.attr_Temperature_read)

    def write_Temperature(self, attr):
        data = attr.get_write_value()
        self.setTemp = data
        if hasattr(self, 'thread') and self.thread.is_alive():
            self.thread.stop()
            self.thread.join(timeout=1.0)
        self.s.sendto(('W002:' + str(data)).encode('utf-8'), self.server)
        self.thread = AttoDRYCheck(self)
        self.thread.start()

    def read_UserTemperature(self, attr):
        attr.set_value(self.attr_UserTemperature_read)

    def read_VtiTemperature(self, attr):
        attr.set_value(self.attr_VtiTemperature_read)

    def read_MagnetTemperature(self, attr):
        attr.set_value(self.attr_MagnetTemperature_read)

    def read_Stage40KTemperature(self, attr):
        attr.set_value(self.attr_Stage40KTemperature_read)

    def read_ReservoirTemperature(self, attr):
        attr.set_value(self.attr_ReservoirTemperature_read)

    # =========================================================================
    # Pressures
    # =========================================================================

    def read_CryostatInPressure(self, attr):
        attr.set_value(self.attr_CryostatInPressure_read)

    def read_CryostatOutPressure(self, attr):
        attr.set_value(self.attr_CryostatOutPressure_read)

    def read_DumpPressure(self, attr):
        attr.set_value(self.attr_DumpPressure_read)

    # =========================================================================
    # Heater powers
    # =========================================================================

    def read_SampleHeaterPower(self, attr):
        attr.set_value(self.attr_SampleHeaterPower_read)

    def read_VtiHeaterPower(self, attr):
        attr.set_value(self.attr_VtiHeaterPower_read)

    def read_ReservoirHeaterPower(self, attr):
        attr.set_value(self.attr_ReservoirHeaterPower_read)

    # =========================================================================
    # Diagnostics
    # =========================================================================

    def read_TurbopumpFrequency(self, attr):
        attr.set_value(self.attr_TurbopumpFrequency_read)

    # =========================================================================
    # Control toggles (R/W)
    # =========================================================================

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

    # =========================================================================
    # Status flags (read-only)
    # =========================================================================

    def read_GoingToBaseTemperature(self, attr):
        attr.set_value(self.attr_GoingToBaseTemperature_read)

    def read_SampleExchangeInProgress(self, attr):
        attr.set_value(self.attr_SampleExchangeInProgress_read)

    def read_SampleReadyToExchange(self, attr):
        attr.set_value(self.attr_SampleReadyToExchange_read)

    def read_ZeroingField(self, attr):
        attr.set_value(self.attr_ZeroingField_read)

    def read_Pumping(self, attr):
        attr.set_value(self.attr_Pumping_read)

    def read_SystemRunning(self, attr):
        attr.set_value(self.attr_SystemRunning_read)

    def read_SampleHeaterOn(self, attr):
        attr.set_value(self.attr_SampleHeaterOn_read)

    # =========================================================================
    # Error / status messages
    # =========================================================================

    def read_ErrorStatus(self, attr):
        attr.set_value(self.attr_ErrorStatus_read)

    def read_ErrorMessage(self, attr):
        attr.set_value(self.attr_ErrorMessage_read)

    def read_ActionMessage(self, attr):
        attr.set_value(self.attr_ActionMessage_read)

    def read_attr_hardware(self, data):
        pass

    # =========================================================================
    # Commands — connection
    # =========================================================================

    def Connect(self):
        """Open UDP socket and handshake with the Windows computer."""
        self.host   = self.LocalIP
        self.port   = int(self.LocalPort)
        self.server = (self.AttoIP, int(self.AttoPort))

        # Stop daemon and close old socket before rebinding to avoid port collision.
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

    # =========================================================================
    # Commands — cryostat operations
    # =========================================================================

    def GoToBaseTemperature(self):
        """Cool the cryostat to its base temperature."""
        self.s.sendto('W006'.encode('utf-8'), self.server)

    def StartSampleExchange(self):
        """Start the sample exchange sequence (warms up the sample space)."""
        self.s.sendto('W007'.encode('utf-8'), self.server)

    def SweepFieldToZero(self):
        """Sweep the magnetic field to zero T."""
        self.s.sendto('W008'.encode('utf-8'), self.server)

    def Cancel(self):
        """Cancel any ongoing operation (base temp, sample exchange, zeroing)."""
        self.s.sendto('W009'.encode('utf-8'), self.server)

    def LowerError(self):
        """Clear the current error condition on the AttoDRY."""
        self.s.sendto('W010'.encode('utf-8'), self.server)

    def toggleStartUpShutdown(self):
        """Toggle the AttoDRY startup / shutdown sequence."""
        self.s.sendto('W011'.encode('utf-8'), self.server)

    def toggleSampleTemperatureControl(self):
        """Toggle sample-only temperature control (independent of VTI)."""
        self.s.sendto('W012'.encode('utf-8'), self.server)


# =============================================================================
# Device class descriptor
# =============================================================================

class AttoDRYClass(PyTango.DeviceClass):

    class_property_list = {}

    device_property_list = {
        'AttoIP':    [PyTango.DevString, 'IP of the Windows PC running AttoDRY2100 software', ["192.168.1.8"]],
        'AttoPort':  [PyTango.DevDouble, 'UDP port on the Windows PC', [11000]],
        'LocalIP':   [PyTango.DevString, 'IP of the local NIC to bind to (0.0.0.0 = all interfaces)', ["0.0.0.0"]],
        'LocalPort': [PyTango.DevLong,   'UDP port to bind on this machine', [11005]],
    }

    cmd_list = {
        # Connection
        'Connect':                      [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'Disconnect':                   [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'Start':                        [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        # Cryostat operations
        'GoToBaseTemperature':          [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'StartSampleExchange':          [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'SweepFieldToZero':             [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'Cancel':                       [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'LowerError':                   [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'toggleStartUpShutdown':        [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
        'toggleSampleTemperatureControl': [[PyTango.DevVoid, "none"], [PyTango.DevVoid, "none"]],
    }

    attr_list = OrderedDict([
        # ── Magnetic field ────────────────────────────────────────────────
        ('MagneticField',                [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('MagneticFieldSetpoint',        [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),

        # ── Temperatures ──────────────────────────────────────────────────
        ('Temperature',                  [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('UserTemperature',              [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('VtiTemperature',               [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('MagnetTemperature',            [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('Stage40KTemperature',          [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('ReservoirTemperature',         [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),

        # ── Pressures ─────────────────────────────────────────────────────
        ('CryostatInPressure',           [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('CryostatOutPressure',          [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('DumpPressure',                 [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),

        # ── Heater powers ─────────────────────────────────────────────────
        ('SampleHeaterPower',            [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('VtiHeaterPower',               [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),
        ('ReservoirHeaterPower',         [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),

        # ── Diagnostics ───────────────────────────────────────────────────
        ('TurbopumpFrequency',           [[PyTango.DevDouble,  PyTango.SCALAR, PyTango.READ]]),

        # ── Control toggles ───────────────────────────────────────────────
        ('toggleMagneticFieldControl',   [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('toggleFulltemperatureControl', [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ_WRITE]]),
        ('togglePersistentMode',         [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ_WRITE]]),

        # ── Status flags ──────────────────────────────────────────────────
        ('SystemRunning',                [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ]]),
        ('Pumping',                      [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ]]),
        ('GoingToBaseTemperature',       [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ]]),
        ('ZeroingField',                 [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ]]),
        ('SampleExchangeInProgress',     [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ]]),
        ('SampleReadyToExchange',        [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ]]),
        ('SampleHeaterOn',               [[PyTango.DevBoolean, PyTango.SCALAR, PyTango.READ]]),

        # ── Error / status messages ───────────────────────────────────────
        ('ErrorStatus',                  [[PyTango.DevLong,    PyTango.SCALAR, PyTango.READ]]),
        ('ErrorMessage',                 [[PyTango.DevString,  PyTango.SCALAR, PyTango.READ]]),
        ('ActionMessage',                [[PyTango.DevString,  PyTango.SCALAR, PyTango.READ]]),
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
