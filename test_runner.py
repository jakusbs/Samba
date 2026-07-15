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
                       "hyst_cycles": 1, "hyst_field_V": 1.0, "hyst_int_time": 0.01,
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
                       "hyst_cycles": 1, "hyst_field_V": 1.0, "hyst_int_time": 0.01,
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
                       "hyst_cycles": 1, "hyst_field_V": 1.0,
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
