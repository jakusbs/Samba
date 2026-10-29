"""
bgcall.py — Samba (shared core)

Run blocking hardware calls off the GUI thread and deliver their results back
on it.

The hardware panels used to run fresh_proxy() + safe_write() straight from
the click handler: a device that did not answer froze the whole window for
the I/O timeout (10 s) plus a database round trip, once per click.  Moving
the writes to a thread must not change what reaches the instrument, though:
the synchronous code executed every request, in the order the operator made
it (Qt queued the clicks while the window was blocked).  BackgroundCall keeps
exactly that — calls that share a *key* (one device) run one at a time, in
request order, and nothing is dropped.  An earlier version dropped a request
whenever another write to the same device was still in flight, so a
frequency change made during a slow amplitude write, a temperature setpoint
sent during a field write, or a Zero-field click during a current write was
silently lost while the panel showed the new value (§89).
"""
import logging
import threading
from collections import deque

from PyQt6.QtCore import QObject, pyqtSignal

from hardware import fresh_proxy, is_sim_proxy, safe_write

log = logging.getLogger(__name__)


class BackgroundCall(QObject):
    """Run fn() on a daemon thread; call on_done(result) on the GUI thread.

    The result is whatever fn returned, or the exception it raised.  Calls
    with the same *key* run one after another in the order requested: a
    call made while another is in flight is queued, never dropped, and
    starts as soon as the previous one has delivered its result.
    on_start(), when given, runs on the GUI thread just before fn starts.

    run() must be called from the GUI thread (the queue is only touched
    there; the worker threads just emit the result signal).
    """
    _done = pyqtSignal(object, object, object)   # key, on_done, result

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy: set = set()
        self._queue: dict = {}                     # key -> deque of calls
        self._done.connect(self._dispatch)

    def busy(self, key) -> bool:
        """True while a call for *key* is running (or delivering its result)."""
        return key in self._busy

    def pending(self, key) -> int:
        """Number of calls for *key* waiting behind the running one."""
        return len(self._queue.get(key, ()))

    def run(self, key, fn, on_done, on_start=None) -> bool:
        """Run fn for *key*; True if it started now, False if it was queued."""
        if key in self._busy:
            self._queue.setdefault(key, deque()).append((fn, on_done, on_start))
            return False
        self._start(key, fn, on_done, on_start)
        return True

    def _start(self, key, fn, on_done, on_start):
        self._busy.add(key)
        if on_start is not None:
            try:
                on_start()
            except Exception:
                log.exception("background call '%s': start handler failed", key)

        def _work():
            try:
                res = fn()
            except Exception as e:                  # delivered as the result
                res = e
            self._done.emit(key, on_done, res)

        threading.Thread(target=_work, daemon=True, name=f"bg:{key}").start()

    def _dispatch(self, key, on_done, res):
        try:
            on_done(res)
        except Exception:
            log.exception("background call '%s': result handler failed", key)
        # The finished call's result is handled before the next one starts,
        # so a queued call sees any state the previous one changed.
        q = self._queue.get(key)
        if q:
            fn, nd, ns = q.popleft()
            if not q:
                del self._queue[key]
            self._start(key, fn, nd, ns)            # key stays busy
        else:
            self._busy.discard(key)


class BackgroundWriteMixin:
    """Status-label plumbing for a hardware panel whose writes run through
    BackgroundCall.  Shared by Samba_main's HardwarePanel and Cryo's
    KeithleyMixin, which used to carry two copies of it.

    The host class provides the label setters ``_set_ok(lbl, msg)``,
    ``_set_err(lbl, msg)`` and ``_set_sim(lbl)``.

    Writes to one device share a key and run in order (see BackgroundCall).
    The status label shows the write in progress and how many wait behind
    it.  A failure stays visible until the queue for that device has
    drained: the next queued write would otherwise overwrite "⚠ …" with
    "… next" a moment later and the operator would never see it.
    """

    @property
    def _bg(self) -> BackgroundCall:
        bg = getattr(self, "_bg_calls", None)
        if bg is None:
            bg = self._bg_calls = BackgroundCall(self)
        return bg

    def _bg_state(self):
        st = getattr(self, "_bg_status", None)
        if st is None:
            st = self._bg_status = {"running": {}, "error": {}}
        return st

    @staticmethod
    def _set_busy(lbl, msg: str):
        lbl.setText(f"… {msg}"); lbl.setStyleSheet("color:#a6adc8;font-size:10px;")

    def _show_progress(self, key, lbl):
        st = self._bg_state()
        text = st["running"].get(key, "")
        n = self._bg.pending(key)
        if n:
            text += f"  (+{n} queued)"
        err = st["error"].get(key)
        if err:
            self._set_err(lbl, f"{err}  ·  … {text}")
        else:
            self._set_busy(lbl, text)

    def _finish_write(self, key, lbl, res, on_ok=None, on_sim=None):
        """GUI-thread half of a background write: res is (kind, msg)."""
        st = self._bg_state()
        st["running"].pop(key, None)
        if isinstance(res, Exception):
            kind, msg = "err", str(res)
        else:
            kind, msg = res
        if kind not in ("ok", "sim"):
            msg = str(msg)[:60]
            st["error"][key] = msg
            self._set_err(lbl, msg)
            return
        earlier = st["error"].get(key)
        if kind == "ok":
            if on_ok: on_ok()
            if earlier:
                self._set_err(lbl, f"{earlier}  ·  ✓ {msg}")
            else:
                self._set_ok(lbl, msg)
        else:
            if on_sim: on_sim()
            if earlier:
                self._set_err(lbl, f"{earlier}  ·  simulation")
            else:
                self._set_sim(lbl)

    def _bg_submit(self, key, lbl, work, msg, on_ok=None, on_sim=None,
                   on_done=None):
        """Queue *work* for device *key*; report on *lbl*.

        *msg* describes the write ("amp → 5 mA"); it may be a callable,
        evaluated on the GUI thread when the write actually starts — for a
        write whose value depends on what the previous one changed (the
        relay toggle).  *on_done(result)* runs after the label is updated,
        whatever the outcome.
        """
        st = self._bg_state()
        if not self._bg.busy(key):
            st["error"].pop(key, None)       # a new burst starts clean

        def _start():
            st["running"][key] = msg() if callable(msg) else msg
            self._show_progress(key, lbl)

        def _done(res):
            try:
                self._finish_write(key, lbl, res, on_ok, on_sim)
            finally:
                if on_done is not None:
                    on_done(res)

        if not self._bg.run(key, work, _done, on_start=_start):
            self._show_progress(key, lbl)    # "… running  (+N queued)"

    def _bg_write(self, key, lbl, dev, writes, ok_msg, on_ok=None, on_sim=None):
        """Connect to *dev* and perform the (attr, value) *writes* in order on
        a background thread, after any write to the same *key* still in
        flight; report on *lbl* from the GUI thread."""
        if not dev:
            self._set_err(lbl, "no device configured"); return

        def _work():
            p, conn_err = fresh_proxy(dev)
            if conn_err:
                return ("err", conn_err)
            if is_sim_proxy(p):
                return ("sim", "")
            for attr, val in writes:
                err = safe_write(p, attr, val)
                if err:
                    return ("err", err)
            return ("ok", ok_msg)

        self._bg_submit(key, lbl, _work, ok_msg, on_ok=on_ok, on_sim=on_sim)
