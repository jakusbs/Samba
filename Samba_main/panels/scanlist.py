"""
panels/scanlist.py — Samba v3
ScanlistPanel — N-scan list with polarity control and current sweep.

Layout mirrors the Trajectory tab: the scan-specific controls sit at the top,
Timing / Metadata / Hardware in a bottom row identical to TrajectoryPanel's.
Those three groups are separate instances from the Trajectory tab's and are
kept in sync by MainWindow (_meta_syncing / _timing_syncing / _link_hw_panels).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox
)
from PyQt6.QtCore import Qt, pyqtSignal

from panels._widgets import NoScrollSpinBox, NoScrollDoubleSpinBox, MokeMetadataGroup
from panels.hardware_panel import HardwarePanel
from current_sweep_ui import CurrentSweepGroup


class ScanlistPanel(QWidget):
    # Emitted when a polarity toggle is changed by the user, so the owning
    # window can persist it into the active scan config.  Programmatic loads
    # (load_config) are silent — see _load_flip.
    polarity_changed = pyqtSignal()

    def __init__(self, setup_getter, parent=None):
        super().__init__(parent)
        self._setup_getter = setup_getter
        root = QVBoxLayout(self); root.setContentsMargins(8, 6, 8, 6); root.setSpacing(6)

        # ── Top row: polarity + scanlist + current sweep ─────────────────────
        # Both left-hand boxes are two rows tall so they line up: the polarity
        # flips stack, and the active config shares row 0 with N scans.
        top_row = QHBoxLayout(); top_row.setSpacing(8)

        pg = QGroupBox("Polarity control"); pl = QVBoxLayout(pg)
        pl.setSpacing(6); pl.setContentsMargins(8, 8, 8, 8)
        self.relay_flip_btn = QPushButton("Relay flip: OFF"); self.relay_flip_btn.setCheckable(True)
        self.relay_flip_btn.toggled.connect(lambda c: self.relay_flip_btn.setText("Relay flip: ON" if c else "Relay flip: OFF"))
        self.field_flip_btn = QPushButton("Field flip: OFF"); self.field_flip_btn.setCheckable(True)
        self.field_flip_btn.toggled.connect(lambda c: self.field_flip_btn.setText("Field flip: ON" if c else "Field flip: OFF"))
        for _b in (self.relay_flip_btn, self.field_flip_btn):
            _b.toggled.connect(lambda _: self.polarity_changed.emit())
        pl.addWidget(self.relay_flip_btn); pl.addWidget(self.field_flip_btn)
        # Boxes expand with the tab (so no gap opens between the rows) but
        # their contents stay pinned to the top instead of floating.
        pl.setAlignment(Qt.AlignmentFlag.AlignTop)
        top_row.addWidget(pg)

        ng = QGroupBox("Scanlist"); nl = QGridLayout(ng); nl.setSpacing(6); nl.setContentsMargins(8, 8, 8, 8)
        # Row 0 is packed left so "Config: x   N scans: n" reads as one line;
        # left to itself the grid strands N scans against the far edge of a
        # box that is over 1700 px wide.
        row0 = QHBoxLayout(); row0.setSpacing(6)
        row0.addWidget(QLabel("Config:"))
        self.active_lbl = QLabel("—")
        self.active_lbl.setStyleSheet("color:#89b4fa;font-weight:bold;")
        row0.addWidget(self.active_lbl)
        row0.addSpacing(18)
        row0.addWidget(QLabel("N scans:"))
        self.n_spin = NoScrollSpinBox(); self.n_spin.setRange(1,9999); self.n_spin.setValue(4)
        self.n_spin.setMaximumWidth(90)
        row0.addWidget(self.n_spin)
        row0.addStretch()
        nl.addLayout(row0, 0, 0, 1, 2)
        nl.addWidget(QLabel("Name:"), 1, 0)
        self.sl_name = QLineEdit(); self.sl_name.setReadOnly(True)
        self.sl_name.setStyleSheet(
            "background:#181825;color:#a6e3a1;border:1px solid #313244;"
            "border-radius:3px;padding:2px 4px;font-family:'Courier New',monospace;font-size:10px;")
        nl.setColumnStretch(1, 1)          # the auto-name gets the spare width
        nl.addWidget(self.sl_name, 1, 1)
        nl.setRowStretch(2, 1)             # keep the two rows at the top
        top_row.addWidget(ng, stretch=1)

        root.addLayout(top_row)

        # Own row, full width — repeats the whole list at several excitation
        # currents (core/current_sweep.py).  Unchecked = one plain scanlist.
        # Given the row stretch so it absorbs spare height instead of leaving
        # a gap above the bottom row when the tab is made taller.
        self.cur_sweep = CurrentSweepGroup()
        root.addWidget(self.cur_sweep, stretch=1)

        # ── Bottom row: Timing + Metadata + Hardware (matches Trajectory) ────
        bot = QHBoxLayout(); bot.setSpacing(4)

        tg = QGroupBox("Timing"); tl = QGridLayout(tg)
        tl.setSpacing(3); tl.setContentsMargins(6, 6, 6, 6)
        def _dbl(lo, hi, dec, v):
            w = NoScrollDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec); w.setValue(v); return w
        tl.addWidget(QLabel("Int (s):"),    0, 0); self.int_time = _dbl(0.001, 3600, 3, 0.1); tl.addWidget(self.int_time, 0, 1)
        tl.addWidget(QLabel("Settle (s):"), 1, 0); self.settle   = _dbl(0,     10,   3, 0.05); tl.addWidget(self.settle,   1, 1)
        tl.addWidget(QLabel("T.out (s):"),  2, 0); self.timeout  = _dbl(0.1,   300,  1, 15.0); tl.addWidget(self.timeout,  2, 1)
        bot.addWidget(tg)

        self.meta = MokeMetadataGroup("Metadata")
        self.meta.changed.connect(self._update_auto_name)
        bot.addWidget(self.meta)

        self.hw = HardwarePanel(self._setup_getter, "Hardware")
        self.hw.setMaximumWidth(700)
        bot.addWidget(self.hw)
        root.addLayout(bot)

        # Auto-update name when HW spins change too.  The current sweep drives
        # amp_spin between currents, so each current's scanlist name picks up
        # its own {I}mA token through this connection.
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

    # ── Polarity control persistence ──────────────────────────────────────────
    @staticmethod
    def _load_flip(btn, label: str, on: bool):
        """Set a polarity toggle without emitting toggled().

        The caption is refreshed explicitly: the toggled() handler that
        normally keeps it in sync is suppressed by blockSignals, so a silent
        setChecked would leave the button showing the previous state.
        """
        blocked = btn.blockSignals(True)
        btn.setChecked(on)
        btn.blockSignals(blocked)
        btn.setText(f"{label}: {'ON' if on else 'OFF'}")

    def load_config(self, cfg: dict):
        """Restore the polarity toggles and current sweep (silently)."""
        self._load_flip(self.relay_flip_btn, "Relay flip",
                        bool(cfg.get("relay_flip", False)))
        self._load_flip(self.field_flip_btn, "Field flip",
                        bool(cfg.get("field_flip", False)))
        self.cur_sweep.load_values(cfg)

    def get_config_partial(self) -> dict:
        """Polarity + current-sweep state for the active scan config."""
        d = {
            "relay_flip": self.relay_flip_btn.isChecked(),
            "field_flip": self.field_flip_btn.isChecked(),
        }
        d.update(self.cur_sweep.get_values())
        return d

    def get_settings(self) -> dict:
        return {
            "n_scans":        self.n_spin.value(),
            "list_name":      self.sl_name.text().strip() or "scanlist",
            "relay_flip":     self.relay_flip_btn.isChecked(),
            "field_flip":     self.field_flip_btn.isChecked(),
            "magnet_current": self.hw.field_spin.value(),
            "metadata":       self.meta.get_values(),
        }
