"""
panels/_widgets.py — Samba v3
Shared widget primitives: NoScroll spin/combo boxes, MokeMetadataGroup, validators.
"""
import os
from typing import List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox,
    QCheckBox, QGroupBox, QComboBox
)
from PyQt6.QtCore import pyqtSignal, Qt

class NoScrollComboBox(QComboBox):
    def wheelEvent(self, ev): ev.ignore()

class NoScrollSpinBox(QSpinBox):
    def wheelEvent(self, ev): ev.ignore()

class NoScrollDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, ev): ev.ignore()

AXIS_OPTIONS = ["Y1", "Y2", "X", "hidden"]


class _NoUnderscoreValidator:
    """Mixin: strip underscores from typed/pasted text."""
    @staticmethod
    def install(le: QLineEdit):
        le.textChanged.connect(lambda t: (
            le.blockSignals(True),
            le.setText(t.replace("_", "")),
            le.blockSignals(False),
        ) if "_" in t else None)


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

