"""
script_console.py — Samba v3
Embedded Python scripting console for ad-hoc measurement automation.

Provides a code editor + output panel that can access:
  • ScanRunner — run scans programmatically
  • hardware module — get_proxy(), safe_read(), safe_write()
  • numpy, h5py — data manipulation
  • Any scan config dict from the active setup

Usage examples the user can type:
    # Read a sensor manually
    p = get_proxy("hpp-N42/measure/ZI2")
    print(safe_read(p, "x1"))

    # Move a stage
    p = get_proxy("smaract2/control/IR-controller")
    safe_write(p, "x", 25000)

    # Run a quick scan from a dict
    cfg = get_active_config()
    cfg["act1_npts"] = 21  # fewer points
    fn = run_scan(cfg)
    print(f"Saved to {fn}")
"""
import sys, io, traceback, textwrap, time
from typing import Optional, Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QLabel, QSplitter, QGroupBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor, QColor, QTextCharFormat


# ─────────────────────────────────────────────────────────────────────────────
# Script execution worker — runs user code off the GUI thread
# ─────────────────────────────────────────────────────────────────────────────
class ScriptWorker(QThread):
    output    = pyqtSignal(str)       # stdout / stderr text
    finished_ = pyqtSignal()          # script completed (success or error)

    def __init__(self, code: str, namespace: dict):
        super().__init__()
        self._code = code
        self._ns   = namespace

    def run(self):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        buf = io.StringIO()
        sys.stdout = sys.stderr = buf
        try:
            exec(compile(self._code, "<console>", "exec"), self._ns)
        except Exception:
            traceback.print_exc(file=buf)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
        text = buf.getvalue()
        if text:
            self.output.emit(text)
        self.finished_.emit()


# ─────────────────────────────────────────────────────────────────────────────
# ScriptConsolePanel — embeddable widget
# ─────────────────────────────────────────────────────────────────────────────
class ScriptConsolePanel(QWidget):
    """
    Python console with code editor and output.

    Call set_context(setup_getter, config_getter) after construction
    to wire it into the running application.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[ScriptWorker] = None
        self._setup_getter:  Optional[Callable] = None
        self._config_getter: Optional[Callable] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(4)

        # Header with help text
        hdr = QLabel(
            "Python console — access hardware, run scans, and process data. "
            "Type help() for available functions.")
        hdr.setStyleSheet("color:#6c7086;font-size:10px;")
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Code editor ───────────────────────────────────────────────────────
        editor_w = QWidget(); el = QVBoxLayout(editor_w)
        el.setContentsMargins(0, 0, 0, 0); el.setSpacing(2)
        el.addWidget(QLabel("Code:"))
        self.editor = QTextEdit()
        self.editor.setStyleSheet(
            "QTextEdit{background:#181825;border:1px solid #313244;"
            "border-radius:4px;color:#cdd6f4;font-family:'Courier New',monospace;"
            "font-size:11px;}")
        self.editor.setTabStopDistance(32)
        self.editor.setPlainText(self._default_code())
        el.addWidget(self.editor, stretch=1)

        btn_row = QHBoxLayout(); btn_row.setSpacing(6)
        self.run_btn = QPushButton("▶  Run"); self.run_btn.setObjectName("start_btn")
        self.run_btn.setFixedHeight(28)
        self.run_btn.clicked.connect(self._run_code)
        self.stop_btn = QPushButton("■  Stop"); self.stop_btn.setObjectName("abort_btn")
        self.stop_btn.setFixedHeight(28); self.stop_btn.setEnabled(False)
        self.clear_btn = QPushButton("Clear output")
        self.clear_btn.setFixedHeight(28)
        self.clear_btn.clicked.connect(lambda: self.output.clear())
        btn_row.addWidget(self.run_btn); btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.clear_btn); btn_row.addStretch()
        el.addLayout(btn_row)
        splitter.addWidget(editor_w)

        # ── Output panel ──────────────────────────────────────────────────────
        out_w = QWidget(); ol = QVBoxLayout(out_w)
        ol.setContentsMargins(0, 0, 0, 0); ol.setSpacing(2)
        ol.addWidget(QLabel("Output:"))
        self.output = QTextEdit(); self.output.setReadOnly(True)
        self.output.setStyleSheet(
            "QTextEdit{background:#12121f;border:1px solid #313244;"
            "border-radius:4px;color:#a6e3a1;font-family:'Courier New',monospace;"
            "font-size:10px;}")
        ol.addWidget(self.output, stretch=1)
        splitter.addWidget(out_w)

        splitter.setSizes([300, 200])
        root.addWidget(splitter)

    def set_context(self, setup_getter, config_getter):
        """Wire into the application after construction."""
        self._setup_getter  = setup_getter
        self._config_getter = config_getter

    @staticmethod
    def _default_code() -> str:
        return textwrap.dedent("""\
            # ── Samba scripting console ──
            # Available functions:
            #   get_proxy(device_path) → proxy object
            #   safe_read(proxy, attr) → (value, error)
            #   safe_write(proxy, attr, val) → error
            #   get_active_config() → current scan config dict
            #   get_active_setup()  → current setup dict
            #   run_scan(cfg)       → filename or None
            #   sleep(seconds), os.path, os.listdir
            #
            # Example: read ZI2 x1
            p = get_proxy("hpp-N42/measure/ZI2")
            val, err = safe_read(p, "x1")
            print(f"ZI2 x1 = {val}")
        """)

    def _build_namespace(self) -> dict:
        """Build a curated execution namespace for the script console.

        The namespace exposes hardware/scan helpers and safe data-processing
        tools.  Potentially destructive modules (os.system, subprocess,
        sys.exit) are intentionally excluded.  os.path operations are
        provided as *path* to allow users to work with data files without
        granting access to shell-execution primitives.
        """
        import numpy as np
        import h5py
        import os as _os
        from hardware import get_proxy, safe_read, safe_write, safe_read_str
        from scan import ScanRunner

        # Restricted os shim — expose only read/path operations.
        # Full os access (os.system, os.popen, os.execv, etc.) is excluded.
        class _RestrictedOs:
            path      = _os.path
            sep       = _os.sep
            listdir   = staticmethod(_os.listdir)
            getcwd    = staticmethod(_os.getcwd)
            makedirs  = staticmethod(_os.makedirs)
            expanduser = staticmethod(_os.path.expanduser)
            join      = staticmethod(_os.path.join)
            exists    = staticmethod(_os.path.exists)

        ns = {
            # Core imports
            "np": np, "numpy": np,
            "h5py": h5py,
            "time": __import__("time"),
            "sleep": time.sleep,
            # Restricted filesystem access (no shell execution)
            "os": _RestrictedOs(),
            "path": _os.path,
            # Hardware
            "get_proxy": get_proxy,
            "safe_read": safe_read,
            "safe_write": safe_write,
            "safe_read_str": safe_read_str,
            # Scan
            "ScanRunner": ScanRunner,
        }

        # Application context (may be None if not wired)
        if self._config_getter:
            ns["get_active_config"] = self._config_getter
        if self._setup_getter:
            ns["get_active_setup"] = self._setup_getter

        # Convenience: run_scan with stdout progress
        def run_scan(cfg, setup=None):
            """Run a scan and print progress. Returns filename or None."""
            if setup is None and self._setup_getter:
                setup = self._setup_getter()
            if setup is None:
                print("Error: no setup available. Pass setup= explicitly.")
                return None
            runner = ScanRunner(cfg, setup)
            fn = runner.run({
                'status':   lambda m: print(m),
                'log':      lambda m: print(m),
                'point':    lambda *a: None,
                'progress': lambda c, t: None,
            })
            return fn

        ns["run_scan"] = run_scan

        # help()
        def console_help():
            print("── Samba Console Help ──")
            print("Hardware:")
            print("  p = get_proxy('device/path')")
            print("  val, err = safe_read(p, 'attribute')")
            print("  err = safe_write(p, 'attribute', value)")
            print()
            print("Scanning:")
            print("  cfg = get_active_config()  # current GUI config")
            print("  cfg['act1_npts'] = 21      # modify as needed")
            print("  fn = run_scan(cfg)         # returns HDF5 path")
            print()
            print("Data:")
            print("  import h5py")
            print("  f = h5py.File('path.h5', 'r')")
            print("  data = f['measurement']['ZI2_x1'][:]")
            print()
            print("Utilities:")
            print("  np (numpy), sleep(seconds), h5py")
            print("  os.path, os.listdir, os.makedirs  (safe subset)")

        ns["help"] = console_help
        return ns

    def _run_code(self):
        """Execute the editor contents in a background thread."""
        if self._worker and self._worker.isRunning():
            return

        code = self.editor.toPlainText().strip()
        if not code:
            return

        self.output.append("─── Running… ───")
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        ns = self._build_namespace()
        self._worker = ScriptWorker(code, ns)
        self._worker.output.connect(self._on_output)
        self._worker.finished_.connect(self._on_finished)
        self._worker.start()

    def _on_output(self, text: str):
        self.output.append(text.rstrip())
        # Auto-scroll
        sb = self.output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_finished(self):
        self.output.append("─── Done ───\n")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        sb = self.output.verticalScrollBar()
        sb.setValue(sb.maximum())
