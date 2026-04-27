# AttoSocket2.py — Windows-side UDP bridge for the AttoDRY2100
# Listens for commands from the Linux TANGO server and translates them
# into PyAttoDRY DLL calls.
#
# Protocol (UDP, all ASCII):
#   TANGO → Windows  "start"   — initial handshake (ignored after first)
#   TANGO → Windows  "ON"      — begin()+Connect() AttoDRY
#   TANGO → Windows  "Read"    — fetch telemetry, reply ReadA…N packet
#   TANGO → Windows  "W001 <v>" — set magnetic field setpoint (T)
#   TANGO → Windows  "W002 <v>" — set temperature setpoint (K)
#   TANGO → Windows  "W003"    — toggleMagneticFieldControl
#   TANGO → Windows  "W004"    — toggleFullTemperatureControl
#   TANGO → Windows  "W005"    — togglePersistentMode
#   TANGO → Windows  "OFF"     — Disconnect()+end(), close socket
#
# Reply packet (on "Read"):
#   ReadA<iCF>B<iCT>C<iCP>D<gMF>E<gST>F<gVT>G<gMT>H<gRT>
#        I<gCoP>J<gCIP>K<gRHP>L<gVHP>M<gVSP>N

from PyAttoDRY import AttoDRY
import socket
import time

# ── Configuration ────────────────────────────────────────────────────────────
HOST     = '192.168.1.8'   # IP of this Windows PC (NIC facing the lab network)
PORT     = 11000           # UDP port to listen on
COM_PORT = 'COM4'          # COM port connected to AttoDRY2100
RECV_TIMEOUT_S  = 10.0     # seconds before recvfrom times out (detects TANGO gone)
CONNECT_WAIT_S  = 10       # seconds to wait for AttoDRY to initialise after Connect()
# ─────────────────────────────────────────────────────────────────────────────


def build_packet():
    """Read all telemetry from the DLL and return the ReadA…N packet string.
    Returns None if any call fails."""
    try:
        iCF  = AttoDRY.isControllingField()
        iCT  = AttoDRY.isControllingTemperature()
        iCP  = AttoDRY.isPersistentModeSet()
        gMF  = AttoDRY.getMagneticField()
        gST  = AttoDRY.getSampleTemperature()
        gVT  = AttoDRY.getVtiTemperature()
        gMT  = AttoDRY.get4KStageTemperature()
        gRT  = AttoDRY.getReservoirTemperature()
        gCoP = AttoDRY.getCryostatOutPressure()
        gCIP = AttoDRY.getCryostatInPressure()
        gRHP = AttoDRY.getReservoirHeaterPower()
        gVHP = AttoDRY.getVtiHeaterPower()
        gVSP = AttoDRY.getSampleHeaterPower()
    except Exception as e:
        print(f'[AttoSocket2] Read error: {e}')
        return None

    return (
        'ReadA' + str(iCF)
        + 'B'   + str(iCT)
        + 'C'   + str(iCP)
        + 'D'   + str(gMF)
        + 'E'   + str(gST)
        + 'F'   + str(gVT)
        + 'G'   + str(gMT)
        + 'H'   + str(gRT)
        + 'I'   + str(gCoP)
        + 'J'   + str(gCIP)
        + 'K'   + str(gRHP)
        + 'L'   + str(gVHP)
        + 'M'   + str(gVSP)
        + 'N'
    )


def connect_attodry():
    """Call begin()+Connect() and wait for the AttoDRY to initialise.
    Returns True if connected and initialised."""
    print('[AttoSocket2] Connecting to AttoDRY...')
    try:
        AttoDRY.begin(setup_version=1)
        AttoDRY.Connect(COMPort=COM_PORT)
    except Exception as e:
        print(f'[AttoSocket2] begin/Connect failed: {e}')
        return False

    # Wait up to CONNECT_WAIT_S for initialisation
    for i in range(CONNECT_WAIT_S):
        time.sleep(1.0)
        try:
            if AttoDRY.isDeviceInitialised() and AttoDRY.isDeviceConnected():
                print('[AttoSocket2] AttoDRY initialised and connected.')
                return True
        except Exception as e:
            print(f'[AttoSocket2] Init check error: {e}')
    print('[AttoSocket2] AttoDRY did not initialise within timeout.')
    return False


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(RECV_TIMEOUT_S)
    s.bind((HOST, PORT))
    print(f'[AttoSocket2] Listening on {HOST}:{PORT}')

    last_packet = None   # last successfully built read packet
    addr = None          # TANGO server address (learned on first contact)

    try:
        while True:
            # ── Receive ──────────────────────────────────────────────────
            try:
                raw, addr = s.recvfrom(256)
            except socket.timeout:
                # TANGO server has not contacted us for RECV_TIMEOUT_S seconds.
                # Just loop — do not crash. Log once every ~60 s to avoid spam.
                print('[AttoSocket2] No data from TANGO (timeout). Waiting...')
                continue
            except OSError as e:
                print(f'[AttoSocket2] Socket error: {e}')
                break

            try:
                data = raw.decode('utf-8').strip()
            except UnicodeDecodeError:
                continue

            # ── Dispatch ─────────────────────────────────────────────────
            if data in ('start', 'ON'):
                # ACK immediately so TANGO's 5 s recvfrom timeout is not hit.
                # connect_attodry() can take up to CONNECT_WAIT_S seconds; TANGO
                # will get socket.timeout on its Read polls during that window
                # but the daemon retries, so it recovers automatically.
                s.sendto(b'ON', addr)
                connect_attodry()

            elif data == 'Read':
                # Check connection health first
                try:
                    if not (AttoDRY.isDeviceConnected() and AttoDRY.isDeviceInitialised()):
                        print('[AttoSocket2] AttoDRY not connected — skipping read.')
                        if last_packet:
                            s.sendto(last_packet.encode('utf-8'), addr)
                        continue
                except Exception:
                    pass  # if health-check itself fails, attempt the read anyway

                pkt = build_packet()
                if pkt is not None:
                    last_packet = pkt
                    s.sendto(pkt.encode('utf-8'), addr)
                elif last_packet is not None:
                    # Send stale data so TANGO daemon does not stall
                    s.sendto(last_packet.encode('utf-8'), addr)

            elif data.startswith('W'):
                try:
                    if data[:4] == 'W001':
                        AttoDRY.setUserMagneticField(float(data[5:]))
                    elif data[:4] == 'W002':
                        AttoDRY.setUserTemperature(float(data[5:]))
                    elif data == 'W003':
                        AttoDRY.toggleMagneticFieldControl()
                    elif data == 'W004':
                        AttoDRY.toggleFullTemperatureControl()
                    elif data == 'W005':
                        AttoDRY.togglePersistentMode()
                except Exception as e:
                    print(f'[AttoSocket2] Write command {data!r} failed: {e}')

            elif data == 'OFF':
                print('[AttoSocket2] Received OFF — disconnecting.')
                try:
                    AttoDRY.Disconnect()
                    AttoDRY.end()
                except Exception as e:
                    print(f'[AttoSocket2] Disconnect error: {e}')
                s.sendto(b'OFF', addr)
                break

    except KeyboardInterrupt:
        print('\n[AttoSocket2] Interrupted by user.')
        try:
            AttoDRY.Disconnect()
            AttoDRY.end()
        except Exception:
            pass
    finally:
        s.close()
        print('[AttoSocket2] Socket closed.')


if __name__ == '__main__':
    main()
