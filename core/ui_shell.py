"""Shared application chrome, persistence feedback and cooperative shutdown."""
import logging
from PyQt6.QtCore import QSettings, QTimer, Qt, QObject, QEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QPushButton, QLabel,
    QMessageBox, QDialog, QFormLayout,
)
from .run_state import RunController, RunPhase
from .setup_lock import lock_health, lock_status, release_lock
from .theme import MOCHA, readable_stylesheet

log = logging.getLogger(__name__)


class _ResponsiveForms(QObject):
    def __init__(self, area, content):
        super().__init__(area)
        self.area, self.content = area, content
        area.viewport().installEventFilter(self)
        QTimer.singleShot(0, self.reflow)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.reflow()
        return False

    def reflow(self):
        from .widgets import ResponsiveRow
        width = self.area.viewport().width() - 32
        for row in self.content.findChildren(ResponsiveRow):
            required = sum(row.itemAt(i).minimumSize().width() for i in range(row.count()))
            required += max(0, row.count() - 1) * row.spacing()
            row.setDirection(row.Direction.TopToBottom if required > width else row.Direction.LeftToRight)


def scroll_panel(panel):
    """Keep the tab's identity while allowing its form to scroll on laptops."""
    old = panel.layout()
    if old is None:
        return
    content = QWidget()
    content.setLayout(old)
    area = QScrollArea(panel)
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setWidget(content)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(area)
    panel._form_reflow = _ResponsiveForms(area, content)


class ApplicationShell:
    """Mixin used by both instrument windows; hardware behavior stays in panels."""

    def _init_shell(self, settings_name, save_function, hardware_available):
        self._shell_settings = QSettings("ETH-Intermag", settings_name)
        self._save_setup_fn = save_function
        self._hardware_available = hardware_available
        self._closing = self._close_ready = False
        self._last_save_ok = True
        self._run_aborted = False
        self.run_controller = RunController(self)
        self.run_controller.phase_changed.connect(self._show_run_phase)
        self.run_controller.stopped.connect(self._finish_close)

    def _install_shell(self, main_layout, action_layout, directory_label,
                       browse_button, server_bar, splitter):
        self._h_split = splitter
        splitter.setSizes([210, 760, 320])
        # Both channel pickers now have labelled two-line rows.
        header = getattr(self.right_panel, "_hdr_widget", None)
        if header is not None:
            header.hide()
        # Paths belong together and need not compete with acquisition controls.
        storage = QWidget()
        storage_layout = QVBoxLayout(storage)
        storage_layout.setContentsMargins(0, 0, 0, 0)
        local_row = QHBoxLayout()
        for widget in (directory_label, self.save_dir, browse_button):
            action_layout.removeWidget(widget)
            local_row.addWidget(widget, 1 if widget is self.save_dir else 0)
        storage_layout.addLayout(local_row)
        main_layout.removeWidget(server_bar)
        storage_layout.addWidget(server_bar)
        main_layout.insertWidget(1, storage)
        storage.hide()
        action_layout.addStretch()
        self.phase_label = QLabel("Ready")
        self.phase_label.setMinimumWidth(72)
        action_layout.addWidget(self.phase_label)
        self.connection_label = QLabel("Hardware mode" if self._hardware_available else "Simulation")
        self.connection_label.setToolTip("Hardware mode uses the configured TANGO devices. Individual device errors appear in the log.")
        action_layout.addWidget(self.connection_label)
        for title, widget, key, default in (
            ("Presets", self.cfg_list, "presets", False),
            ("Channels", self.right_panel, "channels", True),
            ("Storage", storage, "storage", False),
        ):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            visible = self._shell_settings.value(key, default, type=bool)
            button.setChecked(visible)
            widget.setVisible(visible)
            button.toggled.connect(widget.setVisible)
            button.toggled.connect(lambda value, k=key: self._shell_settings.setValue(k, value))
            action_layout.addWidget(button)

        for name in ("traj_panel", "sl_panel", "setup_defaults", "defaults_panel", "bd_cal_panel", "calib_panel"):
            panel = getattr(self, name, None)
            if panel is not None:
                scroll_panel(panel)
        # High-contrast readable type, shared between the two applications.
        for widget in (self, *self.findChildren(QWidget)):
            if widget.styleSheet():
                widget.setStyleSheet(readable_stylesheet(widget.styleSheet()))
        for button in (self.start_btn, self.pause_btn, self.abort_btn):
            button.setStyleSheet(button.styleSheet() + "QPushButton:disabled{background:#313244;color:#a6adc8;}")
        self._show_run_phase(RunPhase.IDLE)
        self._lease_timer = QTimer(self)
        self._lease_timer.setInterval(1000)
        self._lease_timer.timeout.connect(self._check_lease)
        self._lease_timer.start()

    def _restore_shell(self):
        for key, splitter in (("horizontal_split", self._h_split), ("vertical_split", self._v_split)):
            state = self._shell_settings.value(key)
            if state is not None and splitter.restoreState(state):
                self._split_initialised = True

    def _persist_setup(self, name, setup):
        try:
            self._save_setup_fn(name, setup)
        except Exception as exc:
            self._last_save_ok = False
            log.exception("Configuration save failed")
            self.status_lbl.setText(f"Save failed: {exc}")
            self.status_lbl.setStyleSheet(f"color:{MOCHA['red']};")
            return False
        self._last_save_ok = True
        return True

    def _request_start(self):
        if self._scan_running or self._closing:
            return
        self._run_aborted = False
        self.run_controller.set_phase(RunPhase.PREPARING)
        try:
            self._unified_start()
        except Exception as exc:
            self._mark_run_error(str(exc))
            log.exception("Could not start acquisition")
        finally:
            if not self._scan_running and self.run_controller.phase != RunPhase.ERROR:
                self.run_controller.set_phase(RunPhase.IDLE)

    def _mark_run_error(self, message):
        self._run_aborted = True
        self.run_controller.set_phase(RunPhase.ERROR, str(message))
        self.status_lbl.setText(str(message))

    def _show_run_phase(self, phase):
        if not hasattr(self, "phase_label"):
            return
        color = {RunPhase.RUNNING: "green", RunPhase.PAUSED: "peach",
                 RunPhase.ERROR: "red", RunPhase.STOPPING: "peach"}.get(phase, "text")
        self.phase_label.setText(phase.value)
        self.phase_label.setStyleSheet(f"color:{MOCHA[color]};font-weight:bold;")
        self.start_btn.setEnabled(phase in (RunPhase.IDLE, RunPhase.ERROR) and not self._scan_running)
        self.pause_btn.setEnabled(phase in (RunPhase.RUNNING, RunPhase.PAUSED))
        self.abort_btn.setEnabled(self._scan_running and phase != RunPhase.STOPPING)
        if hasattr(self, "_sb"):
            self._sb.setStyleSheet(f"QStatusBar{{background:{MOCHA['mantle']};border-top:2px solid {MOCHA[color]};}}")

    def _check_lease(self):
        if self._closing:
            return
        healthy, message = lock_health(self._active_setup_name)
        mode = "Hardware mode" if self._hardware_available else "Simulation"
        if self._scan_running:
            protection = ("Lease active" if message == "Lease protected" else
                          "Lock lost" if not healthy else
                          "Advisory lock" if message.startswith("Advisory") else "Unprotected")
            self.connection_label.setText(f"{mode} · {protection}")
        else:
            self.connection_label.setText(mode)
        self.connection_label.setToolTip(lock_status(self._active_setup_name))
        if self._scan_running and not healthy:
            for worker in (self._worker, self._sl_worker, self._cs_settle):
                if worker is not None:
                    worker.pause()
            self._cs_paused = True
            self.run_controller.set_phase(RunPhase.PAUSED)
            self.pause_btn.setText("Resume")
            self.status_lbl.setText(f"Paused: {message}")

    def _may_resume(self):
        healthy, message = lock_health(self._active_setup_name)
        if not healthy:
            self.status_lbl.setText(f"Cannot resume: {message}. Abort and reacquire the setup.")
        return healthy

    def _show_run_details(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Run details")
        form = QFormLayout(dialog)
        labels = []
        for title, name in (("Current", "cur"), ("Scan", "scan"), ("Started", "start"),
                            ("Elapsed", "elapsed"), ("Run left", "runleft"),
                            ("Scan left", "scanleft"), ("Dead time", "dead"), ("Done", "done")):
            value = QLabel()
            form.addRow(title, value)
            labels.append((value, getattr(self, "_sb_" + name)))
        def refresh():
            for value, source in labels:
                value.setText(source.text())
        timer = QTimer(dialog)
        timer.timeout.connect(refresh)
        timer.start(500)
        refresh()
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def closeEvent(self, event):
        if self._close_ready:
            release_lock(self._active_setup_name)
            self._save_active_config()
            for key, splitter in (("horizontal_split", self._h_split), ("vertical_split", self._v_split)):
                self._shell_settings.setValue(key, splitter.saveState())
            self._shell_settings.setValue("geometry", self.saveGeometry())
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        if self._scan_running:
            choice = QMessageBox.question(self, "Scan running", "Abort and quit?",
                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if choice != QMessageBox.StandardButton.Yes:
                return
        self._closing = self._run_aborted = True
        self._zero_armed = self._cs_active = False
        self._cs_abort = True
        self._dir_queue = []
        for name in ("_rb_timer", "_rb_sync_timer", "_lease_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        workers = [getattr(self, name, None) for name in ("_worker", "_sl_worker", "_cs_settle", "_rb_worker")]
        workers.append(getattr(self.calib_panel, "_af_worker", None))
        workers.append(getattr(self.calib_panel, "_anc_worker", None))
        workers.append(getattr(self.script_console, "_worker", None))
        self.status_lbl.setText("Stopping workers and finishing data writes…")
        self.run_controller.stop_workers(workers)

    def _finish_close(self):
        self._close_ready = True
        self.close()
