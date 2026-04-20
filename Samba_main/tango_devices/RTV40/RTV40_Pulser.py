#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTV40 TANGO Device Server
=========================
PyTango device server for the Kentech RTV40 (RTV30) High-Voltage Pulse Generator.
USB virtual COM port (serial) communication, ASCII Forth-style protocol.

Usage:
    1. Register in Jive: server RTV40/<instance>, create device
    2. Set device property SerialPort (e.g. /dev/ttyUSB0 or COM3)
    3. Run:  python RTV40_Pulser.py <instance>

Hardware specs (from RTV30 manual):
    Amplitude     >30 V into 50 Ω at 300 ps pulse width
                  >35 V for widths ≥ 400 ps
    Amplitude adj approx 25 % to 100 %
    Pulse width   <300 ps to 20 ns (adjustable)
    Rise/fall     ≤300 ps
    Max PRF       100 kHz
    Polarity      Switchable
    Trigger       External or Internal (10 Hz – 10 kHz)
    Jitter        <20 ps RMS
    Remote        USB virtual COM port

Command protocol (Forth-style ASCII, each command terminated by CR):
    Commands are device properties so they can be updated from Jive without
    modifying this file. Defaults below are based on the RTV30 manual analysis;
    verify against the physical device with SendCommand / SendQuery.

    NOTE ON FORTH NOTATION: most Kentech pulsers push values BEFORE the word,
    e.g. "75 ampl" sets amplitude to 75 %. The CmdXxx properties encode this
    with a Python format string where {val} is the numeric value placeholder.
"""

import threading
import time

import tango
from tango import AttrWriteType, DevState, GreenMode
from tango.server import Device, attribute, command, device_property, run

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


class RTV40(Device):
    """TANGO device server for the Kentech RTV40 (/ RTV30) pulse generator."""

    green_mode = GreenMode.Synchronous

    # ── Device properties ────────────────────────────────────────────────────
    SerialPort = device_property(
        dtype=str, default_value="/dev/ttyUSB0",
        doc="Serial port path (Linux: /dev/ttyUSBx, Windows: COMx).")
    BaudRate = device_property(
        dtype=int, default_value=9600,
        doc="Serial baud rate (typically 9600 or 19200).")
    Timeout = device_property(
        dtype=float, default_value=2.0,
        doc="Serial read timeout in seconds.")
    LineTerminator = device_property(
        dtype=str, default_value="\r",
        doc="Command line terminator sent to device (\\r or \\r\\n).")

    # ── Command string properties (Forth-style; {val} = numeric value) ────────
    CmdSetAmplitude = device_property(
        dtype=str, default_value="{val} ampl",
        doc="Command to set amplitude (0–100 %). {val} is replaced with float.")
    CmdGetAmplitude = device_property(
        dtype=str, default_value="ampl?",
        doc="Query command for current amplitude.")

    CmdSetWidth = device_property(
        dtype=str, default_value="{val} width",
        doc="Command to set pulse width in ns. {val} is replaced with float.")
    CmdGetWidth = device_property(
        dtype=str, default_value="width?",
        doc="Query command for current pulse width.")

    CmdSetRate = device_property(
        dtype=str, default_value="{val} rate",
        doc="Command to set internal trigger rate in Hz. {val} = float.")
    CmdGetRate = device_property(
        dtype=str, default_value="rate?",
        doc="Query command for current internal trigger rate.")

    CmdExtTrig = device_property(
        dtype=str, default_value="ext-trig",
        doc="Command to select external trigger.")
    CmdIntTrig = device_property(
        dtype=str, default_value="int-trig",
        doc="Command to select internal trigger.")
    CmdGetTrigSource = device_property(
        dtype=str, default_value="trig?",
        doc="Query command for trigger source (expected response: 'ext' or 'int').")

    CmdPolPos = device_property(
        dtype=str, default_value="pol+",
        doc="Command to set positive output polarity.")
    CmdPolNeg = device_property(
        dtype=str, default_value="pol-",
        doc="Command to set negative output polarity.")
    CmdGetPolarity = device_property(
        dtype=str, default_value="pol?",
        doc="Query command for polarity (expected response: '+' or '-').")

    CmdEnable = device_property(
        dtype=str, default_value="out-on",
        doc="Command to enable pulse output.")
    CmdDisable = device_property(
        dtype=str, default_value="out-off",
        doc="Command to disable pulse output.")
    CmdGetEnabled = device_property(
        dtype=str, default_value="out?",
        doc="Query command for output enable state.")

    # ── Internal state ────────────────────────────────────────────────────────
    _serial = None
    _lock = None

    # Cached attribute values (updated on every read)
    _amplitude    = 0.0
    _pulse_width  = 1.0
    _trig_source  = 0      # 0=external, 1=internal
    _trig_rate    = 100.0
    _polarity     = 0      # 0=positive, 1=negative
    _output_enabled = False

    # ── TANGO attributes ──────────────────────────────────────────────────────

    Amplitude = attribute(
        label="Amplitude", unit="%",
        dtype=float, access=AttrWriteType.READ_WRITE,
        min_value=0.0, max_value=100.0,
        doc="Output amplitude as percentage of maximum (~35 V into 50 Ω).")

    PulseWidth = attribute(
        label="Pulse width", unit="ns",
        dtype=float, access=AttrWriteType.READ_WRITE,
        min_value=0.3, max_value=20.0,
        doc="Pulse width in nanoseconds (0.3 ns to 20 ns).")

    TriggerSource = attribute(
        label="Trigger source",
        dtype=int, access=AttrWriteType.READ_WRITE,
        min_value=0, max_value=1,
        doc="Trigger source: 0 = External, 1 = Internal.")

    TriggerRate = attribute(
        label="Internal trigger rate", unit="Hz",
        dtype=float, access=AttrWriteType.READ_WRITE,
        min_value=10.0, max_value=100000.0,
        doc="Internal trigger rate in Hz (10 Hz to 100 kHz).")

    Polarity = attribute(
        label="Output polarity",
        dtype=int, access=AttrWriteType.READ_WRITE,
        min_value=0, max_value=1,
        doc="Output polarity: 0 = Positive, 1 = Negative.")

    OutputEnabled = attribute(
        label="Output enabled",
        dtype=bool, access=AttrWriteType.READ_WRITE,
        doc="True when the pulse output is enabled.")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def init_device(self):
        super().init_device()
        self._lock = threading.Lock()
        self._serial = None
        self.set_state(DevState.OFF)
        self.set_status("Not connected. Use Connect command.")
        if not HAS_SERIAL:
            self.set_status("ERROR: pyserial not installed. Run: pip install pyserial")
            self.set_state(DevState.FAULT)

    def delete_device(self):
        self._close_serial()

    # ── Serial helpers ────────────────────────────────────────────────────────

    def _open_serial(self):
        if not HAS_SERIAL:
            raise RuntimeError("pyserial not installed")
        if self._serial and self._serial.is_open:
            return
        self._serial = serial.Serial(
            port=self.SerialPort,
            baudrate=self.BaudRate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.Timeout,
        )
        time.sleep(0.1)   # let the port settle

    def _close_serial(self):
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception:
            pass
        self._serial = None

    def _send(self, cmd: str) -> str:
        """Send cmd string + line terminator, read one line response."""
        with self._lock:
            if not self._serial or not self._serial.is_open:
                raise RuntimeError("Serial port not open")
            raw = (cmd + self.LineTerminator).encode("ascii")
            self._serial.reset_input_buffer()
            self._serial.write(raw)
            resp = self._serial.readline()
            return resp.decode("ascii", errors="replace").strip()

    def _send_no_response(self, cmd: str):
        """Send cmd string without reading a response."""
        with self._lock:
            if not self._serial or not self._serial.is_open:
                raise RuntimeError("Serial port not open")
            raw = (cmd + self.LineTerminator).encode("ascii")
            self._serial.write(raw)
            time.sleep(0.05)

    # ── TANGO commands ────────────────────────────────────────────────────────

    @command
    def Connect(self):
        """Open the serial port and verify communication."""
        try:
            self._open_serial()
            self.set_state(DevState.ON)
            self.set_status(f"Connected on {self.SerialPort} at {self.BaudRate} baud")
        except Exception as e:
            self.set_state(DevState.FAULT)
            self.set_status(f"Connection failed: {e}")
            raise

    @command
    def Disconnect(self):
        """Close the serial port."""
        self._close_serial()
        self.set_state(DevState.OFF)
        self.set_status("Disconnected.")

    @command
    def Enable(self):
        """Enable the pulse output."""
        self._send_no_response(self.CmdEnable)
        self._output_enabled = True

    @command
    def Disable(self):
        """Disable the pulse output."""
        self._send_no_response(self.CmdDisable)
        self._output_enabled = False

    @command(dtype_in=str, doc_in="Raw ASCII command string to send to device.")
    def SendCommand(self, cmd: str):
        """Send a raw command string (no response read). For testing/debugging."""
        self._send_no_response(cmd)

    @command(dtype_in=str, doc_out=str,
             doc_in="Raw ASCII query string.",
             doc_out="Response string from device.")
    def SendQuery(self, cmd: str) -> str:
        """Send a query and return the response line. For testing/debugging."""
        return self._send(cmd)

    # ── Attribute read/write ─────────────────────────────────────────────────

    def read_Amplitude(self):
        try:
            resp = self._send(self.CmdGetAmplitude)
            self._amplitude = float(resp)
        except Exception:
            pass
        return self._amplitude

    def write_Amplitude(self, val: float):
        cmd = self.CmdSetAmplitude.format(val=f"{val:.2f}")
        self._send_no_response(cmd)
        self._amplitude = val

    def read_PulseWidth(self):
        try:
            resp = self._send(self.CmdGetWidth)
            self._pulse_width = float(resp)
        except Exception:
            pass
        return self._pulse_width

    def write_PulseWidth(self, val: float):
        cmd = self.CmdSetWidth.format(val=f"{val:.4f}")
        self._send_no_response(cmd)
        self._pulse_width = val

    def read_TriggerSource(self):
        try:
            resp = self._send(self.CmdGetTrigSource).lower()
            self._trig_source = 1 if "int" in resp else 0
        except Exception:
            pass
        return self._trig_source

    def write_TriggerSource(self, val: int):
        cmd = self.CmdIntTrig if val == 1 else self.CmdExtTrig
        self._send_no_response(cmd)
        self._trig_source = val

    def read_TriggerRate(self):
        try:
            resp = self._send(self.CmdGetRate)
            self._trig_rate = float(resp)
        except Exception:
            pass
        return self._trig_rate

    def write_TriggerRate(self, val: float):
        cmd = self.CmdSetRate.format(val=f"{val:.2f}")
        self._send_no_response(cmd)
        self._trig_rate = val

    def read_Polarity(self):
        try:
            resp = self._send(self.CmdGetPolarity).lower()
            self._polarity = 1 if "-" in resp or "neg" in resp else 0
        except Exception:
            pass
        return self._polarity

    def write_Polarity(self, val: int):
        cmd = self.CmdPolNeg if val == 1 else self.CmdPolPos
        self._send_no_response(cmd)
        self._polarity = val

    def read_OutputEnabled(self):
        try:
            resp = self._send(self.CmdGetEnabled).lower()
            self._output_enabled = "on" in resp or "1" in resp or "true" in resp
        except Exception:
            pass
        return self._output_enabled

    def write_OutputEnabled(self, val: bool):
        if val:
            self.Enable()
        else:
            self.Disable()


def main():
    run([RTV40])


if __name__ == "__main__":
    main()
