#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
ZI2
Zurich Instruments MFLI lock-in amplifier TANGO server (second unit).
Same code as ZI but configured for dev30933 by default.
DeviceId, host and harmonics are device properties so the same code base
can drive any MFLI without source edits.
"""

import time

import numpy as np
import tango
from tango import DevState, AttrWriteType
from tango.server import Device, attribute, command, device_property, run

import zhinst.ziPython as ziPython

from ThreadZI2 import ThreadZI2

__all__ = ["ZI2", "main"]


# 99 % settling-time multipliers, indexed by filter order (1..8).
SETTLE_99 = {1: 4.6, 2: 6.6, 3: 8.4, 4: 10.0,
             5: 11.6, 6: 13.1, 7: 14.6, 8: 16.0}


class ZI2(Device):
    """
    ZI2 MFLI lock-in amplifier (second unit).

    **Properties:**
    - DeviceId: ZI device serial (default 'dev30933')
    - ZI_Host: IP address of the MFLI (default 192.168.1.144)
    - ZI_Port: Data server port (default 8004)
    - ZI_ApiLevel: ziDAQ API level (default 6)
    - Harmonics: harmonic order for each demodulator 0..3
        Default [1,2,3,1] — demod 3 uses harmonic 1 per a 2024 setup change
        (do not "fix" without verifying the experimental setup).
    """

    DeviceId = device_property(
        dtype='str',
        default_value='dev30933',
        doc="ZI device serial (e.g. 'dev30933')"
    )
    ZI_Host = device_property(
        dtype='str',
        default_value='192.168.1.144',
        doc="IP address of the MFLI data server"
    )
    ZI_Port = device_property(
        dtype='int',
        default_value=8004,
        doc="ziDAQ data server port"
    )
    ZI_ApiLevel = device_property(
        dtype='int',
        default_value=6,
        doc="ziDAQ API level"
    )
    Harmonics = device_property(
        dtype=(int,),
        default_value=[1, 2, 3, 1],
        doc="Harmonic order for demods 0..3 (4 ints). "
            "Default [1,2,3,1] for ZI2 per 2024 setup change."
    )

    # ---- lifecycle ------------------------------------------------------

    def init_device(self):
        Device.init_device(self)
        self._x = [0.0, 0.0, 0.0, 0.0]
        self._y = [0.0, 0.0, 0.0, 0.0]
        self._amplitude = 0.0
        self._frequency = 0.0
        self._samplingrate = 1674.0
        self._integrationtime = 1.0
        self._phase = [0.0, 0.0, 0.0, 0.0]
        self._timeconstant = 0.0
        self._filterorder = 1
        self._settlingtime = 0.0
        self._last_collect_s = 0.0

        try:
            self.daq = ziPython.ziDAQServer(self.ZI_Host, self.ZI_Port, self.ZI_ApiLevel)
        except Exception as e:
            self.set_state(DevState.FAULT)
            self.set_status(f"ziDAQServer connect failed: {e}")
            return

        try:
            self._configure_demods()
            self._refresh_cached_settings()
            self.set_state(DevState.ON)
        except Exception as e:
            self.set_state(DevState.FAULT)
            self.set_status(f"MFLI init failed: {e}")

    def _path(self, suffix):
        """Build a full ZI node path: '/dev30933/<suffix>'."""
        return '/{}/{}'.format(self.DeviceId, suffix)

    def _configure_demods(self):
        if len(self.Harmonics) != 4:
            tango.Except.throw_exception(
                "Bad Harmonics property",
                f"Harmonics must contain 4 ints, got {list(self.Harmonics)}",
                "ZI2::init_device")
        for i in range(4):
            self.daq.setInt(self._path(f'demods/{i}/oscselect'), 0)
            self.daq.setDouble(self._path(f'demods/{i}/harmonic'), self.Harmonics[i])
            self.daq.subscribe(self._path(f'demods/{i}/sample'))
        self.flat_dictionary_key = False

    def _refresh_cached_settings(self):
        for i in range(4):
            self._phase[i] = self.daq.getDouble(self._path(f'demods/{i}/phaseshift'))
        self._samplingrate = self.daq.getDouble(self._path('demods/0/rate'))
        self._frequency    = self.daq.getDouble(self._path('oscs/0/freq'))
        self._amplitude    = self.daq.getDouble(self._path('sigouts/0/amplitudes/0'))

    def always_executed_hook(self):
        pass

    # ---- attribute helpers ---------------------------------------------

    def _settling_time(self):
        tc = self.daq.getDouble(self._path('demods/0/timeconstant'))
        order = int(self.daq.getDouble(self._path('demods/0/order')))
        return SETTLE_99.get(order, 16.0) * tc, tc, order

    # ---- demodulator readouts (populated by ThreadZI2) -----------------

    @attribute(dtype=float, access=AttrWriteType.READ)
    def x1(self): return self._x[0]
    @attribute(dtype=float, access=AttrWriteType.READ)
    def x2(self): return self._x[1]
    @attribute(dtype=float, access=AttrWriteType.READ)
    def x3(self): return self._x[2]
    @attribute(dtype=float, access=AttrWriteType.READ)
    def x4(self): return self._x[3]

    @attribute(dtype=float, access=AttrWriteType.READ)
    def y1(self): return self._y[0]
    @attribute(dtype=float, access=AttrWriteType.READ)
    def y2(self): return self._y[1]
    @attribute(dtype=float, access=AttrWriteType.READ)
    def y3(self): return self._y[2]
    @attribute(dtype=float, access=AttrWriteType.READ)
    def y4(self): return self._y[3]

    # ---- generator settings --------------------------------------------

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False)
    def Amplitude(self):
        return self._amplitude

    @Amplitude.write
    def Amplitude(self, value):
        self.daq.setDouble(self._path('sigouts/0/amplitudes/0'), value)
        self.daq.setInt(self._path('sigouts/0/on'), 1)
        time.sleep(0.2)
        self._amplitude = self.daq.getDouble(self._path('sigouts/0/amplitudes/0'))

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE,
               memorized=True, hw_memorized=False)
    def frequency(self):
        return self._frequency

    @frequency.write
    def frequency(self, value):
        self.daq.setDouble(self._path('oscs/0/freq'), value)
        time.sleep(0.2)
        self._frequency = self.daq.getDouble(self._path('oscs/0/freq'))

    # ---- demod sampling/integration ------------------------------------

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE)
    def samplingrate(self):
        return self._samplingrate

    @samplingrate.write
    def samplingrate(self, value):
        for i in range(4):
            self.daq.setDouble(self._path(f'demods/{i}/rate'), value)
        time.sleep(0.2)
        self._samplingrate = self.daq.getDouble(self._path('demods/0/rate'))

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE)
    def integrationtime(self):
        return self._integrationtime

    @integrationtime.write
    def integrationtime(self, value):
        try:
            settling, tc, order = self._settling_time()
            if value < settling:
                self.warn_stream(
                    f'integrationtime {value:.3f}s < settling time '
                    f'{settling:.3f}s (TC={tc:.4f}s, order={order}). '
                    f'Samba handles settling externally before Start().')
            else:
                self.info_stream(
                    f'integrationtime={value:.3f}s, settling={settling:.3f}s, '
                    f'net collection={value - settling:.3f}s — OK')
        except Exception as e:
            self.warn_stream(f'Could not validate settling: {e}')
        self._integrationtime = value

    # ---- per-demod phase shifts ----------------------------------------

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE)
    def phase1(self): return self._phase[0]
    @phase1.write
    def phase1(self, value):
        self.daq.setDouble(self._path('demods/0/phaseshift'), value)
        self._phase[0] = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE)
    def phase2(self): return self._phase[1]
    @phase2.write
    def phase2(self, value):
        self.daq.setDouble(self._path('demods/1/phaseshift'), value)
        self._phase[1] = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE)
    def phase3(self): return self._phase[2]
    @phase3.write
    def phase3(self, value):
        self.daq.setDouble(self._path('demods/2/phaseshift'), value)
        self._phase[2] = value

    @attribute(dtype=float, access=AttrWriteType.READ_WRITE)
    def phase4(self): return self._phase[3]
    @phase4.write
    def phase4(self, value):
        self.daq.setDouble(self._path('demods/3/phaseshift'), value)
        self._phase[3] = value

    # ---- read-only filter info -----------------------------------------

    @attribute(dtype=float, access=AttrWriteType.READ, unit='s',
               doc="Demod 0 low-pass filter time constant (read from hardware)")
    def timeconstant(self):
        self._timeconstant = self.daq.getDouble(self._path('demods/0/timeconstant'))
        return self._timeconstant

    @attribute(dtype=int, access=AttrWriteType.READ,
               doc="Demod 0 filter order 1-8 (read from hardware)")
    def filterorder(self):
        self._filterorder = int(self.daq.getDouble(self._path('demods/0/order')))
        return self._filterorder

    @attribute(dtype=float, access=AttrWriteType.READ, unit='s',
               doc="99% settling time = settle_factor(order) * timeconstant")
    def settlingtime(self):
        settling, _, _ = self._settling_time()
        self._settlingtime = settling
        return self._settlingtime

    # ---- commands -------------------------------------------------------

    @command()
    def Start(self):
        """Run one integration cycle in a background thread."""
        if self.get_state() == DevState.ON:
            self.thread = ThreadZI2(self)
            self.thread.start()
        else:
            self.warn_stream("Thread is already running.")


def main(args=None, **kwargs):
    return run((ZI2,), args=args, **kwargs)


if __name__ == '__main__':
    main()
