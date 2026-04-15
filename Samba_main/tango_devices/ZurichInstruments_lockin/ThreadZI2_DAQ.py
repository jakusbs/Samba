# File:             ThreadZI2.py
# author:           P. Noel, C. Murer (original), rewritten to use ZI DAQ Module
# copyright:        ETH Zurich, Switzerland, D-MATL INTERMAG
#
# v3.1 — Uses ZI's native Data Acquisition Module for averaging.
#         Settling is NOT done here — samba reads the 'settlingtime'
#         attribute and waits before calling Start().

import time
import threading
import PyTango
import numpy as np

DEVICE = 'dev30933'
MIN_COLLECT = 0.05


class ThreadZI2(threading.Thread):
    lock = threading.Lock()

    def __init__(self, parent):
        self.p = parent
        threading.Thread.__init__(self)

    def run(self):
        self.p.set_state(PyTango.DevState.RUNNING)

        try:
            daq = self.p.daq
            collect_time = max(self.p.attr_integrationtime_read, MIN_COLLECT)

            # ── 1. Flush stale samples from buffer ──────────────────────
            daq.poll(0.01, 10, 0, self.p.flat_dictionary_key)

            # ── 2. Create and configure the DAQ module ──────────────────
            h = daq.dataAcquisitionModule()
            h.set('dataAcquisitionModule/device', DEVICE)
            h.set('dataAcquisitionModule/type', 0)
            h.set('dataAcquisitionModule/endless', 0)
            h.set('dataAcquisitionModule/grid/mode', 4)
            h.set('dataAcquisitionModule/grid/cols', 1)
            h.set('dataAcquisitionModule/grid/rows', 1)
            h.set('dataAcquisitionModule/count', 1)
            h.set('dataAcquisitionModule/duration', collect_time)

            demod_paths = []
            for i in range(4):
                for comp in ['x', 'y']:
                    path = '/{}/demods/{}/sample.{}.avg'.format(DEVICE, i, comp)
                    h.subscribe(path)
                    demod_paths.append((i, comp, path))

            # ── 3. Execute and wait ─────────────────────────────────────
            h.execute()

            timeout = collect_time + 5.0
            t0 = time.time()
            while not h.finished():
                time.sleep(0.05)
                if time.time() - t0 > timeout:
                    print('ThreadZI2: DAQ module timed out')
                    break

            # ── 4. Read results ─────────────────────────────────────────
            data = h.read(True)
            h.finish()
            h.clear()

            sqrt2 = np.sqrt(2)
            for demod_idx, comp, path in demod_paths:
                if path in data and len(data[path]) > 0:
                    val = np.mean(data[path][0]['value']) * 1e6 * sqrt2
                else:
                    val = 0.0
                attr_name = 'attr_{}{}_read'.format(comp, demod_idx + 1)
                setattr(self.p, attr_name, val)

            self.p._last_collect_s = collect_time
            self.p._last_n_samples = 1

            print('ZI2: DAQ collected {:.3f}s, native averaging'.format(collect_time))

        except Exception as e:
            print('ThreadZI2 error: {}'.format(e))
            import traceback
            traceback.print_exc()

        self.p.set_state(PyTango.DevState.ON)

    def stop(self):
        self.p.set_state(PyTango.DevState.ON)
