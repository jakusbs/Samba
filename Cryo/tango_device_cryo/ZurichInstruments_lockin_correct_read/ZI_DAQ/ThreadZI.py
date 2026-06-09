# File:             ThreadZI.py
# author:           P. Noel, C. Murer (original)
#
# v4.0 — poll() + numpy averaging (replaces DAQ module approach).
#         Simpler, version-agnostic, equivalent measurement result.
#         Settling is NOT done here — Samba waits before calling Start().

import threading
import PyTango
import numpy as np

DEVICE = 'dev4855'
MIN_COLLECT = 0.05   # minimum collection window (seconds)


class ThreadZI(threading.Thread):
    lock = threading.Lock()

    def __init__(self, parent):
        self.p = parent
        threading.Thread.__init__(self)

    def run(self):
        self.p.set_state(PyTango.DevState.RUNNING)

        try:
            daq = self.p.daq
            collect_time = max(self.p.attr_integrationtime_read, MIN_COLLECT)
            timeout_ms   = int((collect_time + 5.0) * 1000)

            # ── 0. Wake idle data server ────────────────────────────────
            # LabOne pauses sample delivery when the API connection is idle.
            # A getDouble() call re-activates streaming before flush+collect.
            daq.getDouble('/{}/demods/0/rate'.format(DEVICE))

            # ── 1. Flush stale samples ──────────────────────────────────
            daq.poll(0.01, 100, 0, True)

            # ── 2. Collect for integration window ──────────────────────
            data = daq.poll(collect_time, timeout_ms, 0, True)
            if not data:
                self.p.warn_stream(
                    'ZI: server returned no samples for {:.3f}s window '
                    '(idle connection; check LabOne)'.format(collect_time))

            # ── 3. Average each demod channel with numpy ────────────────
            sqrt2 = np.sqrt(2)
            for i in range(4):
                for comp in ['x', 'y']:
                    path = '/{}/demods/{}/sample'.format(DEVICE, i)
                    try:
                        samples = data[path][comp]
                        val = float(np.mean(samples)) * 1e6 * sqrt2
                    except (KeyError, TypeError, ValueError):
                        val = 0.0
                    setattr(self.p, 'attr_{}{}_read'.format(comp, i + 1), val)

            self.p._last_collect_s = collect_time
            self.p.info_stream(
                'ZI: polled {:.3f}s, numpy avg'.format(collect_time))

        except Exception as e:
            try:
                self.p.error_stream('ThreadZI error: {}'.format(e))
            except Exception:
                pass

        self.p.set_state(PyTango.DevState.ON)

    def stop(self):
        self.p.set_state(PyTango.DevState.ON)
