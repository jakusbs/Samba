"""
panels/scanlist.py — Samba v3
ScanlistPanel — N-scan list with polarity control.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QProgressBar
)
from PyQt6.QtCore import Qt

from panels._widgets import NoScrollSpinBox, NoScrollDoubleSpinBox, MokeMetadataGroup
from panels.hardware_panel import HardwarePanel


class ScanlistPanel(QWidget):
    def __init__(self, setup_getter, parent=None):
        super().__init__(parent)
        self._setup_getter = setup_getter
        root = QVBoxLayout(self); root.setContentsMargins(8, 6, 8, 6); root.setSpacing(6)

        # ── Top row: active config + metadata side by side ────────────────────
        top_row = QHBoxLayout(); top_row.setSpacing(8)

        info_w = QWidget(); info_l = QVBoxLayout(info_w)
        info_l.setContentsMargins(0, 0, 0, 0); info_l.setSpacing(4)
        hl0 = QHBoxLayout(); hl0.addWidget(QLabel("Active config:"))
        self.active_lbl = QLabel("—"); self.active_lbl.setStyleSheet("color:#89b4fa;font-weight:bold;")
        hl0.addWidget(self.active_lbl); hl0.addStretch()
        info_l.addLayout(hl0)
        info_l.addStretch()
        top_row.addWidget(info_w)

        self.meta = MokeMetadataGroup("Metadata")
        self.meta.changed.connect(self._update_auto_name)
        top_row.addWidget(self.meta)
        root.addLayout(top_row)

        self.hw = HardwarePanel(self._setup_getter, "Hardware"); root.addWidget(self.hw)

        # ── Timing group — kept in sync with Trajectory tab ──────────────────
        tg = QGroupBox("Timing"); tl = QGridLayout(tg)
        tl.setSpacing(3); tl.setContentsMargins(6, 6, 6, 6)
        def _dbl(lo, hi, dec, v):
            w = NoScrollDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec); w.setValue(v); return w
        tl.addWidget(QLabel("Int (s):"),    0, 0); self.int_time = _dbl(0.001, 3600, 3, 0.1); tl.addWidget(self.int_time, 0, 1)
        tl.addWidget(QLabel("Settle (s):"), 1, 0); self.settle   = _dbl(0,     10,   3, 0.05); tl.addWidget(self.settle,   1, 1)
        tl.addWidget(QLabel("T.out (s):"),  2, 0); self.timeout  = _dbl(0.1,   300,  1, 15.0); tl.addWidget(self.timeout,  2, 1)
        root.addWidget(tg)

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
        return {
            "n_scans":        self.n_spin.value(),
            "list_name":      self.sl_name.text().strip() or "scanlist",
            "relay_flip":     self.relay_flip_btn.isChecked(),
            "field_flip":     self.field_flip_btn.isChecked(),
            "magnet_current": self.hw.field_spin.value(),
            "metadata":       self.meta.get_values(),
        }
