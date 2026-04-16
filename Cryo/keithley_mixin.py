"""
keithley_mixin.py — Samba v3
Shared Keithley 6221 current-source UI builder and control methods.

Used by both HardwarePanel (standard) and CryoHardwarePanel (cryo fork)
to avoid duplicating ~80 lines of identical code.
"""
import logging
import threading
from typing import Optional

from PyQt6.QtWidgets import (
    QGroupBox, QGridLayout, QLabel, QPushButton, QDoubleSpinBox, QAbstractSpinBox,
)
from PyQt6.QtCore import Qt, QTimer

from hardware import fresh_proxy, is_sim_proxy, safe_read, safe_write
from config import KEITHLEY_RANGES
from panels import NoScrollComboBox

log = logging.getLogger(__name__)


def _make_spin(lo: float, hi: float, dec: int, suffix: str,
               width: int, on_enter):
    """Create a QDoubleSpinBox that selects-all on focus and fires on_enter on Return."""
    class QuickSpin(QDoubleSpinBox):
        def focusInEvent(self, ev):
            super().focusInEvent(ev)
            self.selectAll()
        def keyPressEvent(self, ev):
            super().keyPressEvent(ev)
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                on_enter()
        def wheelEvent(self, ev):
            ev.ignore()
    w = QuickSpin()
    w.setRange(lo, hi); w.setDecimals(dec); w.setSuffix(suffix)
    w.setFixedWidth(width)
    w.setStepType(QAbstractSpinBox.StepType.AdaptiveDecimalStepType)
    return w


# ─── Status label helpers ─────────────────────────────────────────────────
def set_ok(lbl: QLabel, msg: str):
    lbl.setText(f"✓ {msg}"); lbl.setStyleSheet("color:#a6e3a1;font-size:10px;")

def set_err(lbl: QLabel, msg: str):
    lbl.setText(f"⚠ {msg}"); lbl.setStyleSheet("color:#f38ba8;font-size:10px;font-weight:bold;")

def set_sim(lbl: QLabel):
    lbl.setText("⚠ Simulation"); lbl.setStyleSheet("color:#fab387;font-size:10px;")


# ─── Keithley UI builder ─────────────────────────────────────────────────
def build_keithley_group(owner) -> QGroupBox:
    """
    Build the Current Source group box and attach widgets to *owner*:
      owner.range_combo, owner.compl_spin, owner.amp_spin,
      owner.freq_spin, owner.current_rb, owner.ks_status, owner.ks_dev_lbl

    Returns the QGroupBox ready to be added to a layout.
    """
    cs = QGroupBox("Current Source")
    csg = QGridLayout(cs)
    csg.setSpacing(3); csg.setContentsMargins(6, 6, 6, 6)

    row = 0
    owner.ks_dev_lbl = QLabel("—")
    owner.ks_dev_lbl.setStyleSheet("color:#6c7086;font-size:9px;")
    csg.addWidget(owner.ks_dev_lbl, row, 0, 1, 6); row += 1

    # Row 1: Range + Compliance
    csg.addWidget(QLabel("Range:"), row, 0)
    owner.range_combo = NoScrollComboBox()
    owner.range_combo.addItems(KEITHLEY_RANGES)
    owner.range_combo.setFixedWidth(70)
    csg.addWidget(owner.range_combo, row, 1)
    btn_range = QPushButton("Set"); btn_range.setFixedWidth(30)
    btn_range.clicked.connect(owner._write_range)
    csg.addWidget(btn_range, row, 2)
    csg.addWidget(QLabel("Compl:"), row, 3)
    owner.compl_spin = _make_spin(0, 105, 2, " V", 80, owner._write_compliance)
    owner.compl_spin.setValue(1.0)
    csg.addWidget(owner.compl_spin, row, 4, 1, 2); row += 1

    # Row 2: Amplitude + Frequency
    csg.addWidget(QLabel("Ampl:"), row, 0)
    owner.amp_spin = _make_spin(-105, 105, 4, " mA", 100, owner._write_amplitude)
    csg.addWidget(owner.amp_spin, row, 1, 1, 2)
    csg.addWidget(QLabel("Freq:"), row, 3)
    owner.freq_spin = _make_spin(0.001, 1e6, 3, " Hz", 100, owner._write_frequency)
    owner.freq_spin.setValue(100.0)
    csg.addWidget(owner.freq_spin, row, 4, 1, 2); row += 1

    # Row 3: I out + Read
    csg.addWidget(QLabel("I out:"), row, 0)
    owner.current_rb = QLabel("—")
    owner.current_rb.setStyleSheet("color:#a6e3a1;font-weight:bold;font-size:11px;")
    csg.addWidget(owner.current_rb, row, 1, 1, 2)
    btn_read = QPushButton("🔄 Read"); btn_read.clicked.connect(owner._read_keithley)
    owner._btn_ks_read = btn_read   # saved so set_scan_running() can disable it
    csg.addWidget(btn_read, row, 3, 1, 3); row += 1

    owner.ks_status = QLabel("")
    owner.ks_status.setWordWrap(True)
    owner.ks_status.setStyleSheet("font-size:9px;")
    csg.addWidget(owner.ks_status, row, 0, 1, 6)

    return cs


# ─── Keithley read / write methods ───────────────────────────────────────
class KeithleyMixin:
    """
    Provides _read_keithley, _write_range, _write_amplitude,
    _write_compliance, _write_frequency.

    The host class must have:
      - _setup() returning the setup dict (with 'keithley_device')
      - _update_dev_labels()
      - ks_status, ks_dev_lbl (QLabels)
      - range_combo, amp_spin, freq_spin, compl_spin, current_rb (widgets)
    """

    def _read_keithley(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        self._update_dev_labels()
        self.ks_status.setText("Reading…")

        amp_a = s.get("keithley_attr_amplitude",  "amplitude")
        frq_a = s.get("keithley_attr_frequency",  "frequency")
        cpl_a = s.get("keithley_attr_compliance", "compliance")
        cur_a = s.get("keithley_attr_current",    "current")

        def _do():
            p, conn_err = fresh_proxy(dev)
            if conn_err:
                QTimer.singleShot(0, self, lambda: set_err(self.ks_status, conn_err))
                return
            amp, e1 = safe_read(p, amp_a)
            frq, e2 = safe_read(p, frq_a)
            cpl, e3 = safe_read(p, cpl_a)
            cur, e4 = safe_read(p, cur_a)
            errs = [e for e in [e1, e2] if e]
            if errs:
                QTimer.singleShot(0, self, lambda: set_err(self.ks_status, errs[0][:60]))
                return
            QTimer.singleShot(0, self, lambda: self._apply_keithley_readback(amp, frq, cpl, cur))

        threading.Thread(target=_do, daemon=True).start()

    def _apply_keithley_readback(self, amp, frq, cpl, cur):
        if amp is not None: self.amp_spin.setValue(amp)
        if frq is not None: self.freq_spin.setValue(frq)
        if cpl is not None: self.compl_spin.setValue(cpl)
        if cur is not None:
            self.current_rb.setText(f"{cur:.4g} mA")
        else:
            self.current_rb.setText("— mA")
        parts = []
        if amp is not None: parts.append(f"amp={amp:.4g}")
        if frq is not None: parts.append(f"freq={frq:.4g}")
        if cpl is not None: parts.append(f"compl={cpl:.2f}V")
        set_ok(self.ks_status, "  ".join(parts))

    def _write_range(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err: set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p): set_sim(self.ks_status); return
        rng_a = s.get("keithley_attr_range", "range")
        err = safe_write(p, rng_a, self.range_combo.currentText())
        if err: set_err(self.ks_status, err[:60])
        else:   set_ok(self.ks_status, f"range → {self.range_combo.currentText()}")

    def _write_amplitude(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err: set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p): set_sim(self.ks_status); return
        amp_a = s.get("keithley_attr_amplitude", "amplitude")
        val   = self.amp_spin.value()
        if abs(val) < 1e-9:
            try:
                p.command_inout("Off")
                set_ok(self.ks_status, "Output OFF (amplitude = 0)")
            except Exception:
                err = safe_write(p, amp_a, 0.0)
                if err: set_err(self.ks_status, err[:60])
                else:   set_ok(self.ks_status, "amp → 0 mA")
        else:
            try:
                p.command_inout("On")
            except Exception:
                log.debug("Keithley 'On' command unavailable or already on")
            err = safe_write(p, amp_a, val)
            if err: set_err(self.ks_status, err[:60])
            else:   set_ok(self.ks_status, f"amp → {val:.4g} mA")

    def _write_compliance(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err: set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p): set_sim(self.ks_status); return
        cpl_a = s.get("keithley_attr_compliance", "compliance")
        err = safe_write(p, cpl_a, self.compl_spin.value())
        if err: set_err(self.ks_status, err[:60])
        else:   set_ok(self.ks_status, f"compliance → {self.compl_spin.value():.2f} V")

    def _write_frequency(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err: set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p): set_sim(self.ks_status); return
        frq_a = s.get("keithley_attr_frequency", "frequency")
        err = safe_write(p, frq_a, self.freq_spin.value())
        if err: set_err(self.ks_status, err[:60])
        else:   set_ok(self.ks_status, f"freq → {self.freq_spin.value():.4g} Hz")
