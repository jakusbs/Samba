"""ScanlistWorker's relay handling against a REAL in-process relay device.

The unit tests in test_runner.py fake `safe_read`/`safe_write`; here the
worker talks to a genuine `DeviceProxy` of `tests/fakes.FakeRelay`, which —
like PyRelais — actuates on every write.  Skipped without pytango.
"""
import os
import sys
import types
import importlib

import pytest

tango = pytest.importorskip("tango")
from tango.test_context import DeviceTestContext                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeRelay                                     # noqa: E402

try:
    import PyQt6  # noqa: F401
except ImportError:                                             # pragma: no cover
    from unittest.mock import MagicMock
    _qt = MagicMock()
    _qt.QtCore.QThread = object
    sys.modules["PyQt6"] = _qt
    sys.modules["PyQt6.QtCore"] = _qt.QtCore
_cfg = types.ModuleType("config")
_cfg.MAX_RETRIES, _cfg.RETRY_DELAY, _cfg.X_TIME = 2, 0.005, "_time_"
sys.modules.setdefault("config", _cfg)
for _p in (os.path.join(ROOT, "core", "scan"), os.path.join(ROOT, "core"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class _FakeSignal:
    def emit(self, *a, **kw):    pass
    def connect(self, *a, **kw): pass


def _import_workers():
    """Import core/scan/workers.py with its Qt signals replaced by no-ops.

    QThread itself is fine to instantiate without a QApplication; the class
    attributes just have to be objects with emit()/connect().
    """
    import PyQt6.QtCore as qc
    old = qc.pyqtSignal
    qc.pyqtSignal = lambda *a, **kw: _FakeSignal()
    try:
        sys.modules.pop("workers", None)
        return importlib.import_module("workers")
    finally:
        qc.pyqtSignal = old


class _Relay:
    """The relay fake's proxy plus its FULL nodb access name.

    pytango 10 freezes the DeviceProxy interface, so the name cannot be
    tucked onto the proxy as a Python attribute; and the worker connects by
    name, so it needs the `tango://host:port/...#dbase=no` form, which
    resolves from any thread (the short name only does in this process).
    """
    def __init__(self, ctx, proxy):
        self.proxy, self.access = proxy, ctx.get_device_access()

    def __getattr__(self, item):
        return getattr(self.proxy, item)

    def __setattr__(self, key, value):
        if key in ("proxy", "access"):
            object.__setattr__(self, key, value)
        else:
            setattr(self.proxy, key, value)          # writes the TANGO attribute


@pytest.fixture(scope="module")
def relay():
    try:
        ctx = DeviceTestContext(FakeRelay, process=True, timeout=30)
        p = ctx.__enter__()
    except Exception as e:                                      # pragma: no cover
        pytest.skip(f"cannot start an in-process device server here: {e}")
    # Everything after __enter__ sits inside the try: a failure here must
    # still stop the server child, or the interpreter hangs at exit joining
    # it (multiprocessing's atexit).
    try:
        yield _Relay(ctx, p)
    finally:
        ctx.__exit__(None, None, None)


def _run_list(relay, n, order, relay_flip=True, tmp_path=None):
    W = _import_workers()
    states = []

    class _Runner:
        def __init__(self, cfg, setup): states.append((int(relay.switchvar), cfg.get("hw_relay_state")))
        def run(self, cbs):             return "/data/scan.h5"
        def abort(self):                pass
        def pause(self):                pass
        def resume(self):               pass
        def is_paused(self):            return False

    setup = {"relay_device": relay.access, "relay_attr": "switchvar",
             "magnet_device": "", "attodry_device": "",
             "save_dir": os.path.join(str(tmp_path), "data"),
             "demagnetize_after_scan": False}
    w = W.ScanlistWorker({"name": "s_trace"}, setup, n, "list",
                         relay_flip, False, flip_order=order)
    w._RELAY_RETRY_S = 0.0
    old_runner, W.ScanRunner = W.ScanRunner, _Runner
    try:
        w._run_list()
    finally:
        W.ScanRunner = old_runner
    txt = os.path.join(setup["save_dir"], "ScanLists", "list.txt")
    return states, txt


def test_relay_switched_only_on_state_change(relay, tmp_path):
    relay.switchvar = 0
    n0 = int(relay.actuations)
    states, txt = _run_list(relay, 8, "ABBA", tmp_path=tmp_path)
    assert [s for s, _ in states] == [0, 1, 1, 0, 0, 1, 1, 0]
    assert int(relay.actuations) - n0 == 4, "ABBA over 8 cycles = 4 switches"
    # metadata and .txt both carry the state the relay was actually in
    assert [m for _, m in states] == [0, 1, 1, 0, 0, 1, 1, 0]
    signs = [int(line.split("\t")[1]) for line in open(txt) if not line.startswith("#")]
    assert signs == [+1, -1, -1, +1, +1, -1, -1, +1]


def test_schedule_starts_from_the_device_state(relay, tmp_path):
    relay.switchvar = 1
    n0 = int(relay.actuations)
    states, _ = _run_list(relay, 4, "AB", tmp_path=tmp_path)
    assert [s for s, _ in states] == [1, 0, 1, 0]
    assert int(relay.actuations) - n0 == 3


def test_flip_disabled_never_touches_the_relay(relay, tmp_path):
    relay.switchvar = 1
    n0 = int(relay.actuations)
    states, _ = _run_list(relay, 4, "ABBA", relay_flip=False, tmp_path=tmp_path)
    assert [s for s, _ in states] == [1, 1, 1, 1]
    assert int(relay.actuations) == n0
