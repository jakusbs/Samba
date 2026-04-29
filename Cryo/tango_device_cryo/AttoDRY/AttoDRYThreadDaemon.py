# AttoDRYThreadDaemon.py — daemon listener thread for AttoDRY UDP packets.
#
# Sends "Read" every 0.2 s, parses the CSV reply packet, and updates all
# attribute caches on the parent device.
#
# Packet format (26 comma-separated fields after "Read:"):
#   idx  field                      idx  field
#    0   isControllingField          13  getSampleHeaterPower
#    1   isControllingTemperature    14  getMagneticFieldSetPoint
#    2   isPersistentModeSet         15  getUserTemperature
#    3   getMagneticField            16  getTurbopumpFrequency
#    4   getSampleTemperature        17  getCryostatOutPressure
#    5   getVtiTemperature           18  isGoingToBaseTemperature
#    6   get4KStageTemperature       19  isSampleExchangeInProgress
#    7   get40KStageTemperature      20  isSampleReadyToExchange
#    8   getReservoirTemperature     21  isZeroingField
#    9   getCryostatInPressure       22  isPumping
#   10   getDumpPressure             23  isSystemRunning
#   11   getReservoirHeaterPower     24  isExchangeHeaterOn
#   12   getVtiHeaterPower           25  isSampleHeaterOn

import socket
import time
import threading

import PyTango

_N_FIELDS = 25


class AttoDRYThread(threading.Thread):

    def __init__(self, parent):
        super(AttoDRYThread, self).__init__()
        self.daemon = True
        self.p = parent
        self.bufsize = 1024
        self._running = True
        self.interval = 0.2

    def stop(self):
        self._running = False
        self.p.set_state(PyTango.DevState.ON)

    def run(self):
        while self._running:
            # Send Read request
            try:
                self.p.s.sendto("Read".encode("utf-8"), self.p.server)
            except Exception:
                break

            # Receive reply
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

            if not pkt.startswith("Read:"):
                time.sleep(self.interval)
                continue

            # Split off optional string section: "Read:<csv>|<err_status>|<err_msg>|<act_msg>"
            sections = pkt.split('|')
            parts = sections[0][5:].split(',')
            if len(parts) < _N_FIELDS:
                time.sleep(self.interval)
                continue

            try:
                iCF  = int(parts[0])
                iCT  = int(parts[1])
                iCP  = int(parts[2])
                gMF  = float(parts[3])
                gST  = float(parts[4])
                gVT  = float(parts[5])
                gMT  = float(parts[6])
                g40K = float(parts[7])
                gRT  = float(parts[8])
                gCIP = float(parts[9])
                gDP  = float(parts[10])
                gRHP = float(parts[11])
                gVHP = float(parts[12])
                gVSP = float(parts[13])
                gMFS = float(parts[14])
                gUT  = float(parts[15])
                gTPF = float(parts[16])
                gCoP = float(parts[17])
                iGBT = int(parts[18])
                iSEP = int(parts[19])
                iSRE = int(parts[20])
                iZF  = int(parts[21])
                iPmp = int(parts[22])
                iSR  = int(parts[23])
                iSH  = int(parts[24])
            except Exception as e:
                try:
                    self.p.error_stream("Listener parse error: " + str(e))
                except Exception:
                    pass
                time.sleep(self.interval)
                continue

            # Update all attribute caches atomically
            with self.p._cache_lock:
                # Internal mirrors used by AttoDRYCheck
                self.p.current_magnetic_field = gMF
                self.p.sample_temperature     = gST

                # Magnetic field
                self.p.attr_MagneticField_read         = gMF
                self.p.attr_MagneticFieldSetpoint_read = gMFS

                # Temperatures
                self.p.attr_Temperature_read          = gST
                self.p.attr_UserTemperature_read      = gUT
                self.p.attr_VtiTemperature_read       = gVT
                self.p.attr_MagnetTemperature_read    = gMT
                self.p.attr_Stage40KTemperature_read  = g40K
                self.p.attr_ReservoirTemperature_read = gRT

                # Pressures
                self.p.attr_CryostatInPressure_read  = gCIP
                self.p.attr_CryostatOutPressure_read = gCoP
                self.p.attr_DumpPressure_read        = gDP

                # Heater powers
                self.p.attr_SampleHeaterPower_read    = gVSP
                self.p.attr_VtiHeaterPower_read       = gVHP
                self.p.attr_ReservoirHeaterPower_read = gRHP

                # Diagnostics
                self.p.attr_TurbopumpFrequency_read = gTPF

                # Control toggles
                self.p.attr_toggleMagneticFieldControl_read   = bool(iCF)
                self.p.attr_toggleFulltemperatureControl_read = bool(iCT)
                self.p.attr_togglePersistentMode_read         = bool(iCP)

                # Status flags
                self.p.attr_GoingToBaseTemperature_read   = bool(iGBT)
                self.p.attr_SampleExchangeInProgress_read = bool(iSEP)
                self.p.attr_SampleReadyToExchange_read    = bool(iSRE)
                self.p.attr_ZeroingField_read             = bool(iZF)
                self.p.attr_Pumping_read                  = bool(iPmp)
                self.p.attr_SystemRunning_read  = bool(iSR)
                self.p.attr_SampleHeaterOn_read = bool(iSH)

                # Error / status messages (optional string section)
                if len(sections) >= 4:
                    try:
                        self.p.attr_ErrorStatus_read  = int(sections[1])
                        self.p.attr_ErrorMessage_read = sections[2]
                        self.p.attr_ActionMessage_read = sections[3]
                    except Exception:
                        pass

            time.sleep(self.interval)
