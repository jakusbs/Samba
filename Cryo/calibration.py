# -*- coding: utf-8 -*-
"""
Cryo calibration panel — extends the shared CalibrationPanel with an
ANC300 stepper-controls group (frequency, voltage, ground per axis).
"""
from core.calibration import *   # noqa: F401,F403 — re-exports everything for backwards compat
from core.calibration import CalibrationPanel

from PyQt6.QtWidgets import (
    QGroupBox, QGridLayout, QLabel, QPushButton,
    QDoubleSpinBox, QCheckBox, QHBoxLayout, QSizePolicy
)
from PyQt6.QtCore import QThread, pyqtSignal

from hardware import fresh_proxy, safe_read, safe_write


# ─────────────────────────────────────────────────────────────────────────────
# _AncIoWorker — reads or writes ANC300 attributes off the GUI thread
# ─────────────────────────────────────────────────────────────────────────────
class _AncIoWorker(QThread):
    done   = pyqtSignal(object)   # emits dict on read, None on write
    error  = pyqtSignal(str)

    def __init__(self, device, mode, values=None):
        super().__init__()
        self._device = device
        self._mode   = mode    # "read" or "write"
        self._values = values  # dict {attr: value} for write mode

    def run(self):
        p, err = fresh_proxy(self._device)
        if err:
            self.error.emit(f"ANC300: {err}"); return
        if self._mode == "read":
            result = {}
            for attr in ("fx", "fy", "fz", "Vx", "Vy", "Vz", "Gx", "Gy", "Gz"):
                v, e = safe_read(p, attr)
                if e is None:
                    result[attr] = v
            self.done.emit(result)
        else:
            for attr, val in self._values.items():
                safe_write(p, attr, val)
            self.done.emit(None)


# ─────────────────────────────────────────────────────────────────────────────
# CryoCalibrationPanel — CalibrationPanel + ANC300 stepper group
# ─────────────────────────────────────────────────────────────────────────────
class CryoCalibrationPanel(CalibrationPanel):
    """Calibration panel with an extra ANC300 stepper-controls column."""

    _SPIN_STYLE = (
        "QDoubleSpinBox{background:#181825;border:1px solid #45475a;border-radius:4px;"
        "color:#cdd6f4;padding:2px 4px;font-size:11px;}"
        "QDoubleSpinBox:focus{border:1px solid #89b4fa;}"
        "QDoubleSpinBox:disabled{color:#585b70;}")
    _CB_STYLE = "QCheckBox{color:#cdd6f4;font-size:11px;spacing:4px;}"
    _BTN_STYLE = (
        "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
        "border-radius:4px;padding:3px 10px;font-size:11px;}"
        "QPushButton:hover{background:#45475a;}"
        "QPushButton:disabled{color:#585b70;border-color:#313244;}")

    def __init__(self, setup_getter, config_getter=None, parent=None,
                 sensor_row_factory=None):
        super().__init__(setup_getter, config_getter, parent,
                         sensor_row_factory=sensor_row_factory)
        self._anc_device = ""
        self._anc_worker = None
        self._build_anc_group()

    def _build_anc_group(self):
        grp = QGroupBox("Stepper (ANC300)")
        g = QGridLayout(grp); g.setSpacing(4); g.setContentsMargins(8, 8, 8, 8)

        # Device label
        self._anc_dev_lbl = QLabel("Device: —")
        self._anc_dev_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        self._anc_dev_lbl.setWordWrap(True)
        g.addWidget(self._anc_dev_lbl, 0, 0, 1, 4)

        # Header row
        for col, txt in enumerate(("Axis", "Freq (Hz)", "Volt (V)", "GND")):
            lbl = QLabel(txt)
            lbl.setStyleSheet("color:#6c7086;font-size:10px;font-weight:bold;")
            g.addWidget(lbl, 1, col)

        self._anc_spins = {}   # {attr: spinbox}
        self._anc_cbs   = {}   # {attr: checkbox}

        for row, axis in enumerate(("X", "Y", "Z"), start=2):
            ax_l = axis.lower()
            g.addWidget(QLabel(axis), row, 0)

            f_spin = QDoubleSpinBox()
            f_spin.setRange(1, 10000); f_spin.setDecimals(0)
            f_spin.setValue(1000); f_spin.setStepType(
                QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
            f_spin.setStyleSheet(self._SPIN_STYLE)
            f_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            g.addWidget(f_spin, row, 1)
            self._anc_spins[f"f{ax_l}"] = f_spin

            v_spin = QDoubleSpinBox()
            v_spin.setRange(0, 60); v_spin.setDecimals(1); v_spin.setValue(30)
            v_spin.setStepType(QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
            v_spin.setStyleSheet(self._SPIN_STYLE)
            v_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            g.addWidget(v_spin, row, 2)
            self._anc_spins[f"V{axis}"] = v_spin

            gnd_cb = QCheckBox()
            gnd_cb.setStyleSheet(self._CB_STYLE)
            g.addWidget(gnd_cb, row, 3)
            self._anc_cbs[f"G{axis}"] = gnd_cb

        # Buttons
        btn_row = QHBoxLayout(); btn_row.setSpacing(6)
        self._anc_read_btn  = QPushButton("Read")
        self._anc_apply_btn = QPushButton("Apply")
        self._anc_read_btn.setStyleSheet(self._BTN_STYLE)
        self._anc_apply_btn.setStyleSheet(
            self._BTN_STYLE.replace("#313244", "#1e3a5f").replace("#45475a", "#2a5298"))
        self._anc_read_btn.clicked.connect(self._anc_read)
        self._anc_apply_btn.clicked.connect(self._anc_apply)
        btn_row.addWidget(self._anc_read_btn)
        btn_row.addWidget(self._anc_apply_btn)
        btn_row.addStretch()
        g.addLayout(btn_row, 5, 0, 1, 4)

        self._anc_status = QLabel("")
        self._anc_status.setWordWrap(True)
        self._anc_status.setStyleSheet("font-size:10px;")
        g.addWidget(self._anc_status, 6, 0, 1, 4)

        self._set_anc_enabled(False)
        self._right_layout.addWidget(grp)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_anc_device(self, path: str):
        self._anc_device = path or ""
        self._anc_dev_lbl.setText(f"Device: {path}" if path else "Device: —")
        self._set_anc_enabled(bool(path))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _set_anc_enabled(self, enabled: bool):
        for w in list(self._anc_spins.values()) + list(self._anc_cbs.values()):
            w.setEnabled(enabled)
        self._anc_read_btn.setEnabled(enabled)
        self._anc_apply_btn.setEnabled(enabled)

    def _anc_read(self):
        if not self._anc_device: return
        self._anc_status.setText("Reading…")
        self._anc_status.setStyleSheet("color:#89b4fa;font-size:10px;")
        w = _AncIoWorker(self._anc_device, "read")
        w.done.connect(self._on_anc_read_done)
        w.error.connect(lambda e: self._anc_err(e))
        w.finished.connect(w.deleteLater)
        self._anc_worker = w; w.start()

    def _on_anc_read_done(self, vals):
        if not isinstance(vals, dict): return
        for attr, spin in self._anc_spins.items():
            if attr in vals and vals[attr] is not None:
                spin.blockSignals(True)
                spin.setValue(float(vals[attr]))
                spin.blockSignals(False)
        for attr, cb in self._anc_cbs.items():
            if attr in vals and vals[attr] is not None:
                cb.blockSignals(True)
                cb.setChecked(bool(vals[attr]))
                cb.blockSignals(False)
        self._anc_status.setText("✓ Read OK")
        self._anc_status.setStyleSheet("color:#a6e3a1;font-size:10px;")

    def _anc_apply(self):
        if not self._anc_device: return
        values = {}
        for attr, spin in self._anc_spins.items():
            values[attr] = spin.value()
        for attr, cb in self._anc_cbs.items():
            values[attr] = cb.isChecked()
        self._anc_status.setText("Applying…")
        self._anc_status.setStyleSheet("color:#89b4fa;font-size:10px;")
        w = _AncIoWorker(self._anc_device, "write", values)
        w.done.connect(lambda _: self._anc_ok("✓ Applied"))
        w.error.connect(lambda e: self._anc_err(e))
        w.finished.connect(w.deleteLater)
        self._anc_worker = w; w.start()

    def _anc_ok(self, msg):
        self._anc_status.setText(msg)
        self._anc_status.setStyleSheet("color:#a6e3a1;font-size:10px;")

    def _anc_err(self, msg):
        self._anc_status.setText(f"⚠ {msg}")
        self._anc_status.setStyleSheet("color:#f38ba8;font-size:10px;")
