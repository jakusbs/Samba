"""Explicit UI run state and non-blocking, cooperative worker shutdown."""
from enum import Enum
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class RunPhase(str, Enum):
    IDLE = "Ready"
    PREPARING = "Preparing"
    RUNNING = "Running"
    PAUSED = "Paused"
    STOPPING = "Stopping"
    ERROR = "Error"


class RunController(QObject):
    phase_changed = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = RunPhase.IDLE
        self.message = ""
        self._workers = []
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._check_stopped)

    def set_phase(self, phase, message=""):
        phase = RunPhase(phase)
        if self.phase == RunPhase.STOPPING and (self._poll.isActive() or
                phase in (RunPhase.PREPARING, RunPhase.RUNNING, RunPhase.PAUSED)):
            return
        self.phase, self.message = phase, message
        self.phase_changed.emit(phase)

    def stop_workers(self, workers):
        """Retain workers until they actually stop; never terminate a writer."""
        self.set_phase(RunPhase.STOPPING)
        self._workers = list(dict.fromkeys(w for w in workers if w is not None))
        for worker in self._workers:
            stop = getattr(worker, "abort", None) or getattr(worker, "stop", None)
            if stop is not None:
                stop()
        self._poll.start()
        QTimer.singleShot(0, self._check_stopped)

    def _check_stopped(self):
        if not self._poll.isActive():
            return
        if any(worker.isRunning() for worker in self._workers):
            return
        self._poll.stop()
        self._workers.clear()
        self.stopped.emit()
