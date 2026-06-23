"""
easter_egg.py — a small hidden treat.

Watches for the Konami code (↑ ↑ ↓ ↓ ← → ← →) anywhere in the app and
reveals SAMBA's *unofficial* backronym.  The filter is installed on the
QApplication and never consumes key events, so it cannot interfere with
spin boxes, text fields or any normal typing.

Debugging: launch with  SAMBA_EGG_DEBUG=1  to print, to stderr, that the
watcher armed and every key it sees — so you can tell whether the keys are
reaching it at all.
"""

import os
import sys

from PyQt6.QtCore import QObject, QEvent, Qt
from PyQt6.QtWidgets import QMessageBox, QApplication

_DEBUG = bool(os.environ.get("SAMBA_EGG_DEBUG"))


def _dbg(msg):
    if _DEBUG:
        sys.stderr.write(f"[samba-egg] {msg}\n")
        sys.stderr.flush()


_KONAMI = [
    Qt.Key.Key_Up.value,    Qt.Key.Key_Up.value,
    Qt.Key.Key_Down.value,  Qt.Key.Key_Down.value,
    Qt.Key.Key_Left.value,  Qt.Key.Key_Right.value,
    Qt.Key.Key_Left.value,  Qt.Key.Key_Right.value,
]

_OFFICIAL = "Strnad & Goldenberger Application for Magnetism Based Analysis"
_UNOFFICIAL = "Somewhat Adequate, Mostly Buggy Application"


class _KonamiFilter(QObject):
    """Application-wide key watcher.  Observes only — always returns False."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._idx = 0
        self._last_sig = None   # (key, timestamp) of the last counted press

    def eventFilter(self, obj, ev):
        # KeyPress arrives as a QKeyEvent; compare plain integer key codes
        # (ev.key() is an int; _KONAMI holds the enum .value ints).
        if ev is not None and ev.type() == QEvent.Type.KeyPress:
            try:
                if ev.isAutoRepeat():
                    return False        # ignore key held down
                key = int(ev.key())
                ts = int(ev.timestamp())
            except Exception:
                return False
            # A single physical press is delivered to this application-wide
            # filter several times as the event propagates up the widget
            # tree.  Those copies share the event timestamp — count only the
            # first, otherwise one press both resets and re-advances the
            # sequence (net zero) and it can never complete.
            sig = (key, ts)
            if sig == self._last_sig:
                return False
            self._last_sig = sig

            _dbg(f"key={key} ts={ts} want={_KONAMI[self._idx]} idx={self._idx}")
            if key == _KONAMI[self._idx]:
                self._idx += 1
                if self._idx >= len(_KONAMI):
                    self._idx = 0
                    _dbg("sequence complete → reveal")
                    self._reveal()
            else:
                self._idx = 1 if key == _KONAMI[0] else 0
        return False   # never consume the event

    def _reveal(self):
        print("\n🎉  SAMBA Konami code! — Somewhat Adequate, Mostly Buggy "
              "Application 🐞\n", flush=True)
        try:
            # Plain text only — the Qt dialog font has no colour-emoji glyphs
            # on most Linux setups, so emoji would render as empty boxes.
            box = QMessageBox(self._window)
            box.setWindowTitle("SAMBA")
            box.setText("Konami code unlocked!")
            box.setInformativeText(
                f"SAMBA also unofficially stands for:\n\n"
                f"    {_UNOFFICIAL}\n\n"
                f"...but you didn't hear that from us.\n"
                f"(Officially: {_OFFICIAL}.)"
            )
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self._window._konami_box = box   # keep a reference alive
            box.show()            # modeless — never blocks a measurement
            box.raise_()
            box.activateWindow()
        except Exception as e:
            _dbg(f"reveal dialog failed: {e}")


def install_easter_egg(window):
    """Install the Konami-code watcher for *window*.  Best-effort: any failure
    is swallowed so the easter egg can never affect normal operation."""
    try:
        app = QApplication.instance()
        if app is None:
            _dbg("no QApplication instance — not installed")
            return None
        filt = _KonamiFilter(window)
        app.installEventFilter(filt)
        window._konami_filter = filt   # keep a reference alive
        _dbg("armed (application event filter installed)")
        return filt
    except Exception as e:
        _dbg(f"install failed: {e}")
        return None
