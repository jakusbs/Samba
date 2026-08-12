"""
core/current_sweep_ui.py — Qt half of the current sweep.

CurrentSweepGroup   the "Current sweep" group box on the Scanlist tab
ThermalSettleWorker the between-currents wait (fixed time or focus plateau)

The sequencing itself lives in the main windows, because starting a scanlist
is app-specific; everything reusable is here or in core/current_sweep.py.
"""
import time
from typing import List, Optional

from PyQt6.QtWidgets import (
    QHBoxLayout, QGridLayout,
    QLabel, QGroupBox, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from hardware import fresh_proxy, is_sim_proxy, trigger_and_read, TANGO_AVAILABLE
from nstep import NStepPair
from current_sweep import (SETTLE_FIXED, SETTLE_PLATEAU, MAX_CURRENTS,
                           PlateauDetector, build_current_list,
                           format_current_list, settle_estimate_s, fmt_hms)


class _NoScrollSpin(QSpinBox):
    def wheelEvent(self, event): event.ignore()


class _NoScrollDouble(QDoubleSpinBox):
    def wheelEvent(self, event): event.ignore()


class _NoScrollCombo(QComboBox):
    def wheelEvent(self, event): event.ignore()


# ─────────────────────────────────────────────────────────────────────────────
# Thermal settle worker
# ─────────────────────────────────────────────────────────────────────────────
class ThermalSettleWorker(QThread):
    """Wait for the sample to reach thermal equilibrium at a new current.

    Two modes (see core/current_sweep.py):

    fixed    sleep for a set time.
    plateau  poll the focus diode at the current focus position and stop once
             the reading stops drifting — bounded below by a minimum wait and
             above by a maximum.

    Pause does NOT stop the clock: the sample keeps warming whether or not
    the operator is watching, so pausing during a settle would only make the
    recorded wait a lie.  Instead the worker holds at the END of the settle
    while paused, so the refocus and the scanlist do not start.
    """

    sample     = pyqtSignal(float, float)   # elapsed_s, FL value
    status_msg = pyqtSignal(str)
    log_msg    = pyqtSignal(str)
    done_      = pyqtSignal(str)            # fixed | plateau | timeout | abort

    _TICK_S = 0.5

    def __init__(self, mode: str, fixed_min: float,
                 fl_dev: str = "", fl_attr: str = "Value",
                 detector: Optional[PlateauDetector] = None,
                 poll_s: float = 5.0, label: str = ""):
        super().__init__()
        self._mode      = mode
        self._fixed_s   = max(0.0, float(fixed_min) * 60.0)
        self._fl_dev    = fl_dev
        self._fl_attr   = (fl_attr or "Value").strip() or "Value"
        self._det       = detector or PlateauDetector()
        # The detector needs at least min_samples inside its trailing window
        # before it will judge anything, so the poll interval has to be a small
        # fraction of that window — otherwise it reports "collecting" forever
        # and every settle runs to its maximum wait.  Floored at 0.5 s so a
        # short window cannot turn into hammering the focus device.
        self._poll_s    = max(0.5, min(float(poll_s), self._det.window_s / 6.0))
        self._label     = label
        self._abort     = False
        self._paused    = False

    # ── control ──────────────────────────────────────────────────────────────
    def abort(self):     self._abort = True
    def pause(self):     self._paused = True
    def resume(self):    self._paused = False
    def is_paused(self): return self._paused

    def _sleep(self, seconds: float) -> bool:
        """Interruptible sleep.  False if aborted."""
        end = time.time() + seconds
        while time.time() < end:
            if self._abort:
                return False
            time.sleep(min(self._TICK_S, max(0.0, end - time.time())))
        return not self._abort

    def _hold_while_paused(self):
        if not self._paused:
            return
        self.status_msg.emit("Thermal settle complete — paused")
        while self._paused and not self._abort:
            time.sleep(0.1)

    # ── run ──────────────────────────────────────────────────────────────────
    def run(self):
        try:
            reason = (self._run_plateau() if self._mode == SETTLE_PLATEAU
                      else self._run_fixed())
        except Exception as e:                      # never strand the sweep
            self.log_msg.emit(f"⚠ Thermal settle failed ({e}) — continuing")
            reason = "error"
        if self._abort:
            reason = "abort"
        else:
            self._hold_while_paused()
            if self._abort:
                reason = "abort"
        self.done_.emit(reason)

    def _run_fixed(self) -> str:
        total = self._fixed_s
        self.log_msg.emit(f"Thermal settle {self._label}: waiting "
                          f"{fmt_hms(total)} for the sample to stabilise…")
        t0 = time.time()
        while not self._abort:
            elapsed = time.time() - t0
            if elapsed >= total:
                break
            self.status_msg.emit(
                f"Thermal settle {self._label}: {fmt_hms(elapsed)} / "
                f"{fmt_hms(total)}")
            if not self._sleep(min(self._TICK_S * 2, total - elapsed)):
                break
        return "abort" if self._abort else "fixed"

    def _run_plateau(self) -> str:
        fl_p, err = fresh_proxy(self._fl_dev)
        if err or (TANGO_AVAILABLE and is_sim_proxy(fl_p)):
            self.log_msg.emit(
                f"⚠ Focus sensor '{self._fl_dev}' unavailable ({err or 'simulated'}) "
                f"— falling back to the maximum wait "
                f"({fmt_hms(self._det.max_wait_s)})")
            self._fixed_s = self._det.max_wait_s
            return self._run_fixed()

        self.log_msg.emit(
            f"Thermal settle {self._label}: watching the focus signal "
            f"(drift < {self._det.tol:.3g} %/min, "
            f"{fmt_hms(self._det.min_wait_s)}–{fmt_hms(self._det.max_wait_s)})…")
        t0        = time.time()
        last_log  = t0
        fails     = 0
        while not self._abort:
            now = time.time()
            # Independent of the detector: a first read that never succeeds
            # would leave the detector empty and its own timeout unreachable.
            if now - t0 >= self._det.max_wait_s:
                self.log_msg.emit(
                    f"Thermal settle {self._label}: maximum wait reached "
                    f"({fmt_hms(now - t0)}) — proceeding")
                return "timeout"

            val, rerr = trigger_and_read(fl_p, self._fl_attr)
            if val is None:
                fails += 1
                # Warn once early and once more if it never recovers; the loop
                # keeps going either way and ends at the maximum wait.
                if fails in (10, 60):
                    self.log_msg.emit(
                        f"⚠ Focus signal unreadable ({rerr}) — the settle will "
                        f"run to its maximum wait")
            else:
                fails = 0
                if self._det.add(now, val):
                    self.sample.emit(now - t0, float(val))

            st = self._det.state(now)
            rate = st.get("rate")
            rate_s = "—" if rate is None else f"{rate:+.2f} %/min"
            self.status_msg.emit(
                f"Thermal settle {self._label}: {fmt_hms(st['elapsed'])} · "
                f"drift {rate_s} · {st['reason']}")
            if now - last_log >= 60.0:
                self.log_msg.emit(
                    f"  settling {fmt_hms(st['elapsed'])} · drift {rate_s} "
                    f"· {st['reason']}")
                last_log = now
            if st["settled"]:
                self.log_msg.emit(
                    f"Thermal settle {self._label}: {st['reason']} after "
                    f"{fmt_hms(st['elapsed'])} (drift {rate_s})")
                return st["reason"]
            if not self._sleep(self._poll_s):
                break
        return "abort"


# ─────────────────────────────────────────────────────────────────────────────
# UI group
# ─────────────────────────────────────────────────────────────────────────────
class CurrentSweepGroup(QGroupBox):
    """Repeat the whole scanlist at a series of excitation currents.

    Checkable: unchecked (the default) means the Start button runs one plain
    scanlist exactly as before.
    """

    changed = pyqtSignal()

    # The group's own checkbox is the enable switch (it also disables every
    # child for free), but the default indicator is a 14 px square that reads
    # as decoration — operators did not find it.  Scoped by objectName so the
    # style cannot cascade to other group boxes (§16).
    _STYLE = """
    QGroupBox#cur_sweep_grp {{
        border:2px solid {edge}; border-radius:6px;
        margin-top:9px; padding-top:9px; font-weight:bold; color:{fg};
    }}
    QGroupBox#cur_sweep_grp::title {{
        subcontrol-origin:margin; left:10px; padding:0 6px; font-size:13px;
    }}
    QGroupBox#cur_sweep_grp::indicator {{
        width:18px; height:18px; border-radius:4px;
        border:2px solid {edge}; background:#313244;
    }}
    QGroupBox#cur_sweep_grp::indicator:checked {{
        background:{fg}; border:2px solid {fg};
    }}
    QGroupBox#cur_sweep_grp::indicator:hover {{ border:2px solid {fg}; }}
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cur_sweep_grp")
        self.setCheckable(True)
        self.setChecked(False)
        # A checkable QGroupBox defaults to StrongFocus, so clicking anywhere
        # on its background hands it the keyboard focus — and then a Space (or
        # Return, depending on the style) toggles it off, which disables every
        # field inside and leaves the operator unable to type.  It is a
        # container, not an input: the switch belongs to the indicator and the
        # title, both of which still work by mouse.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._loading = False        # see §67 — load() must never echo back
        self.setToolTip(
            "Tick to repeat the whole scanlist at each current in the series "
            "below,\nwaiting for the sample to thermalise and refocusing "
            "between them.")

        g = QGridLayout(self); g.setSpacing(4); g.setContentsMargins(8, 4, 8, 4)

        def _dbl(lo, hi, dec, val, suffix, width=86):
            w = _NoScrollDouble(); w.setRange(lo, hi); w.setDecimals(dec)
            w.setValue(val); w.setSuffix(suffix); w.setMinimumWidth(width)
            w.valueChanged.connect(self._emit_changed)
            return w

        # ── column 0/1: the current list ────────────────────────────────────
        g.addWidget(QLabel("Start:"), 0, 0)
        self.start_spin = _dbl(-105, 105, 4, 5.0, " mA")
        g.addWidget(self.start_spin, 0, 1)

        g.addWidget(QLabel("Stop:"), 2, 0)
        self.stop_spin = _dbl(-105, 105, 4, 15.0, " mA")
        g.addWidget(self.stop_spin, 2, 1)

        g.addWidget(QLabel("N:"), 4, 0)
        n_row = QHBoxLayout(); n_row.setSpacing(4)
        self.n_spin = _NoScrollSpin()
        self.n_spin.setRange(2, MAX_CURRENTS); self.n_spin.setValue(3)
        self.n_spin.setMinimumWidth(48)
        self.n_spin.valueChanged.connect(self._emit_changed)
        n_row.addWidget(self.n_spin)
        n_row.addWidget(QLabel("Δ:"))
        self.step_spin = _dbl(0.0001, 210, 4, 5.0, " mA", width=80)
        n_row.addWidget(self.step_spin)
        g.addLayout(n_row, 4, 1)

        # N ↔ Δ coupling, same idiom as the scan ranges (§50): both boxes stay
        # visible and editable, the step is the anchor across span changes.
        self._pair = NStepPair(self.n_spin, self.step_spin,
                               lambda: self.stop_spin.value() - self.start_spin.value(),
                               min_step=1e-4)
        self.start_spin.valueChanged.connect(self._pair.span_changed)
        self.stop_spin.valueChanged.connect(self._pair.span_changed)

        # ── column 2/3: settle strategy ─────────────────────────────────────
        g.addWidget(QLabel("Settle:"), 0, 2)
        self.mode_combo = _NoScrollCombo()
        self.mode_combo.addItem("Wait a fixed time", SETTLE_FIXED)
        self.mode_combo.addItem("Watch focus signal", SETTLE_PLATEAU)
        self.mode_combo.setMinimumWidth(150)
        self.mode_combo.setToolTip(
            "How long to wait after changing the current before refocusing.\n"
            "Watch focus signal: poll the focus diode and continue once it\n"
            "stops drifting, bounded by the minimum and maximum below.")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        g.addWidget(self.mode_combo, 0, 3)

        self._wait_lbl = QLabel("Wait:")
        g.addWidget(self._wait_lbl, 2, 2)
        self.fixed_spin = _dbl(0.0, 600, 1, 10.0, " min")
        g.addWidget(self.fixed_spin, 2, 3)

        self._span_lbl = QLabel("Min / max:")
        g.addWidget(self._span_lbl, 4, 2)
        span_row = QHBoxLayout(); span_row.setSpacing(6)
        self.pl_min_spin = _dbl(0.0, 600, 1, 3.0, " min", width=78)
        self.pl_min_spin.setToolTip(
            "Never continue before this.  Right after a refocus the focus\n"
            "signal sits on its maximum, where it is first-order insensitive\n"
            "to defocus — without this bound the plateau test would report\n"
            "'settled' while the stage is still drifting.")
        self.pl_max_spin = _dbl(0.1, 600, 1, 20.0, " min", width=78)
        self.pl_max_spin.setToolTip("Give up waiting and refocus anyway.")
        span_row.addWidget(self.pl_min_spin, 1); span_row.addWidget(self.pl_max_spin, 1)
        g.addLayout(span_row, 4, 3)

        self._drift_lbl = QLabel("Drift:")
        g.addWidget(self._drift_lbl, 6, 2)
        self.drift_spin = _dbl(0.01, 100, 2, 0.5, " %/min")
        self.drift_spin.setToolTip(
            "Continue once the focus signal changes by less than this, "
            "measured as a slope over a 60 s window.")
        g.addWidget(self.drift_spin, 6, 3)

        # ── options ─────────────────────────────────────────────────────────
        def _cb(text, checked, tip=""):
            w = QCheckBox(text); w.setChecked(checked)
            if tip: w.setToolTip(tip)
            w.toggled.connect(self._emit_changed)
            return w

        self.refocus_cb = _cb(
            "Refocus", True,
            "Run the Calibration tab's autofocus after each settle, with the\n"
            "scan axis parked at the focus position (0 = middle of the device).")
        self.pause_bad_cb = _cb(
            "Pause if focus unreliable", False,
            "Hold the sweep when the autofocus reports an endpoint or a noise\n"
            "peak instead of a real maximum.  Off: log a warning and carry on.")

        self.auto_range_cb = _cb(
            "Auto range", True,
            "Pick the smallest Keithley range that can source each current.\n"
            "Off: the range stays as set and currents beyond it are refused.")
        self.off_at_end_cb = _cb(
            "Output off at end", True,
            "Turn the current source off when the sweep finishes, instead of\n"
            "leaving the last (usually largest) current running.")
        # All four on one row: the box is well over 1500 px wide, and a second
        # checkbox row cost ~56 px of the tab's MINIMUM height — which the
        # bottom half of the window cannot spare.
        opts = QHBoxLayout(); opts.setSpacing(14)
        for _w in (self.refocus_cb, self.pause_bad_cb,
                   self.auto_range_cb, self.off_at_end_cb):
            opts.addWidget(_w)
        opts.addStretch()
        g.addLayout(opts, 6, 0, 1, 2)

        # ── summary ─────────────────────────────────────────────────────────
        self.summary_lbl = QLabel("—")
        self.summary_lbl.setWordWrap(True)
        self.summary_lbl.setStyleSheet("color:#a6adc8;font-size:10px;")
        g.addWidget(self.summary_lbl, 8, 0, 1, 4)

        # Field columns share the width.  Extra HEIGHT is shared equally by
        # every row, so a taller tab spreads the form evenly — the way the X/Y
        # axis boxes behave on the Trajectory tab — instead of opening one gap
        # wherever the grid happens to put the slack.
        g.setColumnStretch(1, 3)
        g.setColumnStretch(3, 4)
        # Spinboxes are fixed-height, so a row holding one cannot grow no
        # matter what stretch it is given — the slack would all pile up in the
        # one flexible row.  Content therefore sits on the EVEN rows and the
        # odd rows in between are empty and stretchable, so enlarging the tab
        # opens the gaps evenly instead of leaving a void at the bottom.
        for _r in range(1, 8, 2):
            g.setRowStretch(_r, 1)

        self.toggled.connect(self._emit_changed)
        self.toggled.connect(self._apply_enabled_style)
        self._apply_enabled_style(False)
        self._on_mode_changed()

    # Keys that must never reach QGroupBox's own check-toggling code.
    _SWALLOW_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Select,
                     Qt.Key.Key_Return, Qt.Key.Key_Enter)

    def event(self, ev):
        """Never let a stray Space/Enter switch the sweep off.

        A key a child does not handle propagates to the parent, and QGroupBox
        toggles its own check on Space — so a Space typed in one of the
        spinboxes arrives here, unchecks the group, and disables every field
        the operator was editing.  QGroupBox does this in event() rather than
        keyPressEvent(), so that is the hook that has to be intercepted;
        NoFocus alone is not enough, because propagation does not need focus.
        """
        t = ev.type()
        if t in (ev.Type.KeyPress, ev.Type.KeyRelease) and \
                ev.key() in self._SWALLOW_KEYS:
            ev.accept()
            return True
        return super().event(ev)

    def _apply_enabled_style(self, on: bool):
        """Title and indicator carry the on/off state — green when armed."""
        fg   = "#a6e3a1" if on else "#a6adc8"
        edge = "#a6e3a1" if on else "#585b70"
        self.setTitle("Current sweep — ON" if on else "Current sweep — OFF")
        self.setStyleSheet(self._STYLE.format(fg=fg, edge=edge))

    # ── internals ────────────────────────────────────────────────────────────
    def _emit_changed(self, *_):
        """Single funnel for every signal (§67): a load must stay silent."""
        self._refresh_summary()
        if not self._loading:
            self.changed.emit()

    def _on_mode_changed(self, *_):
        """Disable the fields the selected mode does not use (§60)."""
        plateau = self.mode() == SETTLE_PLATEAU
        for w in (self._wait_lbl, self.fixed_spin):
            w.setEnabled(not plateau)
        for w in (self._span_lbl, self.pl_min_spin, self.pl_max_spin,
                  self._drift_lbl, self.drift_spin):
            w.setEnabled(plateau)
        self._emit_changed()

    def _refresh_summary(self):
        cur = self.currents()
        per = settle_estimate_s(self.mode(), self.fixed_spin.value(),
                                self.pl_min_spin.value(), self.pl_max_spin.value())
        approx = "≈ " if self.mode() == SETTLE_PLATEAU else ""
        self.summary_lbl.setText(
            f"{len(cur)} currents: {format_current_list(cur)}   ·   "
            f"settle {approx}{fmt_hms(per)} each "
            f"({approx}{fmt_hms(per * len(cur))} total)")

    # ── public API ───────────────────────────────────────────────────────────
    def mode(self) -> str:
        return self.mode_combo.currentData() or SETTLE_FIXED

    def currents(self) -> List[float]:
        return build_current_list(self.start_spin.value(),
                                  self.stop_spin.value(),
                                  self.n_spin.value())

    def settle_estimate_s(self) -> float:
        """Per-current settle time, for the pre-scan estimate."""
        return settle_estimate_s(self.mode(), self.fixed_spin.value(),
                                 self.pl_min_spin.value(),
                                 self.pl_max_spin.value())

    def make_detector(self) -> PlateauDetector:
        return PlateauDetector(window_s=60.0,
                               tol_pct_per_min=self.drift_spin.value(),
                               min_wait_s=self.pl_min_spin.value() * 60.0,
                               max_wait_s=self.pl_max_spin.value() * 60.0)

    def get_values(self) -> dict:
        return {
            "cursweep_enabled":       self.isChecked(),
            "cursweep_start_mA":      self.start_spin.value(),
            "cursweep_stop_mA":       self.stop_spin.value(),
            "cursweep_npts":          self.n_spin.value(),
            "cursweep_step_mA":       self.step_spin.value(),
            "cursweep_settle_mode":   self.mode(),
            "cursweep_fixed_min":     self.fixed_spin.value(),
            "cursweep_plateau_min":   self.pl_min_spin.value(),
            "cursweep_plateau_max":   self.pl_max_spin.value(),
            "cursweep_drift_pct_min": self.drift_spin.value(),
            "cursweep_refocus":       self.refocus_cb.isChecked(),
            "cursweep_pause_bad_focus": self.pause_bad_cb.isChecked(),
            "cursweep_auto_range":    self.auto_range_cb.isChecked(),
            "cursweep_output_off_end": self.off_at_end_cb.isChecked(),
        }

    def load_values(self, cfg: dict):
        """Restore from a scan config without emitting `changed`."""
        self._loading = True
        try:
            self.setChecked(bool(cfg.get("cursweep_enabled", False)))
            self.start_spin.setValue(float(cfg.get("cursweep_start_mA", 5.0)))
            self.stop_spin.setValue(float(cfg.get("cursweep_stop_mA", 15.0)))
            # N last, then re-derive the step: set_npts resets the anchor to
            # the step, matching every other N/Δ pair in the app.
            self._pair.set_npts(int(cfg.get("cursweep_npts", 3)))
            mode = cfg.get("cursweep_settle_mode", SETTLE_FIXED)
            idx = self.mode_combo.findData(mode)
            self.mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.fixed_spin.setValue(float(cfg.get("cursweep_fixed_min", 10.0)))
            self.pl_min_spin.setValue(float(cfg.get("cursweep_plateau_min", 3.0)))
            self.pl_max_spin.setValue(float(cfg.get("cursweep_plateau_max", 20.0)))
            self.drift_spin.setValue(float(cfg.get("cursweep_drift_pct_min", 0.5)))
            self.refocus_cb.setChecked(bool(cfg.get("cursweep_refocus", True)))
            self.pause_bad_cb.setChecked(
                bool(cfg.get("cursweep_pause_bad_focus", False)))
            self.auto_range_cb.setChecked(
                bool(cfg.get("cursweep_auto_range", True)))
            self.off_at_end_cb.setChecked(
                bool(cfg.get("cursweep_output_off_end", True)))
            self._on_mode_changed()
        finally:
            self._loading = False
        self._refresh_summary()
