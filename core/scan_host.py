"""The run-lifecycle code shared by the two main windows.

``MainWindow`` (Samba_main) and ``CryoMainWindow`` (Cryo) are deliberately
independent windows (CLAUDE.md §14), but by October 2026 they had grown 34
byte-identical methods and a dozen more that differed only in a setup-key
spelling, a QSettings name or one app-specific line.  Every fix to the
start/finish/pause/abort path, the status bar or the current-sweep
sequencer therefore had to be made twice — and the two copies drifted.

This mixin is that shared code, once.  It owns no widgets and no state of
its own: it is written against the attributes both windows create in their
``__init__`` / ``_build_ui`` (``traj_panel``, ``sl_panel``, ``calib_panel``,
``bd_cal_panel``, ``plot1d``, ``map2d``, ``status_lbl``, ``log_text``,
``pause_btn``, the ``_worker`` / ``_sl_worker`` / ``_cs_*`` run state …) and
the per-app methods it calls (``_launch_scanlist``, ``_save_active_config``,
``_log_append``, ``_read_hw_snapshot`` …).  The handful of genuine
differences are expressed as class attributes and small hooks at the top of
the class; each window overrides only those.

Inheritance order matters: ``class MainWindow(ScanHostMixin, QMainWindow)``
so the mixin's ``closeEvent`` is found before QMainWindow's.
"""
import logging
import os
import threading
import time as _time
from typing import Optional, Tuple

from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
                             QMessageBox, QStatusBar, QStyle, QWidget)
from PyQt6.QtCore import QSettings, QTimer, Qt

from config import save_setup, KEITHLEY_RANGES
from current_sweep import format_current_list, pick_keithley_range
from current_sweep_ui import ThermalSettleWorker
from device_registry import load_registry
from hardware import fresh_proxy, safe_write, TANGO_AVAILABLE
from lab_notebook import append_measurement, notebook_path as _nb_path
from server_sync import sync_setup
from setup_lock import acquire_lock, release_lock

log = logging.getLogger(__name__)


def _sb_fmt(sec: float) -> str:
    """Format a duration (seconds) as '2m 05s' or '7s'."""
    m, s = divmod(int(sec), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


class ScanHostMixin:
    """Shared run lifecycle for the two main windows — see the module docstring."""

    # ---- per-application knobs and hooks ----------------------------------
    # Samba_main's values are the defaults; CryoMainWindow overrides them.
    _QSETTINGS_APP      = "SambaV3"                 # QSettings application key
    _KEY_KEITHLEY_AMP   = "keithley_amplitude_attr" # setup keys naming the
    _KEY_KEITHLEY_RANGE = "keithley_range_attr"     #   Keithley attributes

    def _disarm_zero_after_scan(self):
        """Abort/quit must never start new stage motion (Samba_main's
        zero-after-scan plan, §56).  No-op where the feature does not exist."""

    def _run_zero_after_scan(self):
        """Move the armed axes back to 0 after a run (Samba_main only)."""

    def _stop_background_workers(self):
        """Stop app-owned background threads on close (Cryo's readback worker)."""

    def _active_sensors(self, cfg: dict) -> list:
        """The sensors a (re)started cycle displays; Samba_main adds DC-hyst."""
        return [s for s in cfg["sensors"] if s["enabled"]]

    def _kerr_display_widgets(self):
        """Every plot that can show θ (Cryo adds its retrace map)."""
        return (self.plot1d, self.map2d)

    # ──────── Setup lock, setup access, saving ────────────────────────────────────

    def _acquire_setup_lock(self) -> bool:
        """Take the multi-computer setup lock; warn and return False if busy.

        Used by every start path (single scan, scanlist, calibration time
        scan) — each of them drives the same physical setup, so all of them
        must hold the lock.
        """
        ok, who = acquire_lock(self._active_setup_name)
        if not ok:
            QMessageBox.warning(
                self, "Setup busy",
                f"Setup '{self._active_setup_name}' is already in use:\n{who}\n\n"
                "Abort that scan first, then retry.")
        return ok

    def _active_setup(self) -> dict:
        return self._setups[self._active_setup_name]

    def _registry_now(self) -> list:
        """Current device registry — the registry panel once built, else disk."""
        dr = getattr(self, "dev_registry", None)
        return dr.get_registry() if dr is not None else load_registry()

    def _on_calib_autofocus_changed(self):
        """Persist the Autofocus group per setup, like the time scan.

        The integration time in particular has to survive a restart:
        the unattended refocuses use it, and a session-only value
        would silently fall back to the default mid-measurement-run.
        """
        setup = self._active_setup()
        setup["calib_autofocus"] = self.calib_panel.get_autofocus_settings()
        try:
            save_setup(self._active_setup_name, setup)
        except Exception as e:
            log.error("Autofocus settings save failed: %s", e, exc_info=True)

    def _on_calib_timescan_changed(self):
        """Persist the calibration tab's own time-scan settings per setup."""
        setup = self._active_setup()
        setup["calib_timescan"] = self.calib_panel.get_timescan_settings()
        save_setup(self._active_setup_name, setup)

    def _bd_cal_save(self, vals: list):
        from datetime import datetime as _dt
        date_str = _dt.now().strftime("%Y-%m-%d %H:%M")
        setup = self._active_setup()
        setup["bd_calibration"]      = vals
        setup["bd_calibration_date"] = date_str
        save_setup(self._active_setup_name, setup)
        self.bd_cal_panel.set_status(f"Saved {date_str} for setup '{self._active_setup_name}'.")

    def _bd_cal_load(self):
        setup = self._active_setup()
        vals = setup.get("bd_calibration")
        date_str = setup.get("bd_calibration_date", "unknown date")
        return vals, date_str

    def _bd_plot_fit(self, result):
        """Show a BD calibration trace (and its fitted levels) in the 1D plot.

        Skipped while a scan runs — the live plot belongs to the measurement.
        """
        if getattr(self, "_scan_running", False):
            self.status_lbl.setText(
                "Fit done — 1D plot not touched while a scan is running.")
            return
        overlay = result.step_curve() if result.ok else None
        name = os.path.basename(result.path or "")
        self.plot1d.show_static(
            result.t, result.dc,
            xlabel="Time (s)", ylabel="DC (V)",
            title=f"BD calibration — {name}" if name else "BD calibration",
            overlay=overlay, overlay_label="fitted levels")
        try:
            self.live_tabs.setCurrentWidget(self.plot1d.parentWidget())
        except Exception:
            pass

    def _push_kerr_calibration(self, vals: list):
        """Hand the current λ/2 values to every plot that can show θ.

        Called whenever the BD Calibration boxes change — a user edit, a
        Fit & Import, a Load saved, or a setup switch — so a plot can never
        convert with another setup's calibration.  An all-zero (or otherwise
        unusable) set disables the pills instead of silently showing zeros.
        """
        for w in self._kerr_display_widgets():
            w.set_kerr_calibration(vals)

    def _on_polarity_changed(self):
        """Persist a user toggle of Relay/Field flip into the active config.

        Skipped while a scan runs: the running worker holds its own polarity
        settings, so writing a mid-run change to disk would misdescribe the
        measurement in progress.
        """
        if self._scan_running:
            return
        self._save_active_config()

    def _on_sample_id_edited(self):
        """User finished editing the Sample field. If the name actually
        changed, offer to start a fresh BD calibration for the new sample:
        Yes → empty the 6 mV values and jump to the BD Calibration tab
        (the tab's first-open reload prompt is suppressed — it would offer
        the OLD sample's calibration right back)."""
        sender = self.sender()
        new_id = sender.text().strip() if sender is not None else \
            self.traj_panel.meta.meta_sample.text().strip()
        if new_id == self._last_sample_id:
            return
        old_id = self._last_sample_id
        self._last_sample_id = new_id
        if not new_id:
            return
        ans = QMessageBox.question(
            self, "New sample",
            f"Sample changed to '{new_id}'"
            + (f" (was '{old_id}')" if old_id else "")
            + ".\n\nStart a new BD calibration for it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.bd_cal_panel.suppress_prompt(self._active_setup_name)
        self.bd_cal_panel.load_calibration([0.0] * 6)
        self.bd_cal_panel.set_status(
            f"New calibration for sample '{new_id}' — enter the 6 mV values and Save.")
        self.bottom_tabs.setCurrentWidget(self.bd_cal_panel)

    # ──────── Metadata / timing mirroring between the Trajectory and Scanlist tabs ─

    def _sync_traj_meta_to_sl(self):
        if self._meta_syncing: return
        self._meta_syncing = True
        try:
            self.sl_panel.meta.load_values(self.traj_panel.meta.get_values())
        finally:
            self._meta_syncing = False

    def _sync_sl_meta_to_traj(self):
        if self._meta_syncing: return
        self._meta_syncing = True
        try:
            self.traj_panel.meta.load_values(self.sl_panel.meta.get_values())
        finally:
            self._meta_syncing = False

    def _sync_traj_timing_to_sl(self):
        if self._timing_syncing: return
        self._timing_syncing = True
        try:
            self.sl_panel.int_time.setValue(self.traj_panel.int_time.value())
            self.sl_panel.settle.setValue(self.traj_panel.settle.value())
            self.sl_panel.timeout.setValue(self.traj_panel.timeout.value())
        finally:
            self._timing_syncing = False

    def _sync_sl_timing_to_traj(self):
        if self._timing_syncing: return
        self._timing_syncing = True
        try:
            self.traj_panel.int_time.setValue(self.sl_panel.int_time.value())
            self.traj_panel.settle.setValue(self.sl_panel.settle.value())
            self.traj_panel.timeout.setValue(self.sl_panel.timeout.value())
        finally:
            self._timing_syncing = False

    # ──────── Status bar ──────────────────────────────────────────────────────────

    def _build_status_bar(self):
        """Seven-field QStatusBar showing live scan-run progress."""
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._sb = sb
        sb.setStyleSheet(self._SB_TINTS["idle"])
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(8, 0, 8, 0); row.setSpacing(0)

        def _mk_field():
            lbl = QLabel("—")
            lbl.setStyleSheet("color:#cdd6f4;font-size:12px;")
            return lbl

        def _mk_caption(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#a6adc8;font-size:12px;")
            return lbl

        def _mk_sep():
            lbl = QLabel(" │ ")
            lbl.setStyleSheet("color:#45475a;font-size:12px;")
            return lbl

        self._sb_cur     = _mk_field()
        self._sb_scan    = _mk_field()
        self._sb_start   = _mk_field()
        self._sb_elapsed = _mk_field()
        self._sb_runleft = _mk_field()
        self._sb_scanleft= _mk_field()
        self._sb_dead    = _mk_field()
        self._sb_done    = _mk_field()
        fields = [
            # "Current" only moves during a sweep; "—" the rest of the time.
            ("Current: ",   self._sb_cur),
            ("Scan: ",      self._sb_scan),
            ("Start: ",     self._sb_start),
            ("Elapsed: ",   self._sb_elapsed),
            ("Run left: ",  self._sb_runleft),
            ("Scan left: ", self._sb_scanleft),
            ("Dead: ",      self._sb_dead),
            ("Done: ",      self._sb_done),
        ]
        for i, (cap, lbl) in enumerate(fields):
            if i:
                row.addWidget(_mk_sep())
            row.addWidget(_mk_caption(cap)); row.addWidget(lbl)
        row.addStretch()
        sb.addPermanentWidget(container, 1)

        # 1 Hz refresh so Elapsed / Run-left / Scan-left tick between points
        self._sb_timer = QTimer(self)
        self._sb_timer.setInterval(1000)
        self._sb_timer.timeout.connect(self._refresh_status_bar)
        self._sb_timer.start()

    def _tint_status_bar(self, state: str):
        """Tint the bottom status bar by scan state: green while running,
        peach while paused (manual or auto), neutral when idle."""
        sb = getattr(self, "_sb", None)
        if sb is not None:
            sb.setStyleSheet(self._SB_TINTS.get(state, self._SB_TINTS["idle"]))

    def _status_bar_run_start(self, cfg: dict, n_scans_total: int):
        """Reset status-bar state at the start of a scan run."""
        self._autopause_notified = False   # re-arm the auto-pause popup
        self._run_start_time     = _time.time()
        self._run_scans_done     = 0
        self._run_scans_total    = max(1, int(n_scans_total))
        self._scan_first_pt_time = 0.0
        self._bar_int_time       = float(cfg.get("integration_time", 0.1) or 0.1)
        self._bar_last_done      = 0
        self._bar_last_total     = 1
        from datetime import datetime as _dt
        self._sb_start.setText(_dt.fromtimestamp(self._run_start_time).strftime("%H:%M:%S"))
        self._sb_scan.setText(f"1/{self._run_scans_total}")
        if not self._cs_active:
            self._sb_cur.setText("—")
        for lbl in (self._sb_elapsed, self._sb_runleft, self._sb_scanleft):
            lbl.setText("0s")
        self._sb_dead.setText("0%"); self._sb_done.setText("0%")

    def _status_bar_scan_done(self):
        """One scan-file finished within a multi-scan run; advance the counter.

        Also restamps per-scan timing so the next scan-file's Scan-left /
        Dead-time estimates start fresh (run-level timing is untouched)."""
        self._run_scans_done = min(self._run_scans_done + 1, self._run_scans_total)
        self._scan_first_pt_time = 0.0
        self._scan_start_time    = _time.time()

    def _status_bar_run_finish(self):
        """Freeze the status bar at 100% when the whole run completes."""
        self._run_scans_done = self._run_scans_total
        self._bar_last_done  = self._bar_last_total
        self._sb_scan.setText(f"{self._run_scans_total}/{self._run_scans_total}")
        self._sb_runleft.setText("0s"); self._sb_scanleft.setText("0s")
        self._sb_done.setText("100%")
        if self._run_start_time:
            self._sb_elapsed.setText(_sb_fmt(_time.time() - self._run_start_time))

    def _refresh_status_bar(self):
        """Recompute and display the seven status-bar fields.

        Cheap no-op while idle (leaves the final frame frozen on completion)."""
        if not self._scan_running:
            return
        now = _time.time()
        done, total = self._bar_last_done, self._bar_last_total
        total = max(1, total)
        scan_elapsed = now - self._scan_start_time if self._scan_start_time else 0.0
        run_elapsed  = now - self._run_start_time  if self._run_start_time  else 0.0

        # Scan-left: warmup-corrected rate (skip the first point's setup overhead)
        if done >= 2 and self._scan_first_pt_time > 0:
            rate = (now - self._scan_first_pt_time) / (done - 1)
            scan_left = rate * (total - done)
        elif done >= 1 and scan_elapsed > 0:
            scan_left = scan_elapsed * (total - done) / done
        else:
            scan_left = 0.0

        # Overall fraction across the whole run (each scan weighted equally)
        frac_in_scan = (done / total) if total else 0.0
        overall_frac = (self._run_scans_done + frac_in_scan) / max(1, self._run_scans_total)
        overall_frac = min(max(overall_frac, 0.0), 1.0)

        # Run-left: proportional on whole-run elapsed (includes inter-scan
        # overhead like field flips / demag / settling that per-point misses)
        if overall_frac > 0.001:
            run_left = run_elapsed * (1 - overall_frac) / overall_frac
        else:
            run_left = 0.0

        # Dead time: current-scan elapsed not spent integrating
        active = done * self._bar_int_time
        dead_pct = (max(0.0, scan_elapsed - active) / scan_elapsed * 100.0
                    ) if scan_elapsed > 0 else 0.0

        done_pct = overall_frac * 100.0
        cur_scan = min(self._run_scans_done + 1, self._run_scans_total)

        self._sb_scan.setText(f"{cur_scan}/{self._run_scans_total}")
        self._sb_elapsed.setText(_sb_fmt(run_elapsed))
        self._sb_runleft.setText(_sb_fmt(run_left))
        self._sb_scanleft.setText(_sb_fmt(scan_left))
        self._sb_dead.setText(f"{dead_pct:.0f}%")
        self._sb_done.setText(f"{done_pct:.0f}%")

    def _on_progress(self, done: int, total: int):
        """Record scan progress for the bottom status bar."""
        self._bar_last_done  = done
        self._bar_last_total = total
        if done == 1:
            self._scan_first_pt_time = _time.time()
        self._refresh_status_bar()

    def _on_status(self, msg):
        self.status_lbl.setText(msg); self._log_append(msg, to_file=False)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum())
        # Detect auto-pause from ScanRunner (single scan or scanlist) and update button
        _active_worker = self._worker or self._sl_worker or self._cs_settle
        if _active_worker and _active_worker.is_paused():
            self.pause_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")
            # Error popup — once per auto-pause event ("AUTO-PAUSED" marker is
            # emitted by every engine auto-pause path; a manual Pause never
            # carries it, so the popup only appears for real failures).
            self._tint_status_bar("paused")
            if "AUTO-PAUSED" in msg and not self._autopause_notified:
                self._autopause_notified = True
                QMessageBox.warning(
                    self, "Measurement paused",
                    f"{msg}\n\nThe scan is holding at the failing point — no "
                    "data has been recorded for it. Fix the device, then "
                    "press Resume to retry the same point (or Abort to stop).")
        elif _active_worker:
            self._autopause_notified = False
            self._tint_status_bar("running")

    # ──────── Run lifecycle: running state, pause, abort, errors ──────────────────

    def _set_running(self, running: bool):
        """Buttons stay always enabled — guards in click handlers prevent misuse."""
        if not running:
            self.pause_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.pause_btn.setText("Pause")
        # Disable hardware Read buttons during scan to prevent concurrent TANGO
        # access on the ZI device (Device_4Impl is single-threaded; simultaneous
        # state() + read_attribute() calls cause IMP_LIMIT CORBA exceptions).
        self._tint_status_bar("running" if running else "idle")
        for panel in (self.traj_panel.hw, self.sl_panel.hw):
            if hasattr(panel, 'set_scan_running'):
                panel.set_scan_running(running)

    def _toggle_pause(self):
        if not self._scan_running: return
        # Between currents a sweep runs a thermal-settle worker, or no worker
        # at all (refocus, phase transitions) — _cs_paused covers those, and is
        # honoured at every phase boundary by _cs_hold().
        worker = self._worker or self._sl_worker or self._cs_settle
        if worker is not None:
            was_paused = worker.is_paused()
            (worker.resume if was_paused else worker.pause)()
        elif self._cs_active:
            was_paused = self._cs_paused
        else:
            return
        if self._cs_active:
            self._cs_paused = not was_paused
        _style = self.style()
        if was_paused:
            self.pause_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self.pause_btn.setText("Pause")
            self._tint_status_bar("running")
        else:
            self.pause_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.pause_btn.setText("Resume")
            self._tint_status_bar("paused")

    def _abort_scanlist(self):
        if not self._scan_running: return
        self._disarm_zero_after_scan()   # Abort must never start new stage motion
        if self._cs_active:
            # Stop the whole sweep, not just the scanlist in progress.  The
            # phase that is actually running picks this up: a settle worker
            # via abort(), a refocus via _stop_autofocus, a hold via _cs_hold.
            self._cs_abort  = True
            self._cs_paused = False
            if self._cs_settle:
                self._cs_settle.abort()
            try:
                self.calib_panel._stop_autofocus()
            except Exception:
                log.debug("Autofocus abort failed", exc_info=True)
            if not self._sl_worker and not self._cs_settle:
                # Nothing running to notice the flag (mid-refocus or between
                # phases) — _cs_hold/_cs_focus_done will finish the sweep.
                self.status_lbl.setText("Aborting current sweep…")
                return
        if self._sl_worker: self._sl_worker.abort()
        self.status_lbl.setText("Aborting scanlist…")

    def _on_sl_worker_finished(self):
        self._sl_worker = None
        if self._cs_active:
            # One current of a sweep is done — the lock stays held and the run
            # keeps going.  _cs_step() ends the sweep if this was the last
            # current or if Abort was pressed.
            self._cs_idx += 1
            self._cs_step()
            return
        release_lock(self._active_setup_name)
        self._set_running(False)
        self._scan_running = False

    def _on_sl_scan_done(self, idx: int, fn: str, hw_snap: Optional[dict] = None):
        """Per-file callback from ScanlistWorker — updates status bar and
        records a lab-notebook entry for the file just written (each
        scanlist file previously produced no notebook row at all).

        ``hw_snap`` is the hardware state of *this* file, re-read by the
        worker; the config still carries the list-start block.  Cryo runs
        several directions per cycle and keeps them in ``_sl_cfg_list``; the
        entry is built from the direction this file belongs to.
        """
        self._status_bar_scan_done()
        t_start = self._sl_scan_t0
        self._sl_scan_t0 = _time.time()   # next file starts now
        cfg_list = getattr(self, "_sl_cfg_list", None) or (
            [self._current_scan_cfg] if self._current_scan_cfg else [])
        if not fn or not cfg_list:
            return
        try:
            setup = self._active_setup()
            nb = _nb_path(setup.get("notebook_dir", "~/moke_data"),
                          self._active_setup_name)
            base_cfg = cfg_list[idx % len(cfg_list)]
            entry = dict(base_cfg)
            if hw_snap is not None:
                for _k in [k for k in entry if k.startswith("hw_")]:
                    del entry[_k]
                entry.update(hw_snap)
            entry["_scan_start_time"] = t_start
            entry["_hdf5_path"] = os.path.abspath(fn)
            # Mark this row as part of the scanlist (blank for single scans).
            if self._sl_worker is not None:
                entry["_scanlist_name"] = getattr(self._sl_worker, "list_name", "")
            append_measurement(nb, entry)
        except Exception:
            log.debug("Lab notebook append failed for scanlist file", exc_info=True)

    def _on_cycle_done(self, cycle_idx: int):
        cfg  = self._current_scan_cfg
        mode, _, __ = self._scan_dims(cfg)
        if mode == "2D": return
        active = self._active_sensors(cfg)
        self._alloc_scan_data(cfg, active); self._setup_live_display(cfg, active)

    def _scan_dims(self, cfg) -> Tuple[str, int, int]:
        """Return (mode, n_x, n_y).  mode: '2D'|'1D'|'1D_Y'|'FIELD'|'TIME'"""
        st = cfg.get("scan_type","SPATIAL")
        sx = cfg.get("scan_x",  True)
        sy = cfg.get("scan_y",  False)
        if st == "DC_HYST":       return "DC_HYST", int(cfg.get("hyst_npts", 100)) * 2, 1
        if st == "FIELD":         return "FIELD",  int(cfg.get("field_npts",101)), 1
        if st == "TR_MOKE":       return "1D",     int(cfg.get("act1_npts",101)), 1
        if sx and sy:             return "2D",     int(cfg.get("act1_npts",51)),   int(cfg.get("act2_npts",51))
        if sy and not sx:         return "1D_Y",   int(cfg.get("act2_npts",51)),   1
        if not sx and not sy:     return "TIME",   int(cfg.get("act1_npts",101)),  1
        return "1D",                               int(cfg.get("act1_npts",51)),   1

    def _on_engine_error(self, tb: str):
        """The scan engine raised: say so where the operator looks.

        Until now a traceback was one red line in the Log tab and nothing
        else changed — the status label kept the last point's text, the
        status bar went back to idle, and nothing said the file was
        incomplete.  The dialog is non-modal so the worker's finish handling
        runs underneath it.
        """
        self._engine_error = tb
        self._log_append(f"\n⚠ ERROR:\n{tb}", level="error")
        self.status_lbl.setText("✗ Scan failed — see the Log tab")
        self._tint_status_bar("error")
        lines = [ln for ln in str(tb).strip().splitlines() if ln.strip()]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Scan failed")
        box.setText("The scan engine stopped with an error:\n\n"
                    + (lines[-1] if lines else str(tb))[:300]
                    + "\n\nThe data file, if one was created, is incomplete. "
                      "The full traceback is in the Log tab and in the log file.")
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.show()
        self._error_box = box

    def _field_seg_estimate(self, total_pts: int):
        """Seconds for `total_pts` field points, or None if not computable."""
        try:
            if total_pts <= 0:
                return None
            int_t  = float(self.traj_panel.int_time.value())
            settle = max(float(self.traj_panel.settle.value()), 0.05)
            zi     = float(getattr(self, "_last_zi_settle", 0.0) or 0.0)
            ramp   = 0.0
            try:
                ramp = max(0.0, float(
                    self._active_setup().get("field_ramp_estimate_s", 0.0)))
            except Exception:
                ramp = 0.0
            return total_pts * (settle + zi + int_t + ramp)
        except Exception:
            return None

    def _initial_hw_read(self):
        """Read hardware panels once on startup. Staggered to avoid simultaneous ZI reads."""
        self.traj_panel.hw.refresh()
        QTimer.singleShot(800, self.sl_panel.hw.refresh)

    # ──────── Refocus (shared by the scanlist boundary hold and the current sweep) ─

    def _refocus_position(self):
        """(x, y) target from the Refocus box; None for a non-scanned axis."""
        try:
            return self.sl_panel.refocus.position()
        except Exception:
            return None, None

    def _park_focus_axes(self, then):
        """Move X/Y to the Refocus position, then call `then()`.

        Used before the thermal settle so the focus diode is watched exactly
        where the refocus will happen — and so there is no stage move between
        the settle and the autofocus (which then finds the axes already in
        place and skips its own park).  Off the GUI thread: two TANGO writes
        to a wedged stage would otherwise freeze the window.
        """
        if not self.sl_panel.refocus.is_enabled():
            then(); return
        x, y = self._refocus_position()
        try:
            info = self.calib_panel.get_axis_info() or {}
        except Exception:
            info = {}
        moves = []
        for key, target in (("x", x), ("y", y)):
            dev, attr = info.get(key, ("", ""))
            if target is not None and dev and attr:
                moves.append((dev, attr, float(target), key.upper()))
        if not moves:
            then(); return

        def _work():
            msgs = []
            for dev, attr, target, lbl in moves:
                proxy, cerr = fresh_proxy(dev)
                if cerr and TANGO_AVAILABLE:
                    msgs.append((f"\u26a0 Cannot park {lbl} at {target:.3f} \u2014 "
                                 f"'{dev}' unreachable ({cerr})", "warning"))
                    continue
                werr = safe_write(proxy, attr, target)
                msgs.append((f"\u26a0 Park {lbl} \u2192 {target:.3f} failed: {werr}",
                             "warning") if werr else
                            (f"{lbl} \u2192 {target:.3f} for the settle", "info"))
            def _apply():
                for m, lv in msgs:
                    self._log_append(m, level=lv)
                then()
            self._post_to_main.emit(_apply)

        threading.Thread(target=_work, daemon=True, name="cs_park_focus").start()

    def _run_refocus(self, on_done) -> bool:
        """Start an autofocus at the Refocus box's position.

        Returns False when nothing was started — refocus switched off, or no
        FL sensor configured — so the caller can carry straight on.  The
        run-wide focus timestamp is stamped by the completion handlers, which
        is what keeps the sweep and the periodic refocus from doing two in a
        row.
        """
        if not self.sl_panel.refocus.is_enabled():
            return False
        x, y = self._refocus_position()
        dz = self.sl_panel.refocus.z_offset()
        self._log_append(
            f"Refocusing at x={'—' if x is None else f'{x:.3f}'}, "
            f"y={'—' if y is None else f'{y:.3f}'}"
            + (f", then Z {dz:+.3f}" if dz else "") + "…", level="info")
        if self.calib_panel.run_autofocus_async(
                on_done, focus_pos=x, focus_pos_y=y,
                z_offset=self.sl_panel.refocus.z_offset()):
            return True
        self._log_append(
            "⚠ Autofocus could not start (no FL sensor configured?) — "
            "continuing at the current focus", level="warning")
        return False

    def _on_sl_refocus_due(self):
        """ScanlistWorker is holding at a scan boundary — autofocus, then let
        it go.  The hold is released whatever happens, so a failed autofocus
        can never strand the scanlist."""
        def _done(res=None):
            self._focus_t = _time.time()
            if res and not res.get("ok"):
                self._log_append(f"⚠ Periodic refocus failed: "
                                 f"{res.get('msg', '')}", level="warning")
            elif res and not res.get("reliable"):
                self._log_append(f"⚠ Periodic refocus unreliable: "
                                 f"{res.get('msg', '')}", level="warning")
            w = self._sl_worker
            if w is not None:
                w.refocus_done(self._focus_t)
        if not self._run_refocus(_done):
            _done(None)

    # ──────── Current sweep sequencer ─────────────────────────────────────────────

    def _cs_begin(self, base, currents: list):
        """Start a current sweep: one scanlist per current, refocusing between.

        ``base`` is whatever this application's ``_launch_scanlist`` takes:
        Samba_main passes the scan config (one direction per cycle), Cryo the
        per-direction config list (trace/retrace), which is why the status
        bar counts ``len(base)`` files per cycle for a list.

        The setup lock is already held and is kept for the whole sweep — it is
        released in _cs_finish, not per scanlist.
        """
        self._cs_active   = True
        self._cs_abort    = False
        self._cs_paused   = False
        self._cs_currents = list(currents)
        self._cs_idx      = 0
        self._cs_base     = base

        sl = self.sl_panel.get_settings()
        is_list = isinstance(base, (list, tuple))
        first   = base[0] if is_list else base
        n_files = len(base) if is_list else 1
        self._status_bar_run_start(first, len(currents) * sl["n_scans"] * n_files)
        self._scan_running = True; self._set_running(True)
        self.log_text.clear()
        self._log_append(
            f"▶ Current sweep — {format_current_list(currents)} "
            f"({len(currents)} scanlists × {sl['n_scans']} cycles)", level="info")
        self._cs_step()

    def _cs_step(self):
        """Run the next current: apply it, settle, refocus, then the scanlist."""
        if self._cs_abort or self._cs_idx >= len(self._cs_currents):
            self._cs_finish(aborted=self._cs_abort)
            return

        def _go():
            mA = self._cs_currents[self._cs_idx]
            self._sb_cur.setText(f"{self._cs_idx + 1}/{len(self._cs_currents)}"
                                 f"  ({mA:.4g} mA)")
            self._log_append(
                f"── Current {self._cs_idx + 1}/{len(self._cs_currents)}: "
                f"{mA:.4g} mA ──", level="info")
            self._cs_apply_current(mA, self._cs_after_current)
        self._cs_hold(_go)

    def _cs_hold(self, then):
        """Run `then` once the sweep is neither paused nor aborted.

        Every phase transition goes through this, so a Pause pressed during a
        refocus (which has no worker of its own) still takes effect — at the
        next boundary rather than mid-motion.
        """
        if self._cs_abort:
            self._cs_finish(aborted=True); return
        if not self._cs_paused:
            self._autopause_notified = False
            then(); return
        QTimer.singleShot(200, lambda: self._cs_hold(then))

    def _cs_apply_current(self, mA: float, then):
        """Write range + amplitude to the Keithley, then call then(ok, mA, rng).

        Runs off the GUI thread: two TANGO writes to an unresponsive source
        would otherwise freeze the window for up to 20 s.
        """
        setup   = self._active_setup()
        dev     = setup.get("keithley_device", "")
        a_amp   = setup.get(self._KEY_KEITHLEY_AMP) or "amplitude"
        a_range = setup.get(self._KEY_KEITHLEY_RANGE) or "range"
        rng     = (pick_keithley_range(mA, KEITHLEY_RANGES)
                   if self.sl_panel.cur_sweep.auto_range_cb.isChecked() else "")
        cur_rng = self.sl_panel.hw.range_combo.currentText()

        def _finish(ok, msgs):
            def _apply():
                for m, lv in msgs:
                    self._log_append(m, level=lv)
                then(ok, mA, rng)
            self._post_to_main.emit(_apply)

        def _work():
            msgs = []
            proxy, cerr = fresh_proxy(dev)
            if cerr and TANGO_AVAILABLE:
                _finish(False, [(f"⚠ Keithley '{dev}' unreachable ({cerr})",
                                 "warning")])
                return
            ok = True
            if rng and rng != cur_rng:
                rerr = safe_write(proxy, a_range, rng)
                ok = ok and not rerr
                msgs.append((f"⚠ Keithley range → {rng} failed: {rerr}", "warning")
                            if rerr else (f"Keithley range → {rng}", "info"))
            # Zero amplitude means "output off" on this source (see
            # HardwarePanel._write_amplitude), so mirror that behaviour.
            if abs(mA) < 1e-9:
                try:
                    proxy.command_inout("Off")
                    msgs.append(("Keithley output OFF (amplitude = 0)", "info"))
                except Exception:
                    werr = safe_write(proxy, a_amp, 0.0)
                    ok = ok and not werr
                    msgs.append((f"⚠ Keithley off failed: {werr}", "warning")
                                if werr else ("Keithley amplitude → 0 mA", "info"))
            else:
                try:
                    proxy.command_inout("On")
                except Exception:
                    pass          # already on, or no On command
                werr = safe_write(proxy, a_amp, float(mA))
                ok = ok and not werr
                msgs.append((f"⚠ Keithley amplitude → {mA:.4g} mA failed: {werr}",
                             "warning") if werr else
                            (f"Keithley amplitude → {mA:.4g} mA", "info"))
            _finish(ok, msgs)

        threading.Thread(target=_work, daemon=True, name="cs_apply_current").start()

    def _cs_after_current(self, ok: bool, mA: float, rng: str):
        """Current is set — show it in the panels, then wait for thermalisation."""
        if self._cs_abort:
            self._cs_finish(aborted=True); return
        if not ok:
            # Measuring on regardless would write files whose {I}mA name and
            # whose hardware are different currents — worse than stopping.
            self._cs_auto_pause(
                f"⚠ AUTO-PAUSED — could not set the current source to "
                f"{mA:.4g} mA. Fix the Keithley, then press Resume to retry "
                f"this current.",
                lambda: self._cs_apply_current(mA, self._cs_after_current))
            return
        # Display only: setValue never writes to hardware (Enter does), and the
        # panel mirroring (§50) carries it to the Trajectory tab.  This is also
        # what re-stamps the scanlist auto-name with the new {I}mA token.
        self.sl_panel.hw.amp_spin.setValue(float(mA))
        if rng:
            idx = self.sl_panel.hw.range_combo.findText(rng)
            if idx >= 0:
                self.sl_panel.hw.range_combo.setCurrentIndex(idx)

        # Park at the refocus position first, then settle there.
        self._park_focus_axes(lambda: self._cs_start_settle(mA))

    def _cs_start_settle(self, mA: float):
        if self._cs_abort:
            self._cs_finish(aborted=True); return
        sweep = self.sl_panel.cur_sweep
        setup = self._active_setup()
        self._cs_settle = ThermalSettleWorker(
            mode=sweep.mode(),
            fixed_min=sweep.fixed_spin.value(),
            fl_dev=setup.get("focus_averagein", "").strip(),
            fl_attr=(setup.get("focus_attr", "") or "Value").strip() or "Value",
            detector=sweep.make_detector(),
            label=f"({self._cs_idx + 1}/{len(self._cs_currents)}, {mA:.4g} mA)")
        self._cs_settle.status_msg.connect(self.status_lbl.setText)
        self._cs_settle.log_msg.connect(self._log_append)
        self._cs_settle.done_.connect(self._cs_settle_done)
        # Live focus trace on the 1D plot so the settling is visible while it
        # happens.  The plot is wiped FIRST, for both modes: otherwise the
        # previous current's trace (or, on the first current, the last scan)
        # stays on screen for the whole settle, which reads as a curve that
        # never returns to zero between currents.
        self._cs_fl_t, self._cs_fl_v = [], []
        self._cs_fl_label = f"{self._cs_idx + 1}/{len(self._cs_currents)}, {mA:.4g} mA"
        try:
            self.plot1d.clear()
        except Exception:
            log.debug("Could not clear the 1D plot for the settle", exc_info=True)
        self._cs_settle.sample.connect(self._cs_on_fl_sample)
        self._cs_settle.start()

    def _cs_on_fl_sample(self, elapsed_s: float, fl: float):
        """Draw the focus signal against time while the sample thermalises.

        show_static drops the scan buffers, which is safe here: the plot is
        re-initialised by _setup_live_display when the scanlist starts.
        """
        self._cs_fl_t.append(float(elapsed_s))
        self._cs_fl_v.append(float(fl))
        if len(self._cs_fl_t) < 2:
            return                       # a single point has nothing to show
        try:
            self.plot1d.show_static(
                self._cs_fl_t, self._cs_fl_v,
                xlabel="Time since current change (s)",
                ylabel="Focus signal",
                title=f"Thermal settle — {self._cs_fl_label}")
            # Anchor the time axis at 0 so the trace visibly grows from the
            # moment the current changed, instead of the axis starting at
            # whenever the first sample happened to land.
            self.plot1d.ax1.set_xlim(left=0.0)
            self.plot1d.canvas.draw_idle()
        except Exception:
            log.debug("Focus-settle plot update failed", exc_info=True)

    def _cs_settle_done(self, reason: str):
        # done_ is emitted from inside run(), so the QThread may not have
        # finished when this handler runs on the GUI thread.  Dropping the last
        # reference there would destroy a still-running QThread; wait() first,
        # which returns immediately because run() has already returned.
        worker, self._cs_settle = self._cs_settle, None
        if worker is not None:
            worker.wait(2000)
        if self._cs_abort or reason == "abort":
            self._cs_finish(aborted=True); return

        def _go():
            if not self._run_refocus(self._cs_focus_done):
                self._launch_scanlist(self._cs_base, reset_status_bar=False)
        self._cs_hold(_go)

    def _cs_focus_done(self, res: dict):
        self._focus_t = _time.time()
        if self._cs_abort:
            self._cs_finish(aborted=True); return
        if not res.get("ok"):
            self._log_append(f"⚠ Refocus failed: {res.get('msg', '')}",
                             level="warning")
        elif not res.get("reliable"):
            self._log_append(f"⚠ Refocus unreliable: {res.get('msg', '')}",
                             level="warning")
        else:
            self._log_append(
                f"✓ Refocused at Z = {res['z']:.3f} (FL = {res['fl']:.4g})",
                level="info")

        run_it = lambda: self._launch_scanlist(self._cs_base,
                                               reset_status_bar=False)
        bad = not (res.get("ok") and res.get("reliable"))
        if bad and self.sl_panel.cur_sweep.pause_bad_cb.isChecked():
            self._cs_auto_pause(
                "⚠ AUTO-PAUSED — the refocus did not find a reliable focus. "
                "Focus by hand on the Calibration tab, then press Resume.",
                run_it)
            return
        self._cs_hold(run_it)

    def _cs_auto_pause(self, msg: str, retry):
        """Hold the sweep and tell the operator why, the same way the scan
        engine's auto-pause does — Resume retries `retry`, Abort ends the run."""
        self._log_append(msg, level="warning")
        self.status_lbl.setText(msg)
        self._cs_paused = True
        self.pause_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.pause_btn.setText("Resume")
        self._tint_status_bar("paused")
        if not self._autopause_notified:
            self._autopause_notified = True
            QMessageBox.warning(self, "Current sweep paused", msg)
        self._cs_hold(retry)

    def _cs_set_output_off(self):
        """Turn the current source off at the end of a sweep (background)."""
        setup = self._active_setup()
        dev   = setup.get("keithley_device", "")
        a_amp = setup.get(self._KEY_KEITHLEY_AMP) or "amplitude"

        def _work():
            proxy, cerr = fresh_proxy(dev)
            if cerr and TANGO_AVAILABLE:
                msg = (f"⚠ Could not turn the current source off — "
                       f"'{dev}' unreachable ({cerr})", "warning")
            else:
                try:
                    proxy.command_inout("Off")
                    msg = ("Current source output OFF (sweep finished)", "info")
                except Exception:
                    werr = safe_write(proxy, a_amp, 0.0)
                    msg = ((f"⚠ Current source off failed: {werr}", "warning")
                           if werr else
                           ("Current source amplitude → 0 mA (sweep finished)",
                            "info"))
            self._post_to_main.emit(lambda: (self._log_append(msg[0], level=msg[1]),
                                             self.sl_panel.hw.amp_spin.setValue(0.0)))

        threading.Thread(target=_work, daemon=True, name="cs_output_off").start()

    def _cs_finish(self, aborted: bool):
        """End the sweep: restore the source, zero the stage (Samba_main), release the lock."""
        self._cs_active = False
        self._cs_paused = False
        sweep = self.sl_panel.cur_sweep
        if sweep.off_at_end_cb.isChecked():
            self._cs_set_output_off()
        else:
            last = (self._cs_currents[min(self._cs_idx, len(self._cs_currents) - 1)]
                    if self._cs_currents else 0.0)
            self._log_append(
                f"⚠ Current source left ON at {last:.4g} mA "
                f"('Output off at end' is unchecked)", level="warning")
        if not aborted:
            self._status_bar_run_finish()
        self._sb_cur.setText("—")
        self._run_zero_after_scan()
        release_lock(self._active_setup_name)
        self._scan_running = False
        self._set_running(False)
        self._sl_worker = None
        self._log_append(
            "■ Current sweep aborted" if aborted else
            f"✓ Current sweep complete — {len(self._cs_currents)} currents",
            level="warning" if aborted else "info")

    # ──────── Directories, NAS sync, window geometry, close ───────────────────────

    def _browse_save_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Save directory", self.save_dir.text())
        if d: self.save_dir.setText(d)

    def _browse_server_dir(self):
        start = self.server_dir.text().strip() or f"/run/user/{os.getuid()}/gvfs"
        d = QFileDialog.getExistingDirectory(self, "Server sync directory", start)
        if d:
            self.server_dir.setText(d)
            self._active_setup()["server_sync_dir"] = d

    def _manual_sync(self):
        setup = self._active_setup()
        server_path = self.server_dir.text().strip()
        if not server_path:
            self.status_lbl.setText("Server path not set — enter a path above")
            return
        setup["server_sync_dir"] = server_path
        self.status_lbl.setText("Syncing to server…")
        def _done(ok):
            QTimer.singleShot(0, lambda: self.status_lbl.setText(
                "Server sync complete" if ok else "Server sync partial (see log)"))
        sync_setup(self._active_setup_name, setup, done_cb=_done)

    def _on_live_tab_changed(self, _idx):
        if self.live_tabs.currentWidget() is self.calib_panel:
            self.calib_panel._read_all()

    def _restore_geometry(self):
        """Restore saved geometry, or open at a sensible preferred size — but
        never larger than the current screen, and never positioned off-display,
        so nothing is clipped on first open (or after a resolution/monitor change)."""
        scr   = QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        s = QSettings("ETH-Intermag", self._QSETTINGS_APP); g = s.value("geometry")
        restored = bool(g) and self.restoreGeometry(bytes(g))
        if not restored:
            self.resize(1360, 920)
        if avail:
            # Clamp size to the usable screen (small margin for window decorations)
            w = min(self.width(),  avail.width()  - 20)
            h = min(self.height(), avail.height() - 60)
            if w < self.width() or h < self.height():
                self.resize(max(w, self.minimumWidth()), max(h, self.minimumHeight()))
            # Pull back on-screen if a saved position lands off the display
            x = min(max(self.x(), avail.left()),
                    max(avail.right()  - self.width(),  avail.left()))
            y = min(max(self.y(), avail.top()),
                    max(avail.bottom() - self.height(), avail.top()))
            if (x, y) != (self.x(), self.y()):
                self.move(x, y)

    def closeEvent(self, ev):
        if self._scan_running:
            r = QMessageBox.question(self, "Scan running", "Abort and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r == QMessageBox.StandardButton.No: ev.ignore(); return
            self._disarm_zero_after_scan()   # quitting must never start new stage motion
            self._cs_active = False    # no further currents after this
            self._cs_abort  = True
            # 10 s, not 2 s: one point can legitimately take longer than 2 s
            # (lock-in settling + integration, and far longer on a field or
            # temperature point), and abandoning the thread mid-HDF5-write is
            # how a file gets truncated.
            for w in [self._worker, self._sl_worker, self._cs_settle]:
                if w: w.abort(); w.wait(10000)
            # _on_worker_finished may never run once the event loop is tearing
            # down, so release the setup lock here or the rig stays "busy" to
            # every other computer until the 12 h stale-lock takeover.
            release_lock(self._active_setup_name)
        self._stop_background_workers()
        self._save_active_config()
        QSettings("ETH-Intermag", self._QSETTINGS_APP).setValue("geometry", self.saveGeometry())
        ev.accept()
