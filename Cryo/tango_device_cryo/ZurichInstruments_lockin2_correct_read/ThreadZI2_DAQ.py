# File:             ThreadZI2.py
# author:           P. Noel, C. Murer (original)
#
# v5.0 — modern tango.server companion: reads DeviceId from parent, writes
#         results into parent._x / parent._y so the new attribute getters
#         pick them up. Settling is NOT done here — Samba waits before Start().

import threading
import tango
import numpy as np

MIN_COLLECT = 0.05   # minimum collection window (seconds)


class ThreadZI2(threading.Thread):
    lock = threading.Lock()

    def __init__(self, parent):
        threading.Thread.__init__(self)
        self.p = parent
        self.daemon = True

    def run(self):
        self.p.set_state(tango.DevState.RUNNING)

        try:
            daq = self.p.daq
            device = self.p.DeviceId
            collect_time = max(self.p._integrationtime, MIN_COLLECT)
            timeout_ms   = int((collect_time + 5.0) * 1000)

            # Wake idle data server — LabOne pauses sample delivery when the
            # API connection is idle; getDouble() re-activates streaming.
            daq.getDouble('/{}/demods/0/rate'.format(device))

            # Flush stale samples
            daq.poll(0.01, 100, 0, True)

            # Collect for the integration window
            data = daq.poll(collect_time, timeout_ms, 0, True)
            if not data:
                self.p.warn_stream(
                    'ZI2: server returned no samples for {:.3f}s window '
                    '(idle connection; check LabOne)'.format(collect_time))

            sqrt2 = np.sqrt(2)
            for i in range(4):
                path = '/{}/demods/{}/sample'.format(device, i)
                for comp, store in (('x', self.p._x), ('y', self.p._y)):
                    try:
                        samples = data[path][comp]
                        store[i] = float(np.mean(samples)) * 1e6 * sqrt2
                    except (KeyError, TypeError, ValueError):
                        store[i] = 0.0

            self.p._last_collect_s = collect_time
            self.p.info_stream(
                'ZI2: polled {:.3f}s, numpy avg'.format(collect_time))

        except Exception as e:
            try:
                self.p.error_stream('ThreadZI2 error: {}'.format(e))
            except Exception:
                pass

        self.p.set_state(tango.DevState.ON)

    def stop(self):
        self.p.set_state(tango.DevState.ON)
