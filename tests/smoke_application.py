"""Exercise either real application in an isolated, hardware-free process."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FLAVOUR = sys.argv[1]
folder = tempfile.TemporaryDirectory()
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['SAMBA_CONFIG_DIR'] = str(Path(folder.name) / 'config')
os.environ['XDG_CONFIG_HOME'] = str(Path(folder.name) / 'xdg')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ('Cryo' if FLAVOUR == 'cryo' else 'Samba_main')))
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox
from core import hardware
assert not hardware.TANGO_AVAILABLE, 'Run this smoke test without the hardware extra'
if FLAVOUR == 'cryo':
    import samba_cryo as module
    Window = module.CryoMainWindow
else:
    import samba as module
    Window = module.MainWindow
Window._initial_hw_read = lambda self: None
original_load = module.load_setup

def load_setup(name):
    setup = original_load(name)
    setup['save_dir'] = str(Path(folder.name) / 'data')
    setup['notebook_dir'] = str(Path(folder.name) / 'notebooks')
    return setup
module.load_setup = load_setup
app = QApplication([])
window = Window()
window.resize(1280, 800)
window.show()

def until(condition, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(.005)
    return False

assert until(window.isVisible)
assert window.minimumSizeHint().width() <= 1280, window.minimumSizeHint().width()
assert window.start_btn.isEnabled()
assert not window.pause_btn.isEnabled()
assert not window.abort_btn.isEnabled()
assert window.plot1d.follow_cb.isChecked()
# A save error must stay visible and must never turn into "Config saved".
with patch.object(window, '_save_setup_fn', side_effect=OSError('test disk full')):
    window._explicit_save()
    assert window.status_lbl.text().startswith('Save failed:'), window.status_lbl.text()
window._explicit_save()
assert window.status_lbl.text().startswith('Config saved')
# NAS completion must be delivered by the GUI thread, including a plain Python worker.
updates = []
original_set_text = window.status_lbl.setText

def record(text):
    updates.append((text, QThread.currentThread() == app.thread()))
    original_set_text(text)
window.status_lbl.setText = record

def sync(_name, _setup, done_cb=None):
    thread = threading.Thread(target=lambda: done_cb(True))
    thread.start(); thread.join()
window.server_dir.setText(str(Path(folder.name) / 'server'))
with patch.object(module, 'sync_setup', side_effect=sync):
    window._manual_sync()
    assert until(lambda: any('sync complete' in t.lower() for t, _ in updates)), updates
assert all(on_gui for _, on_gui in updates), updates
# A completed scanlist enables Start again before the next run.
window._scan_running = True
window._set_running(True)
window._on_sl_worker_finished()
assert not window._scan_running and window.start_btn.isEnabled()
# Model a writer that needs time to finish its last write after cancellation.
class Writer(QThread):
    def __init__(self):
        super().__init__(); self.finish_allowed = threading.Event(); self.aborted = False
    def abort(self):
        self.aborted = True
    def run(self):
        self.finish_allowed.wait(4)
writer = Writer(); window._worker = writer; writer.start()
window._scan_running = True
window._set_running(True)
window._sb_done.setText('25%')
window._abort_scan()
window._status_bar_run_finish()
assert window._sb_done.text() == '25%'
released = []
with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), \
     patch('core.ui_shell.release_lock', side_effect=lambda name: released.append(name)):
    window.close()
    assert writer.aborted
    assert not released
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    assert until(lambda: bool(heartbeat))
    assert window.isVisible() and not released
    writer.finish_allowed.set()
    assert until(lambda: not window.isVisible())
    assert len(released) == 1 and not writer.isRunning()
writer.wait()
print(f'{FLAVOUR}: startup, 1280px layout, save feedback, GUI dispatch and safe shutdown passed')
folder.cleanup()
