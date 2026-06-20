"""
easter_egg.py — a small hidden treat.

Watches for the Konami code (↑ ↑ ↓ ↓ ← → ← → B A) anywhere in the app and
reveals SAMBA's *unofficial* backronym.  The filter is installed on the
QApplication and never consumes key events, so it cannot interfere with
spin boxes, text fields or any normal typing.
"""

from PyQt6.QtCore import QObject, QEvent, Qt
from PyQt6.QtWidgets import QMessageBox, QApplication

_KONAMI = [
    Qt.Key.Key_Up,   Qt.Key.Key_Up,
    Qt.Key.Key_Down, Qt.Key.Key_Down,
    Qt.Key.Key_Left, Qt.Key.Key_Right,
    Qt.Key.Key_Left, Qt.Key.Key_Right,
    Qt.Key.Key_B,    Qt.Key.Key_A,
]

_OFFICIAL = "Strnad & Goldenberger Application for Magnetism Based Analysis"
_UNOFFICIAL = "Somewhat Adequate, Mostly Buggy Application"


class _KonamiFilter(QObject):
    """Application-wide key watcher.  Observes only — always returns False."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._idx = 0

    def eventFilter(self, obj, ev):
        try:
            if ev.type() == QEvent.Type.KeyPress:
                key = ev.key()
                if key == _KONAMI[self._idx]:
                    self._idx += 1
                    if self._idx == len(_KONAMI):
                        self._idx = 0
                        self._reveal()
                else:
                    # restart, allowing this key to be a fresh first step
                    self._idx = 1 if key == _KONAMI[0] else 0
        except Exception:
            self._idx = 0
        return False   # never consume the event

    def _reveal(self):
        box = QMessageBox(self._window)
        box.setWindowTitle("SAMBA")
        box.setText("🎉  Konami code!  🎉")
        box.setInformativeText(
            f"SAMBA also unofficially stands for:\n\n"
            f"    {_UNOFFICIAL}  🐞\n\n"
            f"…but you didn't hear that from us.\n"
            f"(Officially: {_OFFICIAL}.)"
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.show()   # modeless — never blocks a measurement


def install_easter_egg(window):
    """Install the Konami-code watcher for *window*.  Best-effort: any failure
    is swallowed so the easter egg can never affect normal operation."""
    try:
        app = QApplication.instance()
        if app is None:
            return None
        filt = _KonamiFilter(window)
        app.installEventFilter(filt)
        window._konami_filter = filt   # keep a reference alive
        return filt
    except Exception:
        return None
