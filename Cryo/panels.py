"""
panels.py — Samba v3
All reusable UI panels and row widgets:
  SensorPickerRow (registry-driven device/channel/axis picker)
  HardwarePanel
  ConfigListPanel, RightPanel
  ActuatorGroup, TrajectoryPanel, ScanlistPanel
"""
import os, time, collections
from datetime import datetime
from typing import Dict, List, Tuple
import numpy as np

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox,
    QCheckBox, QGroupBox, QTabWidget, QComboBox, QProgressBar,
    QFileDialog, QListWidget, QListWidgetItem, QScrollArea,
    QButtonGroup, QRadioButton, QAbstractItemView, QInputDialog,
    QStackedWidget
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QKeySequence, QIcon
from PyQt6.QtWidgets import QAbstractSpinBox

from config import (
    COLORMAPS, KEITHLEY_RANGES,
    X_NATURAL, X_TIME,
)
from hardware import fresh_proxy, is_sim_proxy, safe_write, safe_read, safe_read_str
from device_registry import load_registry


# ─────────────────────────────────────────────────────────────────────────────
# NoScroll variants — prevent accidental value changes when hovering
# ─────────────────────────────────────────────────────────────────────────────
class NoScrollComboBox(QComboBox):
    def wheelEvent(self, ev): ev.ignore()

class NoScrollSpinBox(QSpinBox):
    def wheelEvent(self, ev): ev.ignore()

class NoScrollDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, ev): ev.ignore()

# ─────────────────────────────────────────────────────────────────────────────
# SensorPickerRow — dropdown-based: [✓] [Device ▾] [Channel ▾] [Axis ▾] [×]
# All device properties (tango path, trigger, integ attr) come from the registry.
# ─────────────────────────────────────────────────────────────────────────────
AXIS_OPTIONS = ["Y1", "Y2", "X", "hidden"]


class SensorPickerRow(QWidget):
    changed          = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, registry: List[dict], device_name: str = "",
                 channel_attr: str = "", axis: str = "Y1",
                 enabled: bool = False, parent=None):
        super().__init__(parent)
        self._registry = registry
        lay = QHBoxLayout(self); lay.setContentsMargins(2, 2, 2, 2); lay.setSpacing(6)

        self.ck = QCheckBox(); self.ck.setChecked(enabled)
        self.ck.stateChanged.connect(lambda _: self.changed.emit())

        # Device dropdown
        self.dev_combo = NoScrollComboBox()
        self.dev_combo.setMinimumWidth(140)
        dev_names = [d["name"] for d in registry]
        self.dev_combo.addItems(dev_names)
        if device_name in dev_names:
            self.dev_combo.setCurrentText(device_name)
        elif dev_names:
            self.dev_combo.setCurrentIndex(0)
        self.dev_combo.currentIndexChanged.connect(self._on_device_changed)

        # Channel dropdown (populated based on selected device)
        self.ch_combo = NoScrollComboBox()
        self.ch_combo.setMinimumWidth(100)
        self._populate_channels(channel_attr)
        self.ch_combo.currentIndexChanged.connect(lambda _: self.changed.emit())

        # Axis dropdown
        self.axis_combo = NoScrollComboBox(); self.axis_combo.addItems(AXIS_OPTIONS)
        self.axis_combo.setCurrentText(axis if axis in AXIS_OPTIONS else "Y1")
        self.axis_combo.setFixedWidth(65)
        self.axis_combo.currentIndexChanged.connect(lambda _: self.changed.emit())

        # Delete button
        del_btn = QPushButton("×"); del_btn.setFixedWidth(22); del_btn.setFixedHeight(22)
        del_btn.setStyleSheet("QPushButton{color:#f38ba8;font-weight:bold;border:1px solid #45475a;"
                              "border-radius:3px;padding:0;background:#313244;}"
                              "QPushButton:hover{background:#45475a;}")
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        del_btn.clicked.connect(self.delete_requested.emit)

        lay.addWidget(self.ck)
        lay.addWidget(self.dev_combo, stretch=2)
        lay.addWidget(self.ch_combo, stretch=1)
        lay.addWidget(self.axis_combo)
        lay.addWidget(del_btn)

    def _on_device_changed(self, _):
        self._populate_channels()
        self.changed.emit()

    def _populate_channels(self, select_attr: str = ""):
        """Fill channel dropdown from the selected device's channels."""
        self.ch_combo.blockSignals(True)
        self.ch_combo.clear()
        dev = self._get_device()
        if dev:
            for ch in dev.get("channels", []):
                label = ch.get("label", ch.get("attr", "?"))
                self.ch_combo.addItem(label, ch.get("attr", ""))
            # Try to select the requested attr
            if select_attr:
                for i in range(self.ch_combo.count()):
                    if self.ch_combo.itemData(i) == select_attr:
                        self.ch_combo.setCurrentIndex(i)
                        break
        self.ch_combo.blockSignals(False)

    def _get_device(self) -> dict:
        """Look up the selected device in the registry."""
        name = self.dev_combo.currentText()
        for d in self._registry:
            if d["name"] == name:
                return d
        return {}

    def _get_channel(self) -> dict:
        """Look up the selected channel in the device."""
        dev = self._get_device()
        attr = self.ch_combo.currentData()
        for ch in dev.get("channels", []):
            if ch.get("attr") == attr:
                return ch
        return {}

    def get(self) -> dict:
        """Return a full sensor dict compatible with the scan engine.
        Includes device_name and channel_attr for config persistence."""
        dev = self._get_device()
        ch  = self._get_channel()
        axis = self.axis_combo.currentText()
        label = ch.get("label", self.ch_combo.currentText())
        return {
            "label":           label,
            "device":          dev.get("tango_path", ""),
            "attribute":       ch.get("attr", self.ch_combo.currentData() or ""),
            "unit":            ch.get("unit", ""),
            "enabled":         self.ck.isChecked(),
            "y_axis":          axis if axis in ("Y1", "Y2") else "Y1",
            "plot_visible":    axis != "hidden",
            "trigger_cmd":     dev.get("trigger_cmd", ""),
            "integ_time_attr": dev.get("integ_time_attr", ""),
            "settling_attr":   dev.get("settling_attr", ""),
            "plot_axis":       axis,
            # Registry keys — used to restore dropdowns on config load
            "device_name":     dev.get("name", ""),
            "channel_attr":    ch.get("attr", self.ch_combo.currentData() or ""),
        }

    def get_axis(self) -> str:
        return self.axis_combo.currentText()

    def update_registry(self, registry: List[dict]):
        """Update registry reference (e.g. after registry edit)."""
        self._registry = registry


# ─────────────────────────────────────────────────────────────────────────────
# HardwarePanel — two-column: current source (left) | field + relay (right)
# Uses fresh_proxy() for every interactive write/read so a stale SimProxy
# cached at startup never silently intercepts real-device operations.
# ─────────────────────────────────────────────────────────────────────────────
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
        frg.addWidget(self.field_spin, row, 1, 1, 2); row += 1

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

        tc,   e1 = safe_read(p, "timeconstant")
        ord_, e2 = safe_read(p, "filterorder")
        st,   e3 = safe_read(p, "settlingtime")
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
            err = safe_write(p, "switchvar", self._relay_state)
            if err:
                self._set_err(self.relay_status, err[:60])
                self._relay_state = 1 - self._relay_state   # revert
                return
            self._set_ok(self.relay_status,
                         f"switchvar → {self._relay_state}  "
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


# ─────────────────────────────────────────────────────────────────────────────
# Helper: block underscores in QLineEdit
# ─────────────────────────────────────────────────────────────────────────────
class _NoUnderscoreValidator:
    """Mixin: strip underscores from typed/pasted text."""
    @staticmethod
    def install(le: QLineEdit):
        le.textChanged.connect(lambda t: (
            le.blockSignals(True),
            le.setText(t.replace("_", "")),
            le.blockSignals(False),
        ) if "_" in t else None)


# ─────────────────────────────────────────────────────────────────────────────
# MokeMetadataGroup — reusable metadata widget (Trajectory + Scanlist)
# ─────────────────────────────────────────────────────────────────────────────
class MokeMetadataGroup(QGroupBox):
    """Metadata fields: operator, sample, notes, incidence, polarization,
    λ/2, λ/4, noDC.  Emits `changed` whenever any value changes."""
    changed = pyqtSignal()

    def __init__(self, title: str = "Metadata", parent=None):
        super().__init__(title, parent)
        top = QHBoxLayout(self)
        top.setSpacing(8); top.setContentsMargins(4, 4, 4, 4)

        # ── Left: Op / Sample / Notes (vertical pairs) ───────────────────────
        left = QGridLayout(); left.setSpacing(2)
        left.setColumnStretch(1, 1)

        left.addWidget(QLabel("Op:"), 0, 0)
        self.meta_operator = QLineEdit(); self.meta_operator.setPlaceholderText("Name")
        self.meta_operator.setMinimumWidth(50)
        _NoUnderscoreValidator.install(self.meta_operator)
        left.addWidget(self.meta_operator, 0, 1)

        left.addWidget(QLabel("Sample:"), 1, 0)
        self.meta_sample = QLineEdit(); self.meta_sample.setPlaceholderText("Sample ID")
        _NoUnderscoreValidator.install(self.meta_sample)
        left.addWidget(self.meta_sample, 1, 1)

        left.addWidget(QLabel("Notes:"), 2, 0)
        self.meta_notes = QLineEdit(); self.meta_notes.setPlaceholderText("…")
        _NoUnderscoreValidator.install(self.meta_notes)
        left.addWidget(self.meta_notes, 2, 1)

        top.addLayout(left, stretch=1)

        # ── Right: Incidence / Polarization / checkboxes ──────────────────────
        right = QGridLayout(); right.setSpacing(2)

        # Row 0: Incidence + mirror-shift
        right.addWidget(QLabel("Incidence:"), 0, 0)
        self.incidence_combo = NoScrollComboBox()
        self.incidence_combo.addItems(["PMOKE", "LMOKE+", "LMOKE", "TMOKE"])
        self.incidence_combo.currentTextChanged.connect(self._on_incidence_changed)
        right.addWidget(self.incidence_combo, 0, 1)

        self._mirror_shift_lbl = QLabel("shift:")
        self._mirror_shift_lbl.setStyleSheet("font-size:9px;")
        self.mirror_shift = NoScrollDoubleSpinBox()
        self.mirror_shift.setRange(-50, 50); self.mirror_shift.setDecimals(2)
        self.mirror_shift.setValue(12.50); self.mirror_shift.setSuffix(" mm")
        self.mirror_shift.setFixedWidth(85)
        self._mirror_shift_lbl.setVisible(False)
        self.mirror_shift.setVisible(False)
        right.addWidget(self._mirror_shift_lbl, 0, 2)
        right.addWidget(self.mirror_shift, 0, 3)

        # Row 1: Polarization + custom
        right.addWidget(QLabel("Polarization:"), 1, 0)
        self.pol_combo = NoScrollComboBox()
        self.pol_combo.addItems(["s", "45°", "p", "other"])
        self.pol_combo.currentTextChanged.connect(self._on_pol_changed)
        right.addWidget(self.pol_combo, 1, 1)
        self.pol_custom = QLineEdit(); self.pol_custom.setPlaceholderText("custom")
        self.pol_custom.setFixedWidth(70)
        self.pol_custom.setVisible(False)
        _NoUnderscoreValidator.install(self.pol_custom)
        right.addWidget(self.pol_custom, 1, 2, 1, 2)

        # Row 2: checkboxes λ/2, λ/4, noDC — all in one line
        cb_row = QHBoxLayout(); cb_row.setSpacing(10)
        self.lam2_cb = QCheckBox("λ/2"); cb_row.addWidget(self.lam2_cb)
        self.lam4_cb = QCheckBox("λ/4"); cb_row.addWidget(self.lam4_cb)
        cb_row.addSpacing(12)
        self.nodc_cb = QCheckBox("noDC"); cb_row.addWidget(self.nodc_cb)
        cb_row.addStretch()
        right.addLayout(cb_row, 2, 0, 1, 4)

        top.addLayout(right)

        # Connect everything to changed signal
        for w in [self.meta_operator, self.meta_sample, self.meta_notes, self.pol_custom]:
            w.textChanged.connect(self.changed.emit)
        for w in [self.incidence_combo, self.pol_combo]:
            w.currentTextChanged.connect(self.changed.emit)
        for w in [self.lam2_cb, self.lam4_cb, self.nodc_cb]:
            w.toggled.connect(self.changed.emit)
        self.mirror_shift.valueChanged.connect(self.changed.emit)

        # Trigger initial visibility
        self._on_incidence_changed(self.incidence_combo.currentText())

    # ── Visibility helpers ────────────────────────────────────────────────────
    def _on_incidence_changed(self, text):
        show = text in ("LMOKE+", "LMOKE")
        self._mirror_shift_lbl.setVisible(show)
        self.mirror_shift.setVisible(show)

    def _on_pol_changed(self, text):
        self.pol_custom.setVisible(text == "other")

    # ── Get / Load ────────────────────────────────────────────────────────────
    def get_values(self) -> dict:
        pol = self.pol_combo.currentText()
        if pol == "other":
            pol = self.pol_custom.text().strip() or "other"
        inc = self.incidence_combo.currentText()
        ms  = self.mirror_shift.value()
        return {
            "operator":     self.meta_operator.text().strip(),
            "sample_id":    self.meta_sample.text().strip(),
            "notes":        self.meta_notes.text().strip(),
            "incidence":    inc,
            "mirror_shift": ms,
            "polarization": pol,
            "lam2":         self.lam2_cb.isChecked(),
            "lam4":         self.lam4_cb.isChecked(),
            "noDC":         self.nodc_cb.isChecked(),
        }

    def load_values(self, cfg: dict):
        self.meta_operator.setText(cfg.get("operator", ""))
        self.meta_sample.setText(cfg.get("sample_id", ""))
        self.meta_notes.setText(cfg.get("notes", ""))
        inc = cfg.get("incidence", "PMOKE")
        idx = self.incidence_combo.findText(inc)
        if idx >= 0: self.incidence_combo.setCurrentIndex(idx)
        self.mirror_shift.setValue(cfg.get("mirror_shift", 12.50))
        pol = cfg.get("polarization", "s")
        
        idx = self.pol_combo.findText(pol)
        if idx >= 0:
            self.pol_combo.setCurrentIndex(idx)
        else:
            self.pol_combo.setCurrentIndex(self.pol_combo.findText("other"))
            self.pol_custom.setText(pol)
        self.lam2_cb.setChecked(cfg.get("lam2", False))
        self.lam4_cb.setChecked(cfg.get("lam4", False))
        self.nodc_cb.setChecked(cfg.get("noDC", False))

    def build_scan_name(self, amplitude_mA: float = 0.0, freq_Hz: float = 0.0,
                         config_name: str = "") -> str:
        """Construct scanlist auto-name from metadata fields.
        Format: date_sample_amplitude_frequency_config_incidence_mirror-shift[_notes][_noDC][_lam2][_lam4]
        """
        v = self.get_values()
        ts = datetime.now().strftime("%Y%m%d")
        sample = v["sample_id"].replace(" ", "-") or "sample"
        amp_str = f"{amplitude_mA:.4g}mA"
        freq_str = f"{freq_Hz:.4g}Hz"
        cfg = config_name.replace(" ", "-").replace("_", "-") or "cfg"
        inc = v["incidence"]
        ms = f"{v['mirror_shift']:.2f}mm".replace(".", "p")
        notes = v["notes"].replace(" ", "-")
        parts = [ts, sample, amp_str, freq_str, cfg, inc, ms]
        if notes:  parts.append(notes)
        if v["noDC"]:  parts.append("noDC")
        if v["lam2"]:  parts.append("lam2")
        if v["lam4"]:  parts.append("lam4")
        return "_".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# ConfigListPanel — setup tabs + per-setup scan config list
# ─────────────────────────────────────────────────────────────────────────────
class ConfigListPanel(QWidget):
    config_selected      = pyqtSignal(int)      # −1 means "copy current"
    new_config_requested = pyqtSignal()         # blank new config
    config_deleted       = pyqtSignal(int)
    config_renamed       = pyqtSignal(int, str)
    save_requested       = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4); lay.setSpacing(6)

        # ── Config management buttons ─────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(3)
        _btn_style = ("QPushButton{background:#313244;border:1px solid #45475a;"
                      "border-radius:4px;padding:0;color:#cdd6f4;font-weight:bold;}"
                      "QPushButton:hover{background:#45475a;}"
                      "QPushButton:pressed{background:#585b70;}")

        def _cfg_btn(icon_name: str, fallback: str, tip: str) -> QPushButton:
            b = QPushButton()
            icon = QIcon.fromTheme(icon_name)
            if not icon.isNull():
                b.setIcon(icon)
            else:
                b.setText(fallback)
            b.setFixedSize(24, 24)
            b.setStyleSheet(_btn_style)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            return b

        new_btn = _cfg_btn("document-new",  "+", "New blank config (Spatial, X-axis)")
        new_btn.clicked.connect(self.new_config)
        btn_row.addWidget(new_btn)

        del_btn_cfg = _cfg_btn("list-remove", "-", "Delete config")
        del_btn_cfg.clicked.connect(self.del_config)
        btn_row.addWidget(del_btn_cfg)

        cpy_btn = _cfg_btn("edit-copy", "C", "Copy current config")
        cpy_btn.clicked.connect(self.copy_config)
        btn_row.addWidget(cpy_btn)

        ren_btn = _cfg_btn("document-edit", "R", "Rename config")
        ren_btn.clicked.connect(self.rename_config)
        btn_row.addWidget(ren_btn)

        sav_btn = _cfg_btn("document-save", "S", "Save config to disk")
        sav_btn.clicked.connect(self.save_requested.emit)
        btn_row.addWidget(sav_btn)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        # ── Single config list (no multi-setup tabs) ──────────────────────────
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        lay.addWidget(self._list, stretch=1)

    def load_setups(self, setups: Dict[str, dict]):
        """Load configs from the first (only) setup in the dict."""
        self._list.clear()
        for _sn, data in setups.items():
            for cfg in data.get("configs", []):
                self._list.addItem(QListWidgetItem(cfg["name"]))
            idx = data.get("active_idx", 0)
            if 0 <= idx < self._list.count():
                self._list.setCurrentRow(idx)
            break  # single setup

    def active_list(self) -> QListWidget:
        return self._list

    def _on_selection_changed(self):
        if self._list.currentRow() >= 0:
            self.config_selected.emit(self._list.currentRow())

    def new_config(self):    self.new_config_requested.emit()
    def copy_config(self):   self.config_selected.emit(-1)
    def del_config(self):
        if self._list.count() > 1:
            self.config_deleted.emit(self._list.currentRow())
    def rename_config(self):
        it = self._list.currentItem()
        if not it: return
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=it.text())
        if ok and name.strip():
            self.config_renamed.emit(self._list.currentRow(), name.strip())

    def remove_item(self, idx: int): self._list.takeItem(idx)
    def rename_item(self, idx: int, name: str):
        if 0 <= idx < self._list.count(): self._list.item(idx).setText(name)
    def sync_name(self, idx: int, name: str):
        if 0 <= idx < self._list.count(): self._list.item(idx).setText(name)
    def add_item(self, name: str) -> int:
        self._list.addItem(QListWidgetItem(name))
        new_idx = self._list.count() - 1
        self._list.setCurrentRow(new_idx)
        return new_idx


# ─────────────────────────────────────────────────────────────────────────────
# RightPanel — unified Devices + Plot panel (dropdown-based, registry-driven)
# ─────────────────────────────────────────────────────────────────────────────
class RightPanel(QWidget):
    sensors_changed     = pyqtSignal()
    plot_config_changed = pyqtSignal()
    refresh_requested   = pyqtSignal()
    display_changed     = pyqtSignal(str, str)   # sensor_label, colormap
    x_axis_changed      = pyqtSignal(str, str)   # key, display_label

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self._registry: List[dict] = load_registry()
        self._sensor_rows: List[SensorPickerRow] = []

        lay = QVBoxLayout(self); lay.setContentsMargins(4, 6, 4, 4); lay.setSpacing(4)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QWidget(); hl = QHBoxLayout(hdr)
        hl.setContentsMargins(4, 0, 4, 0); hl.setSpacing(6)
        for txt, w in [("✓", 18), ("Device", 0), ("Channel", 100), ("Axis", 65)]:
            lb = QLabel(txt); lb.setStyleSheet("color:#6c7086;font-size:10px;")
            if w: lb.setFixedWidth(w)
            hl.addWidget(lb, stretch=(0 if w else 1))
        self._hdr_widget = hdr
        lay.addWidget(hdr)

        # ── Stacked widget: page 0 = normal sensors, page 1 = DC channels ───
        self._sensor_stack = QStackedWidget()

        # Page 0: Normal sensor picker
        page0 = QWidget(); p0l = QVBoxLayout(page0)
        p0l.setContentsMargins(0, 0, 0, 0); p0l.setSpacing(2)
        self._sensor_content = QWidget()
        self._sensor_vlayout = QVBoxLayout(self._sensor_content)
        self._sensor_vlayout.setContentsMargins(0, 0, 0, 0); self._sensor_vlayout.setSpacing(2)
        sensor_scroll = QScrollArea(); sensor_scroll.setWidgetResizable(True)
        sensor_scroll.setWidget(self._sensor_content)
        sensor_scroll.setStyleSheet("QScrollArea{border:none;}")
        p0l.addWidget(sensor_scroll, stretch=1)
        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        add_btn = QPushButton("+ Add channel"); add_btn.clicked.connect(self._add_sensor)
        ref_btn = QPushButton("↺ Refresh"); ref_btn.clicked.connect(self.refresh_requested)
        btn_row.addWidget(add_btn); btn_row.addWidget(ref_btn); btn_row.addStretch()
        p0l.addLayout(btn_row)
        self._sensor_stack.addWidget(page0)

        # Page 1: DC hysteresis channels (registry-based, same picker as page 0)
        page1 = QWidget(); p1l = QVBoxLayout(page1)
        p1l.setContentsMargins(0, 0, 0, 0); p1l.setSpacing(2)
        dc_hdr = QLabel("DC Hysteresis channels")
        dc_hdr.setStyleSheet("color:#89b4fa;font-weight:bold;font-size:11px;")
        p1l.addWidget(dc_hdr)
        self._dc_sensor_content = QWidget()
        self._dc_sensor_vlayout = QVBoxLayout(self._dc_sensor_content)
        self._dc_sensor_vlayout.setContentsMargins(0, 0, 0, 0); self._dc_sensor_vlayout.setSpacing(2)
        dc_scroll = QScrollArea(); dc_scroll.setWidgetResizable(True)
        dc_scroll.setWidget(self._dc_sensor_content)
        dc_scroll.setStyleSheet("QScrollArea{border:none;}")
        p1l.addWidget(dc_scroll, stretch=1)
        dc_btn_row = QHBoxLayout(); dc_btn_row.setSpacing(4)
        dc_add_btn = QPushButton("+ Add channel"); dc_add_btn.clicked.connect(self._add_dc_sensor)
        dc_btn_row.addWidget(dc_add_btn); dc_btn_row.addStretch()
        p1l.addLayout(dc_btn_row)
        self._dc_sensor_rows: List[SensorPickerRow] = []
        self._sensor_stack.addWidget(page1)

        lay.addWidget(self._sensor_stack, stretch=1)

        # ── Plot controls ─────────────────────────────────────────────────────
        pc = QGroupBox("Plot"); pcl = QGridLayout(pc)
        pcl.setSpacing(4); pcl.setContentsMargins(6, 6, 6, 6)

        pcl.addWidget(QLabel("2D sensor:"), 0, 0)
        self.disp_combo = NoScrollComboBox()
        self.disp_combo.currentTextChanged.connect(self._on_disp_changed)
        pcl.addWidget(self.disp_combo, 0, 1)

        pcl.addWidget(QLabel("Colormap:"), 0, 2)
        self.cmap_combo = NoScrollComboBox()
        for cm in COLORMAPS: self.cmap_combo.addItem(cm)
        self.cmap_combo.currentTextChanged.connect(self._on_disp_changed)
        pcl.addWidget(self.cmap_combo, 0, 3)

        pcl.addWidget(QLabel("X axis:"), 1, 0)
        self.x_combo = NoScrollComboBox(); self.x_combo.setToolTip("X axis for 1D plots")
        self._x_options: List[Tuple[str,str]] = [(X_NATURAL,"Natural x"), (X_TIME,"Time (s)")]
        for _, lbl in self._x_options: self.x_combo.addItem(lbl)
        self.x_combo.currentIndexChanged.connect(self._on_x_changed)
        pcl.addWidget(self.x_combo, 1, 1, 1, 3)

        lay.addWidget(pc)

    # ── Registry ──────────────────────────────────────────────────────────────
    def set_registry(self, registry: List[dict]):
        """Update the registry reference (called after registry edits)."""
        self._registry = registry
        for row in self._sensor_rows:
            row.update_registry(registry)
        for row in self._dc_sensor_rows:
            row.update_registry(registry)

    # ── Sensor management ─────────────────────────────────────────────────────
    def _add_sensor(self):
        self._make_picker_row()

    def _make_picker_row(self, device_name: str = "", channel_attr: str = "",
                         axis: str = "Y1", enabled: bool = False) -> SensorPickerRow:
        row = SensorPickerRow(self._registry, device_name, channel_attr,
                              axis, enabled)
        row.changed.connect(self._on_sensors_changed)
        row.delete_requested.connect(lambda: self._delete_row(row))
        self._sensor_vlayout.addWidget(row)
        self._sensor_rows.append(row)
        return row

    def _delete_row(self, row: SensorPickerRow):
        if row in self._sensor_rows:
            self._sensor_rows.remove(row)
            self._sensor_vlayout.removeWidget(row); row.hide(); row.deleteLater()
            self._on_sensors_changed()

    def _on_sensors_changed(self):
        self._refresh_disp_combo()
        self._refresh_x_sensor_items()
        self.sensors_changed.emit()
        self.plot_config_changed.emit()

    def _refresh_disp_combo(self):
        prev = self.disp_combo.currentText()
        self.disp_combo.blockSignals(True); self.disp_combo.clear()
        for r in self._sensor_rows:
            s = r.get()
            if s["enabled"]: self.disp_combo.addItem(s["label"])
        if prev and self.disp_combo.findText(prev) >= 0:
            self.disp_combo.setCurrentText(prev)
        self.disp_combo.blockSignals(False)

    def _refresh_x_sensor_items(self):
        prev_key = self.get_x_key()
        self.x_combo.blockSignals(True); self.x_combo.clear()
        self._x_options = [(X_NATURAL,"Natural x"), (X_TIME,"Time (s)")]
        for row in self._sensor_rows:
            s = row.get()
            if s["enabled"]: self._x_options.append((s["label"], s["label"]))
        for _, lbl in self._x_options: self.x_combo.addItem(lbl)
        keys = [k for k, _ in self._x_options]
        if prev_key in keys: self.x_combo.setCurrentIndex(keys.index(prev_key))
        self.x_combo.blockSignals(False)

    def _on_disp_changed(self):
        self.display_changed.emit(self.disp_combo.currentText(), self.cmap_combo.currentText())

    def _on_x_changed(self, idx):
        if 0 <= idx < len(self._x_options):
            key, lbl = self._x_options[idx]
            self.x_axis_changed.emit(key, lbl)

    # ── Public API ────────────────────────────────────────────────────────────
    def load_sensors(self, sensors: List[dict]):
        """Load sensor list from saved config.
        Uses device_name + channel_attr if saved (new format),
        falls back to tango path reverse lookup (old format)."""
        for row in self._sensor_rows:
            self._sensor_vlayout.removeWidget(row); row.hide(); row.deleteLater()
        self._sensor_rows = []

        # Build reverse lookup for old-format configs: tango_path → device name
        path_to_name = {}
        for d in self._registry:
            path_to_name[d["tango_path"]] = d["name"]
            # Also index lowercase for case-insensitive matching
            path_to_name[d["tango_path"].lower()] = d["name"]

        for s in sensors:
            # Prefer saved device_name (exact match)
            dev_name = s.get("device_name", "")
            if not dev_name:
                # Fallback: reverse lookup from tango path
                dev_path = s.get("device", "")
                dev_name = path_to_name.get(dev_path, "")
                if not dev_name:
                    dev_name = path_to_name.get(dev_path.lower(), "")

            # Prefer saved channel_attr, fallback to attribute
            ch_attr = s.get("channel_attr", s.get("attribute", ""))

            axis = s.get("plot_axis", s.get("y_axis", "Y1"))
            if not s.get("plot_visible", True) and axis not in ("X", "hidden"):
                axis = "hidden"
            enabled = s.get("enabled", False)

            self._make_picker_row(dev_name, ch_attr, axis, enabled)

        self._refresh_disp_combo()
        self._refresh_x_sensor_items()

    def get_sensors(self) -> List[dict]:
        return [r.get() for r in self._sensor_rows]

    def get_display_sensor(self) -> str:
        return self.disp_combo.currentText()

    def get_colormap(self) -> str:
        return self.cmap_combo.currentText()

    def get_x_key(self) -> str:
        # Check if any sensor has axis set to X
        for r in self._sensor_rows:
            s = r.get()
            if s["enabled"] and s.get("plot_axis") == "X":
                return s["label"]
        idx = self.x_combo.currentIndex()
        if 0 <= idx < len(self._x_options): return self._x_options[idx][0]
        return X_NATURAL

    def get_plot_sensors_meta(self) -> List[dict]:
        result = []
        for sr in self._sensor_rows:
            s = sr.get()
            if not s["enabled"]: continue
            axis = s.get("plot_axis", s.get("y_axis", "Y1"))
            if axis in ("hidden", "X"):
                axis = "—"
            result.append({"label": s["label"], "unit": s.get("unit",""), "axis": axis})
        return result

    def set_x_options(self, options: List[Tuple[str,str]]):
        prev_key = self.get_x_key()
        self.x_combo.blockSignals(True); self.x_combo.clear()
        self._x_options = options
        for _, lbl in self._x_options: self.x_combo.addItem(lbl)
        keys = [k for k, _ in self._x_options]
        if prev_key in keys: self.x_combo.setCurrentIndex(keys.index(prev_key))
        self.x_combo.blockSignals(False)

    def set_display(self, sensor: str, cmap: str):
        if self.disp_combo.findText(sensor) >= 0: self.disp_combo.setCurrentText(sensor)
        if self.cmap_combo.findText(cmap)   >= 0: self.cmap_combo.setCurrentText(cmap)

    # ── DC mode switching ────────────────────────────────────────────────────
    def set_dc_mode(self, dc: bool):
        """Switch between normal sensor picker (page 0) and DC channels (page 1)."""
        self._sensor_stack.setCurrentIndex(1 if dc else 0)
        self._hdr_widget.setVisible(not dc)

    def _add_dc_sensor(self):
        self._make_dc_picker_row()

    def _make_dc_picker_row(self, device_name: str = "", channel_attr: str = "",
                             axis: str = "Y1", enabled: bool = False) -> SensorPickerRow:
        row = SensorPickerRow(self._registry, device_name, channel_attr,
                              axis, enabled)
        row.changed.connect(self._on_dc_sensors_changed)
        row.delete_requested.connect(lambda: self._delete_dc_row(row))
        self._dc_sensor_vlayout.addWidget(row)
        self._dc_sensor_rows.append(row)
        return row

    def _delete_dc_row(self, row: SensorPickerRow):
        if row in self._dc_sensor_rows:
            self._dc_sensor_rows.remove(row)
            self._dc_sensor_vlayout.removeWidget(row); row.hide(); row.deleteLater()
            self._on_dc_sensors_changed()

    def _on_dc_sensors_changed(self):
        self.sensors_changed.emit()
        self.plot_config_changed.emit()

    def get_dc_channels(self) -> list:
        """Return hyst_channels list from the DC sensor picker rows."""
        return [r.get() for r in self._dc_sensor_rows]

    def load_dc_channels(self, chs: list):
        """Load DC channel config into the DC page using SensorPickerRow."""
        for row in self._dc_sensor_rows:
            self._dc_sensor_vlayout.removeWidget(row); row.hide(); row.deleteLater()
        self._dc_sensor_rows = []

        # Build reverse lookup for old-format configs
        path_to_name = {d["tango_path"]: d["name"] for d in self._registry}
        path_to_name.update({d["tango_path"].lower(): d["name"] for d in self._registry})

        for s in chs:
            dev_name = s.get("device_name", "")
            if not dev_name:
                dev_path = s.get("device", "")
                dev_name = path_to_name.get(dev_path, path_to_name.get(dev_path.lower(), ""))
            ch_attr = s.get("channel_attr", s.get("attribute", s.get("attr", "")))
            axis = s.get("plot_axis", s.get("y_axis", "Y1"))
            if not s.get("plot_visible", True) and axis not in ("X", "hidden"):
                axis = "hidden"
            enabled = s.get("enabled", False)
            self._make_dc_picker_row(dev_name, ch_attr, axis, enabled)


# ─────────────────────────────────────────────────────────────────────────────
# FieldSegmentList — editable list of (start, stop, npts) segments
# Used by AC Field Sweep to allow multi-step sequences like -2→2, 2→-2, -2→0
# ─────────────────────────────────────────────────────────────────────────────
class FieldSegmentList(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(2)

        self._content = QWidget()
        self._vlayout = QVBoxLayout(self._content)
        self._vlayout.setContentsMargins(0, 0, 0, 0); self._vlayout.setSpacing(2)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setWidget(self._content)
        scroll.setMaximumHeight(108)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        root.addWidget(scroll)

        btn_row = QHBoxLayout(); btn_row.setContentsMargins(0, 2, 0, 0); btn_row.setSpacing(6)
        add_btn = QPushButton("+ Segment"); add_btn.setFixedHeight(22)
        add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add_btn.clicked.connect(lambda: self._add_segment())
        self._summary_lbl = QLabel()
        self._summary_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        btn_row.addWidget(add_btn); btn_row.addWidget(self._summary_lbl); btn_row.addStretch()
        root.addLayout(btn_row)

        self._rows: List[tuple] = []   # (start_spin, stop_spin, n_spin, row_widget)
        self._add_segment(-1.0, 1.0, 101)

    def _add_segment(self, start: float = 0.0, stop: float = 1.0, npts: int = 51):
        row_w = QWidget()
        hl = QHBoxLayout(row_w); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(4)

        def _dbl(v):
            w = NoScrollDoubleSpinBox(); w.setRange(-20, 20); w.setDecimals(4)
            w.setValue(v); w.setFixedWidth(76); return w

        s = _dbl(start); hl.addWidget(s)
        arr = QLabel("→"); arr.setStyleSheet("color:#6c7086;"); hl.addWidget(arr)
        e = _dbl(stop);  hl.addWidget(e)

        # N / Δ toggle
        mode_bg = QButtonGroup(row_w)
        rb_n = QRadioButton("N:"); rb_n.setChecked(True); rb_n.setFixedWidth(30)
        rb_d = QRadioButton("Δ:"); rb_d.setFixedWidth(30)
        mode_bg.addButton(rb_n, 0); mode_bg.addButton(rb_d, 1)
        n = NoScrollSpinBox(); n.setRange(2, 10000); n.setValue(npts); n.setFixedWidth(58)
        default_step = abs(stop - start) / (npts - 1) if npts > 1 else 0.01
        d = NoScrollDoubleSpinBox(); d.setRange(1e-6, 100); d.setDecimals(4)
        d.setValue(max(1e-6, default_step)); d.setFixedWidth(68); d.setVisible(False)
        comp = QLabel(); comp.setStyleSheet("color:#6c7086;font-size:10px;"); comp.setFixedWidth(80)
        for w in [rb_n, n, rb_d, d, comp]: hl.addWidget(w)

        del_btn = QPushButton("×"); del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet(
            "QPushButton{color:#f38ba8;font-weight:bold;border:1px solid #45475a;"
            "border-radius:3px;padding:0;background:#313244;}"
            "QPushButton:hover{background:#45475a;}")
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hl.addWidget(del_btn); hl.addStretch()

        tup = (s, e, n, d, mode_bg, row_w)
        self._vlayout.addWidget(row_w)
        self._rows.append(tup)

        def _upd_comp():
            span = abs(e.value() - s.value())
            if rb_n.isChecked():
                nn = max(2, n.value())
                step = span / (nn - 1) if nn > 1 else span
                comp.setText(f"Δ={step:.4g}")
            else:
                step = max(1e-6, d.value())
                comp.setText(f"N={max(2, int(round(span/step))+1)}")
            self._on_changed()

        def _on_mode(m):
            n.setVisible(m == 0); d.setVisible(m == 1); _upd_comp()

        mode_bg.idClicked.connect(_on_mode)
        s.valueChanged.connect(_upd_comp)
        e.valueChanged.connect(_upd_comp)
        n.valueChanged.connect(_upd_comp)
        d.valueChanged.connect(_upd_comp)
        del_btn.clicked.connect(lambda: self._del_segment(tup))
        _upd_comp()

    def _del_segment(self, tup):
        if len(self._rows) <= 1: return
        self._rows.remove(tup)
        tup[5].hide(); self._vlayout.removeWidget(tup[5]); tup[5].deleteLater()
        self._on_changed()

    def _on_changed(self):
        total = sum(self._seg_npts(t) for t in self._rows)
        n = len(self._rows)
        self._summary_lbl.setText(f"Total N = {total},  {n} segment{'s' if n != 1 else ''}")
        self.changed.emit()

    def _seg_npts(self, tup) -> int:
        s, e, n, d, bg, _ = tup
        if bg.checkedId() == 0:
            return max(2, n.value())
        span = abs(e.value() - s.value())
        return max(2, int(round(span / max(1e-6, d.value()))) + 1)

    def get_segments(self) -> List[list]:
        return [[t[0].value(), t[1].value(), self._seg_npts(t)] for t in self._rows]

    def load_segments(self, segs: List):
        for tup in list(self._rows):
            tup[5].hide(); self._vlayout.removeWidget(tup[5]); tup[5].deleteLater()
        self._rows = []
        for seg in segs:
            self._add_segment(float(seg[0]), float(seg[1]), int(seg[2]))
        if not self._rows:
            self._add_segment(-1.0, 1.0, 101)

    def total_npts(self) -> int:
        return sum(self._seg_npts(t) for t in self._rows)


# ─────────────────────────────────────────────────────────────────────────────
# ActuatorGroup — device/attr/range controls with N ↔ Δ step toggle.
# The group title area contains a checkbox (via setCheckable) that greys out
# the whole group when the axis is disabled — no separate toggle button needed.
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# ScanDirectionList — compact list of up to 2 (start, stop) direction rows.
# Each direction produces one independent scan file. npts is shared (from the
# parent ActuatorGroup's N/Δ row). Second row pre-fills reversed.
# ─────────────────────────────────────────────────────────────────────────────
class ScanDirectionList(QWidget):
    changed = pyqtSignal()
    _MAX_ROWS = 2

    _ADD_STYLE = (
        "QPushButton{background:#313244;color:#a6e3a1;border:1px solid #45475a;"
        "border-radius:3px;padding:1px 6px;font-size:10px;}"
        "QPushButton:hover{background:#45475a;}"
        "QPushButton:disabled{color:#585b70;border-color:#313244;}")
    _DEL_STYLE = (
        "QPushButton{color:#f38ba8;font-weight:bold;border:1px solid #45475a;"
        "border-radius:3px;padding:0;background:#313244;font-size:11px;}"
        "QPushButton:hover{background:#45475a;}")

    def __init__(self, start: float = 0.0, stop: float = 10.0, parent=None):
        super().__init__(parent)
        self._rows: list = []   # list of (start_spin, stop_spin, del_btn, row_widget)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(2)
        self._row_container = QWidget()
        self._row_layout = QVBoxLayout(self._row_container)
        self._row_layout.setContentsMargins(0, 0, 0, 0); self._row_layout.setSpacing(2)
        root.addWidget(self._row_container)

        btn_row = QHBoxLayout(); btn_row.setContentsMargins(0, 1, 0, 0); btn_row.setSpacing(4)
        self._add_btn = QPushButton("＋ Retrace")
        self._add_btn.setFixedHeight(20); self._add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._add_btn.setStyleSheet(self._ADD_STYLE)
        self._add_btn.clicked.connect(self._add_reversed)
        btn_row.addWidget(self._add_btn); btn_row.addStretch()
        root.addLayout(btn_row)

        self._add_row(start, stop)

    def _add_row(self, start: float, stop: float):
        row_w = QWidget()
        hl = QHBoxLayout(row_w); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(4)

        idx_lbl = QLabel("Trace:" if len(self._rows) == 0 else "Retrace:")
        idx_lbl.setStyleSheet("color:#6c7086;font-size:10px;font-weight:bold;")
        idx_lbl.setFixedWidth(48); hl.addWidget(idx_lbl)

        s_spin = NoScrollDoubleSpinBox(); s_spin.setRange(-1e9, 1e9); s_spin.setDecimals(3)
        s_spin.setValue(start); hl.addWidget(s_spin)

        arr = QLabel("→"); arr.setStyleSheet("color:#6c7086;font-size:11px;")
        arr.setFixedWidth(14); hl.addWidget(arr)

        e_spin = NoScrollDoubleSpinBox(); e_spin.setRange(-1e9, 1e9); e_spin.setDecimals(3)
        e_spin.setValue(stop); hl.addWidget(e_spin)

        del_btn = QPushButton("×"); del_btn.setFixedSize(18, 18)
        del_btn.setStyleSheet(self._DEL_STYLE)
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        del_btn.setVisible(len(self._rows) > 0)
        hl.addWidget(del_btn)

        tup = (s_spin, e_spin, del_btn, row_w)
        self._row_layout.addWidget(row_w)
        self._rows.append(tup)

        s_spin.valueChanged.connect(self.changed)
        e_spin.valueChanged.connect(self.changed)
        del_btn.clicked.connect(lambda: self._remove_row(tup))
        self._refresh_add_btn()
        self.changed.emit()

    def _add_reversed(self):
        if not self._rows: return
        s0, e0, *_ = self._rows[0]
        self._add_row(e0.value(), s0.value())

    def _remove_row(self, tup):
        if len(self._rows) <= 1: return
        self._rows.remove(tup)
        tup[3].hide(); self._row_layout.removeWidget(tup[3]); tup[3].deleteLater()
        # Relabel remaining rows
        for i, (s, e, d, w) in enumerate(self._rows):
            lbl = w.layout().itemAt(0).widget()
            if isinstance(lbl, QLabel): lbl.setText("Trace:" if i == 0 else "Retrace:")
            d.setVisible(i > 0)
        self._refresh_add_btn()
        self.changed.emit()

    def _refresh_add_btn(self):
        self._add_btn.setEnabled(len(self._rows) < self._MAX_ROWS)

    # ── Public API ────────────────────────────────────────────────────────────
    def first_start(self) -> float:
        return self._rows[0][0].value() if self._rows else 0.0

    def first_stop(self) -> float:
        return self._rows[0][1].value() if self._rows else 0.0

    def get_directions(self) -> list:
        """Return [[start, stop], ...] for all direction rows."""
        return [[t[0].value(), t[1].value()] for t in self._rows]

    def load_directions(self, dirs: list):
        """Load from [[start, stop], ...]. Clears existing rows first."""
        # Clear all rows
        for tup in list(self._rows):
            tup[3].hide(); self._row_layout.removeWidget(tup[3]); tup[3].deleteLater()
        self._rows = []
        if not dirs:
            dirs = [[0.0, 10.0]]
        for d in dirs[:self._MAX_ROWS]:
            self._add_row(float(d[0]), float(d[1]))


# ─────────────────────────────────────────────────────────────────────────────
# ActuatorGroup — device/attr/range controls with N ↔ Δ step toggle.
# Start/stop are managed by ScanDirectionList (up to 2 directions per axis).
# ─────────────────────────────────────────────────────────────────────────────
class ActuatorGroup(QGroupBox):
    _RO_STYLE = ("background:#1e1e2e;color:#6c7086;border:1px solid #313244;"
                 "border-radius:4px;padding:2px 4px;")

    def __init__(self, title: str, dev: str, attr: str, lbl: str, unit: str,
                 start: float, stop: float, npts: int, step_prefix: str = "Δ",
                 enabled: bool = True, registry: list = None, parent=None):
        super().__init__(title, parent)
        self._step_prefix = step_prefix
        self._dev_path: str = dev
        self._dev_name: str = ""
        self._attr: str = attr
        g = QGridLayout(self); g.setSpacing(4); g.setContentsMargins(8, 8, 8, 8)

        # Row 0: Scan enabled checkbox
        self.scan_cb = QCheckBox("Scan enabled"); self.scan_cb.setChecked(enabled)
        self.scan_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        g.addWidget(self.scan_cb, 0, 0, 1, 6)

        # Row 1: Device display (read-only — set from Setup Defaults tab)
        g.addWidget(QLabel("Device:"), 1, 0)
        self._dev_display = QLineEdit(dev)
        self._dev_display.setReadOnly(True)
        self._dev_display.setStyleSheet(self._RO_STYLE)
        self._dev_display.setMinimumWidth(150)
        self._dev_display.setToolTip("Set in the Setup Defaults tab")
        g.addWidget(self._dev_display, 1, 1, 1, 5)

        # Row 2: Attr display + Label + Unit (all read-only)
        g.addWidget(QLabel("Attr:"), 2, 0)
        self._attr_display = QLineEdit(attr)
        self._attr_display.setReadOnly(True)
        self._attr_display.setStyleSheet(self._RO_STYLE)
        self._attr_display.setFixedWidth(72)
        self._attr_display.setToolTip("Set in the Setup Defaults tab")
        g.addWidget(self._attr_display, 2, 1)
        g.addWidget(QLabel("Label:"), 2, 2)
        self.lbl = QLineEdit(lbl); self.lbl.setFixedWidth(40)
        self.lbl.setReadOnly(True); self.lbl.setStyleSheet(self._RO_STYLE)
        g.addWidget(self.lbl, 2, 3)
        g.addWidget(QLabel("Unit:"), 2, 4)
        self.unit_edit = QLineEdit(unit); self.unit_edit.setFixedWidth(35)
        self.unit_edit.setReadOnly(True); self.unit_edit.setStyleSheet(self._RO_STYLE)
        g.addWidget(self.unit_edit, 2, 5)

        # Row 3: Direction list (replaces fixed start/stop spinboxes)
        self.dir_list = ScanDirectionList(start, stop)
        g.addWidget(self.dir_list, 3, 0, 1, 6)
        self.dir_list.changed.connect(self._upd)

        # Row 4: N / Δ step toggle (shared npts across all directions)
        ns_row = QWidget(); ns_lay = QHBoxLayout(ns_row)
        ns_lay.setContentsMargins(0, 0, 0, 0); ns_lay.setSpacing(4)
        self._mode_bg = QButtonGroup(self)
        self.rb_n    = QRadioButton("N:");    self.rb_n.setChecked(True)
        self.rb_step = QRadioButton(f"{step_prefix}:")
        self._mode_bg.addButton(self.rb_n,    0)
        self._mode_bg.addButton(self.rb_step, 1)
        self._mode_bg.idClicked.connect(self._on_mode)
        self.npts_spin = NoScrollSpinBox();       self.npts_spin.setRange(2, 10000); self.npts_spin.setValue(npts)
        self.step_spin = NoScrollDoubleSpinBox(); self.step_spin.setRange(1e-6, 1e9); self.step_spin.setDecimals(3)
        self.step_spin.setValue(abs(stop - start) / (npts - 1) if npts > 1 else 1.0)
        self.step_spin.setVisible(False)
        self.comp_lbl  = QLabel(); self.comp_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        for w in [self.rb_n, self.npts_spin, self.rb_step, self.step_spin, self.comp_lbl]:
            ns_lay.addWidget(w)
        ns_lay.addStretch(); g.addWidget(ns_row, 4, 0, 1, 6)

        self.npts_spin.valueChanged.connect(self._upd)
        self.step_spin.valueChanged.connect(self._upd)
        self._upd()

    # ── Device info ───────────────────────────────────────────────────────────
    def set_device_info(self, dev_path: str, dev_name: str, attr: str,
                        label: str, unit: str):
        self._dev_path = dev_path; self._dev_name = dev_name; self._attr = attr
        self._dev_display.setText(dev_name or dev_path)
        self._attr_display.setText(attr)
        self.lbl.setText(label); self.unit_edit.setText(unit)

    def set_registry(self, registry: list):
        pass   # device info comes from Setup Defaults

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _on_mode(self, m):
        self.npts_spin.setVisible(m == 0); self.step_spin.setVisible(m == 1); self._upd()

    def _upd(self):
        span = abs(self.dir_list.first_stop() - self.dir_list.first_start())
        if self.rb_n.isChecked():
            n    = max(2, self.npts_spin.value())
            step = span / (n - 1) if n > 1 else span
            self.comp_lbl.setText(f"{self._step_prefix} = {step:.4g}")
        else:
            step = max(1e-9, self.step_spin.value())
            n    = max(2, int(round(span / step)) + 1)
            self.comp_lbl.setText(f"N = {n}")

    def get_npts(self) -> int:
        span = abs(self.dir_list.first_stop() - self.dir_list.first_start())
        if self.rb_n.isChecked(): return max(2, self.npts_spin.value())
        return max(2, int(round(span / max(1e-9, self.step_spin.value()))) + 1)

    def load(self, pfx: str, cfg: dict, enabled: bool = True):
        self.scan_cb.setChecked(enabled)
        # Prefer explicit directions list; fall back to legacy start/stop keys
        dirs = cfg.get(f"{pfx}_directions")
        if dirs:
            self.dir_list.load_directions(dirs)
        else:
            s = cfg.get(f"{pfx}_start", 0.0)
            e = cfg.get(f"{pfx}_stop",  10.0)
            self.dir_list.load_directions([[s, e]])
        self.rb_n.setChecked(True)
        self.npts_spin.setValue(int(cfg.get(f"{pfx}_npts", 51)))
        self._upd()

    def get_partial(self, pfx: str) -> dict:
        dirs = self.dir_list.get_directions()
        return {
            f"{pfx}_device":      self._dev_path,
            f"{pfx}_device_name": self._dev_name,
            f"{pfx}_attr":        self._attr,
            f"{pfx}_label":       self.lbl.text(),
            f"{pfx}_unit":        self.unit_edit.text().strip() or "µm",
            f"{pfx}_start":       dirs[0][0],   # first direction start (backward compat)
            f"{pfx}_stop":        dirs[0][1],   # first direction stop  (backward compat)
            f"{pfx}_npts":        self.get_npts(),
            f"{pfx}_directions":  dirs,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TrajectoryPanel — Spatial Scan (axis groupboxes toggle) | Field Scan
# ─────────────────────────────────────────────────────────────────────────────
class TrajectoryPanel(QWidget):
    scan_mode_changed = pyqtSignal(str)   # "SPATIAL", "FIELD", or "TEMP_SWEEP"

    def __init__(self, setup_getter, parent=None, hw_panel_class=None):
        super().__init__(parent)
        self._setup_getter = setup_getter
        self._hw_panel_class = hw_panel_class or HardwarePanel
        # Rolling field monitor history (≤120 points at 500 ms = 60 s window)
        self._field_hist_t: collections.deque = collections.deque(maxlen=120)
        self._field_hist_v: collections.deque = collections.deque(maxlen=120)
        self._field_t0: float = time.time()
        root = QVBoxLayout(self); root.setContentsMargins(8, 6, 8, 6); root.setSpacing(6)

        # ── Scan type selector — styled pill buttons ─────────────────────────
        type_row = QHBoxLayout(); type_row.setSpacing(0)
        self.scan_bg = QButtonGroup(self); self.scan_bg.setExclusive(True)
        _pill_labels = [("Spatial", 0), ("Field / Temperature", 1)]
        for idx, (label, bid) in enumerate(_pill_labels):
            b = QPushButton(label)
            b.setCheckable(True); b.setChecked(idx == 0)
            b.setFixedHeight(28); b.setMinimumWidth(80)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            # Rounded ends for first/last, flat middle
            if idx == 0:
                radius = "border-top-left-radius:6px;border-bottom-left-radius:6px;border-top-right-radius:0;border-bottom-right-radius:0;"
            elif idx == len(_pill_labels) - 1:
                radius = "border-top-left-radius:0;border-bottom-left-radius:0;border-top-right-radius:6px;border-bottom-right-radius:6px;"
            else:
                radius = "border-radius:0;"
            b.setStyleSheet(
                f"QPushButton{{background:#252538;border:1px solid #45475a;"
                f"color:#6c7086;font-size:11px;font-weight:bold;padding:0 14px;{radius}}}"
                f"QPushButton:hover{{background:#313244;color:#cdd6f4;}}"
                f"QPushButton:checked{{background:#89b4fa;color:#1e1e2e;border-color:#89b4fa;}}")
            self.scan_bg.addButton(b, bid)
            type_row.addWidget(b)
        self.scan_bg.idClicked.connect(lambda _: self._on_type_changed())
        type_row.addStretch()
        self._type_row = type_row   # exposed so samba_cryo can append extra widgets
        root.addLayout(type_row)

        # ── Spatial panel ─────────────────────────────────────────────────────
        self.spatial_w = QWidget(); sp_l = QVBoxLayout(self.spatial_w)
        sp_l.setContentsMargins(0, 0, 0, 0); sp_l.setSpacing(4)

        # ActuatorGroups are now checkable — the title checkbox IS the on/off toggle.
        act_row = QHBoxLayout(); act_row.setSpacing(6)
        self.act1_grp = ActuatorGroup(
            "X axis",
            "smaract2/control/IR-controller", "x", "X", "nm",
            0, 50000, 51, step_prefix="Δx", enabled=True, registry=[])
        self.act2_grp = ActuatorGroup(
            "Y axis",
            "smaract2/control/IR-controller", "y", "Y", "nm",
            0, 50000, 51, step_prefix="Δy", enabled=False, registry=[])
        act_row.addWidget(self.act1_grp); act_row.addWidget(self.act2_grp)
        sp_l.addLayout(act_row)

        # Time-scan info label lives inside act1_grp (X axis window), row 6
        self.time_scan_lbl = QLabel(
            "\u23f1  Time scan active\n"
            "No stage movement. Sensors sampled N times.\n"
            "Change N using the spinbox above.")
        self.time_scan_lbl.setStyleSheet(
            "color:#cba6f7;font-size:10px;background:#2a273f;"
            "border:1px solid #6c3483;border-radius:4px;padding:5px 6px;")
        self.time_scan_lbl.setWordWrap(True)
        self.time_scan_lbl.setVisible(False)
        # Row 6 = below the N/Δ row (rows 0-4 = scan_cb, device, attr, dir_list, N/Δ)
        self.act1_grp.layout().addWidget(self.time_scan_lbl, 6, 0, 1, 4)


        root.addWidget(self.spatial_w)

        # Connect axis toggles → update time-scan banner visibility
        self.act1_grp.scan_cb.stateChanged.connect(lambda _: self._on_axis_toggled())
        self.act2_grp.scan_cb.stateChanged.connect(lambda _: self._on_axis_toggled())
        # Cross-axis retrace mutual exclusion: only one axis may have retrace in 2D
        self.act1_grp.dir_list.changed.connect(self._sync_retrace_buttons)
        self.act2_grp.dir_list.changed.connect(self._sync_retrace_buttons)

        # ── Field panel ───────────────────────────────────────────────────────
        # Layout (all always visible, side-by-side):
        #   [AC params]  |  [Field / Hc monitor]  |  [DC params]  |  [DC live plot]
        # A compact sub-mode selector at the top selects which scan Start runs.
        self.field_w = QWidget(); fw_root = QVBoxLayout(self.field_w)
        fw_root.setContentsMargins(0, 0, 0, 0); fw_root.setSpacing(3)

        # Sub-mode row (compact radio buttons)
        fsub_row = QHBoxLayout(); fsub_row.setSpacing(12)
        self._fsub_bg = QButtonGroup(self)
        self.rb_ac_sw = QRadioButton("▶  Field Sweep");  self.rb_ac_sw.setChecked(True)
        self.rb_dc_hy = QRadioButton("▶  Temperature Sweep")
        self._fsub_bg.addButton(self.rb_ac_sw, 0)
        self._fsub_bg.addButton(self.rb_dc_hy, 1)
        self._fsub_bg.idClicked.connect(self._on_submode_changed)
        for rb in (self.rb_ac_sw, self.rb_dc_hy): fsub_row.addWidget(rb)
        fsub_row.addStretch()
        fw_root.addLayout(fsub_row)

        # Main horizontal row
        horiz = QHBoxLayout(); horiz.setSpacing(5); horiz.setContentsMargins(0, 0, 0, 0)

        # ── Column 1: AC params ───────────────────────────────────────────────
        self._ac_grp = QGroupBox("Field Sweep"); fgl = QGridLayout(self._ac_grp)
        fgl.setSpacing(4); fgl.setContentsMargins(8, 8, 8, 8)

        # Row 0: Field device dropdown
        fgl.addWidget(QLabel("Device:"), 0, 0)
        self._ac_dev_combo = NoScrollComboBox()
        self._ac_dev_combo.setStyleSheet("font-size:10px;")
        self._ac_dev_combo.addItem("(setup default)", "")
        self._ac_dev_combo.currentIndexChanged.connect(self._on_field_dev_changed)
        fgl.addWidget(self._ac_dev_combo, 0, 1)

        # Row 1: Attribute dropdown
        fgl.addWidget(QLabel("Attr:"), 1, 0)
        self._ac_attr_combo = NoScrollComboBox()
        self._ac_attr_combo.setStyleSheet("font-size:10px;")
        self._ac_attr_combo.addItem("(setup default)", "")
        fgl.addWidget(self._ac_attr_combo, 1, 1)

        # Row 2: Multi-segment field list
        self._seg_list = FieldSegmentList()
        fgl.addWidget(self._seg_list, 2, 0, 1, 2)

        # Row 2: AC monitor dropdowns
        ac_mon_row = QHBoxLayout(); ac_mon_row.setSpacing(4)
        ac_mon_row.addWidget(QLabel("Mon:"))
        self._ac_mon_dev = NoScrollComboBox(); self._ac_mon_dev.setStyleSheet("font-size:10px;")
        self._ac_mon_dev.currentIndexChanged.connect(lambda: self._on_mon_dev_changed("ac"))
        ac_mon_row.addWidget(self._ac_mon_dev, stretch=1)
        self._ac_mon_ch = NoScrollComboBox(); self._ac_mon_ch.setStyleSheet("font-size:10px;")
        ac_mon_row.addWidget(self._ac_mon_ch, stretch=1)
        fgl.addLayout(ac_mon_row, 3, 0, 1, 2)
        self._ac_grp.setMinimumWidth(260)
        horiz.addWidget(self._ac_grp)

        # ── Column 2: Shared field monitor canvas (no dropdowns here) ─────────
        self._field_hist_t: collections.deque = collections.deque(maxlen=120)
        self._field_hist_v: collections.deque = collections.deque(maxlen=120)
        self._field_t0: float = time.time()
        self._dc_hc_hist:  collections.deque = collections.deque(maxlen=200)
        self._dc_hsh_hist: collections.deque = collections.deque(maxlen=200)
        self._dc_cyc_hist: collections.deque = collections.deque(maxlen=200)
        self._field_fig = Figure(figsize=(2.8, 1.8), dpi=90, facecolor="#1e1e2e")
        self._field_ax  = self._field_fig.add_subplot(111)
        self._field_canvas = FigureCanvas(self._field_fig)
        self._field_canvas.setMinimumHeight(120); self._field_canvas.setMinimumWidth(160)
        self._style_field_ax("ac")
        horiz.addWidget(self._field_canvas, stretch=2)

        # ── Column 3: Temperature Sweep params ─────────────────────────────────
        self._dc_grp = QGroupBox("Temperature Sweep"); dc_pgl = QGridLayout(self._dc_grp)
        dc_pgl.setSpacing(3); dc_pgl.setContentsMargins(8, 8, 8, 8)

        # Device
        dc_pgl.addWidget(QLabel("Device:"), 0, 0)
        self.dc_dev_combo = NoScrollComboBox()
        self.dc_dev_combo.setStyleSheet("font-size:10px;")
        self.dc_dev_combo.addItem("hpp-N42/attoDRY/attoDRY", "hpp-N42/attoDRY/attoDRY")
        self.dc_dev_combo.currentIndexChanged.connect(self._on_temp_dev_changed)
        dc_pgl.addWidget(self.dc_dev_combo, 0, 1, 1, 3)

        # Attribute
        dc_pgl.addWidget(QLabel("Attr:"), 1, 0)
        self._temp_attr_combo = NoScrollComboBox()
        self._temp_attr_combo.setStyleSheet("font-size:10px;")
        self._temp_attr_combo.addItem("Temperature", "Temperature")
        dc_pgl.addWidget(self._temp_attr_combo, 1, 1, 1, 3)

        def _dbl(lo, hi, dec, v):
            w = NoScrollDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec); w.setValue(v); return w

        # Start / Stop
        dc_pgl.addWidget(QLabel("Start (K):"), 2, 0)
        self._temp_start = _dbl(0.0, 400, 2, 4.0);  dc_pgl.addWidget(self._temp_start, 2, 1)
        dc_pgl.addWidget(QLabel("Stop (K):"),  2, 2)
        self._temp_stop  = _dbl(0.0, 400, 2, 300.0); dc_pgl.addWidget(self._temp_stop,  2, 3)

        # Row 3: N ↔ ΔT toggle (settle time comes from the Timing panel)
        ns_w = QWidget(); ns_l = QHBoxLayout(ns_w)
        ns_l.setContentsMargins(0, 0, 0, 0); ns_l.setSpacing(4)
        self._temp_mode_bg = QButtonGroup(self)
        self._temp_rb_n  = QRadioButton("N:"); self._temp_rb_n.setChecked(True)
        self._temp_rb_dT = QRadioButton("ΔT (K):")
        self._temp_mode_bg.addButton(self._temp_rb_n,  0)
        self._temp_mode_bg.addButton(self._temp_rb_dT, 1)
        self._temp_npts = NoScrollSpinBox(); self._temp_npts.setRange(2, 10000); self._temp_npts.setValue(51)
        self._temp_dT   = NoScrollDoubleSpinBox(); self._temp_dT.setRange(0.01, 400); self._temp_dT.setDecimals(2)
        self._temp_dT.setValue(10.0); self._temp_dT.setVisible(False)
        self._temp_comp_lbl = QLabel(); self._temp_comp_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        for w in [self._temp_rb_n, self._temp_npts, self._temp_rb_dT, self._temp_dT, self._temp_comp_lbl]:
            ns_l.addWidget(w)
        ns_l.addStretch()
        dc_pgl.addWidget(ns_w, 3, 0, 1, 4)
        self._temp_mode_bg.idClicked.connect(self._on_temp_mode)

        self._temp_info = QLabel()
        self._temp_info.setStyleSheet("color:#6c7086;font-size:10px;")
        dc_pgl.addWidget(self._temp_info, 4, 0, 1, 4)
        for w in [self._temp_start, self._temp_stop, self._temp_npts, self._temp_dT]:
            w.valueChanged.connect(self._upd_temp_info)
        self._upd_temp_info()

        # Temperature monitor dropdowns
        dc_mon_row = QHBoxLayout(); dc_mon_row.setSpacing(4)
        dc_mon_row.addWidget(QLabel("Mon:"))
        self._dc_mon_dev = NoScrollComboBox(); self._dc_mon_dev.setStyleSheet("font-size:10px;")
        self._dc_mon_dev.currentIndexChanged.connect(lambda: self._on_mon_dev_changed("dc"))
        dc_mon_row.addWidget(self._dc_mon_dev, stretch=1)
        self._dc_mon_ch = NoScrollComboBox(); self._dc_mon_ch.setStyleSheet("font-size:10px;")
        dc_mon_row.addWidget(self._dc_mon_ch, stretch=1)
        dc_pgl.addLayout(dc_mon_row, 5, 0, 1, 4)
        self._dc_grp.setMinimumWidth(280)
        horiz.addWidget(self._dc_grp)

        fw_root.addLayout(horiz)
        self.field_w.setVisible(False)
        root.addWidget(self.field_w)
        self._on_submode_changed(0)   # apply initial highlight


        # ── Bottom row: Timing (tg) + Metadata (mg) + Hardware (hw) ──────────
        # tg = Timing group: integration time, settle time, move timeout
        # mg = MokeMetadataGroup: operator, sample, notes, incidence, polarization, λ/2, λ/4, noDC
        # hw = HardwarePanel: current source controls (left) + field/relay controls (right)
        # Width is controlled by stretch factors and setMaximumWidth on hw.
        bot = QHBoxLayout(); bot.setSpacing(4)

        # tg — Timing group
        tg  = QGroupBox("Timing"); tl = QGridLayout(tg)
        tl.setSpacing(3); tl.setContentsMargins(6, 6, 6, 6)
        def dbl(lo, hi, dec, v):
            w = NoScrollDoubleSpinBox(); w.setRange(lo,hi); w.setDecimals(dec); w.setValue(v); return w
        tl.addWidget(QLabel("Int (s):"),   0, 0); self.int_time = dbl(0.001,3600,3,0.1); tl.addWidget(self.int_time, 0, 1)
        tl.addWidget(QLabel("Settle (s):"), 1, 0); self.settle   = dbl(0,10,3,0.05);      tl.addWidget(self.settle,   1, 1)
        tl.addWidget(QLabel("T.out (s):"),  2, 0); self.timeout  = dbl(0.1,300,1,15.0);   tl.addWidget(self.timeout,  2, 1)
        # Adaptive settle — extra wait proportional to step size (ANM200 creep compensation)
        self.adap_settle_cb = QCheckBox("Adaptive settle")
        self.adap_settle_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.adap_settle_cb.setToolTip("Extra settle per step — compensates ANM200 piezo creep")
        tl.addWidget(self.adap_settle_cb, 3, 0, 1, 2)
        tl.addWidget(QLabel("s/µm:"), 4, 0)
        self.adap_settle_k = dbl(0, 10, 4, 0.05)
        self.adap_settle_k.setToolTip("Extra seconds of settle per µm of position step")
        tl.addWidget(self.adap_settle_k, 4, 1)
        bot.addWidget(tg)

        # mg — Metadata group (MOKE-specific fields)
        mg = MokeMetadataGroup("Metadata")
        self.meta = mg
        bot.addWidget(mg)

        # hw — Hardware panel (uses injected class for setup-specific panels)
        self.hw = self._hw_panel_class(self._setup_getter, "Hardware")
        self.hw.setMaximumWidth(700)
        bot.addWidget(self.hw)

        root.addLayout(bot)
        self._on_axis_toggled()

        # save_dir is managed by the action bar, stored here for config persistence
        self._save_dir = os.path.expanduser("~/moke_data")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _on_type_changed(self):
        mode = self.scan_bg.checkedId()  # 0=Spatial, 1=Field/Temperature
        self.spatial_w.setVisible(mode == 0)
        self.field_w.setVisible(mode == 1)
        if mode == 0:
            self.scan_mode_changed.emit("SPATIAL")
        else:
            self.scan_mode_changed.emit("TEMP_SWEEP" if self._fsub_bg.checkedId() == 1 else "FIELD")

    def _on_submode_changed(self, mode_id: int):
        """Highlight the active scan mode groupbox; no layout change needed."""
        _ACT  = ("QGroupBox{border:1px solid #89b4fa;border-radius:6px;"
                 "margin-top:9px;padding-top:9px;font-weight:bold;color:#89b4fa;}"
                 "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}")
        _IDLE = ("QGroupBox{border:1px solid #45475a;border-radius:6px;"
                 "margin-top:9px;padding-top:9px;font-weight:bold;color:#45475a;}"
                 "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}")
        self._ac_grp.setStyleSheet(_ACT  if mode_id == 0 else _IDLE)
        self._dc_grp.setStyleSheet(_ACT  if mode_id == 1 else _IDLE)
        self.scan_mode_changed.emit("TEMP_SWEEP" if mode_id == 1 else "FIELD")

    def _on_temp_mode(self, m: int):
        self._temp_npts.setVisible(m == 0)
        self._temp_dT.setVisible(m == 1)
        self._upd_temp_info()

    def _temp_get_npts(self) -> int:
        if self._temp_rb_n.isChecked():
            return max(2, self._temp_npts.value())
        span = abs(self._temp_stop.value() - self._temp_start.value())
        return max(2, int(round(span / max(0.01, self._temp_dT.value()))) + 1)

    def _upd_temp_info(self):
        """Show N/ΔT complement and range summary for the temperature sweep."""
        try:
            start = self._temp_start.value()
            stop  = self._temp_stop.value()
            span  = abs(stop - start)
            if self._temp_rb_n.isChecked():
                nn     = max(2, self._temp_npts.value())
                step_K = span / (nn - 1) if nn > 1 else span
                self._temp_comp_lbl.setText(f"ΔT = {step_K:.2f} K")
            else:
                step_K = max(0.01, self._temp_dT.value())
                nn     = max(2, int(round(span / step_K)) + 1)
                self._temp_comp_lbl.setText(f"N = {nn}")
            self._temp_info.setText(f"{start:.1f} → {stop:.1f} K  |  ΔT = {step_K:.2f} K/step")
        except Exception:
            pass

    def _on_axis_toggled(self):
        x_on = self.act1_grp.scan_cb.isChecked()
        y_on = self.act2_grp.scan_cb.isChecked()
        time_mode = (not x_on and not y_on)

        # Time-scan banner inside the X axis group
        self.time_scan_lbl.setVisible(time_mode)

        # Highlight the X axis groupbox in purple when in time-scan mode
        # so the user immediately sees where to set N
        if time_mode:
            self.act1_grp.setStyleSheet(
                "QGroupBox{border:1px solid #cba6f7;border-radius:6px;"
                "margin-top:9px;padding-top:9px;font-weight:bold;color:#cba6f7;}"
                "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}")
        else:
            self.act1_grp.setStyleSheet(
                "QGroupBox{border:1px solid #45475a;border-radius:6px;"
                "margin-top:9px;padding-top:9px;font-weight:bold;color:#89b4fa;}"
                "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}")
        self._sync_retrace_buttons()

    def _sync_retrace_buttons(self):
        """In 2D mode: only one axis can carry retrace. Disable the add-retrace
        button on whichever axis does not yet have a second direction once the
        other axis already does."""
        x_on = self.act1_grp.scan_cb.isChecked()
        y_on = self.act2_grp.scan_cb.isChecked()
        if not (x_on and y_on):
            # 1D or time — no cross-axis restriction; restore both buttons
            self.act1_grp.dir_list._refresh_add_btn()
            self.act2_grp.dir_list._refresh_add_btn()
            return
        x_has_retrace = len(self.act1_grp.dir_list._rows) > 1
        y_has_retrace = len(self.act2_grp.dir_list._rows) > 1
        self.act1_grp.dir_list._add_btn.setEnabled(
            not y_has_retrace and len(self.act1_grp.dir_list._rows) < 2)
        self.act2_grp.dir_list._add_btn.setEnabled(
            not x_has_retrace and len(self.act2_grp.dir_list._rows) < 2)

    def _on_field_mode(self, m):
        self.fn.setVisible(m == 0); self.fd.setVisible(m == 1)
        self._upd_field_comp()

    def _upd_field_comp(self):
        span = abs(self.fe.value() - self.fs.value())
        if self.rb_fn.isChecked():
            n    = max(2, self.fn.value())
            step = span / (n-1) if n > 1 else span
            self.field_comp_lbl.setText(f"Δ A = {step:.4g}")
        else:
            step = max(1e-9, self.fd.value())
            n    = max(2, int(round(span / step)) + 1)
            self.field_comp_lbl.setText(f"N = {n}")

    def _get_field_npts(self) -> int:
        span = abs(self.fe.value() - self.fs.value())
        if self.rb_fn.isChecked(): return max(2, self.fn.value())
        return max(2, int(round(span / max(1e-9, self.fd.value()))) + 1)

    # ── Field / Hc convergence monitor ───────────────────────────────────────
    def populate_monitor_combo(self, registry: list):
        """Fill monitor, field device/attr, temp device/attr, and actuator combos
        from the device registry.  Safe to call multiple times."""
        self._mon_registry = registry

        # ── Field + Temp monitor dropdowns ────────────────────────────────────
        for dev_combo in (self._ac_mon_dev, self._dc_mon_dev):
            prev = dev_combo.currentText()
            dev_combo.blockSignals(True); dev_combo.clear()
            for dev in registry:
                dev_combo.addItem(dev["name"], dev["tango_path"])
            if prev:
                idx = dev_combo.findText(prev)
                if idx >= 0: dev_combo.setCurrentIndex(idx)
            dev_combo.blockSignals(False)
        self._on_mon_dev_changed("ac")
        self._on_mon_dev_changed("dc")

        # ── Temperature sweep device combo (all devices) ─────────────────────
        prev_dc = self.dc_dev_combo.currentData() or ""
        self.dc_dev_combo.blockSignals(True); self.dc_dev_combo.clear()
        for d in registry:
            self.dc_dev_combo.addItem(d["name"], d["tango_path"])
        if prev_dc:
            for i in range(self.dc_dev_combo.count()):
                if self.dc_dev_combo.itemData(i) == prev_dc:
                    self.dc_dev_combo.setCurrentIndex(i); break
        self.dc_dev_combo.blockSignals(False)
        self._on_temp_dev_changed()

        # ── Field sweep device combo (magnet-type + setup-default) ───────────
        mag_devs = [d for d in registry if d.get("type") == "magnet"]
        show_ac = mag_devs if mag_devs else registry
        prev_ac = self._ac_dev_combo.currentData()
        self._ac_dev_combo.blockSignals(True); self._ac_dev_combo.clear()
        self._ac_dev_combo.addItem("(setup default)", "")
        for d in show_ac:
            self._ac_dev_combo.addItem(d["name"], d["tango_path"])
        if prev_ac is not None:
            for i in range(self._ac_dev_combo.count()):
                if self._ac_dev_combo.itemData(i) == prev_ac:
                    self._ac_dev_combo.setCurrentIndex(i); break
        self._ac_dev_combo.blockSignals(False)
        self._on_field_dev_changed()

        # Actuator device info is set via set_actuator_defaults(), not the registry.

    def _on_mon_dev_changed(self, which: str = "ac"):
        """Populate channel dropdown from the selected device's channels."""
        if which == "dc":
            dev_combo, ch_combo = self._dc_mon_dev, self._dc_mon_ch
        else:
            dev_combo, ch_combo = self._ac_mon_dev, self._ac_mon_ch
        prev_ch = ch_combo.currentText()
        ch_combo.blockSignals(True); ch_combo.clear()
        dev_name = dev_combo.currentText()
        for d in getattr(self, '_mon_registry', []):
            if d["name"] == dev_name:
                for ch in d.get("channels", []):
                    label = ch.get("label", ch.get("attr", "?"))
                    ch_combo.addItem(label, ch.get("attr", ""))
                break
        if prev_ch:
            idx = ch_combo.findText(prev_ch)
            if idx >= 0: ch_combo.setCurrentIndex(idx)
        ch_combo.blockSignals(False)

    def get_monitor_device(self):
        """Return (tango_path, attribute) for the active monitor.
        Uses AC dropdowns when AC mode selected, DC when DC selected."""
        is_dc = self._fsub_bg.checkedId() == 1
        if is_dc:
            dev_path = self._dc_mon_dev.currentData()
            ch_attr  = self._dc_mon_ch.currentData()
        else:
            dev_path = self._ac_mon_dev.currentData()
            ch_attr  = self._ac_mon_ch.currentData()
        if dev_path and ch_attr:
            return (dev_path, ch_attr)
        return ("", "")

    def load_monitor_settings(self, cfg: dict):
        """Restore AC and DC monitor dropdown selections from a saved config.
        Must be called *after* populate_monitor_combo so the items exist."""
        for which, dev_combo, ch_combo, dev_key, attr_key in [
            ("ac", self._ac_mon_dev, self._ac_mon_ch,
             "ac_monitor_device", "ac_monitor_attr"),
            ("dc", self._dc_mon_dev, self._dc_mon_ch,
             "dc_monitor_device", "dc_monitor_attr"),
        ]:
            dev_name = cfg.get(dev_key, "")
            ch_attr  = cfg.get(attr_key, "")
            if dev_name:
                idx = dev_combo.findText(dev_name)
                if idx >= 0:
                    dev_combo.blockSignals(True)
                    dev_combo.setCurrentIndex(idx)
                    dev_combo.blockSignals(False)
                    self._on_mon_dev_changed(which)   # repopulate channel combo
            if ch_attr:
                idx = ch_combo.findData(ch_attr)
                if idx >= 0:
                    ch_combo.setCurrentIndex(idx)

    def _style_field_ax(self, mode: str = "ac"):
        self._field_ax.set_facecolor("#12121f")
        self._field_ax.tick_params(colors="#aaaacc", labelsize=7)
        for sp in self._field_ax.spines.values(): sp.set_edgecolor("#3a3a5c")
        if mode == "ac":
            self._field_ax.set_xlabel("Time (s)",   color="#aaaacc", fontsize=7)
            self._field_ax.set_ylabel("Field (T)",  color="#aaaacc", fontsize=7)
            self._field_ax.set_title("Field monitor (last 60 s)", color="#6c7086", fontsize=7)
        else:
            self._field_ax.set_xlabel("Point",      color="#aaaacc", fontsize=7)
            self._field_ax.set_ylabel("Signal",     color="#aaaacc", fontsize=7)
            self._field_ax.set_title("DC monitor (live)", color="#6c7086", fontsize=7)

    def update_field_monitor(self, val_T, mode: str = "ac"):
        """Called every 500 ms — uses the monitor dropdown device.
        mode: 'ac' or 'dc' to style the axes appropriately."""
        if val_T is None: return
        t = time.time() - self._field_t0
        self._field_hist_t.append(t)
        self._field_hist_v.append(val_T)
        if not self.field_w.isVisible(): return
        self._field_ax.cla(); self._style_field_ax(mode)
        if len(self._field_hist_t) > 1:
            ts = list(self._field_hist_t); vs = list(self._field_hist_v)
            self._field_ax.plot(ts, vs, color="#89b4fa", linewidth=1.3)
            self._field_ax.axhline(0, color="#45475a", linewidth=0.6, linestyle="--")
            x_lo = max(0.0, ts[-1] - 60)
            self._field_ax.set_xlim(x_lo, ts[-1] + 1)
        self._field_fig.tight_layout(pad=0.4)
        self._field_canvas.draw_idle()

    def update_dc_cycle(self, cycle: int, hc: float, hshift: float):
        """Called after each DC cycle — Hc/Hshift are logged to status; no separate plot."""
        pass   # loop is shown by update_dc_live; scalars appear in the status bar

    def reset_dc_monitor(self):
        """Clear DC history and reset the shared monitor to DC mode at scan start."""
        self._dc_hc_hist.clear()
        self._dc_hsh_hist.clear()
        self._dc_cyc_hist.clear()
        self._field_hist_t.clear()
        self._field_hist_v.clear()
        self._field_t0 = time.time()
        self._field_ax.cla(); self._style_field_ax("dc")
        self._field_fig.tight_layout(pad=0.4)
        self._field_canvas.draw_idle()

    def update_dc_live(self, field_arr: "np.ndarray", y_bufs: dict):
        """
        Draw the selected DC channel into the shared monitor canvas.
        Called via dc_loop_ready signal after each completed cycle.
        field_arr : 1-D array of field values in mT
        y_bufs    : {label: 1-D array} for each active channel

        The DC monitor dropdown selects which channel to display:
        if the selected channel attr is 'field' → plot field_arr vs point index,
        otherwise look up the selected label in y_bufs.
        """
        self._field_ax.cla(); self._style_field_ax("dc")

        # Determine what to plot from DC monitor dropdown
        sel_attr  = self._dc_mon_ch.currentData() or "field"
        sel_label = self._dc_mon_ch.currentText() or "Field"

        if sel_attr == "field" or sel_label.lower().startswith("field"):
            plot_data = field_arr
            plot_label = "Field (mT)"
        elif sel_label in y_bufs:
            plot_data = y_bufs[sel_label]
            plot_label = sel_label
        else:
            # Fallback to field
            plot_data = field_arr
            plot_label = "Field (mT)"

        if not isinstance(plot_data, np.ndarray):
            plot_data = np.array(plot_data)
        mask = np.isfinite(plot_data)
        if mask.any():
            pts = np.arange(len(plot_data))
            self._field_ax.plot(pts[mask], plot_data[mask],
                                color="#89b4fa", linewidth=1.2)
            self._field_ax.axhline(0, color="#45475a", linewidth=0.6, linestyle="--")
        self._field_ax.set_ylabel(plot_label, color="#aaaacc", fontsize=7)
        self._field_fig.tight_layout(pad=0.4)
        self._field_canvas.draw_idle()

    # ── Field/Temp device-change helpers ────────────────────────────────────
    def _on_field_dev_changed(self):
        """When field sweep device changes, populate attribute combo from registry."""
        dev_path = self._ac_dev_combo.currentData()
        prev = self._ac_attr_combo.currentData()
        self._ac_attr_combo.blockSignals(True); self._ac_attr_combo.clear()
        self._ac_attr_combo.addItem("(setup default)", "")
        if dev_path:
            for d in getattr(self, '_mon_registry', []):
                if d.get("tango_path") == dev_path:
                    for ch in d.get("channels", []):
                        self._ac_attr_combo.addItem(
                            ch.get("label", ch.get("attr", "?")), ch.get("attr", ""))
                    break
        if prev:
            idx = self._ac_attr_combo.findData(prev)
            if idx >= 0: self._ac_attr_combo.setCurrentIndex(idx)
        self._ac_attr_combo.blockSignals(False)

    def _on_temp_dev_changed(self):
        """When temp sweep device changes, populate attribute combo from registry."""
        dev_path = self.dc_dev_combo.currentData()
        prev = self._temp_attr_combo.currentData()
        self._temp_attr_combo.blockSignals(True); self._temp_attr_combo.clear()
        self._temp_attr_combo.addItem("Temperature", "Temperature")
        if dev_path:
            for d in getattr(self, '_mon_registry', []):
                if d.get("tango_path") == dev_path:
                    for ch in d.get("channels", []):
                        self._temp_attr_combo.addItem(
                            ch.get("label", ch.get("attr", "?")), ch.get("attr", ""))
                    break
        if prev:
            idx = self._temp_attr_combo.findData(prev)
            if idx >= 0: self._temp_attr_combo.setCurrentIndex(idx)
        self._temp_attr_combo.blockSignals(False)

    # ── Defaults injection ────────────────────────────────────────────────────
    def set_actuator_defaults(self,
                              act1_dev: str, act1_attr: str,
                              act1_lbl: str, act1_unit: str,
                              act2_dev: str, act2_attr: str,
                              act2_lbl: str, act2_unit: str):
        """Called when Setup Defaults change — push device info into both actuator groups."""
        self.act1_grp.set_device_info(act1_dev, "", act1_attr, act1_lbl, act1_unit)
        self.act2_grp.set_device_info(act2_dev, "", act2_attr, act2_lbl, act2_unit)

    # ── Load / get config ─────────────────────────────────────────────────────
    def load_config(self, cfg: dict):
        scan_t   = cfg.get("scan_type", "SPATIAL")
        is_field = scan_t in ("FIELD", "TEMP_SWEEP")
        is_temp  = scan_t == "TEMP_SWEEP"

        # Select pill button: 0=Spatial, 1=Field/Temperature
        if is_field:
            self.scan_bg.button(1).setChecked(True)
        else:
            self.scan_bg.button(0).setChecked(True)

        if is_field:
            (self.rb_dc_hy if is_temp else self.rb_ac_sw).setChecked(True)
            self._on_submode_changed(1 if is_temp else 0)
        self.act1_grp.load("act1", cfg, enabled=cfg.get("scan_x", True))
        self.act2_grp.load("act2", cfg, enabled=cfg.get("scan_y", False))
        # Field segments
        segs = cfg.get("field_segments")
        if segs:
            self._seg_list.load_segments(segs)
        else:
            self._seg_list.load_segments([[
                cfg.get("field_start_A", -1.0),
                cfg.get("field_stop_A",   1.0),
                cfg.get("field_npts",     101),
            ]])
        # Field device + attribute
        field_dev = cfg.get("field_device", "")
        for i in range(self._ac_dev_combo.count()):
            if self._ac_dev_combo.itemData(i) == field_dev:
                self._ac_dev_combo.setCurrentIndex(i); break
        field_attr = cfg.get("field_current_attr", "")
        for i in range(self._ac_attr_combo.count()):
            if self._ac_attr_combo.itemData(i) == field_attr:
                self._ac_attr_combo.setCurrentIndex(i); break
        self.adap_settle_cb.setChecked(cfg.get("adaptive_settle_enabled", False))
        self.adap_settle_k.setValue(cfg.get("adaptive_settle_k", 0.05))
        self.int_time.setValue(cfg.get("integration_time", 0.1))
        self.settle.setValue(  cfg.get("settle_time",      0.05))
        self.timeout.setValue( cfg.get("move_timeout",     15.0))
        self.meta.load_values(cfg)
        # Temperature sweep params
        temp_dev = cfg.get("temp_device", "")
        for i in range(self.dc_dev_combo.count()):
            if self.dc_dev_combo.itemData(i) == temp_dev:
                self.dc_dev_combo.setCurrentIndex(i); break
        temp_attr = cfg.get("temp_attr", "Temperature")
        for i in range(self._temp_attr_combo.count()):
            if self._temp_attr_combo.itemData(i) == temp_attr:
                self._temp_attr_combo.setCurrentIndex(i); break
        self._temp_start.setValue(cfg.get("temp_start",  4.0))
        self._temp_stop.setValue( cfg.get("temp_stop",   300.0))
        if cfg.get("temp_dT"):
            self._temp_rb_dT.setChecked(True); self._on_temp_mode(1)
            self._temp_dT.setValue(cfg["temp_dT"])
        else:
            self._temp_rb_n.setChecked(True);  self._on_temp_mode(0)
            self._temp_npts.setValue(int(cfg.get("temp_npts", 51)))
        self._upd_temp_info()
        self._save_dir = os.path.expanduser(
            self._setup_getter().get("save_dir", "~/moke_data"))
        self._on_type_changed(); self._on_axis_toggled()

    def get_config_partial(self) -> dict:
        mode_id  = self.scan_bg.checkedId()  # 0=Spatial, 1=Field/Temperature
        is_field = mode_id == 1
        is_temp  = is_field and (self._fsub_bg.checkedId() == 1)

        if is_temp:
            # Temperature sweep: reuse FIELD scan engine with temp device/attr
            temp_dev  = self.dc_dev_combo.currentData() or ""
            temp_attr = self._temp_attr_combo.currentData() or "Temperature"
            start     = self._temp_start.value()
            stop      = self._temp_stop.value()
            npts      = self._temp_get_npts()
            use_dT    = self._temp_rb_dT.isChecked()
            return {
                "scan_type":    "FIELD",
                "scan_x": False, "scan_y": False,
                "field_segments":    [[start, stop, npts]],
                "field_start_A":     start,
                "field_stop_A":      stop,
                "field_npts":        npts,
                "field_device":      temp_dev,
                "field_current_attr": temp_attr,
                "field_x_label":     temp_attr,
                "field_x_unit":      "K",
                "integration_time":  self.int_time.value(),
                "settle_time":       self.settle.value(),   # from Timing panel
                "move_timeout":      self.timeout.value(),
                "ac_monitor_device": self._ac_mon_dev.currentText(),
                "ac_monitor_attr":   self._ac_mon_ch.currentData() or "",
                "dc_monitor_device": self._dc_mon_dev.currentText(),
                "dc_monitor_attr":   self._dc_mon_ch.currentData() or "",
                # Persistence keys for temperature sweep UI
                "temp_device":  temp_dev,
                "temp_attr":    temp_attr,
                "temp_start":   start,
                "temp_stop":    stop,
                "temp_npts":    npts,
                "temp_dT":      self._temp_dT.value() if use_dT else None,
                **self.meta.get_values(),
            }

        segs = self._seg_list.get_segments()
        p = {
            "scan_type": "FIELD" if is_field else "SPATIAL",
            "scan_x":    self.act1_grp.scan_cb.isChecked() if not is_field else False,
            "scan_y":    self.act2_grp.scan_cb.isChecked() if not is_field else False,
            "adaptive_settle_enabled": self.adap_settle_cb.isChecked(),
            "adaptive_settle_k":       self.adap_settle_k.value(),
        }
        p.update(self.act1_grp.get_partial("act1"))
        p.update(self.act2_grp.get_partial("act2"))
        p.update({
            "field_segments":    segs,
            "field_start_A":     segs[0][0]                 if segs else -1.0,
            "field_stop_A":      segs[-1][1]                if segs else  1.0,
            "field_npts":        sum(int(s[2]) for s in segs) if segs else 101,
            "field_device":      self._ac_dev_combo.currentData() or "",
            "field_current_attr": self._ac_attr_combo.currentData() or "",
            "field_x_label":     "Field",
            "field_x_unit":      "T",
            "integration_time":  self.int_time.value(),
            "settle_time":       self.settle.value(),
            "move_timeout":      self.timeout.value(),
            "ac_monitor_device": self._ac_mon_dev.currentText(),
            "ac_monitor_attr":   self._ac_mon_ch.currentData() or "",
            "dc_monitor_device": self._dc_mon_dev.currentText(),
            "dc_monitor_attr":   self._dc_mon_ch.currentData() or "",
        })
        p.update(self.meta.get_values())
        return p


# ─────────────────────────────────────────────────────────────────────────────
# ScanlistPanel
# ─────────────────────────────────────────────────────────────────────────────
class ScanlistPanel(QWidget):
    def __init__(self, setup_getter, parent=None, hw_panel_class=None):
        super().__init__(parent)
        self._setup_getter = setup_getter
        self._hw_panel_class = hw_panel_class or HardwarePanel
        root = QVBoxLayout(self); root.setContentsMargins(8, 6, 8, 6); root.setSpacing(6)

        # ── Top row: active config + timing + metadata side by side ─────────
        top_row = QHBoxLayout(); top_row.setSpacing(8)

        info_w = QWidget(); info_l = QVBoxLayout(info_w)
        info_l.setContentsMargins(0, 0, 0, 0); info_l.setSpacing(4)
        hl0 = QHBoxLayout(); hl0.addWidget(QLabel("Active config:"))
        self.active_lbl = QLabel("—"); self.active_lbl.setStyleSheet("color:#89b4fa;font-weight:bold;")
        hl0.addWidget(self.active_lbl); hl0.addStretch()
        info_l.addLayout(hl0)
        info_l.addStretch()
        top_row.addWidget(info_w)

        # ── Timing group — kept in sync with Trajectory tab ──────────────────
        tg = QGroupBox("Timing"); tl = QGridLayout(tg)
        tl.setSpacing(3); tl.setContentsMargins(6, 6, 6, 6)
        def _dbl(lo, hi, dec, v):
            w = NoScrollDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec); w.setValue(v); return w
        tl.addWidget(QLabel("Int (s):"),    0, 0); self.int_time = _dbl(0.001, 3600, 3, 0.1); tl.addWidget(self.int_time, 0, 1)
        tl.addWidget(QLabel("Settle (s):"), 1, 0); self.settle   = _dbl(0,     10,   3, 0.05); tl.addWidget(self.settle,   1, 1)
        tl.addWidget(QLabel("T.out (s):"),  2, 0); self.timeout  = _dbl(0.1,   300,  1, 15.0); tl.addWidget(self.timeout,  2, 1)
        top_row.addWidget(tg)

        self.meta = MokeMetadataGroup("Metadata")
        self.meta.changed.connect(self._update_auto_name)
        top_row.addWidget(self.meta)
        root.addLayout(top_row)

        self.hw = self._hw_panel_class(self._setup_getter, "Hardware"); root.addWidget(self.hw)

        sl_row = QHBoxLayout(); sl_row.setSpacing(10)
        pg = QGroupBox("Polarity control"); pl = QHBoxLayout(pg)
        pl.setSpacing(8); pl.setContentsMargins(8, 8, 8, 8)
        self.relay_flip_btn = QPushButton("Relay flip: OFF"); self.relay_flip_btn.setCheckable(True)
        self.relay_flip_btn.toggled.connect(lambda c: self.relay_flip_btn.setText("Relay flip: ON" if c else "Relay flip: OFF"))
        self.field_flip_btn = QPushButton("Field flip: OFF"); self.field_flip_btn.setCheckable(True)
        self.field_flip_btn.toggled.connect(lambda c: self.field_flip_btn.setText("Field flip: ON" if c else "Field flip: OFF"))
        pl.addWidget(self.relay_flip_btn); pl.addWidget(self.field_flip_btn)
        sl_row.addWidget(pg)

        ng = QGroupBox("Scanlist"); nl = QGridLayout(ng); nl.setSpacing(6); nl.setContentsMargins(8, 8, 8, 8)
        nl.addWidget(QLabel("N scans:"), 0, 0)
        self.n_spin = NoScrollSpinBox(); self.n_spin.setRange(1,9999); self.n_spin.setValue(4)
        nl.addWidget(self.n_spin, 0, 1)
        nl.addWidget(QLabel("Name:"), 1, 0)
        self.sl_name = QLineEdit(); self.sl_name.setReadOnly(True)
        self.sl_name.setStyleSheet(
            "background:#181825;color:#a6e3a1;border:1px solid #313244;"
            "border-radius:3px;padding:2px 4px;font-family:'Courier New',monospace;font-size:10px;")
        nl.addWidget(self.sl_name, 1, 1)
        sl_row.addWidget(ng)
        root.addLayout(sl_row)

        pr = QHBoxLayout(); pr.addWidget(QLabel("Scans:"))
        self.list_bar = QProgressBar(); self.list_bar.setFixedHeight(16)
        pr.addWidget(self.list_bar, stretch=1); root.addLayout(pr)

        # Auto-update name when HW spins change too
        self.hw.amp_spin.valueChanged.connect(lambda _: self._update_auto_name())
        self.hw.freq_spin.valueChanged.connect(lambda _: self._update_auto_name())
        # Initial name
        self._update_auto_name()

    def _update_auto_name(self):
        """Auto-construct scanlist name from metadata + HW values."""
        amp = self.hw.amp_spin.value() if hasattr(self.hw, 'amp_spin') else 0.0
        freq = self.hw.freq_spin.value() if hasattr(self.hw, 'freq_spin') else 0.0
        cfg_name = self.active_lbl.text().strip()
        if cfg_name == "—": cfg_name = ""
        self.sl_name.setText(self.meta.build_scan_name(amp, freq, cfg_name))

    def set_active_name(self, name: str):
        self.active_lbl.setText(name)
        self._update_auto_name()

    def get_settings(self) -> dict:
        # field_spin exists on HardwarePanel; CryoHardwarePanel has field_sp
        field_val = 0.0
        if hasattr(self.hw, 'field_spin'):
            field_val = self.hw.field_spin.value()
        elif hasattr(self.hw, 'field_sp'):
            field_val = self.hw.field_sp.value()
        return {
            "n_scans":        self.n_spin.value(),
            "list_name":      self.sl_name.text().strip() or "scanlist",
            "relay_flip":     self.relay_flip_btn.isChecked(),
            "field_flip":     self.field_flip_btn.isChecked(),
            "magnet_current": field_val,
            "metadata":       self.meta.get_values(),
        }
