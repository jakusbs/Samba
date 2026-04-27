# File:             AttoDRYCheck.py
# author:           C. Murer
# copyright:        ETH Zurich, Switzerland, D-MATL INTERMAG
#
# Convergence-checker thread for AttoDRY setpoint transitions.
# Runs after a field or temperature setpoint is written; sets device state
# to MOVING while the measured value differs from the setpoint, then
# restores ON when both are within tolerance.
#
# Requires self.p.setField and self.p.setTemp to be set by the caller
# (write_MagneticField / write_Temperature in AttoDRY.py) before start().

import time
import threading

import PyTango

FIELD_TOL = 0.001   # Tesla
TEMP_TOL  = 0.2     # Kelvin
POLL_INTERVAL = 1.0  # seconds between convergence checks


class AttoDRYCheck(threading.Thread):

    def __init__(self, parent):
        threading.Thread.__init__(self)
        self.p = parent
        self.daemon = True
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.is_set():
            with self.p._cache_lock:
                field_err = abs(self.p.attr_MagneticField_read - self.p.setField)
                temp_err  = abs(self.p.attr_Temperature_read   - self.p.setTemp)

            if field_err <= FIELD_TOL and temp_err <= TEMP_TOL:
                self.p.set_state(PyTango.DevState.ON)
                return

            self.p.set_state(PyTango.DevState.MOVING)
            print('Field error: {:.4f} T   Temp error: {:.3f} K'.format(
                field_err, temp_err))
            # Use wait() so stop() wakes us immediately instead of sleeping
            self._stop_event.wait(timeout=POLL_INTERVAL)

    def stop(self):
        self._stop_event.set()
