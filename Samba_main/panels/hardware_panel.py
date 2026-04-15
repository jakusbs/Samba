"""
panels/hardware_panel.py — Samba v3
HardwarePanel — current source, field/relay controls.
"""
from typing import List

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QDoubleSpinBox, QGroupBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractSpinBox

from config import KEITHLEY_RANGES
from hardware import fresh_proxy, is_sim_proxy, safe_write, safe_read, demagnetize_magnet
from panels._widgets import NoScrollComboBox, NoScrollDoubleSpinBox


class HardwarePanel(QGroupBox):
    def __init__(self, setup_getter, title: str = "Hardware", parent=None):
        super().__init__(title, parent)
        self._setup_getter = setup_getter
        self._relay_state  = 0
        root = QHBoxLayout(self); root.setSpacing(8); root.setContentsMargins(6, 6, 6, 6)

        # ── Lock-in status ────────────────────────────────────────────────────
        li  = QGroupBox("Lock-in"); lig = QGridLayout(li)
        lig.setSpacing(3); lig.setContentsMargins(6, 6, 6, 6)

        row = 0
        self.zi_dev_lbl = QLabel("—")
        self.zi_dev_lbl.setStyleSheet("color:#6c7086;font-size:9px;")
        lig.addWidget(self.zi_dev_lbl, row, 0, 1, 2); row += 1

        lig.addWidget(QLabel("TC:"), row, 0)
        self.zi_tc_lbl = QLabel("—")
        self.zi_tc_lbl.setStyleSheet("color:#cdd6f4;font-weight:bold;")
        lig.addWidget(self.zi_tc_lbl, row, 1); row += 1

        lig.addWidget(QLabel("Order:"), row, 0)
        self.zi_ord_lbl = QLabel("—")
        self.zi_ord_lbl.setStyleSheet("color:#cdd6f4;font-weight:bold;")
        lig.addWidget(self.zi_ord_lbl, row, 1); row += 1

        lig.addWidget(QLabel("Settling:"), row, 0)
        self.zi_set_lbl = QLabel("—")
        self.zi_set_lbl.setStyleSheet("color:#89dceb;font-weight:bold;")
        lig.addWidget(self.zi_set_lbl, row, 1); row += 1

        btn_zi_read = QPushButton("🔄 Read"); btn_zi_read.clicked.connect(self._read_lockin)
        lig.addWidget(btn_zi_read, row, 0, 1, 2); row += 1

        self.zi_status = QLabel("")
        self.zi_status.setWordWrap(True); self.zi_status.setStyleSheet("font-size:9px;")
        lig.addWidget(self.zi_status, row, 0, 1, 2)
        root.addWidget(li)

        # ── Current Source ────────────────────────────────────────────────────
        cs  = QGroupBox("Current Source"); csg = QGridLayout(cs)
        csg.setSpacing(3); csg.setContentsMargins(6, 6, 6, 6)

        row = 0
        self.ks_dev_lbl = QLabel("—")
        self.ks_dev_lbl.setStyleSheet("color:#6c7086;font-size:9px;")
        csg.addWidget(self.ks_dev_lbl, row, 0, 1, 6); row += 1

        # Row 1: Range + Compliance side by side
        csg.addWidget(QLabel("Range:"), row, 0)
        self.range_combo = NoScrollComboBox()
        self.range_combo.addItems(KEITHLEY_RANGES); self.range_combo.setFixedWidth(70)
        csg.addWidget(self.range_combo, row, 1)
        btn_range = QPushButton("Set"); btn_range.setFixedWidth(30)
        btn_range.clicked.connect(self._write_range)
        csg.addWidget(btn_range, row, 2)
        csg.addWidget(QLabel("Compl:"), row, 3)
        self.compl_spin = self._make_spin(0, 105, 2, " V", 80, self._write_compliance)
        self.compl_spin.setValue(1.0)
        csg.addWidget(self.compl_spin, row, 4, 1, 2); row += 1

        # Row 2+: Amplitude, Frequency, I out, Read
        csg.addWidget(QLabel("Ampl:"), row, 0)
        self.amp_spin = self._make_spin(-105, 105, 4, " mA", 100, self._write_amplitude)
        csg.addWidget(self.amp_spin, row, 1, 1, 2)
        csg.addWidget(QLabel("Freq:"), row, 3)
        self.freq_spin = self._make_spin(0.001, 1e6, 3, " Hz", 100, self._write_frequency)
        self.freq_spin.setValue(100.0)
        csg.addWidget(self.freq_spin, row, 4, 1, 2); row += 1

        csg.addWidget(QLabel("I out:"), row, 0)
        self.current_rb = QLabel("—")
        self.current_rb.setStyleSheet("color:#a6e3a1;font-weight:bold;font-size:11px;")
        csg.addWidget(self.current_rb, row, 1, 1, 2)
        btn_read = QPushButton("🔄 Read"); btn_read.clicked.connect(self._read_keithley)
        csg.addWidget(btn_read, row, 3, 1, 3); row += 1

        self.ks_status = QLabel("")
        self.ks_status.setWordWrap(True); self.ks_status.setStyleSheet("font-size:9px;")
        csg.addWidget(self.ks_status, row, 0, 1, 6)
        root.addWidget(cs)

        # ── Right: Field + Relay ──────────────────────────────────────────────
        fr  = QGroupBox("Field & Relay"); frg = QGridLayout(fr)
        frg.setSpacing(3); frg.setContentsMargins(6, 6, 6, 6)

        row = 0
        self.mag_dev_lbl = QLabel("—")
        self.mag_dev_lbl.setStyleSheet("color:#6c7086;font-size:9px;")
        frg.addWidget(self.mag_dev_lbl, row, 0, 1, 3); row += 1

        frg.addWidget(QLabel("Magnet current (A):"), row, 0)
        self.field_spin = self._make_spin(-20, 20, 4, " A", 100, self._write_field)
        frg.addWidget(self.field_spin, row, 1)
        self.zero_field_btn = QPushButton("Zero field")
        self.zero_field_btn.setToolTip("Demagnetize: alternating decay to 0 A")
        self.zero_field_btn.clicked.connect(self._demagnetize)
        frg.addWidget(self.zero_field_btn, row, 2); row += 1

        frg.addWidget(QLabel("Field:"), row, 0)
        self.field_rb = QLabel("— T")
        self.field_rb.setStyleSheet(
            "color:#a6e3a1;font-weight:bold;font-size:13px;"
            "font-family:'Courier New',monospace;")
        frg.addWidget(self.field_rb, row, 1, 1, 2); row += 1

        self.mag_status = QLabel("")
        self.mag_status.setWordWrap(True); self.mag_status.setStyleSheet("font-size:9px;")
        frg.addWidget(self.mag_status, row, 0, 1, 3); row += 1

        frg.addWidget(QLabel("Relay:"), row, 0)
        self.relay_lbl = QLabel("0  (+1)")
        self.relay_lbl.setStyleSheet("color:#a6e3a1;font-weight:bold;")
        frg.addWidget(self.relay_lbl, row, 1)
        self.relay_btn = QPushButton("Toggle"); self.relay_btn.setMinimumWidth(65)
        self.relay_btn.clicked.connect(self._toggle_relay)
        frg.addWidget(self.relay_btn, row, 2); row += 1

        self.relay_status = QLabel("")
        self.relay_status.setWordWrap(True); self.relay_status.setStyleSheet("font-size:9px;")
        frg.addWidget(self.relay_status, row, 0, 1, 3)
        root.addWidget(fr)

    def _setup(self): return self._setup_getter()

    def _set_ok(self, lbl: QLabel, msg: str):
        lbl.setText(f"✓ {msg}"); lbl.setStyleSheet("color:#a6e3a1;font-size:10px;")

    def _set_err(self, lbl: QLabel, msg: str):
        lbl.setText(f"⚠ {msg}"); lbl.setStyleSheet("color:#f38ba8;font-size:10px;font-weight:bold;")

    def _set_sim(self, lbl: QLabel):
        lbl.setText("⚠ Simulation (no real device)"); lbl.setStyleSheet("color:#fab387;font-size:10px;")

    def _update_dev_labels(self):
        s = self._setup()
        self.ks_dev_lbl.setText(s.get("keithley_device", "—") or "—")
        self.mag_dev_lbl.setText(s.get("magnet_device",  "—") or "—")

    @staticmethod
    def _make_spin(lo, hi, dec, suffix, width, on_enter):
        """
        Create a QDoubleSpinBox that:
        - selects all text when it gains focus (click once → type value directly)
        - calls on_enter() when Return/Enter is pressed
        """
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

    # ── Lock-in ───────────────────────────────────────────────────────────────
    def _read_lockin(self):
        s = self._setup(); dev = s.get("zi_device", "")
        if not dev:
            self.zi_dev_lbl.setText("(not configured)")
            self.zi_status.setText("")
            return
        self.zi_dev_lbl.setText(dev)
        p, conn_err = fresh_proxy(dev)
        if conn_err:
            self._set_err(self.zi_status, conn_err); return

        tc,   e1 = safe_read(p, s.get("zi_tc_attr",       "timeconstant"))
        ord_, e2 = safe_read(p, s.get("zi_order_attr",    "filterorder"))
        st,   e3 = safe_read(p, s.get("zi_settling_attr", "settlingtime"))
        errs = [e for e in [e1, e2, e3] if e]
        if errs:
            self._set_err(self.zi_status, errs[0][:60]); return

        if tc is not None:
            if tc >= 1.0:
                self.zi_tc_lbl.setText(f"{tc:.4f} s")
            elif tc >= 1e-3:
                self.zi_tc_lbl.setText(f"{tc*1000:.3f} ms")
            else:
                self.zi_tc_lbl.setText(f"{tc*1e6:.1f} µs")
        else:
            self.zi_tc_lbl.setText("—")

        self.zi_ord_lbl.setText(str(int(ord_)) if ord_ is not None else "—")

        if st is not None:
            st_str = (f"{st:.3f} s" if st >= 1.0
                      else f"{st*1000:.1f} ms" if st >= 1e-3
                      else f"{st:.4f} s")
            self.zi_set_lbl.setText(f"{st_str}  (99%)")
        else:
            self.zi_set_lbl.setText("—")

        self._set_ok(self.zi_status, "OK")

    # ── Keithley ──────────────────────────────────────────────────────────────
    def _read_keithley(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev); self._update_dev_labels()
        if conn_err:
            self._set_err(self.ks_status, conn_err); return

        amp, e1 = safe_read(p, "amplitude")
        frq, e2 = safe_read(p, "frequency")
        cpl, e3 = safe_read(p, "compliance")
        cur, e4 = safe_read(p, "current")
        errs = [e for e in [e1, e2] if e]
        if errs:
            self._set_err(self.ks_status, errs[0][:60]); return

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
        self._set_ok(self.ks_status, "  ".join(parts))

    # ── Individual-attribute writes (called by Enter on each spinbox) ──────────
    def _write_range(self):
        """Write only the range; used by the Set Range button."""
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err: self._set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p): self._set_sim(self.ks_status); return
        err = safe_write(p, "range", self.range_combo.currentText())
        if err: self._set_err(self.ks_status, err[:60])
        else:   self._set_ok(self.ks_status, f"range → {self.range_combo.currentText()}")

    def _write_amplitude(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err: self._set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p): self._set_sim(self.ks_status); return
        val = self.amp_spin.value()
        if abs(val) < 1e-9:
            # Zero amplitude → turn output OFF
            try:
                p.command_inout("Off")
                self._set_ok(self.ks_status, "Output OFF (amplitude = 0)")
            except Exception as e:
                # Fallback: write 0 if Off command not available
                err = safe_write(p, "amplitude", 0.0)
                if err: self._set_err(self.ks_status, err[:60])
                else:   self._set_ok(self.ks_status, "amp → 0 mA (Off cmd failed)")
        else:
            # Non-zero → ensure output is ON, then write amplitude
            try: p.command_inout("On")
            except Exception: pass  # On command may not exist or may already be on
            err = safe_write(p, "amplitude", val)
            if err: self._set_err(self.ks_status, err[:60])
            else:   self._set_ok(self.ks_status, f"amp → {val:.4g} mA")

    def _write_compliance(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err: self._set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p): self._set_sim(self.ks_status); return
        err = safe_write(p, "compliance", self.compl_spin.value())
        if err: self._set_err(self.ks_status, err[:60])
        else:   self._set_ok(self.ks_status, f"compliance → {self.compl_spin.value():.2f} V")

    def _write_frequency(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err: self._set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p): self._set_sim(self.ks_status); return
        err = safe_write(p, "frequency", self.freq_spin.value())
        if err: self._set_err(self.ks_status, err[:60])
        else:   self._set_ok(self.ks_status, f"freq → {self.freq_spin.value():.4g} Hz")

    # ── Send All button: writes range + amplitude + frequency together ────────
    def _write_keithley(self):
        s = self._setup(); dev = s.get("keithley_device", "")
        p, conn_err = fresh_proxy(dev); self._update_dev_labels()
        if conn_err:
            self._set_err(self.ks_status, conn_err); return
        if is_sim_proxy(p):
            self._set_sim(self.ks_status); return
        # Write range first — even though it can't be read back, it can be set
        e1 = safe_write(p, "range",     self.range_combo.currentText())
        e2 = safe_write(p, "amplitude", self.amp_spin.value())
        e3 = safe_write(p, "frequency", self.freq_spin.value())
        errs = [e for e in [e1, e2, e3] if e]
        if errs:
            self._set_err(self.ks_status, errs[0][:60])
        else:
            self._set_ok(self.ks_status,
                         f"Sent: range={self.range_combo.currentText()}  "
                         f"amp={self.amp_spin.value():.4g} mA  "
                         f"freq={self.freq_spin.value():.4g} Hz")

    # ── Field ─────────────────────────────────────────────────────────────────
    def _write_field(self):
        s = self._setup(); dev = s.get("magnet_device", "")
        p, conn_err = fresh_proxy(dev); self._update_dev_labels()
        if conn_err:
            self._set_err(self.mag_status, conn_err); return
        if is_sim_proxy(p):
            self._set_sim(self.mag_status); return
        attr = s.get("magnet_current_attr", "current_polar")
        val  = self.field_spin.value()
        err  = safe_write(p, attr, val)
        if err:
            self._set_err(self.mag_status, err[:60])
        else:
            self._set_ok(self.mag_status, f"Sent {attr} = {val:.4f} A")

    def _demagnetize(self):
        """Run demagnetization in a background thread."""
        import threading
        s = self._setup(); dev = s.get("magnet_device", "")
        p, conn_err = fresh_proxy(dev); self._update_dev_labels()
        if conn_err:
            self._set_err(self.mag_status, conn_err); return
        if is_sim_proxy(p):
            self._set_sim(self.mag_status); return
        attr = s.get("magnet_current_attr", "current_polar")
        self.zero_field_btn.setEnabled(False)
        self._set_ok(self.mag_status, "Demagnetizing…")

        def _run():
            demagnetize_magnet(p, attr,
                               log_fn=lambda m: self.mag_status.setText(m))
            self.zero_field_btn.setEnabled(True)

        threading.Thread(target=_run, daemon=True).start()

    # ── Relay ─────────────────────────────────────────────────────────────────
    def _toggle_relay(self):
        self._relay_state = 1 - self._relay_state
        s = self._setup(); dev = s.get("relay_device", "")
        p, conn_err = fresh_proxy(dev)
        if conn_err:
            self._set_err(self.relay_status, conn_err)
            self._relay_state = 1 - self._relay_state   # revert
            return
        if is_sim_proxy(p):
            self._set_sim(self.relay_status)
        else:
            relay_attr = s.get("relay_attr", "switchvar")
            err = safe_write(p, relay_attr, self._relay_state)
            if err:
                self._set_err(self.relay_status, err[:60])
                self._relay_state = 1 - self._relay_state   # revert
                return
            self._set_ok(self.relay_status,
                         f"{relay_attr} → {self._relay_state}  "
                         f"({'−1' if self._relay_state else '+1'})")
        self._update_relay_label()

    def _update_relay_label(self):
        if self._relay_state == 0:
            self.relay_lbl.setText("0  (+1)")
            self.relay_lbl.setStyleSheet("color:#a6e3a1;font-weight:bold;")
        else:
            self.relay_lbl.setText("1  (−1)")
            self.relay_lbl.setStyleSheet("color:#f38ba8;font-weight:bold;")

    def update_field_readback(self, val_T):
        self.field_rb.setText(f"{val_T:.1f} mT" if val_T is not None else "— mT")

    def refresh(self):
        """Re-read all hardware values. Called on tab switch."""
        self._read_lockin()
        self._read_keithley()

    def get_relay_state(self) -> int: return self._relay_state

    def set_relay_state(self, state: int):
        """Set relay state and update the label (used by scanlist worker)."""
        self._relay_state = state
        self._update_relay_label()

    def update_relay_label(self):
        """Public alias for _update_relay_label."""
        self._update_relay_label()

    def demagnetize(self):
        """Public interface for demagnetization."""
        self._demagnetize()

