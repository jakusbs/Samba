#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DG645 TANGO Device Server
=========================
PyTango device server for the Stanford Research Systems DG645 Digital Delay
Generator. Ethernet (TCP/IP socket) communication.

Full control: 8 delay channels, 5 outputs, trigger config, burst mode,
prescalers, settings storage.

Usage:
    1. Register in Jive: server DG645/<instance>, create device
    2. Set device properties: Host (mandatory), Port (default 5025)
    3. Run:  python DG645.py <instance>

DG645 Command Reference (from SRS manual rev 1.1):
    DLAY i,j,t / DLAY?i    Delay channel i rel. to j by t seconds
    TSRC i / TSRC?          Trigger source (0=Int,1=ExtR,2=ExtF,3=SS,4=Line)
    TRAT f / TRAT?          Trigger rate Hz
    TLVL v / TLVL?          Trigger level V
    HOLD t / HOLD?          Trigger holdoff s
    PRES i,n / PRES?i       Prescaler (i: 0=trig, 1=AB, 2=CD, 3=EF, 4=GH)
    LAMP i,v / LAMP?i       Output amplitude V
    LOFF i,v / LOFF?i       Output offset V
    LPOL i,p / LPOL?i       Output polarity (0=neg, 1=pos)
    BURM i / BURM?          Burst mode enable
    BURC n / BURC?          Burst count
    BURP t / BURP?          Burst period s
    BURD t / BURD?          Burst delay s
    BURT i / BURT?          Burst T0 first only
    INHB i / INHB?          Trigger inhibit mode
    LERR?                   Last error code
    INSE?                   Instrument status register
    *IDN? *RST *TRG *SAV *RCL *CLS   Standard IEEE-488.2

Channel indices: T0=0 T1=1 A=2 B=3 C=4 D=5 E=6 F=7 G=8 H=9
Output  indices: T0=0 AB=1 CD=2 EF=3 GH=4
"""

import socket
import time
import threading

import tango
from tango import AttrWriteType, DevState, DispLevel, GreenMode
from tango.server import (Device, attribute, command,
                          device_property, class_property, run)


class DG645(Device):
    """TANGO device for SRS DG645 Digital Delay Generator."""

    green_mode = GreenMode.Synchronous

    # === Class Properties ===
    CommunicationTimeout = class_property(
        dtype=float, default_value=3.0,
        doc="Socket timeout in seconds.")
    ReconnectInterval = class_property(
        dtype=float, default_value=5.0,
        doc="Min seconds between reconnect attempts.")
    PollingPeriod = class_property(
        dtype=int, default_value=3000,
        doc="Default state polling period (ms).")

    # === Device Properties ===
    Host = device_property(dtype=str, mandatory=True,
                           doc="DG645 IP address or hostname.")
    Port = device_property(dtype=int, default_value=5025,
                           doc="DG645 TCP port (default 5025).")

    # === Internal ===
    _socket = None
    _lock = None
    _last_reconnect = 0.0

    # -----------------------------------------------------------------
    # Init / delete
    # -----------------------------------------------------------------
    def init_device(self):
        Device.init_device(self)
        self._lock = threading.Lock()
        self.set_state(DevState.INIT)
        self.set_status("Initializing...")
        try:
            self._connect()
            idn = self._query("*IDN?")
            if "DG645" not in idn:
                self.set_state(DevState.FAULT)
                self.set_status(f"Unexpected identity: {idn}")
                return
            self.set_state(DevState.ON)
            self.set_status(f"Connected: {idn}")
            self.info_stream(f"Connected: {idn}")
        except Exception as e:
            self.set_state(DevState.FAULT)
            self.set_status(f"Connection failed: {e}")
            self.error_stream(f"init_device: {e}")

    def delete_device(self):
        self._disconnect()

    # -----------------------------------------------------------------
    # Socket layer
    # -----------------------------------------------------------------
    def _connect(self):
        self._disconnect()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(self.CommunicationTimeout)
        self._socket.connect((self.Host, self.Port))
        self._socket.settimeout(0.2)
        try:
            self._socket.recv(4096)
        except socket.timeout:
            pass
        self._socket.settimeout(self.CommunicationTimeout)

    def _disconnect(self):
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def _reconnect(self):
        now = time.time()
        if now - self._last_reconnect < self.ReconnectInterval:
            raise RuntimeError("Reconnect rate-limited.")
        self._last_reconnect = now
        self._connect()
        idn = self._query("*IDN?")
        self.set_state(DevState.ON)
        self.set_status(f"Reconnected: {idn}")

    def _send(self, cmd):
        with self._lock:
            if not self._socket:
                self._reconnect()
            try:
                self._socket.sendall((cmd + "\n").encode("ascii"))
            except (socket.error, OSError) as e:
                self.set_state(DevState.FAULT)
                self.set_status(f"Comm error: {e}")
                self._socket = None
                raise

    def _query(self, cmd):
        with self._lock:
            if not self._socket:
                self._reconnect()
            try:
                self._socket.sendall((cmd + "\n").encode("ascii"))
                data = b""
                while b"\n" not in data and b"\r" not in data:
                    chunk = self._socket.recv(4096)
                    if not chunk:
                        raise ConnectionError("Socket closed.")
                    data += chunk
                return data.decode("ascii").strip()
            except (socket.error, OSError) as e:
                self.set_state(DevState.FAULT)
                self.set_status(f"Comm error: {e}")
                self._socket = None
                raise

    # -----------------------------------------------------------------
    # Delay attributes  (A=2 .. H=9)
    # DLAY? ch returns "ref,delay".  DLAY ch,ref,delay sets both.
    # -----------------------------------------------------------------
    def _read_delay(self, ch):
        parts = self._query(f"DLAY?{ch}").split(",")
        return float(parts[1])

    def _read_ref(self, ch):
        parts = self._query(f"DLAY?{ch}").split(",")
        return int(parts[0])

    def _write_delay(self, ch, val):
        """Write delay, preserving the current reference channel."""
        ref = self._read_ref(ch)
        self._send(f"DLAY {ch},{ref},{val:.12e}")

    def _write_ref(self, ch, ref):
        """Write reference channel, preserving the current delay value."""
        delay = self._read_delay(ch)
        self._send(f"DLAY {ch},{ref},{delay:.12e}")

    DelayA = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
                       unit="s", display_unit="ns", format="%14.12e",
                       label="Delay A", display_level=DispLevel.OPERATOR)
    def read_DelayA(self):     return self._read_delay(2)
    def write_DelayA(self, v): self._write_delay(2, v)

    DelayB = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
                       unit="s", display_unit="ns", format="%14.12e",
                       label="Delay B", display_level=DispLevel.OPERATOR)
    def read_DelayB(self):     return self._read_delay(3)
    def write_DelayB(self, v): self._write_delay(3, v)

    DelayC = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
                       unit="s", display_unit="ns", format="%14.12e",
                       label="Delay C", display_level=DispLevel.OPERATOR)
    def read_DelayC(self):     return self._read_delay(4)
    def write_DelayC(self, v): self._write_delay(4, v)

    DelayD = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
                       unit="s", display_unit="ns", format="%14.12e",
                       label="Delay D", display_level=DispLevel.OPERATOR)
    def read_DelayD(self):     return self._read_delay(5)
    def write_DelayD(self, v): self._write_delay(5, v)

    DelayE = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
                       unit="s", display_unit="ns", format="%14.12e",
                       label="Delay E", display_level=DispLevel.OPERATOR)
    def read_DelayE(self):     return self._read_delay(6)
    def write_DelayE(self, v): self._write_delay(6, v)

    DelayF = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
                       unit="s", display_unit="ns", format="%14.12e",
                       label="Delay F", display_level=DispLevel.OPERATOR)
    def read_DelayF(self):     return self._read_delay(7)
    def write_DelayF(self, v): self._write_delay(7, v)

    DelayG = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
                       unit="s", display_unit="ns", format="%14.12e",
                       label="Delay G", display_level=DispLevel.OPERATOR)
    def read_DelayG(self):     return self._read_delay(8)
    def write_DelayG(self, v): self._write_delay(8, v)

    DelayH = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
                       unit="s", display_unit="ns", format="%14.12e",
                       label="Delay H", display_level=DispLevel.OPERATOR)
    def read_DelayH(self):     return self._read_delay(9)
    def write_DelayH(self, v): self._write_delay(9, v)

    # Reference channel per delay (0=T0, 1=T1, 2=A, 3=B, … 9=H)
    DelayRefA = attribute(dtype=int, access=AttrWriteType.READ_WRITE, label="Ref A")
    def read_DelayRefA(self):     return self._read_ref(2)
    def write_DelayRefA(self, v): self._write_ref(2, v)

    DelayRefB = attribute(dtype=int, access=AttrWriteType.READ_WRITE, label="Ref B")
    def read_DelayRefB(self):     return self._read_ref(3)
    def write_DelayRefB(self, v): self._write_ref(3, v)

    DelayRefC = attribute(dtype=int, access=AttrWriteType.READ_WRITE, label="Ref C")
    def read_DelayRefC(self):     return self._read_ref(4)
    def write_DelayRefC(self, v): self._write_ref(4, v)

    DelayRefD = attribute(dtype=int, access=AttrWriteType.READ_WRITE, label="Ref D")
    def read_DelayRefD(self):     return self._read_ref(5)
    def write_DelayRefD(self, v): self._write_ref(5, v)

    DelayRefE = attribute(dtype=int, access=AttrWriteType.READ_WRITE, label="Ref E")
    def read_DelayRefE(self):     return self._read_ref(6)
    def write_DelayRefE(self, v): self._write_ref(6, v)

    DelayRefF = attribute(dtype=int, access=AttrWriteType.READ_WRITE, label="Ref F")
    def read_DelayRefF(self):     return self._read_ref(7)
    def write_DelayRefF(self, v): self._write_ref(7, v)

    DelayRefG = attribute(dtype=int, access=AttrWriteType.READ_WRITE, label="Ref G")
    def read_DelayRefG(self):     return self._read_ref(8)
    def write_DelayRefG(self, v): self._write_ref(8, v)

    DelayRefH = attribute(dtype=int, access=AttrWriteType.READ_WRITE, label="Ref H")
    def read_DelayRefH(self):     return self._read_ref(9)
    def write_DelayRefH(self, v): self._write_ref(9, v)

    # Pulse widths (read-only convenience)
    PulseWidthAB = attribute(dtype=float, access=AttrWriteType.READ,
                             unit="s", display_unit="ns", format="%14.12e",
                             label="Width AB")
    def read_PulseWidthAB(self):
        return self._read_delay(3) - self._read_delay(2)

    PulseWidthCD = attribute(dtype=float, access=AttrWriteType.READ,
                             unit="s", display_unit="ns", format="%14.12e",
                             label="Width CD")
    def read_PulseWidthCD(self):
        return self._read_delay(5) - self._read_delay(4)

    PulseWidthEF = attribute(dtype=float, access=AttrWriteType.READ,
                             unit="s", display_unit="ns", format="%14.12e",
                             label="Width EF")
    def read_PulseWidthEF(self):
        return self._read_delay(7) - self._read_delay(6)

    PulseWidthGH = attribute(dtype=float, access=AttrWriteType.READ,
                             unit="s", display_unit="ns", format="%14.12e",
                             label="Width GH")
    def read_PulseWidthGH(self):
        return self._read_delay(9) - self._read_delay(8)

    # -----------------------------------------------------------------
    # Trigger
    # -----------------------------------------------------------------
    TriggerSource = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Trigger Source",
        doc="0=Int 1=ExtR 2=ExtF 3=SS 4=Line")
    def read_TriggerSource(self):
        return int(self._query("TSRC?"))
    def write_TriggerSource(self, v):
        self._send(f"TSRC {v}")

    TriggerRate = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="Hz", format="%10.6f", label="Trigger Rate",
        doc="Internal rate, 100 µHz to 10 MHz.")
    def read_TriggerRate(self):
        return float(self._query("TRAT?"))
    def write_TriggerRate(self, v):
        self._send(f"TRAT {v:.6f}")

    TriggerLevel = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%6.4f", label="Trigger Level",
        doc="Ext trigger threshold, ±3.5 V.")
    def read_TriggerLevel(self):
        return float(self._query("TLVL?"))
    def write_TriggerLevel(self, v):
        self._send(f"TLVL {v:.4f}")

    TriggerHoldoff = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="s", format="%14.12e", label="Holdoff")
    def read_TriggerHoldoff(self):
        return float(self._query("HOLD?"))
    def write_TriggerHoldoff(self, v):
        self._send(f"HOLD {v:.12e}")

    TriggerPrescale = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Trigger Prescale", doc="Divides ext trigger (1 to 2^30-1).")
    def read_TriggerPrescale(self):
        return int(self._query("PRES?0"))
    def write_TriggerPrescale(self, v):
        self._send(f"PRES 0,{v}")

    TriggerInhibit = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Trigger Inhibit",
        doc="0=Off 1=Triggers 2=AB 3=AB+CD 4=All")
    def read_TriggerInhibit(self):
        return int(self._query("INHB?"))
    def write_TriggerInhibit(self, v):
        self._send(f"INHB {v}")

    # -----------------------------------------------------------------
    # Output levels
    # -----------------------------------------------------------------
    def _read_amp(self, i):  return float(self._query(f"LAMP?{i}"))
    def _write_amp(self, i, v): self._send(f"LAMP {i},{v:.3f}")
    def _read_off(self, i):  return float(self._query(f"LOFF?{i}"))
    def _write_off(self, i, v): self._send(f"LOFF {i},{v:.3f}")
    def _read_pol(self, i):  return int(self._query(f"LPOL?{i}"))
    def _write_pol(self, i, v): self._send(f"LPOL {i},{v}")

    # T0
    AmplitudeT0 = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Amp T0")
    def read_AmplitudeT0(self): return self._read_amp(0)
    def write_AmplitudeT0(self, v): self._write_amp(0, v)
    OffsetT0 = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Offset T0")
    def read_OffsetT0(self): return self._read_off(0)
    def write_OffsetT0(self, v): self._write_off(0, v)
    PolarityT0 = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Pol T0", doc="0=neg 1=pos")
    def read_PolarityT0(self): return self._read_pol(0)
    def write_PolarityT0(self, v): self._write_pol(0, v)

    # AB
    AmplitudeAB = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Amp AB")
    def read_AmplitudeAB(self): return self._read_amp(1)
    def write_AmplitudeAB(self, v): self._write_amp(1, v)
    OffsetAB = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Offset AB")
    def read_OffsetAB(self): return self._read_off(1)
    def write_OffsetAB(self, v): self._write_off(1, v)
    PolarityAB = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Pol AB", doc="0=neg 1=pos")
    def read_PolarityAB(self): return self._read_pol(1)
    def write_PolarityAB(self, v): self._write_pol(1, v)

    # CD
    AmplitudeCD = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Amp CD")
    def read_AmplitudeCD(self): return self._read_amp(2)
    def write_AmplitudeCD(self, v): self._write_amp(2, v)
    OffsetCD = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Offset CD")
    def read_OffsetCD(self): return self._read_off(2)
    def write_OffsetCD(self, v): self._write_off(2, v)
    PolarityCD = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Pol CD")
    def read_PolarityCD(self): return self._read_pol(2)
    def write_PolarityCD(self, v): self._write_pol(2, v)

    # EF
    AmplitudeEF = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Amp EF")
    def read_AmplitudeEF(self): return self._read_amp(3)
    def write_AmplitudeEF(self, v): self._write_amp(3, v)
    OffsetEF = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Offset EF")
    def read_OffsetEF(self): return self._read_off(3)
    def write_OffsetEF(self, v): self._write_off(3, v)
    PolarityEF = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Pol EF")
    def read_PolarityEF(self): return self._read_pol(3)
    def write_PolarityEF(self, v): self._write_pol(3, v)

    # GH
    AmplitudeGH = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Amp GH")
    def read_AmplitudeGH(self): return self._read_amp(4)
    def write_AmplitudeGH(self, v): self._write_amp(4, v)
    OffsetGH = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="V", format="%5.3f", label="Offset GH")
    def read_OffsetGH(self): return self._read_off(4)
    def write_OffsetGH(self, v): self._write_off(4, v)
    PolarityGH = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Pol GH")
    def read_PolarityGH(self): return self._read_pol(4)
    def write_PolarityGH(self, v): self._write_pol(4, v)

    # -----------------------------------------------------------------
    # Output prescalers
    # -----------------------------------------------------------------
    PrescaleAB = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Prescale AB")
    def read_PrescaleAB(self): return int(self._query("PRES?1"))
    def write_PrescaleAB(self, v): self._send(f"PRES 1,{v}")

    PrescaleCD = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Prescale CD")
    def read_PrescaleCD(self): return int(self._query("PRES?2"))
    def write_PrescaleCD(self, v): self._send(f"PRES 2,{v}")

    PrescaleEF = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Prescale EF")
    def read_PrescaleEF(self): return int(self._query("PRES?3"))
    def write_PrescaleEF(self, v): self._send(f"PRES 3,{v}")

    PrescaleGH = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Prescale GH")
    def read_PrescaleGH(self): return int(self._query("PRES?4"))
    def write_PrescaleGH(self, v): self._send(f"PRES 4,{v}")

    # -----------------------------------------------------------------
    # Burst mode
    # -----------------------------------------------------------------
    BurstMode = attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
        label="Burst Mode")
    def read_BurstMode(self): return bool(int(self._query("BURM?")))
    def write_BurstMode(self, v): self._send(f"BURM {1 if v else 0}")

    BurstCount = attribute(dtype=int, access=AttrWriteType.READ_WRITE,
        label="Burst Count", doc="1 to 2^32-1")
    def read_BurstCount(self): return int(self._query("BURC?"))
    def write_BurstCount(self, v): self._send(f"BURC {v}")

    BurstPeriod = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="s", format="%14.12e", label="Burst Period",
        doc="100 ns to 42.9 s, 10 ns steps.")
    def read_BurstPeriod(self): return float(self._query("BURP?"))
    def write_BurstPeriod(self, v): self._send(f"BURP {v:.12e}")

    BurstDelay = attribute(dtype=float, access=AttrWriteType.READ_WRITE,
        unit="s", format="%14.12e", label="Burst Delay")
    def read_BurstDelay(self): return float(self._query("BURD?"))
    def write_BurstDelay(self, v): self._send(f"BURD {v:.12e}")

    BurstT0First = attribute(dtype=bool, access=AttrWriteType.READ_WRITE,
        label="Burst T0 First Only")
    def read_BurstT0First(self): return bool(int(self._query("BURT?")))
    def write_BurstT0First(self, v): self._send(f"BURT {1 if v else 0}")

    # -----------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------
    Identity = attribute(dtype=str, access=AttrWriteType.READ, label="IDN")
    def read_Identity(self): return self._query("*IDN?")

    InstrumentStatus = attribute(dtype=int, access=AttrWriteType.READ,
        label="Status Register")
    def read_InstrumentStatus(self): return int(self._query("INSE?"))

    LastError = attribute(dtype=int, access=AttrWriteType.READ,
        label="Last Error", doc="0 = no error")
    def read_LastError(self): return int(self._query("LERR?"))

    # -----------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------
    @command
    def Reset(self):
        """Reset to factory defaults."""
        self._send("*RST")

    @command
    def SingleShot(self):
        """Fire single trigger."""
        self._send("*TRG")

    @command
    def Reconnect(self):
        """Force reconnection."""
        self._reconnect()

    @command(dtype_in=int)
    def StoreSettings(self, slot):
        """Store settings to slot 0-9."""
        self._send(f"*SAV {slot}")

    @command(dtype_in=int)
    def RecallSettings(self, slot):
        """Recall settings from slot 0-9."""
        self._send(f"*RCL {slot}")

    @command(dtype_in=str, dtype_out=str)
    def SendCommand(self, cmd):
        """Send arbitrary SCPI. Returns response if query, else 'OK'."""
        if cmd.strip().endswith("?"):
            return self._query(cmd.strip())
        self._send(cmd.strip())
        return "OK"

    @command(dtype_in=[float])
    def SetLinkedDelay(self, params):
        """Set linked delay: [channel, reference, seconds].
        Indices: T0=0 T1=1 A=2 B=3 C=4 D=5 E=6 F=7 G=8 H=9"""
        ch, ref, t = int(params[0]), int(params[1]), params[2]
        self._send(f"DLAY {ch},{ref},{t:.12e}")

    @command(dtype_in=[float])
    def SetOutputLevel(self, params):
        """Set output: [output_idx, amplitude_V, offset_V, polarity].
        Outputs: T0=0 AB=1 CD=2 EF=3 GH=4. Polarity: 0=neg 1=pos."""
        out = int(params[0])
        self._send(f"LAMP {out},{params[1]:.3f}")
        self._send(f"LOFF {out},{params[2]:.3f}")
        self._send(f"LPOL {out},{int(params[3])}")

    @command
    def ClearStatus(self):
        self._send("*CLS")

    @command
    def GoLocal(self):
        self._send("LCAL")

    @command
    def GoRemote(self):
        self._send("REMT")

    # -----------------------------------------------------------------
    # State
    # -----------------------------------------------------------------
    def dev_state(self):
        if not self._socket:
            return DevState.FAULT
        try:
            self._query("*IDN?")
            return DevState.ON
        except Exception:
            return DevState.FAULT

    def dev_status(self):
        if self.dev_state() == DevState.ON:
            return f"Connected to DG645 at {self.Host}:{self.Port}"
        return f"FAULT: Cannot reach DG645 at {self.Host}:{self.Port}"


if __name__ == "__main__":
    run([DG645])
