"""Run progress shared by the Green/IR and Cryo windows."""
import time as _time
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QStatusBar, QWidget, QHBoxLayout, QLabel, QPushButton

def _sb_fmt(sec):
    m, s = divmod(max(0, int(sec)), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"

class RunStatus:
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
            lbl = QLabel("—", self)
            lbl.hide()
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

            ("Scan: ",      self._sb_scan),

            ("Elapsed: ",   self._sb_elapsed),
            ("Run left: ",  self._sb_runleft),


            ("Done: ",      self._sb_done),
        ]
        for i, (cap, lbl) in enumerate(fields):
            if i:
                row.addWidget(_mk_sep())
            row.addWidget(_mk_caption(cap)); row.addWidget(lbl); lbl.show()
        row.addStretch()
        details = QPushButton("Details…")
        details.clicked.connect(self._show_run_details)
        row.addWidget(details)
        sb.addPermanentWidget(container, 1)

        # 1 Hz refresh so Elapsed / Run-left / Scan-left tick between points
        self._sb_timer = QTimer(self)
        self._sb_timer.setInterval(1000)
        self._sb_timer.timeout.connect(self._refresh_status_bar)
        self._sb_timer.start()


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


    def _status_bar_run_start(self, cfg: dict, n_scans_total: int):
        """Reset status-bar state at the start of a scan run."""
        self._run_aborted = False
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


    def _status_bar_run_finish(self):
        """Freeze the status bar at 100% when the whole run completes."""
        if self._run_aborted:
            self._sb_runleft.setText("—")
            self._sb_scanleft.setText("—")
            return
        self._run_scans_done = self._run_scans_total
        self._bar_last_done  = self._bar_last_total
        self._sb_scan.setText(f"{self._run_scans_total}/{self._run_scans_total}")
        self._sb_runleft.setText("0s"); self._sb_scanleft.setText("0s")
        self._sb_done.setText("100%")
        if self._run_start_time:
            self._sb_elapsed.setText(_sb_fmt(_time.time() - self._run_start_time))


    def _status_bar_scan_done(self):
        """One scan-file finished within a multi-scan run; advance the counter.

        Also restamps per-scan timing so the next scan-file's Scan-left /
        Dead-time estimates start fresh (run-level timing is untouched)."""
        self._run_scans_done = min(self._run_scans_done + 1, self._run_scans_total)
        self._scan_first_pt_time = 0.0
        self._scan_start_time    = _time.time()
