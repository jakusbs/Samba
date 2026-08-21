"""Tests for per-point retry and trigger recovery in core/scan/runner.py.

Run from the repo root:
    python test_runner.py -v

No Qt, TANGO, or lab hardware needed.
"""
import os, sys, time, types, unittest
from unittest.mock import MagicMock
import numpy as np

# ── Stubs for modules not available without the lab environment ───────────────

# PyQt6 — only the two names imported at module level in runner.py
_qt = MagicMock()
_qt.QtCore.QThread    = object       # ScanWorker base class (unused in tests)
_qt.QtCore.pyqtSignal = lambda *a, **kw: None
sys.modules.setdefault('PyQt6',        _qt)
sys.modules.setdefault('PyQt6.QtCore', _qt.QtCore)

# tango — DevState values must match what runner stores in _RUNNING
_tango = types.ModuleType('tango')
class _DS:
    RUNNING = 'RUNNING'
    ON      = 'ON'
    MOVING  = 'MOVING'
    FAULT   = 'FAULT'
_tango.DevState    = _DS
_tango.DeviceProxy = MagicMock()
_tango.Database    = MagicMock()
sys.modules['tango'] = _tango

# config — constants used by runner.py
_config = types.ModuleType('config')
_config.MAX_RETRIES = 2        # 2 internal read retries inside _do_acquire
_config.RETRY_DELAY = 0.005   # 5 ms between internal read retries (fast tests)
_config.X_TIME      = '_time_'
sys.modules['config'] = _config

# hardware — we control fresh_proxy per test via _hw.fresh_proxy
_hw = types.ModuleType('hardware')

class _FallbackProxy:
    def state(self):                        return 'ON'
    def command_inout_asynch(self, *a):     pass
    def command_inout(self, *a):            pass
    def read_attribute(self, attr):         r = MagicMock(); r.value = 0.0; return r
    def read_attributes(self, attrs):       return [self.read_attribute(a) for a in attrs]
    def set_timeout_millis(self, ms):       pass

_hw.get_proxy          = lambda path: _FallbackProxy()
_hw.fresh_proxy        = lambda path: (_FallbackProxy(), None)   # overridden per test
_hw.safe_read          = lambda proxy, attr, **kw: (0.0, None)
_hw.safe_write         = lambda proxy, attr, val, **kw: None
_hw.demagnetize_magnet = MagicMock()
sys.modules['hardware'] = _hw

# ── Import runner after stubs are in place ────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core', 'scan'))
import runner as _runner_mod                          # noqa: E402
from runner import ScanRunner, AUTO_PAUSE_THRESHOLD   # noqa: E402

# ── Shared helpers ────────────────────────────────────────────────────────────

_RUNNING_SET = {'RUNNING'}   # matches runner's _RUNNING when tango stub loaded

def _noop(*a, **kw):
    pass

def _make_runner():
    """Minimal ScanRunner with no Qt / config needed."""
    r = ScanRunner.__new__(ScanRunner)
    r._abort   = False
    r._paused  = False
    r._trigger_consec_fails = {}
    return r


class InstantProxy:
    """
    Fake TANGO proxy that completes an integration cycle in ~3 ms.

    trigger_fail_n  how many times command_inout_asynch raises before succeeding
    read_fail_n     how many times read_attribute raises before succeeding
    read_val        value returned on a successful read
    """
    def __init__(self, read_val=1.23, trigger_fail_n=0, read_fail_n=0):
        self._read_val      = read_val
        self._trigger_fails = trigger_fail_n
        self._read_fails    = read_fail_n
        self._done_at       = 0.0

    def command_inout_asynch(self, cmd, *a):
        if self._trigger_fails > 0:
            self._trigger_fails -= 1
            raise Exception("TRANSIENT — simulated trigger failure")
        self._done_at = time.time() + 0.003   # 3 ms integration window

    def command_inout(self, cmd, *a):
        self.command_inout_asynch(cmd)

    def state(self):
        return 'RUNNING' if time.time() < self._done_at else 'ON'

    def read_attribute(self, attr):
        if self._read_fails > 0:
            self._read_fails -= 1
            raise Exception("Read error — simulated")
        r = MagicMock()
        r.value = self._read_val
        return r

    def read_attributes(self, attrs):
        return [self.read_attribute(a) for a in attrs]

    def set_timeout_millis(self, ms):
        pass


def _std_args(proxy=None, read_val=1.0):
    """Standard (devp, dev_sensors, trigger_devs, cfg) for a single ZI device."""
    if proxy is None:
        proxy = InstantProxy(read_val=read_val)
    dev          = 'dev://zi1'
    devp         = {dev: proxy}
    dev_sensors  = {dev: [{'attribute': 'x1', 'label': 'ZI x1'},
                          {'attribute': 'y1', 'label': 'ZI y1'}]}
    trigger_devs = {dev: 'Start'}
    cfg          = {'move_timeout': 5.0}
    return devp, dev_sensors, trigger_devs, cfg


def _acquire(runner, devp, dev_sensors, trigger_devs, cfg, int_time=0.0):
    return runner._do_acquire(
        devp, dev_sensors, trigger_devs,
        int_time, time.time(), _RUNNING_SET, cfg, 15000, _noop)


# ─────────────────────────────────────────────────────────────────────────────
# 1. _do_acquire — happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestDoAcquireHappyPath(unittest.TestCase):

    def test_returns_correct_values_and_ok_true(self):
        r = _make_runner()
        devp, dev_sensors, trigger_devs, cfg = _std_args(read_val=7.77)
        vals, t_trigger, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertTrue(ok)
        self.assertAlmostEqual(vals['ZI x1'], 7.77)
        self.assertAlmostEqual(vals['ZI y1'], 7.77)
        self.assertGreaterEqual(t_trigger, 0.0)

    def test_trigger_devs_unchanged_on_success(self):
        r = _make_runner()
        devp, dev_sensors, trigger_devs, cfg = _std_args()
        original = dict(trigger_devs)
        _acquire(r, devp, dev_sensors, trigger_devs, cfg)
        self.assertEqual(trigger_devs, original)

    def test_no_trigger_devices_falls_back_to_sleep(self):
        """Empty trigger_devs must still read sensors and return ok=True."""
        r = _make_runner()
        dev = 'dev://zi1'
        proxy = InstantProxy(read_val=2.0)
        devp        = {dev: proxy}
        dev_sensors = {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]}
        cfg         = {'move_timeout': 5.0}

        vals, _, ok = r._do_acquire(
            devp, dev_sensors, {}, 0.0, time.time(), _RUNNING_SET, cfg, 0, _noop)

        self.assertTrue(ok)
        self.assertAlmostEqual(vals['ZI x1'], 2.0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. _do_acquire — read failures
# ─────────────────────────────────────────────────────────────────────────────

class TestDoAcquireReadFailure(unittest.TestCase):

    def test_persistent_failure_gives_nan_and_ok_false(self):
        """If read_attribute always raises, ok=False and vals are NaN."""
        r = _make_runner()
        devp, dev_sensors, trigger_devs, cfg = _std_args(
            proxy=InstantProxy(read_fail_n=999))

        vals, _, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertFalse(ok)
        self.assertTrue(np.isnan(vals['ZI x1']))
        self.assertTrue(np.isnan(vals['ZI y1']))

    def test_fails_within_internal_retries_then_succeeds(self):
        """
        Fails MAX_RETRIES=2 times internally, succeeds on the 3rd attempt
        → ok=True (internal read retries are transparent to the caller).
        """
        r = _make_runner()
        # read_fail_n=2: fails attempt 0 and 1, succeeds attempt 2
        proxy = InstantProxy(read_val=5.5, read_fail_n=2)
        devp, dev_sensors, trigger_devs, cfg = _std_args(proxy=proxy)

        vals, _, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertTrue(ok)
        self.assertAlmostEqual(vals['ZI x1'], 5.5)

    def test_fails_one_more_than_retries_gives_nan(self):
        """
        Fails MAX_RETRIES+1=3 times — one more than the internal retry budget
        → ok=False.
        """
        r = _make_runner()
        proxy = InstantProxy(read_fail_n=3)  # MAX_RETRIES+1 = 3
        devp, dev_sensors, trigger_devs, cfg = _std_args(proxy=proxy)

        vals, _, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertFalse(ok)
        self.assertTrue(np.isnan(vals['ZI x1']))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Trigger recovery via proxy refresh
# ─────────────────────────────────────────────────────────────────────────────

class TestTriggerRecovery(unittest.TestCase):

    def setUp(self):
        # Patch fresh_proxy in the runner module's namespace (where it's called)
        self._orig_fresh = _runner_mod.fresh_proxy

    def tearDown(self):
        _runner_mod.fresh_proxy = self._orig_fresh

    def test_single_trigger_failure_recovers_via_proxy_refresh(self):
        """
        Proxy trigger raises once → fresh_proxy is called → new proxy works.
        Device stays in trigger_devs; devp[dev] is updated to the new proxy.
        """
        r = _make_runner()
        dev        = 'dev://zi1'
        bad_proxy  = InstantProxy(trigger_fail_n=1, read_val=3.14)
        good_proxy = InstantProxy(read_val=3.14)

        devp         = {dev: bad_proxy}
        dev_sensors  = {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]}
        trigger_devs = {dev: 'Start'}
        cfg          = {'move_timeout': 5.0}

        _runner_mod.fresh_proxy = lambda path: (good_proxy, None)

        vals, _, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertTrue(ok,         "Should recover after proxy refresh")
        self.assertIn(dev, trigger_devs,     "Device must stay in trigger_devs")
        self.assertIs(devp[dev], good_proxy, "devp must point to the refreshed proxy")
        self.assertAlmostEqual(vals['ZI x1'], 3.14)

    def test_persistent_trigger_failure_fails_point_not_removed(self):
        """
        A device whose trigger keeps failing must NOT be removed from
        trigger_devs (removal let the scan continue forever, silently
        recording the device's stale attribute values).  Instead every
        attempt returns ok=False with the device's sensors forced to NaN,
        so the per-point retry loop auto-pauses the scan.
        """
        r = _make_runner()
        dev = 'dev://zi1'

        class AlwaysFail(InstantProxy):
            def command_inout_asynch(self, *a):
                raise Exception("permanent failure")
            def command_inout(self, *a):
                raise Exception("permanent failure")

        _runner_mod.fresh_proxy = lambda path: (AlwaysFail(), None)

        devp         = {dev: AlwaysFail()}
        dev_sensors  = {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]}
        trigger_devs = {dev: 'Start'}
        cfg          = {'move_timeout': 5.0}

        for _ in range(AUTO_PAUSE_THRESHOLD):
            vals, _t, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)
            self.assertFalse(ok,
                             "Untriggered device must fail the point")
            self.assertTrue(np.isnan(vals['ZI x1']),
                            "Stale read must be replaced by NaN")

        self.assertIn(dev, trigger_devs,
                      "Failing device must stay triggered (retried on Resume)")

    def test_state_poll_failure_fails_point(self):
        """
        A device that triggers fine but whose state() cannot be polled in
        Phase B (5 consecutive failures) must fail the point with NaN —
        a successful read after an unverified acquisition may be stale.
        """
        r = _make_runner()
        dev = 'dev://zi1'

        class NoState(InstantProxy):
            def state(self):
                raise Exception("state poll failure — simulated")

        devp         = {dev: NoState(read_val=3.14)}
        dev_sensors  = {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]}
        trigger_devs = {dev: 'Start'}
        cfg          = {'move_timeout': 5.0}

        vals, _t, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)
        self.assertFalse(ok, "Unverifiable acquisition must fail the point")
        self.assertTrue(np.isnan(vals['ZI x1']),
                        "Possibly-stale read must be replaced by NaN")

    def test_consec_fail_counter_resets_on_recovery(self):
        """
        After one trigger failure + recovery the consecutive-failure counter
        must be 0, not 1.
        """
        r = _make_runner()
        dev  = 'dev://zi1'
        bad  = InstantProxy(trigger_fail_n=1)
        good = InstantProxy()

        devp         = {dev: bad}
        dev_sensors  = {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]}
        trigger_devs = {dev: 'Start'}
        cfg          = {'move_timeout': 5.0}

        _runner_mod.fresh_proxy = lambda path: (good, None)
        _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertEqual(r._trigger_consec_fails.get(dev, 0), 0,
                         "Counter must reset to 0 after successful recovery")


class TestUnhealthyDeviceFailsPoint(unittest.TestCase):
    """
    A lock-in that has lost its instrument connection stays reachable over
    TANGO: ZI/ZI2 Start() silently no-ops when the device is not ON (it only
    warn_streams "Thread is already running"), the state never becomes
    RUNNING, and attribute reads keep answering with the CACHED values of the
    last successful integration.  Every individual call succeeds, so the scan
    used to record a full file of frozen, plausible-looking data.
    """

    def _faulted(self, read_val=7.77):
        class Faulted(InstantProxy):
            """Reachable, answers reads, but permanently in FAULT."""
            def command_inout_asynch(self, cmd, *a):
                pass                      # no exception, and no integration
            def command_inout(self, cmd, *a):
                pass
            def state(self):
                return 'FAULT'
        return Faulted(read_val=read_val)

    def test_faulted_device_fails_point_with_nan(self):
        r   = _make_runner()
        dev = 'dev://zi1'
        devp         = {dev: self._faulted()}
        dev_sensors  = {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]}
        trigger_devs = {dev: 'Start'}
        cfg          = {'move_timeout': 5.0}

        vals, _t, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertFalse(ok, "A device in FAULT must fail the point")
        self.assertTrue(np.isnan(vals['ZI x1']),
                        "Stale cached value must be replaced by NaN, "
                        "never recorded as a measurement")
        self.assertIn(dev, r._last_bad_devs,
                      "Auto-pause message must name the faulted device")

    def test_faulted_device_stays_in_trigger_devs(self):
        """It must keep being triggered so a Reconnect in Jive recovers it."""
        r   = _make_runner()
        dev = 'dev://zi1'
        trigger_devs = {dev: 'Start'}
        _acquire(r, {dev: self._faulted()},
                 {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]},
                 trigger_devs, {'move_timeout': 5.0})
        self.assertIn(dev, trigger_devs)

    def test_healthy_device_unaffected(self):
        """The guard must not fire on a normal acquisition."""
        r = _make_runner()
        devp, dev_sensors, trigger_devs, cfg = _std_args(read_val=2.5)
        vals, _t, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)
        self.assertTrue(ok)
        self.assertAlmostEqual(vals['ZI x1'], 2.5)
        self.assertEqual(r._last_bad_devs, [])

    def test_stuck_running_times_out_and_fails_point(self):
        """
        A device whose acquisition thread died without resetting the state
        stays RUNNING forever.  Phase B used to log the timeout and read
        anyway — an unfinished integration is not this point's value.
        """
        r   = _make_runner()
        dev = 'dev://zi1'

        class StuckRunning(InstantProxy):
            def state(self):
                return 'RUNNING'

        devp         = {dev: StuckRunning(read_val=1.0)}
        dev_sensors  = {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]}
        trigger_devs = {dev: 'Start'}
        cfg          = {'move_timeout': 0.05}     # keep the test fast

        vals, _t, ok = _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertFalse(ok, "Phase-B timeout must fail the point")
        self.assertTrue(np.isnan(vals['ZI x1']))
        self.assertIn(dev, r._last_bad_devs)

    def test_nan_from_successful_read_fails_point(self):
        """
        A device that reports NaN (lock-in poll returned no samples) has not
        measured anything, even though the read call succeeded.
        """
        r   = _make_runner()
        dev = 'dev://zi1'
        devp         = {dev: InstantProxy(read_val=float('nan'))}
        dev_sensors  = {dev: [{'attribute': 'x1', 'label': 'ZI x1'}]}
        trigger_devs = {dev: 'Start'}

        vals, _t, ok = _acquire(r, devp, dev_sensors, trigger_devs,
                                {'move_timeout': 5.0})

        self.assertFalse(ok, "A NaN reading must not count as a good point")
        self.assertIn(dev, r._last_bad_devs)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Per-point retry loop (the logic in run() that calls _do_acquire)
# ─────────────────────────────────────────────────────────────────────────────

class TestPerPointRetryLoop(unittest.TestCase):
    """
    Simulate the retry loop from run() without running a full scan.
    _do_acquire is replaced with a scripted mock on the runner instance.
    """

    def _run_loop(self, runner, acquire_sequence):
        """
        Faithful copy of the new while/for retry-loop from runner.run(), driven
        by a scripted sequence of (vals, ok) results from _do_acquire.

        On pause (all attempts exhausted) we force-abort instead of blocking
        forever — the test can still inspect runner._paused == True.

        Returns (final_vals, log_tags) where log_tags records what happened.
        """
        it  = iter(acquire_sequence)
        log = []

        def _mock_acquire(*a, **kw):
            vals, ok = next(it)
            return vals, 0.0, ok

        runner._do_acquire = _mock_acquire   # instance-level patch

        vals = {}
        while not runner._abort:
            _point_ok = False
            for _pt_attempt in range(AUTO_PAUSE_THRESHOLD):
                if runner._abort: break
                vals, _, _ok = runner._do_acquire(
                    None, None, None, None, None, None, None, None, _noop)
                if _ok:
                    if _pt_attempt > 0:
                        log.append(f"recovered:{_pt_attempt + 1}")
                    _point_ok = True
                    break
                elif _pt_attempt < AUTO_PAUSE_THRESHOLD - 1:
                    log.append(f"retry:{_pt_attempt + 1}")
                else:
                    log.append("pause")
                    runner._paused = True

            if _point_ok or runner._abort:
                break

            # All attempts failed — in tests, force-abort to avoid infinite wait.
            # The caller can inspect runner._paused to confirm the pause occurred.
            runner._abort = True

        return vals, log

    def test_success_on_first_attempt(self):
        r = _make_runner()
        good = {'ZI x1': 1.0}
        vals, log = self._run_loop(r, [(good, True)])

        self.assertFalse(r._paused)
        self.assertEqual(vals, good)
        self.assertEqual(log, [], "No retries expected")

    def test_recovery_on_second_attempt(self):
        r = _make_runner()
        good = {'ZI x1': 2.0}
        vals, log = self._run_loop(r, [
            ({'ZI x1': np.nan}, False),
            (good, True),
        ])

        self.assertFalse(r._paused)
        self.assertEqual(vals, good)
        self.assertIn('retry:1',    log)
        self.assertIn('recovered:2', log)

    def test_recovery_on_last_allowed_attempt(self):
        """Succeeds on attempt N = AUTO_PAUSE_THRESHOLD — scan must not pause."""
        r = _make_runner()
        good = {'ZI x1': 3.0}
        fail = ({'ZI x1': np.nan}, False)
        results = [fail] * (AUTO_PAUSE_THRESHOLD - 1) + [(good, True)]
        vals, log = self._run_loop(r, results)

        self.assertFalse(r._paused)
        self.assertEqual(vals, good)
        self.assertIn(f'recovered:{AUTO_PAUSE_THRESHOLD}', log)

    def test_all_retries_exhausted_pauses_scan(self):
        r = _make_runner()
        bad     = {'ZI x1': np.nan}
        results = [(bad, False)] * AUTO_PAUSE_THRESHOLD
        vals, log = self._run_loop(r, results)

        self.assertTrue(r._paused)
        self.assertIn('pause', log)
        # Exactly AUTO_PAUSE_THRESHOLD-1 "retry" log entries before the pause
        retry_entries = [e for e in log if e.startswith('retry:')]
        self.assertEqual(len(retry_entries), AUTO_PAUSE_THRESHOLD - 1)

    def test_abort_during_retry_stops_loop(self):
        """If runner._abort is set during a retry, the loop exits cleanly."""
        r = _make_runner()
        call_count = [0]

        def _mock_acquire(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 2:
                r._abort = True   # abort mid-retry
            return {'ZI x1': np.nan}, 0.0, False

        r._do_acquire = _mock_acquire

        # Mirror the while/for structure from runner.run()
        while not r._abort:
            _point_ok = False
            for _pt_attempt in range(AUTO_PAUSE_THRESHOLD):
                if r._abort: break
                _, _, _ok = r._do_acquire(None, None, None, None,
                                          None, None, None, None, _noop)
                if _ok:
                    _point_ok = True
                    break
                elif _pt_attempt == AUTO_PAUSE_THRESHOLD - 1:
                    r._paused = True
            if _point_ok or r._abort:
                break
            r._abort = True   # prevent infinite loop in test

        self.assertFalse(r._paused, "Abort must not trigger pause")
        self.assertEqual(call_count[0], 2)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Zigzag 2D traversal order
# ─────────────────────────────────────────────────────────────────────────────

class TestZigzag2D(unittest.TestCase):
    """End-to-end run() over a 3×2 grid, capturing the point-callback order.

    Zigzag must reverse the *physical* X traversal on odd Y rows while keeping
    the spatial data-column index ix correct (ascending-X storage)."""

    def _run_grid(self, zigzag, fast_axis="act1"):
        import tempfile
        proxy = InstantProxy(read_val=1.0)   # shared by stage + sensor devices

        _orig = (_runner_mod.fresh_proxy, _runner_mod.get_proxy,
                 _runner_mod._make_filename)
        _runner_mod.fresh_proxy    = lambda path: (proxy, None)
        _runner_mod.get_proxy      = lambda path: proxy
        _runner_mod._make_filename = lambda cfg: "test.h5"

        sensors = [{
            "enabled": True, "device": "dev://zi", "attribute": "x1",
            "label": "ZI x1", "trigger_cmd": "Start",
            "integ_time_attr": "", "settling_attr": "",
        }]
        order = []   # (iy, ix) in callback order
        filled = {}  # (iy, ix) -> value, to confirm every cell is written
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = {
                    "scan_type": "SPATIAL", "scan_x": True, "scan_y": True,
                    "zigzag": zigzag, "fast_axis": fast_axis, "name": "t",
                    "act1_start": 0.0, "act1_stop": 2.0, "act1_npts": 3,
                    "act2_start": 0.0, "act2_stop": 1.0, "act2_npts": 2,
                    "act1_label": "X", "act1_unit": "nm", "act2_label": "Y",
                    "act1_device": "dev://stage", "act2_device": "dev://stage",
                    "act1_attr": "x", "act2_attr": "y",
                    "integration_time": 0.0, "settle_time": 0.0,
                    "move_timeout": 5.0, "sensors": sensors,
                }
                r = ScanRunner(cfg, {"save_dir": td})
                r._open_hdf5     = lambda *a, **k: MagicMock()
                r._write_point   = lambda *a, **k: None
                r._finalize_hdf5 = lambda *a, **k: None

                def _pt(ix, iy, x, v):
                    order.append((iy, ix))
                    filled[(iy, ix)] = v.get("ZI x1")
                r.run({"point": _pt})
        finally:
            (_runner_mod.fresh_proxy, _runner_mod.get_proxy,
             _runner_mod._make_filename) = _orig
        return order, filled

    def test_zigzag_reverses_odd_rows(self):
        order, _ = self._run_grid(zigzag=True)
        row0 = [ix for (iy, ix) in order if iy == 0]
        row1 = [ix for (iy, ix) in order if iy == 1]
        self.assertEqual(row0, [0, 1, 2], "even row should sweep X forward")
        self.assertEqual(row1, [2, 1, 0], "odd row should sweep X reversed")

    def test_no_zigzag_keeps_forward(self):
        order, _ = self._run_grid(zigzag=False)
        row0 = [ix for (iy, ix) in order if iy == 0]
        row1 = [ix for (iy, ix) in order if iy == 1]
        self.assertEqual(row0, [0, 1, 2])
        self.assertEqual(row1, [0, 1, 2], "without zigzag every row sweeps forward")

    def test_y_fast_outer_is_x(self):
        """Y-fast: X stepped once per column, Y swept inside. Visit order groups
        by column (ix), inner index iy ascending; data still stored [iy, ix]."""
        order, filled = self._run_grid(zigzag=False, fast_axis="act2")
        self.assertEqual(
            order,
            [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)],
            "Y-fast should sweep Y within each X column")
        # Every cell of the 2×3 grid must be written exactly once
        self.assertEqual(set(filled), {(iy, ix) for iy in range(2) for ix in range(3)})

    def test_y_fast_zigzag_reverses_odd_columns(self):
        """Y-fast + zigzag reverses the Y sweep on odd X columns (ix=1)."""
        order, _ = self._run_grid(zigzag=True, fast_axis="act2")
        col0 = [iy for (iy, ix) in order if ix == 0]
        col1 = [iy for (iy, ix) in order if ix == 1]
        col2 = [iy for (iy, ix) in order if ix == 2]
        self.assertEqual(col0, [0, 1], "even column sweeps Y forward")
        self.assertEqual(col1, [1, 0], "odd column sweeps Y reversed")
        self.assertEqual(col2, [0, 1])


# ─────────────────────────────────────────────────────────────────────────────
# 6. Actuator connection guard (no scan against a simulated stage)
# ─────────────────────────────────────────────────────────────────────────────

class TestActuatorGuard(unittest.TestCase):
    """With TANGO available, an unreachable actuator must abort the scan
    before any data is taken — a SimProxy stand-in would silently produce a
    plausible-looking file of fake data."""

    def _run_1d(self, fresh):
        _orig = (_runner_mod.fresh_proxy, _runner_mod._make_filename)
        _runner_mod.fresh_proxy    = fresh
        _runner_mod._make_filename = lambda cfg: "test.h5"
        points = []
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                cfg = {
                    "scan_type": "SPATIAL", "scan_x": True, "scan_y": False,
                    "name": "t", "act1_start": 0.0, "act1_stop": 1.0,
                    "act1_npts": 2, "act1_label": "X", "act1_unit": "nm",
                    "act1_device": "dev://stage", "act1_attr": "x",
                    "integration_time": 0.0, "settle_time": 0.0,
                    "move_timeout": 5.0,
                    "sensors": [{"enabled": True, "device": "dev://zi",
                                 "attribute": "x1", "label": "ZI x1",
                                 "trigger_cmd": "Start",
                                 "integ_time_attr": "", "settling_attr": ""}],
                }
                r = ScanRunner(cfg, {"save_dir": td})
                r._open_hdf5     = lambda *a, **k: MagicMock()
                r._write_point   = lambda *a, **k: None
                r._finalize_hdf5 = lambda *a, **k: None
                fn = r.run({"point": lambda ix, iy, x, v: points.append(ix)})
        finally:
            (_runner_mod.fresh_proxy, _runner_mod._make_filename) = _orig
        return fn, points

    def test_unreachable_actuator_aborts_scan(self):
        proxy = InstantProxy()
        fn, points = self._run_1d(lambda p: (proxy, "connection refused"))
        self.assertIsNone(fn, "Scan must not start against a sim actuator")
        self.assertEqual(points, [], "No points must be acquired")

    def test_reachable_actuator_runs(self):
        proxy = InstantProxy(read_val=1.0)
        fn, points = self._run_1d(lambda p: (proxy, None))
        self.assertEqual(len(points), 2, "Healthy connection must scan normally")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Interleaved trace/retrace traversal (Cryo 2D path)
# ─────────────────────────────────────────────────────────────────────────────

class TestInterleaved2D(unittest.TestCase):
    """Interleaved 2D now routes through _acquire_point_retry — verify the
    traversal order and full grid coverage survived the rewiring."""

    def _run_grid(self):
        import tempfile
        proxy = InstantProxy(read_val=1.0)
        _orig = (_runner_mod.fresh_proxy, _runner_mod._make_filename)
        _runner_mod.fresh_proxy    = lambda path: (proxy, None)
        _runner_mod._make_filename = lambda cfg: "test.h5"
        trace, retrace = [], []
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = {
                    "scan_type": "SPATIAL", "scan_x": True, "scan_y": True,
                    "_interleaved_2d": True, "_interleave_axis": "x", "name": "t",
                    "act1_start": 0.0, "act1_stop": 2.0, "act1_npts": 3,
                    "act2_start": 0.0, "act2_stop": 1.0, "act2_npts": 2,
                    "act1_label": "X", "act1_unit": "nm", "act2_label": "Y",
                    "act1_device": "dev://stage", "act2_device": "dev://stage",
                    "act1_attr": "x", "act2_attr": "y",
                    "integration_time": 0.0, "settle_time": 0.0,
                    "move_timeout": 5.0,
                    "sensors": [{"enabled": True, "device": "dev://zi",
                                 "attribute": "x1", "label": "ZI x1",
                                 "trigger_cmd": "Start",
                                 "integ_time_attr": "", "settling_attr": ""}],
                }
                r = ScanRunner(cfg, {"save_dir": td})
                r._open_hdf5     = lambda *a, **k: MagicMock()
                r._write_point   = lambda *a, **k: None
                r._finalize_hdf5 = lambda *a, **k: None
                r.run({"point":         lambda ix, iy, x, v: trace.append((iy, ix)),
                       "point_retrace": lambda ix, iy, x, v: retrace.append((iy, ix))})
        finally:
            (_runner_mod.fresh_proxy, _runner_mod._make_filename) = _orig
        return trace, retrace

    def test_trace_and_retrace_cover_grid(self):
        trace, retrace = self._run_grid()
        full = {(iy, ix) for iy in range(2) for ix in range(3)}
        self.assertEqual(set(trace),   full, "Trace must visit every cell")
        self.assertEqual(set(retrace), full, "Retrace must visit every cell")

    def test_retrace_sweeps_reversed(self):
        trace, retrace = self._run_grid()
        self.assertEqual([ix for (iy, ix) in trace if iy == 0],   [0, 1, 2])
        self.assertEqual([ix for (iy, ix) in retrace if iy == 0], [2, 1, 0],
                         "Retrace must sweep X in reverse")


# ─────────────────────────────────────────────────────────────────────────────
# 8. HDF5 write-failure detection
# ─────────────────────────────────────────────────────────────────────────────

class TestWritePointFailure(unittest.TestCase):
    """_write_point used to swallow every exception; now it logs the first
    failure and auto-pauses after AUTO_PAUSE_THRESHOLD consecutive ones."""

    def _broken_file(self):
        f = MagicMock()
        f.attrs.__getitem__.side_effect = RuntimeError("disk full")
        return f

    def _runner_with_logs(self):
        r = _make_runner()
        r._write_fail_streak = 0
        r._log_lines = []
        r._lg = r._log_lines.append
        r._st = lambda *a: None
        return r

    def test_first_failure_is_logged(self):
        r = self._runner_with_logs()
        r._write_point(self._broken_file(), 0, 0, 0.0, 0.0, {}, [], "SPATIAL_X")
        self.assertTrue(any("write failed" in m for m in r._log_lines))
        self.assertFalse(r._paused)

    def test_consecutive_failures_pause(self):
        r = self._runner_with_logs()
        for _ in range(AUTO_PAUSE_THRESHOLD):
            r._write_point(self._broken_file(), 0, 0, 0.0, 0.0, {}, [], "SPATIAL_X")
        self.assertTrue(r._paused, "Persistent write failure must pause the scan")


# ─────────────────────────────────────────────────────────────────────────────
# 9. FIELD scan waits for ramping magnets (MOVING state)
# ─────────────────────────────────────────────────────────────────────────────

class TestFieldRampWait(unittest.TestCase):
    """FIELD scans (and temperature sweeps, which use the same path) must
    wait while the magnet device reports MOVING — the AttoDRY superconducting
    magnet ramps for minutes, and reading earlier records the field mid-ramp."""

    class RampProxy:
        RAMP_S = 0.06   # device stays MOVING this long after a setpoint write

        def __init__(self):
            self._until = 0.0
            self.violations = 0
            self.ramps = 0

        def write_attribute(self, attr, val):
            self._until = time.time() + self.RAMP_S
            self.ramps += 1

        def state(self):
            return 'MOVING' if time.time() < self._until else 'ON'

        def read_attribute(self, attr):
            if time.time() < self._until:
                self.violations += 1
            r = MagicMock(); r.value = 0.42
            return r

    def test_field_scan_waits_for_ramp(self):
        import tempfile
        mag = self.RampProxy()
        zi  = InstantProxy(read_val=1.0)
        _orig = (_runner_mod.fresh_proxy, _runner_mod._make_filename,
                 _runner_mod.safe_write, _runner_mod.safe_read)
        _runner_mod.fresh_proxy    = lambda p: ((mag if 'mag' in p else zi), None)
        _runner_mod._make_filename = lambda cfg: "test.h5"
        _runner_mod.safe_write     = lambda p, a, v, **kw: p.write_attribute(a, v)
        _runner_mod.safe_read      = lambda p, a, **kw: (p.read_attribute(a).value, None)
        points = []
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = {
                    "scan_type": "FIELD", "scan_x": True, "scan_y": False,
                    "name": "t", "field_start_A": 0.0, "field_stop_A": 1.0,
                    "field_npts": 2, "field_device": "dev://mag",
                    "integration_time": 0.0, "settle_time": 0.0,
                    "move_timeout": 5.0,
                    "sensors": [{"enabled": True, "device": "dev://zi",
                                 "attribute": "x1", "label": "ZI x1",
                                 "trigger_cmd": "Start",
                                 "integ_time_attr": "", "settling_attr": ""}],
                }
                r = ScanRunner(cfg, {"save_dir": td,
                                     "field_settle_timeout": 5.0})
                r._open_hdf5     = lambda *a, **k: MagicMock()
                r._write_point   = lambda *a, **k: None
                r._finalize_hdf5 = lambda *a, **k: None
                r.run({"point": lambda ix, iy, x, v: points.append(x)})
        finally:
            (_runner_mod.fresh_proxy, _runner_mod._make_filename,
             _runner_mod.safe_write, _runner_mod.safe_read) = _orig
        self.assertEqual(len(points), 2)
        self.assertEqual(mag.ramps, 2, "one setpoint write per point")
        self.assertEqual(mag.violations, 0,
                         "field must never be read while the magnet is MOVING")


# ─────────────────────────────────────────────────────────────────────────────
# 10. Setup-lock stale-stamp parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupLockStamp(unittest.TestCase):
    """Stale-lock recovery relies on parsing the timestamp in the info stamp."""

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from core import setup_lock
        self.sl = setup_lock

    def test_fresh_stamp_age_near_zero(self):
        age = self.sl._stamp_age_hours(self.sl._make_stamp())
        self.assertIsNotNone(age)
        self.assertLess(abs(age), 0.01)

    def test_old_stamp_is_stale(self):
        age = self.sl._stamp_age_hours("pc3:412 @ 2020-01-01 08:00:00")
        self.assertGreater(age, self.sl.STALE_LOCK_HOURS)

    def test_legacy_stamp_without_date_unparseable(self):
        # Old-format stamps (no date) must be treated as held, not stale
        self.assertIsNone(self.sl._stamp_age_hours("pc3:412 @ 14:02:31"))
        self.assertIsNone(self.sl._stamp_age_hours(""))
        self.assertIsNone(self.sl._stamp_age_hours(None))


# ─────────────────────────────────────────────────────────────────────────────
# 11. FIELD/temperature x-axis units come from config (not hardcoded)
# ─────────────────────────────────────────────────────────────────────────────

class TestFieldAxisUnits(unittest.TestCase):
    """_open_hdf5 must label the FIELD axis from field_x_label/unit +
    field_setpoint_unit, so a temperature sweep is 'Temperature [K]' and a
    Beckhoff field scan is 'Field [mT]' — not the old hardcoded Field/T/A."""

    def _open(self, cfg_extra):
        import tempfile, h5py
        r = ScanRunner.__new__(ScanRunner)
        r._abort = False; r._paused = False
        cfg = {"name": "t", "integration_time": 0.1, "settle_time": 0.0,
               "move_timeout": 15.0, "field_segments": [[0.0, 1.0, 4]]}
        cfg.update(cfg_extra)
        x_plan = np.linspace(0.0, 1.0, 4); y_plan = np.array([0.0])
        with tempfile.TemporaryDirectory() as td:
            fn = os.path.join(td, "t.h5")
            f = r._open_hdf5(fn, x_plan, y_plan, [], cfg["field_x_label"],
                             cfg["field_x_unit"], "FIELD", cfg)
            self.assertIsNotNone(f, "open failed")
            d = f["data"]
            xkey = str(f.attrs["_x_key"])
            actual = (d[xkey].attrs["label"], d[xkey].attrs["unit"])
            sp = d[xkey + "_setpoint"].attrs["unit"]
            f.close()
        return xkey, actual, sp

    def test_temperature_sweep_labels(self):
        xkey, (lbl, unit), sp = self._open({
            "field_x_label": "Temperature", "field_x_unit": "K",
            "field_setpoint_unit": "K"})
        self.assertEqual(xkey, "actuator_temperature")
        self.assertEqual((lbl, unit), ("Temperature", "K"))
        self.assertEqual(sp, "K", "setpoint must be K, not the old hardcoded A")

    def test_beckhoff_field_is_mT(self):
        xkey, (lbl, unit), sp = self._open({
            "field_x_label": "Field", "field_x_unit": "mT",
            "field_setpoint_unit": "A"})
        self.assertEqual((lbl, unit), ("Field", "mT"),
                         "Beckhoff field readback is mT, not the old hardcoded T")
        self.assertEqual(sp, "A", "current setpoint is Ampere")


# ─────────────────────────────────────────────────────────────────────────────
# 12. DC hysteresis HDF5 — duplicate channel labels must not crash file creation
# ─────────────────────────────────────────────────────────────────────────────

class TestDcHystDuplicateLabels(unittest.TestCase):
    """Two enabled hyst channels whose labels sanitize to the same dataset key
    used to raise 'Unable to create dataset (name already exists)'. They must
    be deduplicated like the spatial/field path."""

    def _run(self, channels):
        import tempfile, h5py
        proxy = InstantProxy(read_val=1.0)
        _orig = (_runner_mod.fresh_proxy, _runner_mod._make_filename)
        _runner_mod.fresh_proxy    = lambda p: (proxy, None)
        _runner_mod._make_filename = lambda cfg: "t.h5"
        msgs = []
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = {"scan_type": "DC_HYST", "name": "t",
                       "hyst_device": "dev://hyst", "hyst_npts": 4,
                       "hyst_cycles": 1, "hyst_field_A": 1.0, "hyst_int_time": 0.01,
                       "hyst_channels": channels, "sensors": []}
                r = ScanRunner(cfg, {"save_dir": td})
                # Abort immediately after file creation so we only test _open path
                r._read_and_emit_hyst_loop = lambda *a, **k: {}
                r.abort()
                fn = r.run({"status": lambda m: msgs.append(m),
                            "log": lambda m: msgs.append(m)})
        finally:
            (_runner_mod.fresh_proxy, _runner_mod._make_filename) = _orig
        return msgs

    def test_duplicate_blank_labels_do_not_crash(self):
        chans = [{"label": "", "attr": "result1", "enabled": True, "y_axis": "Y1"},
                 {"label": "", "attr": "result2", "enabled": True, "y_axis": "Y2"}]
        msgs = self._run(chans)
        self.assertFalse(any("already exists" in m for m in msgs),
                         "duplicate labels must be deduplicated, not crash: " + repr(msgs))

    def test_identical_labels_do_not_crash(self):
        chans = [{"label": "MOKE", "attr": "result1", "enabled": True, "y_axis": "Y1"},
                 {"label": "MOKE", "attr": "result5", "enabled": True, "y_axis": "Y2"}]
        msgs = self._run(chans)
        self.assertFalse(any("already exists" in m for m in msgs), repr(msgs))


class TestDcHystCalibration(unittest.TestCase):
    """The DC-hyst HDF5 file must carry the BD (λ/2) calibration array under
    /data/calibration, exactly like the spatial/field path in _open_hdf5 —
    previously it was only written by _open_hdf5, so DC-hyst files lacked it."""

    def _run(self, bd_cal):
        import os, glob, tempfile, h5py
        proxy = InstantProxy(read_val=1.0)
        _orig = (_runner_mod.fresh_proxy, _runner_mod._make_filename)
        _runner_mod.fresh_proxy    = lambda p: (proxy, None)
        _runner_mod._make_filename = lambda cfg: "cal.h5"
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = {"scan_type": "DC_HYST", "name": "cal",
                       "hyst_device": "dev://hyst", "hyst_npts": 4,
                       "hyst_cycles": 1, "hyst_field_A": 1.0, "hyst_int_time": 0.01,
                       "hyst_channels": [{"label": "MOKE", "attr": "result1",
                                          "enabled": True, "y_axis": "Y1"}],
                       "sensors": []}
                if bd_cal is not None:
                    cfg["bd_calibration"] = bd_cal
                r = ScanRunner(cfg, {"save_dir": td})
                r._read_and_emit_hyst_loop = lambda *a, **k: {}
                r.abort()
                r.run({"status": lambda m: None, "log": lambda m: None})
                paths = glob.glob(os.path.join(td, "**", "cal.h5"), recursive=True)
                self.assertTrue(paths, "DC-hyst file was not created")
                with h5py.File(paths[0], "r") as f:
                    if "calibration" not in f["data"]:
                        return None
                    ds = f["data"]["calibration"]
                    return (ds[...], dict(ds.attrs))
        finally:
            (_runner_mod.fresh_proxy, _runner_mod._make_filename) = _orig

    def test_calibration_written_to_hdf5(self):
        vals = [0.05, 1.10, 2.18, 3.27, 4.40, 5.51]
        res = self._run(vals)
        self.assertIsNotNone(res, "/data/calibration missing from DC-hyst file")
        arr, attrs = res
        self.assertEqual([float(x) for x in arr], vals)
        def _s(v): return v.decode() if isinstance(v, bytes) else str(v)
        self.assertEqual(_s(attrs.get("unit")), "mV")
        self.assertEqual(_s(attrs.get("role")), "calibration")

    def test_no_calibration_key_writes_no_dataset(self):
        self.assertIsNone(self._run(None),
                          "calibration dataset must be absent when cfg has no bd_calibration")

    def test_all_zero_calibration_not_written(self):
        # All-zero = the BD panel was never filled for this setup — must not be
        # recorded as if it were a real λ/2 sweep (analysis falls back to
        # calibration.txt instead).
        self.assertIsNone(self._run([0.0] * 6),
                          "all-zero calibration must be skipped, not written")


# ─────────────────────────────────────────────────────────────────────────────
# 13. DC hysteresis — raw per-cycle data saved to /data/cycles
# ─────────────────────────────────────────────────────────────────────────────

class _CycleProxy:
    """Fake PyHysteresis exposing GetNumberOfCycles / GetCycle(n).

    Each GetCycle(n) returns 7 blocks of `blk` points (field + result1..6),
    filled with the value `n` so each cycle is trivially identifiable.
    """
    def __init__(self, ncyc, blk, fail_get=()):
        self._ncyc = ncyc
        self._blk  = blk
        self._fail = set(fail_get)

    def command_inout(self, cmd, *a):
        if cmd == "GetNumberOfCycles":
            return self._ncyc
        if cmd == "GetCycle":
            n = a[0]
            if n in self._fail:
                raise Exception(f"simulated GetCycle({n}) failure")
            return (np.ones(7 * self._blk, dtype=float) * float(n)).tolist()
        raise Exception(f"unexpected command {cmd}")


class TestDcHystCycleSave(unittest.TestCase):

    def _save(self, proxy, n_loop, channels=None):
        """Returns (present, blocks_dict, group_attrs).

        blocks_dict maps 'field','result1'..'result6' → 2-D arrays, mirroring
        the /data/cycles GROUP layout (one dataset per quantity).
        """
        import h5py
        r = _make_runner()
        active = channels or [
            {"label": "MOKE (R1)", "attr": "result1", "enabled": True}]
        f = h5py.File("mem.h5", "w", driver="core", backing_store=False)
        f.create_group("data")
        try:
            r._save_hyst_cycles(f, proxy, active, n_loop, _noop)
            present = "cycles" in f["data"]
            blocks, gattr = None, None
            if present:
                grp = f["data"]["cycles"]
                blocks = {k: grp[k][...] for k in grp.keys()}
                gattr = dict(grp.attrs)
        finally:
            f.close()
        return present, blocks, gattr

    def test_stores_group_of_2d_arrays(self):
        # blk == n_loop == 8 (npts=4 → 2*npts)
        present, blocks, gattr = self._save(_CycleProxy(ncyc=3, blk=8), n_loop=8)
        self.assertTrue(present)
        # one 2-D [n_cycles, n_loop] dataset per quantity, not a 3-D cube
        for name in ("field", "result1", "result6"):
            self.assertIn(name, blocks)
            self.assertEqual(blocks[name].shape, (3, 8))
        # cycle n is filled with value n
        for n in range(1, 4):
            self.assertTrue(np.allclose(blocks["result1"][n - 1], float(n)))
        self.assertEqual(int(gattr["n_cycles"]), 3)

    def test_no_cycles_writes_no_group(self):
        present, _, _ = self._save(_CycleProxy(ncyc=0, blk=8), n_loop=8)
        self.assertFalse(present)

    def test_missing_command_is_swallowed(self):
        class NoCmd:
            def command_inout(self, *a):
                raise Exception("GetNumberOfCycles not implemented")
        present, _, _ = self._save(NoCmd(), n_loop=8)
        self.assertFalse(present)

    def test_partial_cycle_failure_keeps_the_rest(self):
        present, blocks, gattr = self._save(
            _CycleProxy(ncyc=3, blk=8, fail_get=(2,)), n_loop=8)
        self.assertTrue(present)
        self.assertEqual(int(gattr["n_cycles"]), 2)        # cycle 2 dropped
        r1 = blocks["result1"]
        self.assertTrue(np.allclose(r1[0], 1.0))
        self.assertTrue(np.all(np.isnan(r1[1])))           # failed cycle → NaN
        self.assertTrue(np.allclose(r1[2], 3.0))


# ─────────────────────────────────────────────────────────────────────────────
# 14. DC hysteresis — Analysis/samba_io.py reads /data/cycles round-trip
# ─────────────────────────────────────────────────────────────────────────────

class _RampCycleProxy:
    """GetCycle(n): field ramps -10n..+10n, result1 = n + optional spike."""
    def __init__(self, ncyc, blk, spike_cycle=None, spike_val=1000.0):
        self.n, self.blk = ncyc, blk
        self.spike_cycle, self.spike_val = spike_cycle, spike_val

    def command_inout(self, cmd, *a):
        if cmd == "GetNumberOfCycles":
            return self.n
        if cmd == "GetCycle":
            n = a[0]
            field = np.linspace(-10.0 * n, 10.0 * n, self.blk)
            r1 = np.ones(self.blk) * (self.spike_val
                                      if n == self.spike_cycle else float(n))
            blocks = [field, r1] + [np.ones(self.blk) * n for _ in range(5)]
            return np.concatenate(blocks).tolist()
        raise Exception("bad command")


class TestHystCycleRoundTrip(unittest.TestCase):
    """A.1 writer (ScanRunner._save_hyst_cycles) ↔ A.3 reader
    (Analysis/samba_io.load_hyst_cycles + re-average + outliers)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Analysis'))
        import samba_io as _sio
        cls.sio = _sio

    def _write(self, proxy, n_loop, channels):
        import tempfile, h5py
        r = _make_runner()
        tmp = tempfile.mktemp(suffix='.h5')
        f = h5py.File(tmp, 'w'); f.create_group('data')
        r._save_hyst_cycles(f, proxy, channels, n_loop, _noop)
        f.close()
        return tmp

    def test_roundtrip_shapes_labels_and_average(self):
        import os as _os
        chans = [{"label": "MOKE (R1)", "attr": "result1", "enabled": True},
                 {"label": "R5 field",  "attr": "result5", "enabled": True}]
        tmp = self._write(_RampCycleProxy(5, 8), 8, chans)
        try:
            cyc = self.sio.load_hyst_cycles(tmp)
        finally:
            _os.remove(tmp)
        self.assertIsNotNone(cyc)
        self.assertEqual(cyc['n_cycles'], 5)
        self.assertEqual(cyc['cube'].shape, (5, 7, 8))
        self.assertEqual(cyc['labels'].get('result1'), "MOKE (R1)")
        self.assertEqual(cyc['labels'].get('result5'), "R5 field")
        # result1 of cycle n is filled with n → mean over all = 3.0
        self.assertAlmostEqual(float(np.nanmean(cyc['result1'])), 3.0)
        # exclude cycles 1 and 5 → average over {2,3,4}, result1 mean = 3.0
        avg = self.sio.hyst_cycle_average(cyc, exclude=(1, 5))
        self.assertEqual(avg['included'], [2, 3, 4])
        self.assertAlmostEqual(float(np.nanmean(avg['result1'])), 3.0)

    def test_missing_dataset_returns_none(self):
        import tempfile, h5py, os as _os
        tmp = tempfile.mktemp(suffix='.h5')
        f = h5py.File(tmp, 'w'); f.create_group('data')
        f['data'].create_dataset('actuator_field', data=np.zeros(8))
        f.close()
        try:
            self.assertIsNone(self.sio.load_hyst_cycles(tmp))
        finally:
            _os.remove(tmp)

    def test_outlier_detection_flags_spiked_cycle(self):
        import os as _os
        chans = [{"label": "MOKE", "attr": "result1", "enabled": True}]
        # cycle 3 spikes far from the others → flagged as outlier
        tmp = self._write(_RampCycleProxy(6, 8, spike_cycle=3), 8, chans)
        try:
            cyc = self.sio.load_hyst_cycles(tmp)
            outliers = self.sio.hyst_detect_outliers(cyc, 'result1', n_sigma=3.0)
        finally:
            _os.remove(tmp)
        self.assertIn(3, outliers)

    def test_all_excluded_raises(self):
        import os as _os
        chans = [{"label": "MOKE", "attr": "result1", "enabled": True}]
        tmp = self._write(_RampCycleProxy(2, 8), 8, chans)
        try:
            cyc = self.sio.load_hyst_cycles(tmp)
            with self.assertRaises(ValueError):
                self.sio.hyst_cycle_average(cyc, exclude=(1, 2))
        finally:
            _os.remove(tmp)


class TestHystAlign(unittest.TestCase):
    """hyst_align_cycles: per-half-loop baseline alignment removes balanced-
    diode drift (per-cycle level jumps + up/down branch offset) while leaving
    the loop amplitude untouched."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Analysis'))
        import samba_io as _sio
        cls.sio = _sio

    def _make_cyc(self, n_cyc=6, half=20, cyc_step=5.0, branch_off=0.3,
                  nan_cycle=None):
        """Synthetic loop: ±1 saturation, switching at ±2 mT; cycle c is offset
        by c*cyc_step, and the up/down halves by ±branch_off."""
        f_up   = np.linspace(-10, 10, half)
        f_down = f_up[::-1]
        y_up   = np.tanh((f_up   - 2.0) * 2.0)   # switches at +2 going up
        y_down = np.tanh((f_down + 2.0) * 2.0)   # switches at −2 going down
        field = np.tile(np.concatenate([f_up, f_down]), (n_cyc, 1))
        loop  = np.concatenate([y_up + branch_off, y_down - branch_off])
        sig   = np.stack([loop + c * cyc_step for c in range(n_cyc)])
        cyc = {'field': field, 'valid': np.ones(n_cyc, bool),
               'n_cycles': n_cyc}
        for name in ('result1', 'result2', 'result3',
                     'result4', 'result5', 'result6'):
            cyc[name] = sig.copy()
        if nan_cycle is not None:
            for name in ('result1', 'result2', 'result3',
                         'result4', 'result5', 'result6'):
                cyc[name][nan_cycle] = np.nan
            cyc['valid'][nan_cycle] = False
        return cyc

    def test_align_removes_cycle_offsets(self):
        cyc = self._make_cyc()
        ali = self.sio.hyst_align_cycles(cyc)
        # every cycle's +saturation level must now be identical
        sat = ali['result1'][:, 18]        # near +10 mT on the up sweep
        self.assertLess(float(np.ptp(sat)), 1e-9)
        self.assertTrue(ali.get('aligned'))
        # original dict untouched (shallow copy with new arrays)
        self.assertGreater(float(np.ptp(cyc['result1'][:, 18])), 1.0)

    def test_align_closes_branch_offset_in_average(self):
        cyc = self._make_cyc()
        half = 20; nt = 2   # tail_frac 0.10 of 20
        def branch_gap(avg):
            up, dn = avg['result1'][:half], avg['result1'][half:]
            return float(np.nanmean(up[-nt:]) - np.nanmean(dn[:nt]))  # both at +sat
        raw = self.sio.hyst_cycle_average(cyc)
        ali = self.sio.hyst_cycle_average(cyc, align=True)
        self.assertGreater(abs(branch_gap(raw)), 0.5)   # 2×branch_off ≈ 0.6
        self.assertLess(abs(branch_gap(ali)), 1e-6)

    def test_align_preserves_amplitude(self):
        cyc = self._make_cyc()
        ali = self.sio.hyst_align_cycles(cyc)
        up = ali['result1'][0, :20]
        amp = up[-2:].mean() - up[:2].mean()   # +sat minus −sat on the up sweep
        self.assertAlmostEqual(amp, 2.0, delta=0.01)

    def test_invalid_cycle_passes_through(self):
        cyc = self._make_cyc(nan_cycle=2)
        ali = self.sio.hyst_align_cycles(cyc)
        self.assertTrue(np.all(np.isnan(ali['result1'][2])))
        # average with align skips it and still closes the branch offset
        avg = self.sio.hyst_cycle_average(cyc, align=True)
        self.assertEqual(avg['included'], [1, 2, 4, 5, 6])


# ─────────────────────────────────────────────────────────────────────────────
# 15. DC hysteresis — recorded-source selection written at scan start (A.4)
# ─────────────────────────────────────────────────────────────────────────────

class TestDcHystSourceWrite(unittest.TestCase):
    """_run_dc_hyst must push cfg['hyst_sources'] to the device's source1..6
    attributes before measuring; an older server that rejects them is tolerated."""

    def _run(self, sources, write_hook=None):
        import tempfile
        writes = []   # (attr, val)
        proxy = InstantProxy(read_val=1.0)
        _orig = (_runner_mod.fresh_proxy, _runner_mod._make_filename,
                 _runner_mod.safe_write)
        _runner_mod.fresh_proxy    = lambda p: (proxy, None)
        _runner_mod._make_filename = lambda cfg: "t.h5"

        def _sw(p, attr, val, **kw):
            writes.append((attr, val))
            return write_hook(attr) if write_hook else None
        _runner_mod.safe_write = _sw
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = {"scan_type": "DC_HYST", "name": "t",
                       "hyst_device": "dev://hyst", "hyst_npts": 4,
                       "hyst_cycles": 1, "hyst_field_A": 1.0,
                       "hyst_int_time": 0.01, "hyst_sources": sources,
                       "hyst_channels": [{"label": "R1", "attr": "result1",
                                          "enabled": True, "y_axis": "Y1"}],
                       "sensors": []}
                r = ScanRunner(cfg, {"save_dir": td})
                r._read_and_emit_hyst_loop = lambda *a, **k: {}
                r.abort()                       # stop after the config writes
                r.run({"status": _noop, "log": _noop})
        finally:
            (_runner_mod.fresh_proxy, _runner_mod._make_filename,
             _runner_mod.safe_write) = _orig
        return writes

    def test_sources_written_in_order(self):
        writes = self._run([1, 2, 13, 4, 15, 6])
        src = [(a, v) for a, v in writes if a.startswith("source")]
        self.assertEqual(src, [("source1", 1), ("source2", 2), ("source3", 13),
                               ("source4", 4), ("source5", 15), ("source6", 6)])

    def test_field_amplitude_written_from_ampere_key(self):
        writes = self._run([1, 2, 3, 4, 5, 6])
        self.assertIn(("MagneticField", 1.0), writes)

    def test_older_server_rejecting_source_is_tolerated(self):
        # safe_write returns an error string for source* → loop breaks, no raise
        writes = self._run(
            [1, 2, 3, 4, 5, 6],
            write_hook=lambda attr: "no such attr" if attr.startswith("source") else None)
        # base params still attempted; scan didn't crash (we got here)
        self.assertTrue(any(a == "MagneticField" for a, _ in writes))


class TestSampleMetadata(unittest.TestCase):
    """_write_hw_metadata records device_id + device resistances so the
    analysis can read the calibration/resistivity from the file's metadata."""

    def _write(self, cfg):
        import h5py, tempfile
        p = os.path.join(tempfile.mkdtemp(), "m.h5")
        with h5py.File(p, "w") as f:
            _runner_mod._write_hw_metadata(f.create_group("metadata"), cfg)
        with h5py.File(p, "r") as f:
            return dict(f["metadata"].attrs)

    def test_device_id_and_resistances_written(self):
        a = self._write({"device_id": "devX", "r_4wire_ohm": 2500.0,
                         "r_2wire_ohm": 3000.0})
        self.assertEqual(a["device_id"], "devX")
        self.assertAlmostEqual(float(a["r_4wire_ohm"]), 2500.0)
        self.assertAlmostEqual(float(a["r_2wire_ohm"]), 3000.0)

    def test_missing_fields_default_safely(self):
        a = self._write({})
        self.assertEqual(a["device_id"], "")
        self.assertAlmostEqual(float(a["r_4wire_ohm"]), 0.0)
        self.assertAlmostEqual(float(a["r_2wire_ohm"]), 0.0)

    def test_legacy_kohm_key_converted_to_ohm(self):
        a = self._write({"r_4wire_kohm": 2.5, "r_2wire_kohm": 3.0})
        self.assertAlmostEqual(float(a["r_4wire_ohm"]), 2500.0)
        self.assertAlmostEqual(float(a["r_2wire_ohm"]), 3000.0)

    def test_fm_thickness_written(self):
        a = self._write({"fm_thickness_nm": 3.5})
        self.assertAlmostEqual(float(a["fm_thickness_nm"]), 3.5)
        b = self._write({})
        self.assertAlmostEqual(float(b["fm_thickness_nm"]), 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# 16. Lab notebook — scanlist column + append-only in-place migration
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core'))
import lab_notebook as _nb_mod                          # noqa: E402


class TestLabNotebookScanlistColumn(unittest.TestCase):
    """The new 'Scanlist' column is the LAST column; an existing notebook whose
    header lacks it is migrated in place (old rows padded), never column-shifted."""

    def _read(self, path):
        import csv
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.reader(fh))

    def test_scanlist_name_recorded_and_blank_for_single(self):
        import tempfile
        nb = os.path.join(tempfile.mkdtemp(), "lab.csv")
        _nb_mod.append_measurement(nb, {"name": "s1", "_scanlist_name": "list_A"})
        _nb_mod.append_measurement(nb, {"name": "s2"})   # single scan → blank
        rows = self._read(nb)
        col = rows[0].index("Scanlist")
        self.assertEqual(col, 7, "Scanlist must be the 8th CSV column")
        self.assertEqual(rows[1][col], "list_A")
        self.assertEqual(rows[2][col], "")

    def test_appends_column_without_shifting_old_rows(self):
        import csv, tempfile
        nb = os.path.join(tempfile.mkdtemp(), "lab.csv")
        # Simulate an OLD notebook whose header is the current one minus the
        # last column — a strict prefix (append-only schema growth).
        old_headers = _nb_mod._HEADERS[:-1]
        with open(nb, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(old_headers)
            w.writerow(["v"] * len(old_headers))   # one legacy row
        # Appending a new measurement must migrate in place, not back up.
        _nb_mod.append_measurement(nb, {"name": "new"})
        self.assertFalse(os.path.exists(nb + ".bak"), "should migrate in place, no .bak")
        rows = self._read(nb)
        self.assertEqual(rows[0], _nb_mod._HEADERS)             # header upgraded
        self.assertEqual(len(rows[1]), len(_nb_mod._HEADERS))   # old row padded
        self.assertEqual(rows[1][-1], "")                       # padded blank

    def test_non_prefix_header_change_backs_up(self):
        import csv, tempfile
        nb = os.path.join(tempfile.mkdtemp(), "lab.csv")
        with open(nb, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(["Totally", "Different", "Header"])
        _nb_mod.append_measurement(nb, {"name": "x"})
        self.assertTrue(os.path.exists(nb + ".bak"), "reordered header → backup")
        rows = self._read(nb)
        self.assertEqual(rows[0], _nb_mod._HEADERS)


class TestDcHystFieldAmpere(unittest.TestCase):
    """The DC-hyst amplitude is a coil CURRENT [A] — the PyHysteresis device
    divides `MagneticField` by its AmperePerVolt property to get the DAC
    voltage.  The value is written unchanged, recorded as MagneticField_A, and
    configs written before the rename (hyst_field_V) must still be honoured."""

    def _run(self, cfg_extra):
        import os, glob, tempfile, h5py
        writes = []
        proxy = InstantProxy(read_val=1.0)
        _orig = (_runner_mod.fresh_proxy, _runner_mod._make_filename,
                 _runner_mod.safe_write)
        _runner_mod.fresh_proxy    = lambda p: (proxy, None)
        _runner_mod._make_filename = lambda cfg: "amp.h5"
        _runner_mod.safe_write     = lambda p, attr, val, **kw: writes.append(
            (attr, val))
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = {"scan_type": "DC_HYST", "name": "amp",
                       "hyst_device": "dev://hyst", "hyst_npts": 4,
                       "hyst_cycles": 1, "hyst_int_time": 0.01,
                       "hyst_channels": [{"label": "MOKE", "attr": "result1",
                                          "enabled": True, "y_axis": "Y1"}],
                       "sensors": []}
                cfg.update(cfg_extra)
                r = ScanRunner(cfg, {"save_dir": td})
                r._read_and_emit_hyst_loop = lambda *a, **k: {}
                r.abort()
                r.run({"status": _noop, "log": _noop})
                paths = glob.glob(os.path.join(td, "**", "amp.h5"), recursive=True)
                self.assertTrue(paths, "DC-hyst file was not created")
                with h5py.File(paths[0], "r") as f:
                    return writes, dict(f["metadata"].attrs)
        finally:
            (_runner_mod.fresh_proxy, _runner_mod._make_filename,
             _runner_mod.safe_write) = _orig

    def test_ampere_key_written_and_recorded(self):
        writes, meta = self._run({"hyst_field_A": 2.5})
        self.assertIn(("MagneticField", 2.5), writes)
        self.assertAlmostEqual(float(meta["MagneticField_A"]), 2.5)
        self.assertNotIn("MagneticField_V", meta)

    def test_legacy_volt_key_still_read(self):
        writes, meta = self._run({"hyst_field_V": 3.25})
        self.assertIn(("MagneticField", 3.25), writes)
        self.assertAlmostEqual(float(meta["MagneticField_A"]), 3.25)


class TestHystFieldKeyMigration(unittest.TestCase):
    """Samba_main config migration v5→v6 renames hyst_field_V → hyst_field_A
    (same number — the value was always an Ampere) and drops the old key."""

    def _config_mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "samba_main_config_mig",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "Samba_main", "config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_value_carried_over_old_key_dropped(self):
        m = self._config_mod()
        cfg = {"_schema_version": 5, "hyst_field_V": 4.5}
        m._migrate_config(cfg)
        self.assertEqual(cfg["hyst_field_A"], 4.5)
        self.assertNotIn("hyst_field_V", cfg)
        self.assertEqual(cfg["_schema_version"], m.SCHEMA_VERSION)

    def test_new_key_wins_and_default_config_uses_ampere(self):
        m = self._config_mod()
        cfg = {"_schema_version": 5, "hyst_field_V": 1.0, "hyst_field_A": 7.0}
        m._migrate_config(cfg)
        self.assertEqual(cfg["hyst_field_A"], 7.0)
        self.assertNotIn("hyst_field_V", m.make_default_config())
        self.assertIn("hyst_field_A", m.make_default_config())


class TestSetupLoadStatus(unittest.TestCase):
    """load_setup must never silently swallow an unreadable setup file:
    it backs the file up to <name>.json.bad and reports _load_status so the
    app can warn instead of quietly overwriting the file with defaults
    (the 'copied .config to a new machine, IR configs gone' report)."""

    def _fresh_config(self, tmpdir):
        """Import the real Samba_main/config.py against a temp CONFIG_DIR."""
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "samba_main_config",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "Samba_main", "config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.CONFIG_DIR = Path(tmpdir)
        return mod

    def test_valid_file_status_ok(self):
        import json, tempfile
        tmp = tempfile.mkdtemp()
        cfgmod = self._fresh_config(tmp)
        good = cfgmod.make_default_setup("Green")
        good.pop("_load_status", None)
        with open(os.path.join(tmp, "Green.json"), "w") as f:
            json.dump(good, f)
        d = cfgmod.load_setup("Green")
        self.assertEqual(d.get("_load_status"), "ok")

    def test_corrupt_file_backed_up_and_reported(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        cfgmod = self._fresh_config(tmp)
        p = os.path.join(tmp, "IR.json")
        with open(p, "w") as f:
            f.write("{ this is not json")
        d = cfgmod.load_setup("IR")
        self.assertTrue(d.get("_load_status", "").startswith("error"),
                        d.get("_load_status"))
        self.assertTrue(os.path.exists(p + ".bad"),
                        "unreadable file must be backed up")
        with open(p + ".bad") as f:
            self.assertEqual(f.read(), "{ this is not json",
                             "backup must preserve the original bytes")
        with open(p) as f:
            self.assertEqual(f.read(), "{ this is not json",
                             "original must not be touched by load")

    def test_missing_file_reported(self):
        import tempfile
        cfgmod = self._fresh_config(tempfile.mkdtemp())
        d = cfgmod.load_setup("IR")
        self.assertEqual(d.get("_load_status"), "missing")

    def test_save_strips_load_status(self):
        import json, tempfile
        tmp = tempfile.mkdtemp()
        cfgmod = self._fresh_config(tmp)
        d = cfgmod.make_default_setup("Green")
        d["_load_status"] = "missing"
        cfgmod.save_setup("Green", d)
        with open(os.path.join(tmp, "Green.json")) as f:
            saved = json.load(f)
        self.assertNotIn("_load_status", saved)


class TestZeroAfterScanConfig(unittest.TestCase):
    """Per-axis 'Zero after scan' persistence contract.

    The flag decides whether real stage motion happens once a scan ends, so
    it must default to False everywhere: a config that predates the feature
    must never come back armed."""

    def _fresh_config(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "samba_main_config_zero",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "Samba_main", "config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_defaults_present_and_off(self):
        cfgmod = self._fresh_config()
        cfg = cfgmod.make_default_config("scan")
        self.assertIs(cfg["act1_zero_after"], False)
        self.assertIs(cfg["act2_zero_after"], False)

    def test_migration_backfills_old_config(self):
        cfgmod = self._fresh_config()
        old = {"_schema_version": 5, "scan_type": "SPATIAL"}
        cfgmod._migrate_config(old)
        self.assertIs(old["act1_zero_after"], False)
        self.assertIs(old["act2_zero_after"], False)
        self.assertEqual(old["_schema_version"], cfgmod.SCHEMA_VERSION)

    def test_migration_preserves_existing_choice(self):
        cfgmod = self._fresh_config()
        cfg = {"_schema_version": 5, "act1_zero_after": True}
        cfgmod._migrate_config(cfg)
        self.assertIs(cfg["act1_zero_after"], True)
        self.assertIs(cfg["act2_zero_after"], False)


class TestPolarityControlConfig(unittest.TestCase):
    """Scanlist polarity control (relay / field flip) persistence + metadata.

    The flags decide whether the analysis can separate positive and negative
    polarity groups, so they must round-trip into the config and be recorded
    in the HDF5 file of every scanlist scan — but stay absent on single scans,
    which flip nothing."""

    def _fresh_config(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "samba_main_config_polarity",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "Samba_main", "config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_defaults_present_and_off(self):
        cfg = self._fresh_config().make_default_config("scan")
        self.assertIs(cfg["relay_flip"], False)
        self.assertIs(cfg["field_flip"], False)

    def test_migration_backfills_old_config(self):
        cfgmod = self._fresh_config()
        old = {"_schema_version": 6, "scan_type": "SPATIAL"}
        cfgmod._migrate_config(old)
        self.assertIs(old["relay_flip"], False)
        self.assertIs(old["field_flip"], False)
        self.assertEqual(old["_schema_version"], cfgmod.SCHEMA_VERSION)

    def test_migration_preserves_existing_choice(self):
        cfgmod = self._fresh_config()
        cfg = {"_schema_version": 6, "field_flip": True}
        cfgmod._migrate_config(cfg)
        self.assertIs(cfg["field_flip"], True)
        self.assertIs(cfg["relay_flip"], False)

    def _meta_attrs(self, cfg):
        import h5py, tempfile
        p = os.path.join(tempfile.mkdtemp(), "pol.h5")
        with h5py.File(p, "w") as f:
            _runner_mod._write_hw_metadata(f.create_group("metadata"), cfg)
        with h5py.File(p, "r") as f:
            return dict(f["metadata"].attrs)

    def test_hdf5_records_flags_when_set(self):
        a = self._meta_attrs({"relay_flip": True, "field_flip": False})
        self.assertTrue(bool(a["relay_flip"]))
        self.assertFalse(bool(a["field_flip"]))

    def test_hdf5_omits_flags_for_single_scan(self):
        """A single scan never sets the keys — recording False would claim a
        polarity configuration that was never applied."""
        a = self._meta_attrs({})
        self.assertNotIn("relay_flip", a)
        self.assertNotIn("field_flip", a)


class TestNStepPair(unittest.TestCase):
    """core/nstep.py — N ↔ Δ-step coupling used by the trajectory panels."""

    class _FakeSpin:
        """Minimal spinbox: value()/setValue()/valueChanged.connect."""
        def __init__(self, value, integer=False, lo=None, hi=None):
            self._v = value; self._int = integer
            self._lo = lo; self._hi = hi
            self._subs = []
        def value(self):
            return self._v
        def maximum(self):
            return self._hi if self._hi is not None else 2147483647
        def setValue(self, v):
            v = int(v) if self._int else float(v)
            if self._int and not (-2147483648 <= v <= 2147483647):
                raise OverflowError("argument 1 overflowed")   # like Qt
            if self._lo is not None: v = max(self._lo, v)
            if self._hi is not None: v = min(self._hi, v)
            if v == self._v:
                return                       # Qt suppresses no-change signals
            self._v = v
            for fn in list(self._subs):
                fn(v)
        class _Sig:
            def __init__(self, spin): self._spin = spin
            def connect(self, fn):    self._spin._subs.append(fn)
        @property
        def valueChanged(self):
            return self._Sig(self)

    def _mk(self, start=0.0, stop=100.0, npts=51):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core'))
        import nstep
        span = {"v": stop - start}
        n = self._FakeSpin(npts, integer=True, lo=2, hi=10000)
        s = self._FakeSpin(1.0)
        pair = nstep.NStepPair(n, s, lambda: span["v"])
        pair.set_npts(npts)
        return pair, n, s, span

    def test_load_derives_step_and_anchors_step(self):
        pair, n, s, _ = self._mk(0, 100, 51)
        self.assertAlmostEqual(s.value(), 2.0)     # 100 / 50
        self.assertEqual(pair.anchor, "step")

    def test_edit_step_derives_n(self):
        pair, n, s, _ = self._mk(0, 100, 51)
        s.setValue(5.0)                            # user types Δ = 5
        self.assertEqual(n.value(), 21)            # 100/5 + 1
        self.assertEqual(pair.anchor, "step")

    def test_edit_n_derives_step(self):
        pair, n, s, _ = self._mk(0, 100, 51)
        n.setValue(101)                            # user types N = 101
        self.assertAlmostEqual(s.value(), 1.0)
        self.assertEqual(pair.anchor, "n")

    def test_span_change_preserves_step_by_default(self):
        pair, n, s, span = self._mk(0, 100, 51)    # step = 2
        span["v"] = 200.0
        pair.span_changed()
        self.assertAlmostEqual(s.value(), 2.0)     # step kept (the base)
        self.assertEqual(n.value(), 101)           # N recomputed

    def test_span_change_preserves_n_after_n_edit(self):
        pair, n, s, span = self._mk(0, 100, 51)
        n.setValue(11)                             # anchor → n, step = 10
        span["v"] = 200.0
        pair.span_changed()
        self.assertEqual(n.value(), 11)            # N kept
        self.assertAlmostEqual(s.value(), 20.0)    # step recomputed

    def test_zero_span_leaves_values_untouched(self):
        pair, n, s, span = self._mk(0, 100, 51)
        span["v"] = 0.0
        pair.span_changed()
        self.assertEqual(n.value(), 51)            # time-scan safety: no clobber
        s.setValue(3.0)                            # step edit with zero span
        self.assertEqual(n.value(), 51)

    def test_set_step_derives_n(self):
        pair, n, s, _ = self._mk(0, 100, 51)
        pair.set_step(4.0)                         # config load with stored ΔT
        self.assertAlmostEqual(s.value(), 4.0)
        self.assertEqual(n.value(), 26)
        self.assertEqual(pair.anchor, "step")

    def test_tiny_step_clamps_to_spin_max_no_overflow(self):
        # Typing a step starting with "0" used to emit an intermediate value
        # clamped to the spin minimum (1e-6); over a 50000 nm span the derived
        # N exceeded Qt's 32-bit range and setValue raised OverflowError.
        pair, n, s, span = self._mk(0, 50000.0, 51)
        s.setValue(1e-6)                           # must not raise
        self.assertEqual(n.value(), 10000)         # clamped to the N box max
        self.assertEqual(pair.anchor, "step")


# ─────────────────────────────────────────────────────────────────────────────
# 17. Live plots — "Recent" y-scale mode (±max|y| over the last N points)
# ─────────────────────────────────────────────────────────────────────────────

import plot_interact as _pi_mod                          # noqa: E402


class TestRecentSymmetricYlim(unittest.TestCase):
    """core/plot_interact.recent_symmetric_ylim — the maths behind the
    Full/Recent y-scale pill on the 1D and calibration plots.

    The mode exists to follow a signal down to zero across orders of
    magnitude, so the two properties that matter are: zero stays exactly
    centred, and old large values must not keep the axis wide."""

    def test_symmetric_about_zero(self):
        lo, hi = _pi_mod.recent_symmetric_ylim([[-3.0, 1.0, 2.0]])
        self.assertAlmostEqual(lo, -hi)
        self.assertGreaterEqual(hi, 3.0)        # covers max|y|, plus padding

    def test_ignores_points_before_the_window(self):
        """A huge early transient must not hold the axis open — that is the
        whole point of the mode."""
        y = [1000.0] * 50 + [1e-6] * 50         # settles to µ-scale
        lo, hi = _pi_mod.recent_symmetric_ylim(y and [y], window=50)
        self.assertLess(hi, 1e-5)
        self.assertAlmostEqual(lo, -hi)

    def test_window_shorter_than_data_available(self):
        lo, hi = _pi_mod.recent_symmetric_ylim([[5.0, 4.0, 3.0]], window=2)
        self.assertGreaterEqual(hi, 4.0)        # last two are 4 and 3
        self.assertLess(hi, 5.0)                # the 5.0 is outside the window

    def test_combines_across_curves_on_one_axis(self):
        lo, hi = _pi_mod.recent_symmetric_ylim([[0.1, 0.2], [-7.0, 0.3]])
        self.assertGreaterEqual(hi, 7.0)
        self.assertAlmostEqual(lo, -hi)

    def test_nan_is_ignored(self):
        y = [np.nan, 2.0, np.nan, 1.0]
        lo, hi = _pi_mod.recent_symmetric_ylim([y])
        self.assertGreaterEqual(hi, 2.0)
        self.assertTrue(np.isfinite(lo) and np.isfinite(hi))

    def test_no_finite_data_returns_none(self):
        """Caller leaves the existing limits alone rather than collapsing."""
        self.assertIsNone(_pi_mod.recent_symmetric_ylim([]))
        self.assertIsNone(_pi_mod.recent_symmetric_ylim([[]]))
        self.assertIsNone(_pi_mod.recent_symmetric_ylim([[np.nan, np.inf]]))

    def test_all_zero_tail_stays_non_degenerate(self):
        """matplotlib rejects a zero-width range; a nulled signal must not
        crash the live plot."""
        lo, hi = _pi_mod.recent_symmetric_ylim([[0.0, 0.0, 0.0]])
        self.assertLess(lo, hi)
        self.assertAlmostEqual(lo, -hi)


class TestNulByteStringAttrs(unittest.TestCase):
    """_wsa strips embedded NULs before writing an HDF5 string attribute.

    HDF5 VLEN strings cannot contain NUL bytes — h5py raises ValueError.
    TANGO DevString readbacks come from fixed-size C buffers and are often
    null-padded ("20mA\\x00\\x00\\x00"), and those land in the hardware
    snapshot.  Most string attrs are written by _open_hdf5 at scan START, so
    without stripping, one padded device string aborts the whole measurement
    with an error that points nowhere near the real cause."""

    def _attrs(self, cfg_val):
        import h5py, tempfile
        p = os.path.join(tempfile.mkdtemp(), "nul.h5")
        with h5py.File(p, "w") as f:
            g = f.create_group("metadata")
            _runner_mod._wsa(g, "device_id", cfg_val)
        with h5py.File(p, "r") as f:
            return dict(f["metadata"].attrs)

    def test_null_padded_string_is_written(self):
        a = self._attrs("PyKeithley2\x00\x00\x00")
        self.assertEqual(a["device_id"], "PyKeithley2")

    def test_interior_null_is_removed(self):
        a = self._attrs("20mA\x00range")
        self.assertEqual(a["device_id"], "20mArange")

    def test_clean_string_is_untouched(self):
        a = self._attrs("20mA")
        self.assertEqual(a["device_id"], "20mA")

    def test_raw_h5py_would_reject_it(self):
        """Guards the premise: if h5py ever accepts NULs the strip is moot,
        but until then removing it re-breaks scan start."""
        import h5py, tempfile
        p = os.path.join(tempfile.mkdtemp(), "raw.h5")
        with h5py.File(p, "w") as f:
            g = f.create_group("m")
            with self.assertRaises(ValueError):
                g.attrs.create("x", data="a\x00b",
                               dtype=h5py.string_dtype())


import bd_fit as _bd                                     # noqa: E402


def _staircase(levels, hold=300, ramp=60, noise=2e-5, seed=1, t_step=0.01):
    """A hand-turned λ/2 sweep: flat holds joined by gradual ramps."""
    r = np.random.default_rng(seed)
    y = []
    for i, L in enumerate(levels):
        if i and ramp:
            y.extend(np.linspace(levels[i - 1], L, ramp + 2)[1:-1])
        y.extend([L] * hold)
    y = np.asarray(y, dtype=float) + r.normal(0, noise, len(y))
    return np.arange(len(y)) * t_step, y


class TestBDStepFit(unittest.TestCase):
    """core/bd_fit.py — λ/2 calibration staircase fitting.

    A wrong calibration silently rescales every later SOT result, so the
    contract is: fit accurately when the trace really is a staircase, and
    refuse outright otherwise.  Never return plausible-looking numbers for a
    trace that isn't one."""

    LEVELS = np.array([0.05, 1.10, 2.18, 3.27, 4.40, 5.51]) * 1e-3   # volts

    def test_six_levels_recovered(self):
        t, dc = _staircase(self.LEVELS)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(len(r.levels_V), 6)
        np.testing.assert_allclose(r.levels_V, self.LEVELS, atol=2e-5)

    def test_values_converted_to_mV(self):
        """The panel's boxes are mV; the trace is V."""
        t, dc = _staircase(self.LEVELS)
        r = _bd.fit_calibration(t, dc)
        np.testing.assert_allclose(r.levels_mV,
                                   np.asarray(r.levels_V) * 1000.0)
        self.assertAlmostEqual(r.levels_mV[-1], 5.51, places=1)

    def test_gradual_transitions(self):
        """The plate is turned by hand, so transitions span many samples.
        A per-sample-difference detector walks straight through those."""
        for ramp in (6, 60, 200, 300):
            t, dc = _staircase(self.LEVELS, ramp=ramp)
            r = _bd.fit_calibration(t, dc)
            self.assertTrue(r.ok, f"ramp={ramp}: {r.reason}")
            np.testing.assert_allclose(r.levels_V, self.LEVELS, atol=3e-5,
                                       err_msg=f"ramp={ramp}")

    def test_parking_holds_around_the_sweep_are_excluded(self):
        """The real-data failure this rule exists for.

        Levels and layout taken from
        20260810/102928_TIME_N37Cr_10_Ni_15__001_calibration.h5: the operator
        parks the plate at +37 mV before the sweep and returns to −5 mV after
        it.  Selecting "the six closest to 0 V" picks both parking holds and
        drops two genuine ticks; the sweep is not centred on zero.
        """
        sweep = np.array([43.0, 21.6, 0.9, -20.0, -41.8, -63.6]) * 1e-3
        trace = np.concatenate([[37.3e-3, 38.0e-3], sweep, [-4.8e-3]])
        t, dc = _staircase(trace, seed=11)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        np.testing.assert_allclose(r.levels_V, sweep, atol=3e-5)
        # The parking levels must not appear among the imported values.
        for parked in (37.3, 38.0, -4.8):
            self.assertFalse(any(abs(v - parked) < 0.5 for v in r.levels_mV),
                             f"{parked} mV leaked into the calibration")

    def test_spurious_holds_nearer_zero_than_real_ticks(self):
        """A parking hold close to 0 V must still lose to the even staircase."""
        sweep = np.array([43.0, 21.6, 0.9, -20.0, -41.8, -63.6]) * 1e-3
        trace = np.concatenate([[0.4e-3], sweep, [-1.0e-3]])
        t, dc = _staircase(trace, seed=12)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        np.testing.assert_allclose(r.levels_V, sweep, atol=3e-5)

    def test_selected_levels_are_time_ordered(self):
        """The boxes are ticks 0..25 in the order the operator stepped
        through them — i.e. time order."""
        trace = np.concatenate([[37e-3], self.LEVELS, [-25e-3]])
        t, dc = _staircase(trace, seed=2)
        r = _bd.fit_calibration(t, dc)
        times = [p.t0 for p in r.selected]
        self.assertEqual(times, sorted(times))

    def test_uniform_spacing_reported(self):
        t, dc = _staircase(self.LEVELS)
        r = _bd.fit_calibration(t, dc)
        self.assertLess(r.spacing_cv, 0.05)
        self.assertAlmostEqual(r.mean_step_V * 1000, 1.09, places=1)

    def test_split_hold_is_rejoined(self):
        """A hold broken in two by a glitch would otherwise shift the whole
        consecutive run by one plateau."""
        sweep = np.array([43.0, 21.6, 0.9, -20.0, -41.8, -63.6]) * 1e-3
        # Second tick recorded as two nearly-equal fragments.
        trace = np.array([43.0, 21.6, 21.55, 0.9, -20.0, -41.8, -63.6]) * 1e-3
        t, dc = _staircase(trace, seed=13)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(len(r.levels_V), 6)
        np.testing.assert_allclose(r.levels_V, sweep, atol=1e-4)

    def test_drifting_flat_trace_refused(self):
        """A flat trace with slow drift can be carved into six evenly spaced
        "levels" tens of µV apart — uniform enough to pass the spacing test on
        its own.  Three real files on the lab machine did exactly that.  The
        step must also be large compared with the noise."""
        t = np.arange(3000) * 0.02
        dc = 0.017 + np.linspace(0, -1e-4, t.size)          # 0.1 mV of drift
        dc = dc + np.random.default_rng(15).normal(0, 3e-5, t.size)
        r = _bd.fit_calibration(t, dc)
        self.assertFalse(r.ok, f"accepted a flat trace: {r.levels_mV}")

    def test_real_step_survives_the_noise_guard(self):
        """The guard must not reject a genuine sweep: real files measure
        250-420 sigma per step, the flat ones under 10."""
        t, dc = _staircase(self.LEVELS, noise=2e-5)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)

    def test_uneven_spacing_refused(self):
        """Six holds that are not an even staircase are not a λ/2 sweep."""
        ragged = np.array([1.0, 1.4, 9.0, 9.3, 25.0, 25.2]) * 1e-3
        t, dc = _staircase(ragged, seed=14)
        r = _bd.fit_calibration(t, dc)
        self.assertFalse(r.ok)
        self.assertIn("unevenly spaced", r.reason)

    def test_offset_trace(self):
        """Real DC sits well away from 0 V (~16 mV on the IR setup)."""
        lv = self.LEVELS + 16.5e-3
        t, dc = _staircase(lv, seed=3)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        np.testing.assert_allclose(r.levels_V, lv, atol=2e-5)

    def test_too_few_plateaus_refused(self):
        t, dc = _staircase(self.LEVELS[:3], seed=6)
        r = _bd.fit_calibration(t, dc)
        self.assertFalse(r.ok)
        self.assertIn("need 6", r.reason)
        self.assertEqual(r.levels_V, [])

    def test_flat_trace_refused(self):
        """An aborted scan is flat — it must not yield a calibration."""
        t = np.arange(500) * 0.01
        dc = 0.0165 + np.random.default_rng(7).normal(0, 2e-5, 500)
        r = _bd.fit_calibration(t, dc)
        self.assertFalse(r.ok)

    def test_fragmented_trace_refused(self):
        """Sawtooth, not a staircase: no holds at all.  Must refuse rather
        than carve six look-alike levels out of the ramps."""
        t = np.arange(4000) * 0.01
        dc = 5e-3 * np.abs(((t / 2.0) % 2.0) - 1.0)
        dc = dc + np.random.default_rng(9).normal(0, 2e-5, dc.size)
        r = _bd.fit_calibration(t, dc)
        self.assertFalse(r.ok)

    def test_step_curve_breaks_between_plateaus(self):
        """The overlay must not connect across holds — NaN separators."""
        t, dc = _staircase(self.LEVELS)
        r = _bd.fit_calibration(t, dc)
        x, y = r.step_curve()
        self.assertEqual(np.isnan(x).sum(), 6)
        finite = y[np.isfinite(y)]
        np.testing.assert_allclose(np.unique(np.round(finite, 12)).size, 6)

    def test_filename_pattern(self):
        self.assertTrue(_bd.is_time_calibration("103813_TIME_W_15_2e_calibration.h5"))
        self.assertTrue(_bd.is_time_calibration(
            "/a/b/151505_TIME_N37Cr_10_Ni_5__001_-Federica_calibration.h5"))
        for bad in ("151610_TIME_W_15_2e_SOT_y.h5",     # not a calibration
                    "123125_TIME_x_scan_y.h5",
                    "calibration.h5",                    # no HHMMSS_TIME_
                    "103813_SPATIAL_W_calibration.h5"):  # not a TIME scan
            self.assertFalse(_bd.is_time_calibration(bad), bad)

    def test_load_dc_time_roundtrip(self):
        import h5py, tempfile
        p = os.path.join(tempfile.mkdtemp(), "103813_TIME_x_calibration.h5")
        t_in, dc_in = _staircase(self.LEVELS)
        with h5py.File(p, "w") as f:
            g = f.create_group("data")
            g.create_dataset("time", data=t_in)
            g.create_dataset("DC", data=dc_in)
        t, dc = _bd.load_dc_time(p)
        self.assertEqual(t.size, t_in.size)
        r = _bd.fit_file(p)
        self.assertTrue(r.ok, r.reason)
        np.testing.assert_allclose(r.levels_V, self.LEVELS, atol=2e-5)

    def test_load_rejects_file_without_dc(self):
        import h5py, tempfile
        p = os.path.join(tempfile.mkdtemp(), "x.h5")
        with h5py.File(p, "w") as f:
            f.create_group("data").create_dataset("time", data=np.arange(5.0))
        with self.assertRaises(ValueError):
            _bd.load_dc_time(p)

    def test_nan_samples_dropped(self):
        import h5py, tempfile
        p = os.path.join(tempfile.mkdtemp(), "n.h5")
        t_in, dc_in = _staircase(self.LEVELS)
        dc_in = dc_in.copy(); dc_in[::500] = np.nan      # partial/aborted rows
        with h5py.File(p, "w") as f:
            g = f.create_group("data")
            g.create_dataset("time", data=t_in)
            g.create_dataset("DC", data=dc_in)
        t, dc = _bd.load_dc_time(p)
        self.assertTrue(np.isfinite(dc).all())
        self.assertLess(t.size, t_in.size)

    def test_latest_h5_picks_newest(self):
        import tempfile, time as _t
        d = tempfile.mkdtemp()
        for name in ("a.h5", "b.h5"):
            with open(os.path.join(d, name), "w") as f:
                f.write("x")
            _t.sleep(0.01)
        open(os.path.join(d, "notes.txt"), "w").close()
        self.assertEqual(os.path.basename(_bd.latest_h5(d)), "b.h5")
        self.assertIsNone(_bd.latest_h5(os.path.join(d, "nope")))


class TestBDSymmetryTieBreak(unittest.TestCase):
    """Choosing WHICH six of a longer uniform staircase are the tick positions.

    The operator often steps past the six ticks, leaving several overlapping
    runs of six that are all evenly spaced.  Step uniformity then cannot tell
    them apart — on the real file 20260811/124802 the two candidates scored
    1.8 % and 1.9 %, and the 1.8 % winner was the wrong six.  The λ/2 sweep is
    taken about the balance point, where the balanced diode reads zero, so the
    tick levels straddle zero; that is the tie-breaker.
    """

    def test_centred_run_wins_over_marginally_more_uniform_one(self):
        """Reproduces 124802: 8 uniform holds, the centred six must win."""
        levels = np.array([95.59, 59.14, 22.36, -14.06, -52.39,
                           -89.90, -127.33, -163.61]) * 1e-3
        t, dc = _staircase(levels, hold=250, ramp=80, noise=2e-5, seed=7)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        np.testing.assert_allclose(r.levels_V, levels[:6], atol=5e-5)
        self.assertLess(abs(r.midpoint_mV), 10.0,
                        f"chosen span must straddle zero, got "
                        f"{r.midpoint_mV:+.2f} mV")
        self.assertGreater(r.n_candidates, 1,
                           "the ambiguity must be reported to the operator")

    def test_offcentre_sweep_still_fits(self):
        """A sweep genuinely not centred on zero must not be distorted.

        Only one run is evenly spaced here (the parking holds break the
        spacing), so symmetry must not be allowed to pull the selection away
        from it — this is the 102928 case that killed the old
        'six values closest to zero' rule.
        """
        ticks = np.array([43.0, 21.6, 0.9, -20.0, -41.8, -63.6]) * 1e-3
        levels = np.concatenate(([37.0e-3], ticks, [-5.0e-3]))
        t, dc = _staircase(levels, hold=250, ramp=60, noise=2e-5, seed=3)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        np.testing.assert_allclose(r.levels_V, ticks, atol=5e-5)

    def test_uniformity_still_dominates_a_badly_uneven_run(self):
        """A perfectly centred but unevenly spaced run must not win.

        Symmetry only breaks ties between comparably uniform runs; it can
        never promote a run that is not a staircase.
        """
        # A clean staircase far from zero, then a wildly uneven set of holds
        # that happens to be centred on zero.  The uneven group must not
        # continue the 10 mV spacing, or it would extend the staircase and
        # legitimately offer a more centred uniform run.
        good = np.array([100.0, 90.0, 80.0, 70.0, 60.0, 50.0]) * 1e-3
        uneven = np.array([-39.0, 35.0, -36.0, 2.0, -40.0, 38.0]) * 1e-3
        t, dc = _staircase(np.concatenate((good, uneven)),
                           hold=250, ramp=60, noise=2e-5, seed=11)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        np.testing.assert_allclose(r.levels_V, good, atol=5e-5)

    def test_single_candidate_reports_no_ambiguity(self):
        t, dc = _staircase(TestBDStepFit.LEVELS)
        r = _bd.fit_calibration(t, dc)
        self.assertTrue(r.ok, r.reason)
        self.assertEqual(r.n_candidates, 1)

    def test_tie_tolerance_never_exceeds_the_uniformity_bar(self):
        """A run admitted only by the tie tolerance must still pass
        MAX_SPACING_CV, so the tie-break can never turn a good fit into a
        refusal."""
        self.assertLessEqual(
            max(min(_bd.MAX_SPACING_CV * _bd.CV_TIE_FRAC, _bd.CV_TIE_ABS),
                _bd.MAX_SPACING_CV),
            _bd.MAX_SPACING_CV)


class TestSafeReadStringAttr(unittest.TestCase):
    """safe_read must never turn a string attribute into its first character.

    `float(raw[0]) if hasattr(raw, "__len__")` was written for SmarAct
    position arrays, but a str also has __len__ — so the Keithley's string
    `range` attribute read back as float(("20mA")[0]) == 2.0, and "100mA" as
    1.0.  Plausible numbers that matched no range in the combo, which is what
    made the panel report `range=2.0 (not selectable)` while the device was
    plainly on 20mA.  String attributes must go through safe_read_str.
    """

    @staticmethod
    def _hardware():
        """Import the real core/hardware.py (the suite stubs `hardware`)."""
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "core", "hardware.py")
        spec = importlib.util.spec_from_file_location("_hw_real", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    class _P:
        def __init__(self, val): self.val = val
        def read_attribute(self, attr):
            r = MagicMock(); r.value = self.val; return r
        def set_timeout_millis(self, ms): pass

    def test_range_strings_are_not_truncated_to_a_number(self):
        hw = self._hardware()
        for s in ("0.0002mA", "0.002mA", "0.02mA", "0.2mA",
                  "2mA", "20mA", "100mA"):
            val, err = hw.safe_read(self._P(s), "range", timeout=None)
            self.assertIsNone(
                val, f"safe_read({s!r}) must not yield a number, got {val!r}")
            self.assertIsNotNone(err, f"safe_read({s!r}) must report an error")

    def test_safe_read_str_returns_the_whole_string(self):
        hw = self._hardware()
        val, err = hw.safe_read_str(self._P("20mA"), "range", timeout=None)
        self.assertIsNone(err)
        self.assertEqual(val, "20mA")

    def test_numeric_string_still_converts(self):
        """A device that reports a number as text must keep working."""
        hw = self._hardware()
        val, err = hw.safe_read(self._P("1.5"), "x", timeout=None)
        self.assertIsNone(err)
        self.assertAlmostEqual(val, 1.5)

    def test_arrays_and_scalars_unaffected(self):
        hw = self._hardware()
        self.assertAlmostEqual(
            hw.safe_read(self._P([3.5, 9.9]), "x", timeout=None)[0], 3.5)
        self.assertAlmostEqual(
            hw.safe_read(self._P(np.array([2.25])), "x", timeout=None)[0], 2.25)
        self.assertAlmostEqual(
            hw.safe_read(self._P(7.0), "x", timeout=None)[0], 7.0)

    def test_panels_read_the_range_as_a_string(self):
        """All three Keithley panel copies must use safe_read_str for range."""
        root = os.path.dirname(os.path.abspath(__file__))
        for rel in ("Samba_main/panels/hardware_panel.py",
                    "Cryo/keithley_mixin.py",
                    "Cryo/panels.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("rng, e5 = safe_read_str(", src,
                          f"{rel}: the range read must use safe_read_str")


class TestDefaultsPanelLoadGuard(unittest.TestCase):
    """Setup Defaults must not echo `defaults_changed` while load() runs.

    The main windows respond to that signal by merging get_values() into the
    setup dict and SAVING it.  Fired mid-load it captures a half-restored
    panel: every widget after the one that emitted still holds the previous
    setup's state or its construction default.  Observed damage was Cryo's
    `keithley_device` being overwritten with the first device-registry entry
    (a lock-in) and persisted to disk, because the Keithley combo is restored
    further down load() than the spinbox that fired.

    Checked at source level: the failure mode is someone wiring a NEW widget
    straight to `defaults_changed`, which no behavioural test of today's
    widgets would notice.  Qt is stubbed in this suite, so the panels
    themselves cannot be instantiated here.
    """

    PANELS = [
        ("Cryo/defaults_panel.py", "_emit_changed"),
        ("Samba_main/panels/setup_defaults.py", "_on_changed"),
    ]

    def _src(self, rel):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_load_sets_the_loading_guard(self):
        for rel, _funnel in self.PANELS:
            src = self._src(rel)
            self.assertIn("self._loading = True", src,
                          f"{rel}: load() must set the _loading guard")
            self.assertIn("self._loading = False", src,
                          f"{rel}: the guard must be cleared again")

    def test_funnel_consults_the_guard(self):
        for rel, funnel in self.PANELS:
            src = self._src(rel)
            i = src.index(f"def {funnel}(")
            body = src[i:i + 600]
            self.assertIn("if not self._loading:", body,
                          f"{rel}: {funnel}() must check _loading before emitting")

    def test_no_widget_is_wired_straight_to_defaults_changed(self):
        """Every emit must route through the funnel.

        A signal-to-signal connection (`w.changed.connect(self.defaults_changed)`)
        or a `lambda: self.defaults_changed.emit()` cannot consult the guard.
        """
        for rel, funnel in self.PANELS:
            src = self._src(rel)
            bad = [ln.strip() for n, ln in enumerate(src.splitlines(), 1)
                   if ".connect(self.defaults_changed)" in ln
                   or ("defaults_changed.emit()" in ln
                       and "lambda" in ln)]
            self.assertEqual(bad, [], f"{rel}: wire these through {funnel}(): {bad}")

    def test_only_the_funnel_emits(self):
        for rel, funnel in self.PANELS:
            src = self._src(rel)
            emits = [n for n, ln in enumerate(src.splitlines(), 1)
                     if "self.defaults_changed.emit()" in ln]
            self.assertEqual(
                len(emits), 1,
                f"{rel}: defaults_changed should be emitted only inside "
                f"{funnel}(), found {len(emits)} emit(s) at lines {emits}")

class TestAppVersion(unittest.TestCase):
    """Samba_main window title carries the vX.YZ application version."""

    def test_version_matches_scheme(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "samba_main_config_version",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "Samba_main", "config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertRegex(mod.APP_VERSION, r"^\d+\.\d{2}$")


class TestScanValidation(unittest.TestCase):
    """core/validation.py — shared pre-scan sanity checks (was Cryo-only)."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "samba_validation",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "core", "validation.py"))
        self.v = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.v)

    def _cfg(self, **kw):
        base = {"scan_type": "SPATIAL", "scan_x": True, "scan_y": False,
                "act1_npts": 51, "act2_npts": 1, "integration_time": 0.1,
                "act1_start": -10.0, "act1_stop": 10.0, "act1_unit": "nm"}
        base.update(kw); return base

    def test_sane_config_passes(self):
        self.assertIsNone(self.v.validate_scan_config(self._cfg()))

    def test_point_count_limit(self):
        err = self.v.validate_scan_config(self._cfg(act1_npts=99_999))
        self.assertIsNotNone(err); self.assertIn("safety limit", err)

    def test_2d_total_limit(self):
        err = self.v.validate_scan_config(
            self._cfg(scan_y=True, act1_npts=2000, act2_npts=2000))
        self.assertIsNotNone(err); self.assertIn("Total scan points", err)

    def test_zero_integration_time_rejected(self):
        err = self.v.validate_scan_config(self._cfg(integration_time=0.0))
        self.assertIsNotNone(err); self.assertIn("Integration time", err)

    def test_travel_limits_enforced_when_configured(self):
        setup = {"act1_min": -5.0, "act1_max": 5.0}
        err = self.v.validate_scan_config(self._cfg(), setup)
        self.assertIsNotNone(err)
        self.assertIn("travel limits", err)

    def test_travel_limits_absent_means_no_check(self):
        """A setup that never defined limits behaves exactly as before."""
        self.assertIsNone(self.v.validate_scan_config(self._cfg(), {}))

    def test_travel_limits_ignored_for_disabled_axis(self):
        setup = {"act2_min": -1.0, "act2_max": 1.0}
        cfg = self._cfg(scan_y=False, act2_start=-500.0, act2_stop=500.0)
        self.assertIsNone(self.v.validate_scan_config(cfg, setup))

    def test_field_scan_needs_two_points(self):
        err = self.v.validate_scan_config(
            {"scan_type": "FIELD", "field_segments": [[0, 1, 1]],
             "integration_time": 0.1})
        self.assertIsNotNone(err)


def _run_cfg(sensors=None, **over):
    """Minimal 1-D SPATIAL config usable with run()."""
    cfg = {
        "scan_type": "SPATIAL", "scan_x": True, "scan_y": False, "name": "t",
        "act1_start": 0.0, "act1_stop": 2.0, "act1_npts": 3,
        "act1_label": "X", "act1_unit": "nm",
        "act1_device": "dev://stage", "act1_attr": "x",
        "act2_device": "dev://stage", "act2_attr": "y",
        "act2_start": 0.0, "act2_stop": 1.0, "act2_npts": 1,
        "act2_label": "Y", "act2_unit": "nm",
        "integration_time": 0.0, "settle_time": 0.0, "move_timeout": 5.0,
        "sensors": sensors if sensors is not None else [{
            "enabled": True, "device": "dev://zi", "attribute": "x1",
            "label": "ZI x1", "trigger_cmd": "Start",
            "integ_time_attr": "", "settling_attr": "",
        }],
    }
    cfg.update(over)
    return cfg


class _RunnerPatch:
    """Patch runner's proxy factories + filename for an end-to-end run()."""

    def __init__(self, fresh=None, proxy=None):
        self._proxy = proxy or InstantProxy(read_val=1.0)
        self._fresh = fresh or (lambda path: (self._proxy, None))

    def __enter__(self):
        self._orig = (_runner_mod.fresh_proxy, _runner_mod.get_proxy,
                      _runner_mod._make_filename, _runner_mod.TANGO_AVAILABLE)
        _runner_mod.fresh_proxy    = self._fresh
        _runner_mod.get_proxy      = lambda path: self._proxy
        _runner_mod._make_filename = lambda cfg: "test.h5"
        return self

    def __exit__(self, *a):
        (_runner_mod.fresh_proxy, _runner_mod.get_proxy,
         _runner_mod._make_filename, _runner_mod.TANGO_AVAILABLE) = self._orig
        return False


class TestUnreachableSensorRefusesStart(unittest.TestCase):
    """A sensor device that cannot be reached must stop the scan.

    SimProxy answers any unknown attribute with a constant ~1.0 plus noise, so
    a dead lock-in yields a complete, plausible-looking file of fake data --
    worse than a dead stage, which the engine already refuses to run with."""

    def test_scan_refuses_when_sensor_unreachable(self):
        import tempfile
        statuses = []
        dead = lambda path: (InstantProxy(), "not exported")
        with tempfile.TemporaryDirectory() as td, _RunnerPatch(fresh=dead):
            _runner_mod.TANGO_AVAILABLE = True
            r = ScanRunner(_run_cfg(), {"save_dir": td})
            out = r.run({"status": statuses.append})
        self.assertIsNone(out, "scan must not start with an unreachable sensor")
        self.assertTrue(any("unreachable" in s for s in statuses), statuses)

    def test_sim_mode_still_runs(self):
        """Without pytango the UI must still work on a dev box."""
        import tempfile
        proxy = InstantProxy(read_val=1.0)
        sim = lambda path: (proxy, "pytango not installed")
        with tempfile.TemporaryDirectory() as td, _RunnerPatch(fresh=sim,
                                                               proxy=proxy):
            _runner_mod.TANGO_AVAILABLE = False
            out = ScanRunner(_run_cfg(), {"save_dir": td}).run({})
        self.assertIsNotNone(out)


class TestProvenanceMetadata(unittest.TestCase):
    """Every scan file records which software produced it.

    Without it a file cannot be matched to the code that wrote it, and this
    acquisition software changes weekly."""

    def test_provenance_keys_written(self):
        import h5py, tempfile
        _runner_mod._PROVENANCE = None          # force recompute
        p = os.path.join(tempfile.mkdtemp(), "prov.h5")
        with h5py.File(p, "w") as f:
            _runner_mod._write_hw_metadata(f.create_group("metadata"), {})
        with h5py.File(p, "r") as f:
            a = dict(f["metadata"].attrs)
        self.assertIn("hostname", a)
        self.assertIn("samba_git_commit", a)
        self.assertRegex(a["samba_git_commit"], r"^[0-9a-f]{7,}(-dirty)?$")

    def test_provenance_is_cached(self):
        _runner_mod._PROVENANCE = None
        first = _runner_mod._provenance()
        self.assertIs(first, _runner_mod._provenance())


class TestDemagStartCurrent(unittest.TestCase):
    """Demagnetization starts at the peak current the scan actually applied.

    Starting the alternating decay below the field the sample just saw does
    not demagnetize it -- it leaves remanence that biases the next scan."""

    def _run_field(self, setup_extra=None, segments=None):
        import tempfile
        cfg = _run_cfg(scan_type="FIELD",
                       field_segments=segments or [[-7.5, 7.5, 4]],
                       field_x_label="Field", field_x_unit="mT",
                       field_setpoint_unit="A")
        setup = {"magnet_device": "dev://magnet",
                 "magnet_current_attr": "current_polar",
                 "magnet_field_attr": "field_polar_corr",
                 "demagnetize_after_scan": True}
        setup.update(setup_extra or {})
        _orig_demag = _runner_mod.demagnetize_magnet
        calls = []
        _runner_mod.demagnetize_magnet = (
            lambda p, attr, **kw: calls.append(kw))
        try:
            with tempfile.TemporaryDirectory() as td, _RunnerPatch():
                setup["save_dir"] = td
                ScanRunner(cfg, setup).run({})
        finally:
            _runner_mod.demagnetize_magnet = _orig_demag
        return calls

    def test_start_current_follows_scan_peak(self):
        calls = self._run_field()
        self.assertTrue(calls, "demagnetize_magnet was not called")
        self.assertAlmostEqual(calls[0].get("start_A"), 7.5)

    def test_setup_override_wins(self):
        calls = self._run_field({"demag_start_A": 9.0},
                                segments=[[-2.0, 2.0, 3]])
        self.assertTrue(calls)
        self.assertAlmostEqual(calls[0].get("start_A"), 9.0)


class TestAutoPauseNamesDevice(unittest.TestCase):
    """The auto-pause message names the failing device.

    "fix the issue and press Resume" is not actionable without knowing which
    device to open in Jive."""

    def test_failed_device_is_named(self):
        r = _make_runner()
        r._last_bad_devs = ["hpp-N42/measure/ZI2"]
        msgs = []

        def _st(m):
            msgs.append(m)
            if "AUTO-PAUSED" in m:
                r._abort = True          # release the pause-wait loop

        r._do_acquire = lambda *a, **kw: ({}, 0.0, False)
        r._acquire_point_retry({}, {}, {}, 0.01, time.time(), _RUNNING_SET,
                               {"move_timeout": 1.0}, 0, 0.0, False,
                               "X", 0.0, _noop, _st)
        paused = [m for m in msgs if "AUTO-PAUSED" in m]
        self.assertTrue(paused, msgs)
        self.assertIn("hpp-N42/measure/ZI2", paused[0])


class TestRecentWindowSetting(unittest.TestCase):
    """The Recent y-scale window is a per-setup value, not a constant.

    It lives in Setup Defaults rather than the plot toolbars — a per-setup
    preference, not something adjusted mid-measurement."""

    def _cfgmod(self, app):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            f"recent_window_cfg_{app}",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         app, "config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_default_is_ten_in_every_setup(self):
        for app in ("Samba_main", "Cryo"):
            mod = self._cfgmod(app)
            for name, setup in mod.SETUP_HW_DEFAULTS.items():
                self.assertEqual(setup.get("recent_window"), 10,
                                 f"{app}/{name} missing or wrong recent_window")

    def test_constant_lowered_to_ten(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "recent_window_pi",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "core", "plot_interact.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.RECENT_WINDOW, 10)

    def test_window_argument_is_honoured(self):
        """A smaller window must ignore an earlier large transient."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "recent_window_pi2",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "core", "plot_interact.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        y = [1000.0] + [0.01] * 20
        lo10, hi10 = mod.recent_symmetric_ylim([y], window=10)
        lo50, hi50 = mod.recent_symmetric_ylim([y], window=50)
        self.assertLess(hi10, 1.0)
        self.assertGreater(hi50, 100.0)


# ─────────────────────────────────────────────────────────────────────────────
# 22. Current sweep — current list, range selection, plateau detection, config
# ─────────────────────────────────────────────────────────────────────────────

import current_sweep as _cs_mod                          # noqa: E402


class TestCurrentSweepList(unittest.TestCase):
    """The list of excitation currents the sweep steps through."""

    def test_linear_inclusive(self):
        self.assertEqual(_cs_mod.build_current_list(5, 15, 3), [5.0, 10.0, 15.0])

    def test_descending_when_start_above_stop(self):
        # "from / to" is taken literally — high→low is a legitimate order.
        self.assertEqual(_cs_mod.build_current_list(15, 5, 3), [15.0, 10.0, 5.0])

    def test_single_point(self):
        self.assertEqual(_cs_mod.build_current_list(7.25, 99, 1), [7.25])

    def test_rounded_to_four_decimals(self):
        # The {I}mA filename token must be stable, so no 8.333333333333.
        vals = _cs_mod.build_current_list(5, 15, 4)
        self.assertEqual(vals, [5.0, 8.3333, 11.6667, 15.0])

    def test_format_elides_long_lists(self):
        s = _cs_mod.format_current_list(list(range(1, 20)))
        self.assertIn("…", s)
        self.assertTrue(s.endswith("mA"))


class TestKeithleyRangePick(unittest.TestCase):
    """The source clips to its range, so a sweep crossing 2 → 20 mA has to
    move the range with it."""

    RANGES = ["2mA", "20mA", "100mA"]

    def test_smallest_fitting_range(self):
        self.assertEqual(_cs_mod.pick_keithley_range(1.5, self.RANGES), "2mA")
        self.assertEqual(_cs_mod.pick_keithley_range(5, self.RANGES), "20mA")
        self.assertEqual(_cs_mod.pick_keithley_range(20, self.RANGES), "20mA")
        self.assertEqual(_cs_mod.pick_keithley_range(21, self.RANGES), "100mA")

    def test_negative_uses_magnitude(self):
        self.assertEqual(_cs_mod.pick_keithley_range(-5, self.RANGES), "20mA")

    def test_none_when_nothing_fits(self):
        self.assertIsNone(_cs_mod.pick_keithley_range(150, self.RANGES))

    def test_parse_variants(self):
        self.assertEqual(_cs_mod.parse_range_mA("20mA"), 20.0)
        self.assertEqual(_cs_mod.parse_range_mA("100 mA"), 100.0)
        self.assertEqual(_cs_mod.parse_range_mA("1A"), 1000.0)
        self.assertIsNone(_cs_mod.parse_range_mA("nonsense"))


class TestSweepValidation(unittest.TestCase):
    """A sweep that cannot run must be refused before the setup lock is taken."""

    RANGES = ["2mA", "20mA", "100mA"]

    def test_ok(self):
        self.assertIsNone(_cs_mod.validate_sweep([5, 10, 15], self.RANGES))

    def test_empty_refused(self):
        self.assertIsNotNone(_cs_mod.validate_sweep([], self.RANGES))

    def test_over_hardware_range_refused(self):
        err = _cs_mod.validate_sweep([5, 150], self.RANGES)
        self.assertIsNotNone(err)
        self.assertIn("150", err)

    def test_fixed_range_refuses_currents_above_it(self):
        err = _cs_mod.validate_sweep([1, 5], self.RANGES,
                                     auto_range=False, fixed_range="2mA")
        self.assertIsNotNone(err)
        self.assertIn("2mA", err)

    def test_too_many_currents_refused(self):
        err = _cs_mod.validate_sweep(list(range(1, 200)), self.RANGES)
        self.assertIsNotNone(err)
        self.assertIn(str(_cs_mod.MAX_CURRENTS), err)


class TestPlateauDetector(unittest.TestCase):
    """Deciding the sample has thermalised from the focus-diode trace."""

    @staticmethod
    def _exp_decay(det, t_end, tau=60.0, step=5.0):
        """Feed a settling exponential; return when it was first called settled."""
        import math
        t = 0.0
        while t <= t_end:
            det.add(t, 10.0 - 2.0 * (1 - math.exp(-t / tau)))
            st = det.state(t)
            if st["settled"]:
                return t, st
            t += step
        return None, det.state(t_end)

    def test_minimum_wait_blocks_an_early_plateau(self):
        """The bound is not cosmetic: right after a refocus the FL signal sits
        on its maximum, where dFL/dz ~ 0, so a flat start is expected."""
        det = _cs_mod.PlateauDetector(window_s=60, tol_pct_per_min=0.5,
                                      min_wait_s=300, max_wait_s=1200)
        for i in range(40):
            det.add(i * 5.0, 10.0)          # perfectly flat
        st = det.state(195.0)
        self.assertFalse(st["settled"])
        self.assertEqual(st["reason"], "minimum wait")

    def test_plateau_detected_after_settling(self):
        det = _cs_mod.PlateauDetector(window_s=60, tol_pct_per_min=0.5,
                                      min_wait_s=100, max_wait_s=1200)
        t, st = self._exp_decay(det, 900.0)
        self.assertIsNotNone(t)
        self.assertEqual(st["reason"], "plateau")
        self.assertGreaterEqual(t, 100.0)

    def test_gives_up_at_max_wait(self):
        det = _cs_mod.PlateauDetector(window_s=60, tol_pct_per_min=0.5,
                                      min_wait_s=100, max_wait_s=300)
        t = 0.0
        while t <= 400:
            det.add(t, 10.0 - 0.01 * t)     # never stops drifting
            st = det.state(t)
            if st["settled"]:
                break
            t += 5.0
        self.assertTrue(st["settled"])
        self.assertEqual(st["reason"], "timeout")
        self.assertGreaterEqual(t, 300.0)

    def test_non_finite_samples_dropped(self):
        det = _cs_mod.PlateauDetector()
        self.assertFalse(det.add(0.0, float("nan")))
        self.assertFalse(det.add(0.0, None))
        self.assertTrue(det.add(0.0, 1.0))

    def test_no_data_is_not_settled(self):
        st = _cs_mod.PlateauDetector().state(0.0)
        self.assertFalse(st["settled"])
        self.assertIsNone(st["rate"])

    def test_rate_is_percent_of_signal_per_minute(self):
        det = _cs_mod.PlateauDetector(window_s=60)
        for i in range(13):                 # 60 s at 5 s spacing
            det.add(i * 5.0, 100.0 - i * 5.0 * 0.1)   # −0.1 units/s of ~100
        rate = det.rate_pct_per_min(60.0)
        self.assertLess(rate, 0.0)
        self.assertAlmostEqual(abs(rate), 6.0, delta=1.5)


class TestCurrentSweepConfig(unittest.TestCase):
    """Config defaults and the schema migration that adds them."""

    def _load(self, app_dir, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               app_dir, "config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_disabled_by_default_in_both_apps(self):
        for app_dir, name in (("Samba_main", "sm_cfg"), ("Cryo", "cryo_cfg")):
            cfg = self._load(app_dir, name).make_default_config()
            self.assertFalse(cfg["cursweep_enabled"], app_dir)
            self.assertEqual(cfg["cursweep_settle_mode"], _cs_mod.SETTLE_FIXED)

    def test_v8_to_v9_backfills(self):
        mod = self._load("Samba_main", "sm_cfg_mig")
        old = {"_schema_version": 8, "name": "x"}
        mod._migrate_config(old)
        self.assertEqual(old["_schema_version"], mod.SCHEMA_VERSION)
        self.assertFalse(old["cursweep_enabled"])
        self.assertEqual(old["cursweep_fixed_min"], 10.0)

    def test_migration_preserves_an_existing_choice(self):
        mod = self._load("Samba_main", "sm_cfg_keep")
        cfg = {"_schema_version": 8, "cursweep_enabled": True,
               "cursweep_npts": 7}
        mod._migrate_config(cfg)
        self.assertTrue(cfg["cursweep_enabled"])
        self.assertEqual(cfg["cursweep_npts"], 7)

    def test_cryo_migration_backfills(self):
        mod = self._load("Cryo", "cryo_cfg_mig")
        cfg = {"name": "x"}
        mod._migrate_config(cfg)
        self.assertFalse(cfg["cursweep_enabled"])
        self.assertEqual(cfg["cursweep_auto_range"], True)


class TestRefocusConfig(unittest.TestCase):
    """The Refocus box is the single switch for every automatic autofocus."""

    def _load(self, app_dir, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               app_dir, "config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_off_by_default_in_both_apps(self):
        for app_dir, name in (("Samba_main", "sm_rf"), ("Cryo", "cryo_rf")):
            cfg = self._load(app_dir, name).make_default_config()
            self.assertFalse(cfg["refocus_enabled"], app_dir)
            self.assertEqual(cfg["refocus_every_min"], 30.0, app_dir)
            self.assertEqual(cfg["refocus_x"], 0.0, app_dir)
            self.assertEqual(cfg["refocus_y"], 0.0, app_dir)

    def test_sweep_no_longer_carries_its_own_refocus_flag(self):
        """One switch: the sweep's checkbox was removed, so the key must not
        come back in the defaults and be mistaken for the live setting."""
        self.assertNotIn("cursweep_refocus", _cs_mod.CURRENT_SWEEP_DEFAULTS)
        for app_dir, name in (("Samba_main", "sm_rf2"), ("Cryo", "cryo_rf2")):
            cfg = self._load(app_dir, name).make_default_config()
            self.assertNotIn("cursweep_refocus", cfg, app_dir)

    def test_migration_backfills_off(self):
        mod = self._load("Samba_main", "sm_rf_mig")
        old = {"_schema_version": 9, "name": "x"}
        mod._migrate_config(old)
        self.assertFalse(old["refocus_enabled"])
        self.assertEqual(old["refocus_every_min"], 30.0)

    def test_migration_preserves_an_existing_choice(self):
        mod = self._load("Samba_main", "sm_rf_keep")
        cfg = {"_schema_version": 9, "refocus_enabled": True,
               "refocus_every_min": 5.0, "refocus_x": 1234.0}
        mod._migrate_config(cfg)
        self.assertTrue(cfg["refocus_enabled"])
        self.assertEqual(cfg["refocus_every_min"], 5.0)
        self.assertEqual(cfg["refocus_x"], 1234.0)

    def test_cryo_migration_backfills(self):
        mod = self._load("Cryo", "cryo_rf_mig")
        cfg = {"name": "x"}
        mod._migrate_config(cfg)
        self.assertFalse(cfg["refocus_enabled"])
        self.assertEqual(cfg["refocus_every_min"], 30.0)


class TestRefocusDue(unittest.TestCase):
    """When a periodic refocus is owed at a scan boundary."""

    def test_first_boundary_is_due(self):
        # No autofocus yet this run — the very first check is due.
        self.assertTrue(_cs_mod.refocus_due(None, 1000.0, 30.0))

    def test_not_due_before_the_interval(self):
        self.assertFalse(_cs_mod.refocus_due(1000.0, 1000.0 + 29 * 60, 30.0))

    def test_due_once_the_interval_has_passed(self):
        self.assertTrue(_cs_mod.refocus_due(1000.0, 1000.0 + 30 * 60, 30.0))
        self.assertTrue(_cs_mod.refocus_due(1000.0, 1000.0 + 31 * 60, 30.0))

    def test_zero_interval_disables_the_periodic_refocus(self):
        # 0 = only at the start and on current changes, never at boundaries.
        self.assertFalse(_cs_mod.refocus_due(None, 1e9, 0.0))
        self.assertFalse(_cs_mod.refocus_due(1000.0, 1e9, 0.0))

    def test_garbage_interval_is_not_due(self):
        self.assertFalse(_cs_mod.refocus_due(None, 1000.0, None))
        self.assertFalse(_cs_mod.refocus_due(None, 1000.0, "soon"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
