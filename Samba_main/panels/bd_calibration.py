"""
panels/bd_calibration.py — Samba v3
BDCalibrationPanel — λ/2 plate (BD) calibration table.
Tick positions: 0, 5, 10, 15, 20, 25  →  6 mV values.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QGroupBox, QMessageBox, QFrame
)
from PyQt6.QtCore import pyqtSignal, Qt

from panels._widgets import NoScrollDoubleSpinBox

TICKS = [0, 5, 10, 15, 20, 25]


class BDCalibrationPanel(QWidget):
    """λ/2 plate calibration — 6 mV values at tick positions 0,5,10,15,20,25."""

    calibration_changed = pyqtSignal(list)   # emits list of 6 floats (mV)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._prompted_setups: set = set()   # setups for which we already asked

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10); root.setSpacing(10)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QLabel("λ/2 Plate Calibration")
        hdr.setStyleSheet("color:#cba6f7;font-size:13px;font-weight:bold;")
        root.addWidget(hdr)

        desc = QLabel(
            "Enter the measured MOKE signal (mV) at each tick position of the λ/2 plate.\n"
            "The calibration is saved in every HDF5 scan file under /data/calibration.")
        desc.setStyleSheet("color:#a6adc8;font-size:10px;")
        desc.setWordWrap(True)
        root.addWidget(desc)

        # ── Calibration table ─────────────────────────────────────────────────
        cal_grp = QGroupBox("Calibration Values")
        cal_grp.setStyleSheet(
            "QGroupBox{border:1px solid #45475a;border-radius:6px;"
            "margin-top:9px;padding-top:9px;font-weight:bold;color:#cba6f7;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}")
        cal_lay = QGridLayout(cal_grp)
        cal_lay.setSpacing(6); cal_lay.setContentsMargins(10, 14, 10, 10)

        # Column headers (tick values)
        for col, tick in enumerate(TICKS):
            lbl = QLabel(f"{tick}°")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#6c7086;font-size:10px;font-weight:bold;")
            cal_lay.addWidget(lbl, 0, col + 1)

        # Row 0: tick label
        cal_lay.addWidget(QLabel("Ticks:"), 1, 0)
        for col, tick in enumerate(TICKS):
            lbl = QLabel(str(tick))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                "background:#313244;border:1px solid #45475a;border-radius:3px;"
                "padding:3px 6px;color:#a6adc8;font-size:11px;")
            cal_lay.addWidget(lbl, 1, col + 1)

        # Row 1: mV spinboxes (editable)
        mv_lbl = QLabel("mV:")
        mv_lbl.setStyleSheet("color:#cdd6f4;font-weight:bold;")
        cal_lay.addWidget(mv_lbl, 2, 0)
        self._mv_spins: list = []
        for col in range(6):
            sp = NoScrollDoubleSpinBox()
            sp.setRange(-1e6, 1e6); sp.setDecimals(4); sp.setValue(0.0)
            sp.setMinimumWidth(80)
            sp.valueChanged.connect(self._on_value_changed)
            cal_lay.addWidget(sp, 2, col + 1)
            self._mv_spins.append(sp)

        root.addWidget(cal_grp)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        self._save_btn = QPushButton("Save calibration")
        self._save_btn.setStyleSheet(
            "QPushButton{background:#a6e3a1;color:#1e1e2e;font-weight:bold;"
            "border:none;border-radius:5px;padding:4px 14px;}"
            "QPushButton:hover{background:#94d992;}")
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        self._load_btn = QPushButton("Load saved")
        self._load_btn.setStyleSheet(
            "QPushButton{background:#89b4fa;color:#1e1e2e;font-weight:bold;"
            "border:none;border-radius:5px;padding:4px 14px;}"
            "QPushButton:hover{background:#7aa2e8;}")
        self._load_btn.clicked.connect(self._on_load_last)
        btn_row.addWidget(self._load_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Status label ──────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#45475a;")
        root.addWidget(sep)

        self._status_lbl = QLabel("No calibration saved yet.")
        self._status_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        root.addWidget(self._status_lbl)

        root.addStretch()

        # External callbacks — set by samba.py
        self._save_cb = None   # callable(vals: list)
        self._load_cb = None   # callable() -> (vals, date_str) or (None, "")

    # ── Public API ────────────────────────────────────────────────────────────

    def set_callbacks(self, save_cb, load_cb):
        """
        save_cb(vals: list) — called when user clicks Save
        load_cb() -> (vals: list | None, date_str: str) — called to retrieve saved values
        """
        self._save_cb = save_cb
        self._load_cb = load_cb

    def get_calibration(self) -> list:
        return [sp.value() for sp in self._mv_spins]

    def load_calibration(self, vals: list):
        """Load 6 mV values into the spinboxes without emitting calibration_changed."""
        for i, sp in enumerate(self._mv_spins):
            if i < len(vals):
                sp.blockSignals(True)
                sp.setValue(float(vals[i]))
                sp.blockSignals(False)

    def set_status(self, text: str):
        self._status_lbl.setText(text)

    def maybe_prompt(self, setup_name: str):
        """Called the first time this tab is shown per setup per session.
        If a saved calibration exists, ask the user whether to load it."""
        if setup_name in self._prompted_setups:
            return
        self._prompted_setups.add(setup_name)

        if self._load_cb is None:
            return
        vals, date_str = self._load_cb()
        if vals is None:
            return

        mv_preview = ", ".join(f"{v:.4g}" for v in vals)
        reply = QMessageBox.question(
            self,
            "Load last calibration?",
            f"A saved λ/2 calibration exists for setup '{setup_name}'.\n\n"
            f"Saved: {date_str}\n"
            f"Values (mV): {mv_preview}\n\n"
            "Load it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.load_calibration(vals)
            self._status_lbl.setText(f"Loaded calibration from {date_str}.")

    # ── Internals ─────────────────────────────────────────────────────────────

    def _on_value_changed(self):
        self.calibration_changed.emit(self.get_calibration())

    def _on_save(self):
        vals = self.get_calibration()
        if self._save_cb:
            self._save_cb(vals)
        # Status updated externally via set_status

    def _on_load_last(self):
        if self._load_cb is None:
            return
        vals, date_str = self._load_cb()
        if vals is None:
            QMessageBox.information(self, "No saved calibration",
                                    "No calibration has been saved for this setup yet.")
            return
        self.load_calibration(vals)
        self._status_lbl.setText(f"Loaded calibration from {date_str}.")
