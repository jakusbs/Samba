# File:             AttoDRYThreadDaemon.py
# author:           N. Kercher
# copyright:        ETH Zurich, Switzerland, D-MATL INTERMAG
#
# Daemon listener thread for AttoDRY UDP packets.
# Sends "Read" every 0.2 s, parses the "ReadA...N" response,
# and updates all attribute caches on the parent device.

import re
import socket
import time
import threading

import PyTango


class AttoDRYThread(threading.Thread):

    def __init__(self, parent):
        super(AttoDRYThread, self).__init__()
        self.daemon = True
        self.p = parent
        self.bufsize = 256
        self._running = True
        self.interval = 0.2

    def stop(self):
        self._running = False
        self.p.set_state(PyTango.DevState.ON)

    def run(self):
        while self._running:
            # Send "Read" request
            try:
                self.p.s.sendto("Read".encode("utf-8"), self.p.server)
            except Exception:
                break

            # Wait for a valid packet
            try:
                data, addr = self.p.s.recvfrom(self.bufsize)
            except socket.timeout:
                time.sleep(self.interval)
                continue
            except OSError:
                break

            try:
                pkt = data.decode("utf-8").strip()
            except UnicodeDecodeError:
                time.sleep(self.interval)
                continue

            if not pkt.startswith("ReadA"):
                time.sleep(self.interval)
                continue

            # Parse all fields by letter markers
            m_iCF  = re.search(r"A(.*)B",  pkt)
            m_iCT  = re.search(r"B(.*)C",  pkt)
            m_iCP  = re.search(r"C(.*)D",  pkt)
            m_gMF  = re.search(r"D(.*)E",  pkt)
            m_gST  = re.search(r"E(.*)F",  pkt)
            m_gVT  = re.search(r"F(.*)G",  pkt)
            m_gMT  = re.search(r"G(.*)H",  pkt)
            m_gRT  = re.search(r"H(.*)I",  pkt)
            m_gCoP = re.search(r"I(.*)J",  pkt)
            m_gCIP = re.search(r"J(.*)K",  pkt)
            m_gRHP = re.search(r"K(.*)L",  pkt)
            m_gVHP = re.search(r"L(.*)M",  pkt)
            m_gVSP = re.search(r"M(.*)N",  pkt)

            if not all([m_iCF, m_iCT, m_iCP, m_gMF, m_gST, m_gVT,
                        m_gMT, m_gRT, m_gCoP, m_gCIP, m_gRHP, m_gVHP, m_gVSP]):
                time.sleep(self.interval)
                continue

            try:
                iCF  = int(m_iCF.group(1))
                iCT  = int(m_iCT.group(1))
                iCP  = int(m_iCP.group(1))
                gMF  = float(m_gMF.group(1))
                gST  = float(m_gST.group(1))
                gVT  = float(m_gVT.group(1))
                gMT  = float(m_gMT.group(1))
                gRT  = float(m_gRT.group(1))
                gCoP = float(m_gCoP.group(1))
                gCIP = float(m_gCIP.group(1))
                gRHP = float(m_gRHP.group(1))
                gVHP = float(m_gVHP.group(1))
                gVSP = float(m_gVSP.group(1))
            except Exception as e:
                try:
                    self.p.error_stream("Listener conversion error: " + str(e))
                except Exception:
                    pass
                time.sleep(self.interval)
                continue

            # Update internal state mirrors and TANGO attribute caches atomically
            with self.p._cache_lock:
                self.p.is_controlling_field       = iCF
                self.p.is_controlling_temperature = iCT
                self.p.is_persistent_mode_set     = iCP
                self.p.current_magnetic_field     = gMF
                self.p.sample_temperature         = gST
                self.p.vti_temperature            = gVT
                self.p.magnet_temperature         = gMT
                self.p.reservoir_temperature      = gRT
                self.p.cryostat_out_pressure      = gCoP
                self.p.cryostat_in_pressure       = gCIP
                self.p.reservoir_heater_power     = gRHP
                self.p.vti_heater_power           = gVHP
                self.p.sample_heater_power        = gVSP

                self.p.attr_toggleMagneticFieldControl_read   = bool(iCF)
                self.p.attr_toggleFulltemperatureControl_read = bool(iCT)
                self.p.attr_togglePersistentMode_read         = bool(iCP)
                self.p.attr_MagneticField_read                = gMF
                self.p.attr_Temperature_read                  = gST
                self.p.attr_VtiTemperature_read               = gVT
                self.p.attr_MagnetTemperature_read            = gMT
                self.p.attr_ReservoirTemperature_read         = gRT
                self.p.attr_CryostatOutPressure_read          = gCoP
                self.p.attr_CryostatInPressure_read           = gCIP
                self.p.attr_ReservoirHeaterPower_read         = gRHP
                self.p.attr_VtiHeaterPower_read               = gVHP
                self.p.attr_SampleHeaterPower_read            = gVSP

            time.sleep(self.interval)
