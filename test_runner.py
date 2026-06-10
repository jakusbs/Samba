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

    def test_persistent_trigger_failure_removes_device(self):
        """
        After AUTO_PAUSE_THRESHOLD consecutive failures (across calls) the
        device is permanently removed from trigger_devs.
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
            _acquire(r, devp, dev_sensors, trigger_devs, cfg)

        self.assertNotIn(dev, trigger_devs,
                         "Persistently failing device must be removed")

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
# 9. Setup-lock stale-stamp parsing
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
