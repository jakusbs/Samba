"""
scan/workers.py — Samba v3
ScanWorker (single scan QThread) and ScanlistWorker (N-scan list QThread).
"""
import copy, os, time, traceback
from datetime import datetime
from typing import Dict, List, Optional
import numpy as np

from PyQt6.QtCore import QThread, pyqtSignal

from config import X_TIME
from hardware import (get_proxy, fresh_proxy, is_sim_proxy, safe_read,
                      safe_write, demagnetize_magnet, TANGO_AVAILABLE)
from core.scan.runner import ScanRunner


class ScanWorker(QThread):
    point_done        = pyqtSignal(int, int, float, dict)
    point_retrace     = pyqtSignal(int, int, float, dict)   # interleaved 2D retrace points
    progress          = pyqtSignal(int, int)
    status_msg        = pyqtSignal(str)
    log_msg           = pyqtSignal(str)
    scan_done         = pyqtSignal(str)
    scan_done_retrace = pyqtSignal(str)   # emitted when interleaved retrace file is ready
    scan_aborted      = pyqtSignal()
    error_msg         = pyqtSignal(str)
    dc_loop_ready     = pyqtSignal(object, object)   # (field_arr, y_bufs dict)

    def __init__(self, cfg: dict, setup: dict):
        super().__init__()
        self._runner = ScanRunner(cfg, setup)

    def abort(self):     self._runner.abort()
    def pause(self):     self._runner.pause()
    def resume(self):    self._runner.resume()
    def is_paused(self): return self._runner._paused

    def run(self):
        try:
            fn = self._runner.run({
                'point':         self.point_done.emit,
                'point_retrace': self.point_retrace.emit,
                'progress':      self.progress.emit,
                'status':        self.status_msg.emit,
                'log':           self.log_msg.emit,
                'dc_loop':       self.dc_loop_ready.emit,
            })
            if fn:
                self.scan_done.emit(fn)
            else:
                self.scan_aborted.emit()
            rfn = getattr(self._runner, '_retrace_filename', None)
            if rfn:
                self.scan_done_retrace.emit(rfn)
        except Exception:
            self.error_msg.emit(traceback.format_exc())


# ─────────────────────────────────────────────────────────────────────────────
# ScanlistWorker — runs N scans with polarity management on a QThread
# ─────────────────────────────────────────────────────────────────────────────
class ScanlistWorker(QThread):
    point_done    = pyqtSignal(int, int, float, dict)
    progress      = pyqtSignal(int, int)
    list_progress = pyqtSignal(int, int)
    cycle_done    = pyqtSignal(int)
    status_msg    = pyqtSignal(str)
    log_msg       = pyqtSignal(str)
    scan_done     = pyqtSignal(int, str)
    all_done      = pyqtSignal(str)
    scan_aborted  = pyqtSignal()
    error_msg     = pyqtSignal(str)
    relay_changed = pyqtSignal(int)   # emitted whenever relay state is written

    def __init__(self, cfg_or_list, setup: dict, n_scans: int,
                 list_name: str, relay_flip: bool, field_flip: bool,
                 setup_name: str = ""):
        super().__init__()
        # Accept either a single config dict or a per-cycle list of configs.
        # For trace+retrace both cfgs run per cycle; field flip happens between cycles.
        self.cfg_list = cfg_or_list if isinstance(cfg_or_list, list) else [cfg_or_list]
        self.setup = setup; self.n_scans = n_scans
        self.list_name = list_name
        self.relay_flip = relay_flip; self.field_flip = field_flip
        self.setup_name = setup_name
        self._abort = False; self._paused = False; self._runner = None
        self._relay_state = 0

    def abort(self):
        self._abort = True
        if self._runner: self._runner.abort()

    def pause(self):
        self._paused = True
        if self._runner: self._runner.pause()

    def resume(self):
        self._paused = False
        if self._runner: self._runner.resume()

    def is_paused(self):
        if self._runner: return self._runner.is_paused()
        return self._paused

    def run(self):
        try:
            self._run_list()
        except Exception:
            self.error_msg.emit(traceback.format_exc())

    # ── Field flip (polarity reversal between cycles) ─────────────────────────
    def _flip_field(self, mag_p, mag_cur, mag_fld):
        """Reverse the magnet current and wait for the field to settle.

        Read/write failures are retried 3×; if still failing the scanlist
        auto-pauses on the spot — running the next cycle at the wrong
        polarity would corrupt the pos/neg analysis grouping.  On Resume
        the flip is retried from scratch.
        """
        while not self._abort:
            cur_val = err = None
            for _ in range(3):
                cur_val, err = safe_read(mag_p, mag_cur)
                if cur_val is not None:
                    break
                time.sleep(1.0)
            if cur_val is None:
                self._pause_on_flip_error(f"field flip read failed: {err}")
                continue
            if abs(cur_val) <= 1e-6:
                return   # nothing to flip
            for _ in range(3):
                err = safe_write(mag_p, mag_cur, -cur_val)
                if not err:
                    break
                time.sleep(1.0)
            if err:
                self._pause_on_flip_error(f"field flip write failed: {err}")
                continue
            self.log_msg.emit(f"Field flipped: {cur_val:.4f} A → {-cur_val:.4f} A")
            self._wait_field_settled(mag_p, mag_fld)
            return

    def _pause_on_flip_error(self, msg: str):
        self.log_msg.emit(f"⚠ {msg} — pausing scanlist")
        self.status_msg.emit("⚠ AUTO-PAUSED — field flip failed; "
                             "fix the magnet and press Resume")
        self.pause()
        while self.is_paused() and not self._abort:
            time.sleep(0.1)
        if not self._abort:
            self.log_msg.emit("  ↩ Resuming — retrying field flip…")

    def _wait_field_settled(self, mag_p, mag_fld):
        """Wait until the field stops changing (rate-of-change settles to ~0).

        No target value is assumed — just wait for |Δfield/0.5s| to drop
        below field_settle_rate (default 2 mT equivalent).
        """
        rate_thr = self.setup.get("field_settle_rate",    2.0)
        timeout  = self.setup.get("field_settle_timeout", 300.0)
        t_flip   = time.time()
        last_log = t_flip
        prev_fv, _ = safe_read(mag_p, mag_fld)
        self.log_msg.emit(f"  Settling field (threshold {rate_thr} /0.5s)…")
        time.sleep(0.5)
        while not self._abort:
            while self._paused and not self._abort:
                time.sleep(0.1)
            elapsed = time.time() - t_flip
            if elapsed > timeout:
                self.log_msg.emit(f"⚠ Field settle timeout after {timeout:.0f} s")
                return
            fv, ferr = safe_read(mag_p, mag_fld)
            if ferr or fv is None or prev_fv is None:
                time.sleep(0.5); prev_fv = fv; continue
            rate = abs(fv - prev_fv)   # change over last 0.5 s
            if time.time() - last_log >= 10.0:
                self.log_msg.emit(f"  Waiting for field: {fv:+.4f}  "
                                  f"(Δ={rate:.4f}/0.5s, {elapsed:.0f} s)")
                last_log = time.time()
            if rate <= rate_thr:
                self.log_msg.emit(f"Field settled: {fv:+.4f}  "
                                  f"(Δ={rate:.4f}/0.5s, {elapsed:.1f} s)")
                return
            prev_fv = fv
            time.sleep(0.5)

    def _run_list(self):
        # Polarity devices must be real connections: a cached SimProxy would
        # silently accept the relay/field flips and the whole scanlist would
        # be recorded with wrong polarity bookkeeping.  fresh_proxy bypasses
        # the cache; the guard below refuses to start if a flip is enabled
        # but its device is unreachable.
        relay_p, relay_err = fresh_proxy(self.setup.get("relay_device", ""))

        # Field flip device selection:
        # - samba_main: magnet_device (Beckhoff), write current (A), read field (T)
        # - Cryo: magnet_device is empty → fall back to attodry_device,
        #   write/read MagneticField (T) directly
        _mag_dev = self.setup.get("magnet_device", "")
        if _mag_dev:
            mag_cur = self.setup.get("magnet_current_attr", "current_polar")
            mag_fld = self.setup.get("magnet_field_attr",   "field_polar_corr")
        else:
            _mag_dev = self.setup.get("attodry_device", "")
            mag_cur  = self.setup.get("attodry_attr_field_set", "MagneticField")
            mag_fld  = self.setup.get("attodry_attr_field_rb",  "MagneticField")
        mag_p, mag_err = fresh_proxy(_mag_dev)

        if TANGO_AVAILABLE:
            problems = []
            if self.relay_flip and is_sim_proxy(relay_p):
                problems.append(f"relay '{self.setup.get('relay_device', '')}'"
                                f" — {relay_err}")
            if self.field_flip and is_sim_proxy(mag_p):
                problems.append(f"magnet '{_mag_dev}' — {mag_err}")
            if problems:
                msg = ("Scanlist not started — polarity device(s) unreachable:\n  "
                       + "\n  ".join(problems))
                self.log_msg.emit(f"✗ {msg}")
                self.error_msg.emit(msg)
                self.scan_aborted.emit()
                return

        relay_attr = self.setup.get("relay_attr", "switchvar")
        try:
            self._relay_state = int(relay_p.read_attribute(relay_attr).value)
            self.relay_changed.emit(self._relay_state)
        except Exception:
            self._relay_state = 0

        base     = os.path.expanduser(self.setup.get("save_dir", "~/moke_data"))
        # Place ScanLists alongside the data dir (not inside it).
        # If save_dir is ~/moke_data/Data_Samba_Green, put scanlists in
        # ~/moke_data/ScanLists_Green.  Fall back to <save_dir>/ScanLists.
        if self.setup_name:
            parent   = os.path.dirname(base.rstrip(os.sep))
            sl_dir   = os.path.join(parent, f"ScanLists_{self.setup_name}")
        else:
            sl_dir   = os.path.join(base, "ScanLists")
        os.makedirs(sl_dir, exist_ok=True)
        # list_name already begins with a YYYYMMDD date (from build_scan_name);
        # no time suffix by request.  If the same scanlist name is run again
        # the same day, a _2/_3… suffix avoids overwriting the earlier list.
        txt_path = os.path.join(sl_dir, f"{self.list_name}.txt")
        _n = 2
        while os.path.exists(txt_path):
            txt_path = os.path.join(sl_dir, f"{self.list_name}_{_n}.txt")
            _n += 1

        results = []
        scan_idx = 0   # global counter across all cycles × directions
        for i in range(self.n_scans):
            if self._abort: break
            # ── Field flip ────────────────────────────────────────────────────
            # Skip the flip on cycle 0; flipping starts from cycle 1 onward.
            # Flip happens once per cycle, BEFORE trace AND retrace.
            if self.field_flip and i > 0:
                self._flip_field(mag_p, mag_cur, mag_fld)
                if self._abort: break

            # Field value recorded into the scanlist txt — the analysis
            # pipeline derives polarity from sign(field_T), so a 0.0
            # fallback would silently corrupt the pos/neg grouping.
            # Record NaN instead so a failed readback is visible downstream.
            field_T, verr = float('nan'), None
            for _ in range(3):
                v, verr = safe_read(mag_p, mag_fld)
                if v is not None:
                    field_T = v; break
                time.sleep(1.0)
            if field_T != field_T:   # NaN check
                self.log_msg.emit(
                    f"⚠ Field readback failed ({verr}) — recording NaN field "
                    f"for cycle {i+1} (check polarity grouping in analysis)")

            # ── Run all directions in this cycle (trace, then retrace if present) ──
            for sc_template in self.cfg_list:
                if self._abort: break

                name = sc_template.get("name", "")
                if   name.endswith("_trace"):   dir_lbl = " [trace]"
                elif name.endswith("_retrace"): dir_lbl = " [retrace]"
                else:                           dir_lbl = ""

                self.status_msg.emit(
                    f"Cycle {i+1}/{self.n_scans}{dir_lbl}  "
                    f"relay={'1(−1)' if self._relay_state else '0(+1)'}  "
                    f"field={field_T:+.3f} T")

                try:
                    relay_p.write_attribute(relay_attr, self._relay_state)
                    self.relay_changed.emit(self._relay_state)
                except Exception as e:
                    self.log_msg.emit(f"⚠ relay: {e}")

                sc = copy.deepcopy(sc_template)
                self._runner = ScanRunner(sc, self.setup)
                fn = self._runner.run({
                    'point':    self.point_done.emit,
                    'progress': self.progress.emit,
                    'status':   self.status_msg.emit,
                    'log':      self.log_msg.emit,
                })

                relay_sign = +1 if self._relay_state == 0 else -1
                if fn and self._abort:
                    # Aborted mid-scan: the partial HDF5 exists (marked
                    # "aborted") but must NOT be recorded in the scanlist —
                    # the analysis would average the truncated scan in.
                    self.log_msg.emit(
                        f"Aborted scan not recorded in scanlist: {fn}")
                elif fn:
                    results.append((fn, relay_sign, field_T))
                    self.scan_done.emit(scan_idx, fn)
                scan_idx += 1
                self.cycle_done.emit(i)   # reset live display between directions

            self.list_progress.emit(i + 1, self.n_scans)
            if self.relay_flip: self._relay_state = 1 - self._relay_state

        # Auto-demagnetize after scanlist — disabled for superconducting magnets
        # (set "demagnetize_after_scan": false in setup to suppress)
        if self.field_flip and self.setup.get("demagnetize_after_scan", True):
            self.log_msg.emit("Auto-demagnetizing magnet after scanlist…")
            demagnetize_magnet(mag_p, mag_cur,
                               log_fn=lambda m: self.log_msg.emit(m))

        if results:
            with open(txt_path, "w") as f:
                f.write(f"# Scanlist: {self.list_name}  {datetime.now().isoformat()}\n")
                f.write("# path\trelay_sign\tfield_T\n")
                for fn, rs, fT in results:
                    f.write(f"{fn}\t{rs:+d}\t{fT:.6f}\n")
            self.all_done.emit(txt_path)
        else:
            self.scan_aborted.emit()
