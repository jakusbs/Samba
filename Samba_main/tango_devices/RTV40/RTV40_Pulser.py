#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RTV40 TANGO Device Server
=========================
PyTango device server for the Kentech RTV40 (RTV30) High-Voltage Pulse Generator.
USB virtual COM port (FTDI VCP driver), ASCII PowerForth protocol.

Usage:
    1. Register in Jive: server RTV40/<instance>, create device
    2. Set device property SerialPort (e.g. /dev/ttyUSB0 or COM3)
    3. Run:  python RTV40_Pulser.py <instance>

Hardware specs (from RTV30 manual):
    Amplitude     >30 V into 50 Ω at 300 ps pulse width
                  >35 V for widths ≥ 400 ps
    Amplitude adj 1 V to 35 V (units of 0.1 V on the wire)
    Pulse width   300 ps to 20 ns (300 ps to 20000 ps on the wire)
    Rise/fall     ≤ 300 ps
    Max PRF       100 kHz
    Polarity      Switchable (0 = Negative, 1 = Positive)
    Trigger       0 = Off, 1 = External, 2 = Internal (10 Hz – 100 kHz)
    Jitter        < 20 ps RMS
    Remote        USB virtual COM port (FTDI VCP, 115200 baud)

Command protocol (PowerForth ASCII, each command terminated by CR):
    All commands are NOT case sensitive.
    Format: <value> !<command><CR>  — sets a value
            ?<command><CR>          — reads a value back
    The device echoes the command and appends " ok" when done.
    Query response format: <value> ok

    Wire units differ from TANGO attribute units — conversions are done here:
        Amplitude  wire = int(V × 10), range 10–350 → TANGO attr in V (1.0–35.0)
        PulseWidth wire = int(ns × 1000) in ps, range 300–20000 → TANGO in ns (0.3–20.0)
        TriggerSource wire = 0/1/2 integer → TANGO attr int 0/1/2
        Polarity      wire = 0/1 integer   → TANGO attr int 0/1
        TriggerRate   wire = integer Hz    → TANGO attr in Hz (10–100000)
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
        dtype=int, default_value=115200,
        doc="Serial baud rate. RTV40 uses 115200.")
    Timeout = device_property(
        dtype=float, default_value=1.0,
        doc="Serial readline timeout in seconds. Keep well below the TANGO client timeout (3 s).")
    LineTerminator = device_property(
        dtype=str, default_value="\r",
        doc="Command line terminator sent to device (always \\r for RTV40).")

    # ── Command string properties ─────────────────────────────────────────────
    # {val} is replaced with the integer wire-unit value before sending.
    CmdSetAmplitude = device_property(
        dtype=str, default_value="{val} !amplitude",
        doc="Set amplitude. {val} = integer in 0.1 V units (10–350).")
    CmdGetAmplitude = device_property(
        dtype=str, default_value="?amplitude",
        doc="Query amplitude. Response: integer 0.1 V units + ' ok'.")

    CmdSetWidth = device_property(
        dtype=str, default_value="{val} !width",
        doc="Set pulse width. {val} = integer in ps (300–20000).")
    CmdGetWidth = device_property(
        dtype=str, default_value="?width",
        doc="Query pulse width. Response: integer ps + ' ok'.")

    CmdSetTrigger = device_property(
        dtype=str, default_value="{val} !trigger",
        doc="Set trigger mode. {val} = 0 (Off), 1 (External), 2 (Internal).")
    CmdGetTrigSource = device_property(
        dtype=str, default_value="?trigger",
        doc="Query trigger mode. Response: 0/1/2 + ' ok'.")

    CmdSetPolarity = device_property(
        dtype=str, default_value="{val} !polarity",
        doc="Set polarity. {val} = 0 (Negative) or 1 (Positive).")
    CmdGetPolarity = device_property(
        dtype=str, default_value="?polarity",
        doc="Query polarity. Response: 0 or 1 + ' ok'.")

    CmdSetRate = device_property(
        dtype=str, default_value="{val} !rate",
        doc="Set internal trigger rate. {val} = integer Hz (10–100000).")
    CmdGetRate = device_property(
        dtype=str, default_value="?rate",
        doc="Query internal trigger rate. Response: integer Hz + ' ok'.")

    CmdLocal = device_property(
        dtype=str, default_value="local",
        doc="Return control to front panel keyboard.")
    CmdForceTrig = device_property(
        dtype=str, default_value="forcetrig",
        doc="Force a software trigger (works in all trigger modes).")

    # ── Internal state ────────────────────────────────────────────────────────
    _serial = None
    _lock = None

    _amplitude    = 10.0   # V
    _pulse_width  = 1.0    # ns
    _trig_source  = 0      # 0=Off, 1=External, 2=Internal
    _trig_rate    = 1000.0 # Hz
    _polarity     = 1      # 0=Negative, 1=Positive

    # ── TANGO attributes ──────────────────────────────────────────────────────

    Amplitude = attribute(
        label="Amplitude", unit="V",
        dtype=float, access=AttrWriteType.READ_WRITE,
        min_value=1.0, max_value=35.0,
        doc="Output amplitude in Volts (1.0–35.0 V). Wire unit: 0.1 V integer.")

    PulseWidth = attribute(
        label="Pulse width", unit="ns",
        dtype=float, access=AttrWriteType.READ_WRITE,
        min_value=0.3, max_value=20.0,
        doc="Pulse width in ns (0.3–20 ns). Wire unit: ps integer (300–20000).")

    TriggerSource = attribute(
        label="Trigger source",
        dtype=int, access=AttrWriteType.READ_WRITE,
        min_value=0, max_value=2,
        doc="Trigger mode: 0 = Off, 1 = External, 2 = Internal.")

    TriggerRate = attribute(
        label="Internal trigger rate", unit="Hz",
        dtype=float, access=AttrWriteType.READ_WRITE,
        min_value=10.0, max_value=100000.0,
        doc="Internal trigger rate in Hz (10–100 000). Wire unit: integer Hz.")

    Polarity = attribute(
        label="Output polarity",
        dtype=int, access=AttrWriteType.READ_WRITE,
        min_value=0, max_value=1,
        doc="Output polarity: 0 = Negative, 1 = Positive.")

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
            xonxoff=False,
            rtscts=False,
            timeout=self.Timeout,
        )
        time.sleep(0.1)

    def _close_serial(self):
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception:
            pass
        self._serial = None

    def _send(self, cmd: str) -> str:
        """Send cmd + CR, read the single response line (device replies with CR+LF)."""
        with self._lock:
            if not self._serial or not self._serial.is_open:
                raise RuntimeError("Serial port not open")
            raw = (cmd + self.LineTerminator).encode("ascii")
            self._serial.reset_input_buffer()
            self._serial.write(raw)
            self._serial.flush()
            resp = self._serial.readline()
            return resp.decode("ascii", errors="replace").strip()

    def _send_no_response(self, cmd: str):
        """Send cmd and wait for 'ok' acknowledgement (discarded)."""
        self._send(cmd)

    def _parse_response(self, resp: str) -> str:
        """Extract the data token from a response like '250 ok' or '5500 ok'."""
        parts = resp.split()
        # Response is typically: [echo_of_cmd...] value ok
        # The last non-'ok' token is the value for query responses.
        tokens = [p for p in parts if p.lower() != "ok"]
        return tokens[-1] if tokens else resp

    # ── TANGO commands ────────────────────────────────────────────────────────

    @command
    def Connect(self):
        """Open the serial port. Sending any character triggers remote mode."""
        try:
            self._open_serial()
            # Any character activates remote mode and elicits the banner
            self._serial.write(b"\r")
            time.sleep(0.3)
            self._serial.reset_input_buffer()
            self.set_state(DevState.ON)
            self.set_status(f"Connected on {self.SerialPort} at {self.BaudRate} baud")
        except Exception as e:
            self.set_state(DevState.FAULT)
            self.set_status(f"Connection failed: {e}")
            raise

    @command
    def Disconnect(self):
        """Send 'local' then close the serial port."""
        try:
            self._send_no_response(self.CmdLocal)
        except Exception:
            pass
        self._close_serial()
        self.set_state(DevState.OFF)
        self.set_status("Disconnected.")

    @command
    def Local(self):
        """Return control to the front panel keyboard."""
        self._send_no_response(self.CmdLocal)

    @command
    def ForceTrigger(self):
        """Force a software trigger (works in all trigger modes)."""
        self._send_no_response(self.CmdForceTrig)

    @command(dtype_in=str, dtype_out=str,
             doc_in="Raw ASCII command string.",
             doc_out="Full response string from device.")
    def SendQuery(self, cmd: str) -> str:
        """Send a raw query and return the full response. For testing/debugging."""
        return self._send(cmd)

    @command(dtype_in=str,
             doc_in="Raw ASCII command string (no response expected).")
    def SendCommand(self, cmd: str):
        """Send a raw command and wait for 'ok'. For testing/debugging."""
        self._send_no_response(cmd)

    # ── Attribute read/write ─────────────────────────────────────────────────

    def read_Amplitude(self):
        try:
            resp = self._send(self.CmdGetAmplitude)
            raw = int(self._parse_response(resp))   # 0.1 V units
            self._amplitude = raw / 10.0
        except Exception:
            pass
        return self._amplitude

    def write_Amplitude(self, val: float):
        wire = int(round(val * 10))   # V → 0.1 V integer
        wire = max(10, min(350, wire))
        self._send_no_response(self.CmdSetAmplitude.format(val=wire))
        self._amplitude = val

    def read_PulseWidth(self):
        try:
            resp = self._send(self.CmdGetWidth)
            raw = int(self._parse_response(resp))   # ps
            self._pulse_width = raw / 1000.0        # ps → ns
        except Exception:
            pass
        return self._pulse_width

    def write_PulseWidth(self, val: float):
        wire = int(round(val * 1000))   # ns → ps integer
        wire = max(300, min(20000, wire))
        self._send_no_response(self.CmdSetWidth.format(val=wire))
        self._pulse_width = val

    def read_TriggerSource(self):
        try:
            resp = self._send(self.CmdGetTrigSource)
            self._trig_source = int(self._parse_response(resp))
        except Exception:
            pass
        return self._trig_source

    def write_TriggerSource(self, val: int):
        self._send_no_response(self.CmdSetTrigger.format(val=val))
        self._trig_source = val

    def read_TriggerRate(self):
        try:
            resp = self._send(self.CmdGetRate)
            self._trig_rate = float(self._parse_response(resp))
        except Exception:
            pass
        return self._trig_rate

    def write_TriggerRate(self, val: float):
        wire = int(round(val))
        wire = max(10, min(100000, wire))
        self._send_no_response(self.CmdSetRate.format(val=wire))
        self._trig_rate = val

    def read_Polarity(self):
        try:
            resp = self._send(self.CmdGetPolarity)
            self._polarity = int(self._parse_response(resp))
        except Exception:
            pass
        return self._polarity

    def write_Polarity(self, val: int):
        self._send_no_response(self.CmdSetPolarity.format(val=val))
        self._polarity = val


def main():
    run([RTV40])


if __name__ == "__main__":
    main()
