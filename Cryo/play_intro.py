"""
play_intro.py — SAMBA v8 splash screen
Shows a branded loading screen while the main window initializes.

Requirements: PyQt6 (already a project dependency)
Assets:       samba_splash.png (720×480) in the same directory as this script
"""

import os, time
from PyQt6.QtWidgets import QSplashScreen, QApplication
from PyQt6.QtGui import QPixmap, QFont, QColor, QPainter
from PyQt6.QtCore import Qt, QTimer, QEventLoop


def _asset(name):
    """Resolve asset path relative to this script."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


_splash_start_time = None


def show_splash(app: QApplication) -> QSplashScreen:
    """
    Create and display the splash screen.  Returns the QSplashScreen so the
    caller can call finish_splash() once the window is ready.
    """
    global _splash_start_time
    _splash_start_time = time.monotonic()

    pixmap = QPixmap(_asset("samba_splash.png"))
    if pixmap.isNull():
        # Fallback: plain coloured splash if PNG missing
        pixmap = QPixmap(720, 480)
        pixmap.fill(QColor("#1e1e2e"))
        p = QPainter(pixmap)
        p.setPen(QColor("#cdd6f4"))
        f = QFont("Segoe UI", 36, QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "SAMBA v8")
        p.end()

    splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
    splash.setStyleSheet("color: #a6adc8; font-size: 12px;")
    splash.show()
    splash.showMessage(
        "  Initializing…",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
        QColor("#a6adc8"),
    )
    app.processEvents()
    return splash


def update_splash(splash: QSplashScreen, message: str):
    """Update the status text on the splash screen."""
    splash.showMessage(
        f"  {message}",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
        QColor("#a6adc8"),
    )
    QApplication.instance().processEvents()


def finish_splash(splash: QSplashScreen, main_window, min_seconds: float = 3.0):
    """
    Close the splash and show the main window, but guarantee the splash
    stays visible for at least *min_seconds* so the user can see it.
    """
    global _splash_start_time

    elapsed = time.monotonic() - (_splash_start_time or time.monotonic())
    remaining_ms = max(0, int((min_seconds - elapsed) * 1000))

    if remaining_ms > 0:
        # Keep processing events while we wait so the UI stays responsive
        loop = QEventLoop()
        QTimer.singleShot(remaining_ms, loop.quit)
        loop.exec()

    main_window.show()
    splash.finish(main_window)


# Legacy entry point kept for backward compat
def play_intro():
    """No-op — the old VLC video intro is replaced by the Qt splash."""
    pass
