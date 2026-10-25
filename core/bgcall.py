"""
bgcall.py — Samba (shared core)

Run a blocking call off the GUI thread and deliver its result back on it.

The hardware panels used to run fresh_proxy() + safe_write() straight from
the click handler: a device that did not answer froze the whole window for
the I/O timeout (10 s) plus a database round trip, once per click.  Reads
had already been moved to daemon threads with per-panel signals; this is
the same pattern, generic, so every write can use it in two lines.
"""
import logging
import threading

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


class BackgroundCall(QObject):
    """Run fn() on a daemon thread; call on_done(result) on the GUI thread.

    The result is whatever fn returned, or the exception it raised.  Only one
    call per *key* is in flight at a time: a second request while the first
    is pending is dropped and run() returns False, so a double Enter cannot
    queue two writes of the same setting.
    """
    _done = pyqtSignal(object, object, object)   # key, on_done, result

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy: set = set()
        self._done.connect(self._dispatch)

    def busy(self, key) -> bool:
        return key in self._busy

    def run(self, key, fn, on_done) -> bool:
        if key in self._busy:
            return False
        self._busy.add(key)

        def _work():
            try:
                res = fn()
            except Exception as e:                  # delivered as the result
                res = e
            self._done.emit(key, on_done, res)

        threading.Thread(target=_work, daemon=True, name=f"bg:{key}").start()
        return True

    def _dispatch(self, key, on_done, res):
        self._busy.discard(key)
        try:
            on_done(res)
        except Exception:
            log.exception("background call '%s': result handler failed", key)
