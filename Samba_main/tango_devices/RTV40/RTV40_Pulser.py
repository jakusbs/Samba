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
    4. Call the Connect command from Jive to open the port.

Hardware specs (from RTV30 manual):
    Amplitude     >30 V into 50 Ω at 300 ps pulse width
                  >35 V for widths ≥ 400 ps
    Amplitude adj 1 V to 35 V (units of 0.1 V on the wire)
    Pulse width   300 ps to 20 ns (300–20000 ps on the wire)
    Rise/fall     ≤ 300 ps
    Max PRF       100 kHz
    Polarity      Switchable (0 = Negative, 1 = Positive)
    Trigger       0 = Off, 1 = External, 2 = Internal (10 Hz – 100 kHz)
    Jitter        < 20 ps RMS
    Remote        USB virtual COM port (FTDI VCP, 115200 baud)

Command protocol (PowerForth ASCII, terminated by CR):
    Set:   <value> !<command><CR>   — device replies: <echo> ok<CR><LF>
    Query: ?<command><CR>           — device replies: <value> ok<CR><LF>
    Commands are NOT case sensitive.

Wire unit conversions:
    Amplitude  wire = int(V × 10), range 10–350  ↔  TANGO attr in V (1.0–35.0)
    PulseWidth wire = int(ns × 1000) in ps, range 300–20000  ↔  TANGO in ns
    TriggerSource wire = 0/1/2 integer  ↔  TANGO int 0/1/2
    Polarity      wire = 0/1 integer    ↔  TANGO int 0/1
    TriggerRate   wire = integer Hz     ↔  TANGO float Hz

Threading model:
    A single background thread (_poll_loop) owns all serial reads.
    TANGO attribute read methods return cached values only — no serial I/O.
    Write methods send the command through the shared lock and update the cache.
    This prevents command interleaving when TANGO polls multiple attributes.
"""

import threading
import time

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
        doc="Serial readline timeout in seconds. Must be < TANGO client timeout (3 s).")
    PollInterval = device_property(
        dtype=float, default_value=2.0,
        doc="Seconds between background read cycles. Set 0 to disable polling.")

    # ── Command string properties ─────────────────────────────────────────────
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
    _serial      = None
    _lock        = None
    _poll_thread = None
    _stop_poll   = None

    # Cached attribute values (updated only by _poll_all, written by write methods)
    _amplitude   = 10.0
    _pulse_width = 1.0
    _trig_source = 0
    _trig_rate   = 1000.0
    _polarity    = 1

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
        self._lock      = threading.Lock()
        self._stop_poll = threading.Event()
        self._serial    = None
        self._poll_thread = None
        self.set_state(DevState.OFF)
        self.set_status("Not connected. Use Connect command.")
        if not HAS_SERIAL:
            self.set_status("ERROR: pyserial not installed. Run: pip install pyserial")
            self.set_state(DevState.FAULT)

    def delete_device(self):
        self._stop_polling()
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
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        time.sleep(0.1)

    def _close_serial(self):
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception:
            pass
        self._serial = None

    def _send(self, cmd: str) -> str:
        """Send one command and read one response line. Caller must hold _lock."""
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not open")
        raw = (cmd + "\r").encode()
        self._serial.reset_input_buffer()
        self._serial.write(raw)
        self._serial.flush()
        resp = self._serial.readline()
        return resp.decode("ascii", errors="replace").strip()

    def _parse_response(self, resp: str) -> str:
        """Return the data token from a response like '250 ok' or '?rate 5000 ok'."""
        tokens = [t for t in resp.split() if t.lower() != "ok"]
        return tokens[-1] if tokens else ""

    # ── Background poll thread ────────────────────────────────────────────────

    def _start_polling(self):
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="RTV40-poll", daemon=True)
        self._poll_thread.start()

    def _stop_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            self._stop_poll.set()
            self._poll_thread.join(timeout=5.0)
        self._poll_thread = None
        self._stop_poll = threading.Event()

    def _poll_loop(self):
        """Read all attributes from the device in a loop; update cache."""
        while not self._stop_poll.is_set():
            self._poll_all()
            self._stop_poll.wait(self.PollInterval)

    def _poll_all(self):
        """One complete read cycle — all five attributes, serialised by _lock."""
        reads = [
            (self.CmdGetAmplitude,  self._update_amplitude),
            (self.CmdGetWidth,      self._update_width),
            (self.CmdGetTrigSource, self._update_trig_source),
            (self.CmdGetRate,       self._update_trig_rate),
            (self.CmdGetPolarity,   self._update_polarity),
        ]
        for cmd, updater in reads:
            if self._stop_poll.is_set():
                return
            with self._lock:
                try:
                    resp = self._send(cmd)
                    updater(resp)
                except Exception as e:
                    self.warn_stream(f"poll {cmd!r}: {e!r}")

    def _update_amplitude(self, resp):
        self._amplitude = int(self._parse_response(resp)) / 10.0

    def _update_width(self, resp):
        self._pulse_width = int(self._parse_response(resp)) / 1000.0

    def _update_trig_source(self, resp):
        self._trig_source = int(self._parse_response(resp))

    def _update_trig_rate(self, resp):
        self._trig_rate = float(self._parse_response(resp))

    def _update_polarity(self, resp):
        self._polarity = int(self._parse_response(resp))

    # ── TANGO commands ────────────────────────────────────────────────────────

    @command
    def Connect(self):
        """Open the serial port and start the background poll thread."""
        try:
            self._stop_polling()
            self._open_serial()
            # Trigger remote mode; wait for banner then discard it
            self._serial.write(b"\r")
            self._serial.flush()
            time.sleep(1.0)
            self._serial.reset_input_buffer()
            if self.PollInterval > 0:
                self._start_polling()
            self.set_state(DevState.ON)
            self.set_status(
                f"Connected on {self.SerialPort} at {self.BaudRate} baud "
                f"(poll every {self.PollInterval} s)")
        except Exception as e:
            self.set_state(DevState.FAULT)
            self.set_status(f"Connection failed: {e}")
            raise

    @command
    def Disconnect(self):
        """Stop the poll thread, send 'local', close the port."""
        self._stop_polling()
        try:
            with self._lock:
                self._send(self.CmdLocal)
        except Exception:
            pass
        self._close_serial()
        self.set_state(DevState.OFF)
        self.set_status("Disconnected.")

    @command
    def Local(self):
        """Return control to the front panel keyboard."""
        with self._lock:
            self._send(self.CmdLocal)

    @command
    def ForceTrigger(self):
        """Force a software trigger (works in all trigger modes)."""
        with self._lock:
            self._send(self.CmdForceTrig)

    @command(dtype_in=str, dtype_out=str,
             doc_in="Raw ASCII command string.",
             doc_out="Full response string from device.")
    def SendQuery(self, cmd: str) -> str:
        """Send a raw query; returns response. Pauses poll thread via lock."""
        with self._lock:
            return self._send(cmd)

    @command(dtype_in=str,
             doc_in="Raw ASCII command string.")
    def SendCommand(self, cmd: str):
        """Send a raw command. Pauses poll thread via lock."""
        with self._lock:
            self._send(cmd)

    # ── Attribute reads — return cached values, no serial I/O ────────────────

    def read_Amplitude(self):
        return self._amplitude

    def read_PulseWidth(self):
        return self._pulse_width

    def read_TriggerSource(self):
        return self._trig_source

    def read_TriggerRate(self):
        return self._trig_rate

    def read_Polarity(self):
        return self._polarity

    # ── Attribute writes — send command, update cache ─────────────────────────

    def write_Amplitude(self, val: float):
        wire = max(10, min(350, int(round(val * 10))))
        with self._lock:
            self._send(self.CmdSetAmplitude.format(val=wire))
        self._amplitude = val

    def write_PulseWidth(self, val: float):
        wire = max(300, min(20000, int(round(val * 1000))))
        with self._lock:
            self._send(self.CmdSetWidth.format(val=wire))
        self._pulse_width = val

    def write_TriggerSource(self, val: int):
        with self._lock:
            self._send(self.CmdSetTrigger.format(val=val))
        self._trig_source = val

    def write_TriggerRate(self, val: float):
        wire = max(10, min(100000, int(round(val))))
        with self._lock:
            self._send(self.CmdSetRate.format(val=wire))
        self._trig_rate = val

    def write_Polarity(self, val: int):
        with self._lock:
            self._send(self.CmdSetPolarity.format(val=val))
        self._polarity = val


def main():
    run([RTV40])


if __name__ == "__main__":
    main()
