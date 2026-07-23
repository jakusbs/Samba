"""
panels/trajectory.py — Samba v3
TrajectoryPanel, ActuatorGroup, FieldSegmentList — scan trajectory controls.
"""
import os, time, collections
from typing import Dict, List, Tuple
import numpy as np

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox,
    QCheckBox, QGroupBox, QComboBox, QScrollArea,
    QButtonGroup, QRadioButton
)
from PyQt6.QtCore import pyqtSignal, Qt

from config import X_NATURAL, X_TIME
from nstep import NStepPair
from panels._widgets import (
    NoScrollComboBox, NoScrollSpinBox, NoScrollDoubleSpinBox,
    MokeMetadataGroup
)
from panels.hardware_panel import HardwarePanel


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
        hl.addWidget(QLabel("N:"))
        n = NoScrollSpinBox(); n.setRange(2, 10000); n.setValue(npts); n.setFixedWidth(58)
        hl.addWidget(n)
        hl.addWidget(QLabel("Δ:"))
        d = NoScrollDoubleSpinBox(); d.setRange(1e-6, 40); d.setDecimals(4)
        d.setFixedWidth(70)
        hl.addWidget(d)
        del_btn = QPushButton("×"); del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet(
            "QPushButton{color:#f38ba8;font-weight:bold;border:1px solid #45475a;"
            "border-radius:3px;padding:0;background:#313244;}"
            "QPushButton:hover{background:#45475a;}")
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hl.addWidget(del_btn); hl.addStretch()

        # N and Δ both stay visible/editable; the step is the base (start/stop
        # edits keep Δ and recompute N).  N remains what the engine runs.
        pair = NStepPair(n, d, lambda: e.value() - s.value())
        pair.set_npts(npts)

        tup = (s, e, n, row_w, pair)
        self._vlayout.addWidget(row_w)
        self._rows.append(tup)

        s.valueChanged.connect(self._on_changed)
        e.valueChanged.connect(self._on_changed)
        n.valueChanged.connect(self._on_changed)
        s.valueChanged.connect(pair.span_changed)
        e.valueChanged.connect(pair.span_changed)
        del_btn.clicked.connect(lambda: self._del_segment(tup))
        self._on_changed()

    def _del_segment(self, tup):
        if len(self._rows) <= 1: return
        self._rows.remove(tup)
        tup[3].hide(); self._vlayout.removeWidget(tup[3]); tup[3].deleteLater()
        self._on_changed()

    def _on_changed(self):
        total = sum(int(t[2].value()) for t in self._rows)
        n = len(self._rows)
        self._summary_lbl.setText(f"Total N = {total},  {n} segment{'s' if n != 1 else ''}")
        self.changed.emit()

    def get_segments(self) -> List[list]:
        return [[t[0].value(), t[1].value(), int(t[2].value())] for t in self._rows]

    def load_segments(self, segs: List):
        for tup in list(self._rows):
            tup[3].hide(); self._vlayout.removeWidget(tup[3]); tup[3].deleteLater()
        self._rows = []
        for seg in segs:
            self._add_segment(float(seg[0]), float(seg[1]), int(seg[2]))
        if not self._rows:
            self._add_segment(-1.0, 1.0, 101)

    def total_npts(self) -> int:
        return sum(int(t[2].value()) for t in self._rows)



class ActuatorGroup(QGroupBox):
    """Scan-geometry controls for a single actuator axis.

    Device path and attribute are no longer stored here — they live in
    SetupDefaultsPanel and are injected into the config by MainWindow at
    scan-start time.  This group only shows start / stop / N and the
    display-only label / unit that come from the setup defaults.
    """

    def __init__(self, title: str, lbl: str, unit: str,
                 start: float, stop: float, npts: int, step_prefix: str = "Δ",
                 enabled: bool = True, parent=None):
        super().__init__(title, parent)
        g = QGridLayout(self); g.setSpacing(4); g.setContentsMargins(8, 8, 8, 8)

        # Row 0: Scan enabled checkbox
        self.scan_cb = QCheckBox("Scan enabled"); self.scan_cb.setChecked(enabled)
        self.scan_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        g.addWidget(self.scan_cb, 0, 0, 1, 6)

        _ro_style = ("background:#1e1e2e;color:#6c7086;border:1px solid #313244;"
                     "border-radius:4px;padding:2px 4px;")

        # Row 1: Device + Attr (display-only — populated from Setup Defaults)
        g.addWidget(QLabel("Device:"), 1, 0)
        self.dev_lbl = QLineEdit(); self.dev_lbl.setReadOnly(True)
        self.dev_lbl.setPlaceholderText("— set in Setup Defaults —")
        self.dev_lbl.setStyleSheet(_ro_style + "font-size:10px;")
        g.addWidget(self.dev_lbl, 1, 1, 1, 4)
        self.attr_lbl = QLineEdit(); self.attr_lbl.setReadOnly(True)
        self.attr_lbl.setMinimumWidth(60)
        self.attr_lbl.setStyleSheet(_ro_style + "font-size:10px;")
        g.addWidget(self.attr_lbl, 1, 5)

        # Row 2: Label + Unit (display-only — populated from Setup Defaults)
        # Layout mirrors row 3 (Start/Stop): label spans cols 1-2, unit spans cols 4-5
        g.addWidget(QLabel("Label:"), 2, 0)
        self.lbl = QLineEdit(lbl)
        self.lbl.setReadOnly(True)
        self.lbl.setMinimumWidth(60)
        self.lbl.setStyleSheet(_ro_style)
        g.addWidget(self.lbl, 2, 1, 1, 2)
        g.addWidget(QLabel("Unit:"), 2, 3)
        self.unit_edit = QLineEdit(unit)
        self.unit_edit.setReadOnly(True)
        self.unit_edit.setMinimumWidth(50)
        self.unit_edit.setStyleSheet(_ro_style)
        g.addWidget(self.unit_edit, 2, 4, 1, 2)
        g.setColumnStretch(1, 2); g.setColumnStretch(4, 2)

        # Row 3: Start / Stop
        g.addWidget(QLabel("Start:"), 3, 0)
        self.start = NoScrollDoubleSpinBox(); self.start.setRange(-1e9, 1e9); self.start.setDecimals(3)
        self.start.setValue(start); g.addWidget(self.start, 3, 1, 1, 2)
        g.addWidget(QLabel("Stop:"), 3, 3)
        self.stop  = NoScrollDoubleSpinBox(); self.stop.setRange(-1e9, 1e9);  self.stop.setDecimals(3)
        self.stop.setValue(stop);  g.addWidget(self.stop,  3, 4, 1, 2)

        # Row 4: N + Δ step — both always visible and editable.  Editing one
        # derives the other; the step is the base value (start/stop edits keep
        # the step and recompute N — see NStepPair).
        ns_row = QWidget(); ns_lay = QHBoxLayout(ns_row)
        ns_lay.setContentsMargins(0, 0, 0, 0); ns_lay.setSpacing(4)
        self.npts_spin = NoScrollSpinBox();       self.npts_spin.setRange(2, 10000); self.npts_spin.setValue(npts)
        self.step_spin = NoScrollDoubleSpinBox(); self.step_spin.setRange(1e-6, 1e9); self.step_spin.setDecimals(3)
        self.step_spin.setValue(abs(stop - start) / (npts - 1) if npts > 1 else 1.0)
        for w in [QLabel("N:"), self.npts_spin,
                  QLabel(f"{step_prefix}:"), self.step_spin]:
            ns_lay.addWidget(w)
        ns_lay.addStretch(); g.addWidget(ns_row, 4, 0, 1, 6)

        self._pair = NStepPair(self.npts_spin, self.step_spin,
                               lambda: self.stop.value() - self.start.value())
        for w in [self.start, self.stop]:
            w.valueChanged.connect(self._pair.span_changed)

    # ── Update from setup defaults ────────────────────────────────────────────
    def set_defaults(self, dev: str, attr: str, lbl: str, unit: str):
        """Called by samba.py when Setup Defaults change."""
        self.dev_lbl.setText(dev); self.dev_lbl.setToolTip(dev)
        self.attr_lbl.setText(attr)
        self.lbl.setText(lbl)
        self.unit_edit.setText(unit)

    # ── Standard helpers ──────────────────────────────────────────────────────
    def get_npts(self) -> int:
        return max(2, self.npts_spin.value())

    def load(self, pfx: str, cfg: dict, enabled: bool = True):
        self.scan_cb.setChecked(enabled)
        # label and unit come from setup defaults (may be in cfg if already merged)
        self.lbl.setText(cfg.get(f"{pfx}_label", self.lbl.text()))
        self.unit_edit.setText(cfg.get(f"{pfx}_unit", self.unit_edit.text()))
        self.start.setValue(cfg.get(f"{pfx}_start",  0.0))
        self.stop.setValue( cfg.get(f"{pfx}_stop",  50000.0))
        self._pair.set_npts(int(cfg.get(f"{pfx}_npts", 51)))

    def get_partial(self, pfx: str) -> dict:
        """Return scan-geometry values (no device/attr — injected from defaults)."""
        return {
            f"{pfx}_label": self.lbl.text(),
            f"{pfx}_unit":  self.unit_edit.text().strip() or "µm",
            f"{pfx}_start": self.start.value(),
            f"{pfx}_stop":  self.stop.value(),
            f"{pfx}_npts":  self.get_npts(),
        }




class TrajectoryPanel(QWidget):
    scan_mode_changed = pyqtSignal(str)   # "SPATIAL", "FIELD", "DC_HYST", or "TR_MOKE"

    def __init__(self, setup_getter, parent=None):
        super().__init__(parent)
        self._setup_getter = setup_getter
        # Rolling field monitor history (≤120 points at 500 ms = 60 s window)
        self._field_hist_t: collections.deque = collections.deque(maxlen=120)
        self._field_hist_v: collections.deque = collections.deque(maxlen=120)
        self._field_t0: float = time.time()
        root = QVBoxLayout(self); root.setContentsMargins(8, 6, 8, 6); root.setSpacing(6)

        # ── Scan type selector — styled pill buttons ─────────────────────────
        type_row = QHBoxLayout(); type_row.setSpacing(0)
        self.scan_bg = QButtonGroup(self); self.scan_bg.setExclusive(True)
        _pill_labels = [("Spatial", 0), ("Field", 1), ("TR-MOKE", 2)]
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
        root.addLayout(type_row)

        # ── Spatial panel ─────────────────────────────────────────────────────
        self.spatial_w = QWidget(); sp_l = QVBoxLayout(self.spatial_w)
        sp_l.setContentsMargins(0, 0, 0, 0); sp_l.setSpacing(4)

        # ActuatorGroups are now checkable — the title checkbox IS the on/off toggle.
        act_row = QHBoxLayout(); act_row.setSpacing(6)
        self.act1_grp = ActuatorGroup(
            "X axis", "X", "nm", 0, 50000, 51,
            step_prefix="Δx", enabled=True)
        self.act2_grp = ActuatorGroup(
            "Y axis", "Y", "nm", 0, 50000, 51,
            step_prefix="Δy", enabled=False)
        # Zigzag + fast-axis container inside act2_grp — only shown when both
        # X and Y are on (i.e. a real 2-D raster, where these settings apply)
        self.zigzag_w = QWidget()
        zz_l = QVBoxLayout(self.zigzag_w); zz_l.setContentsMargins(0, 2, 0, 0); zz_l.setSpacing(3)
        self.zigzag_cb = QCheckBox("Zigzag (reverse direction on every fast line)")
        self.zigzag_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        zz_l.addWidget(self.zigzag_cb)

        # Fast (main) scanning axis — which axis is swept per line (inner loop).
        # X (default): for each Y row, sweep all X.  Y: for each X column, sweep
        # all Y.  Data is stored identically; only the physical traversal differs.
        fa_row = QHBoxLayout(); fa_row.setSpacing(0)
        fa_lbl = QLabel("Fast axis:"); fa_lbl.setStyleSheet("color:#a6adc8;font-size:11px;")
        fa_row.addWidget(fa_lbl); fa_row.addSpacing(6)
        self.fast_axis_bg = QButtonGroup(self); self.fast_axis_bg.setExclusive(True)
        for _idx, _lab in enumerate(("X", "Y")):
            b = QPushButton(_lab)
            b.setCheckable(True); b.setChecked(_idx == 0)
            b.setFixedHeight(24); b.setMinimumWidth(46)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if _idx == 0:
                radius = ("border-top-left-radius:6px;border-bottom-left-radius:6px;"
                          "border-top-right-radius:0;border-bottom-right-radius:0;")
            else:
                radius = ("border-top-left-radius:0;border-bottom-left-radius:0;"
                          "border-top-right-radius:6px;border-bottom-right-radius:6px;")
            b.setStyleSheet(
                f"QPushButton{{background:#252538;border:1px solid #45475a;"
                f"color:#6c7086;font-size:11px;font-weight:bold;padding:0 10px;{radius}}}"
                f"QPushButton:hover{{background:#313244;color:#cdd6f4;}}"
                f"QPushButton:checked{{background:#a6e3a1;color:#1e1e2e;border-color:#a6e3a1;}}")
            self.fast_axis_bg.addButton(b, _idx)
            fa_row.addWidget(b)
        fa_row.addStretch()
        zz_l.addLayout(fa_row)

        self.zigzag_w.setVisible(False)   # hidden until both axes on
        # Row 6 of act2_grp grid
        self.act2_grp.layout().addWidget(self.zigzag_w, 6, 0, 1, 4)
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
        # Row 6 = below the N/Δ row (rows 0-4 = scan_cb, device, attr, start/stop, N/Δ)
        self.act1_grp.layout().addWidget(self.time_scan_lbl, 6, 0, 1, 4)

        root.addWidget(self.spatial_w)

        # Connect axis toggles → update zigzag visibility
        self.act1_grp.scan_cb.stateChanged.connect(lambda _: self._on_axis_toggled())
        self.act2_grp.scan_cb.stateChanged.connect(lambda _: self._on_axis_toggled())

        # ── Field panel ───────────────────────────────────────────────────────
        # Layout (all always visible, side-by-side):
        #   [AC params]  |  [Field / Hc monitor]  |  [DC params]  |  [DC live plot]
        # A compact sub-mode selector at the top selects which scan Start runs.
        self.field_w = QWidget(); fw_root = QVBoxLayout(self.field_w)
        fw_root.setContentsMargins(0, 0, 0, 0); fw_root.setSpacing(3)

        # Sub-mode row (compact radio buttons)
        fsub_row = QHBoxLayout(); fsub_row.setSpacing(12)
        self._fsub_bg = QButtonGroup(self)
        self.rb_ac_sw = QRadioButton("▶  AC Field Sweep");  self.rb_ac_sw.setChecked(True)
        self.rb_dc_hy = QRadioButton("▶  DC Hysteresis")
        self._fsub_bg.addButton(self.rb_ac_sw, 0)
        self._fsub_bg.addButton(self.rb_dc_hy, 1)
        self._fsub_bg.idClicked.connect(self._on_submode_changed)
        for rb in (self.rb_ac_sw, self.rb_dc_hy): fsub_row.addWidget(rb)
        fsub_row.addStretch()
        fw_root.addLayout(fsub_row)

        # Main horizontal row
        horiz = QHBoxLayout(); horiz.setSpacing(5); horiz.setContentsMargins(0, 0, 0, 0)

        # ── Column 1: AC params ───────────────────────────────────────────────
        self._ac_grp = QGroupBox("AC Field Sweep"); fgl = QGridLayout(self._ac_grp)
        fgl.setSpacing(4); fgl.setContentsMargins(8, 8, 8, 8)

        # Row 0: Field device dropdown
        fgl.addWidget(QLabel("Device:"), 0, 0)
        self._ac_dev_combo = NoScrollComboBox()
        self._ac_dev_combo.setStyleSheet("font-size:10px;")
        self._ac_dev_combo.addItem("(setup default)", "")
        fgl.addWidget(self._ac_dev_combo, 0, 1)

        # Row 1: Multi-segment field list
        self._seg_list = FieldSegmentList()
        fgl.addWidget(self._seg_list, 1, 0, 1, 2)

        # Row 2: AC monitor dropdowns
        ac_mon_row = QHBoxLayout(); ac_mon_row.setSpacing(4)
        ac_mon_row.addWidget(QLabel("Mon:"))
        self._ac_mon_dev = NoScrollComboBox(); self._ac_mon_dev.setStyleSheet("font-size:10px;")
        self._ac_mon_dev.currentIndexChanged.connect(lambda: self._on_mon_dev_changed("ac"))
        ac_mon_row.addWidget(self._ac_mon_dev, stretch=1)
        self._ac_mon_ch = NoScrollComboBox(); self._ac_mon_ch.setStyleSheet("font-size:10px;")
        ac_mon_row.addWidget(self._ac_mon_ch, stretch=1)
        fgl.addLayout(ac_mon_row, 2, 0, 1, 2)
        self._ac_grp.setMaximumWidth(310)
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

        # ── Column 3: DC Hysteresis params ────────────────────────────────────
        self._dc_grp = QGroupBox("DC Hysteresis"); dc_pgl = QGridLayout(self._dc_grp)
        dc_pgl.setSpacing(3); dc_pgl.setContentsMargins(8, 8, 8, 8)
        dc_pgl.addWidget(QLabel("Device:"), 0, 0)
        self.dc_dev_combo = NoScrollComboBox()
        self.dc_dev_combo.setStyleSheet("font-size:10px;")
        # Populate later via populate_monitor_combo; add placeholder for now
        self.dc_dev_combo.addItem("hpp-N42/beckhoff/pyhystlongi",
                                  "hpp-N42/beckhoff/pyhystlongi")
        dc_pgl.addWidget(self.dc_dev_combo, 0, 1, 1, 3)

        def _dbl(lo, hi, dec, v):
            w = NoScrollDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec); w.setValue(v); return w
        def _int(lo, hi, v):
            w = NoScrollSpinBox(); w.setRange(lo, hi); w.setValue(v); return w

        dc_pgl.addWidget(QLabel("Field (V):"), 1, 0)
        self.dc_field_V = _dbl(0.001, 20, 3, 1.0);  dc_pgl.addWidget(self.dc_field_V, 1, 1)
        dc_pgl.addWidget(QLabel("Int (s):"),   1, 2)
        self.dc_int_t   = _dbl(0.01, 600, 2, 2.0);   dc_pgl.addWidget(self.dc_int_t,   1, 3)
        dc_pgl.addWidget(QLabel("Pts/half:"),  2, 0)
        self.dc_npts    = _int(1, 350, 100);           dc_pgl.addWidget(self.dc_npts,    2, 1)
        dc_pgl.addWidget(QLabel("Cycles:"),    2, 2)
        self.dc_cycles  = _int(1, 9999, 1);            dc_pgl.addWidget(self.dc_cycles,  2, 3)
        self.dc_dur_lbl = QLabel()
        self.dc_dur_lbl.setStyleSheet("color:#6c7086;font-size:10px;")
        dc_pgl.addWidget(self.dc_dur_lbl, 3, 0, 1, 4)
        for w in [self.dc_int_t, self.dc_npts, self.dc_cycles]:
            w.valueChanged.connect(self._upd_dc_dur)
        self._upd_dc_dur()
        # DC monitor dropdowns at bottom of DC group
        dc_mon_row = QHBoxLayout(); dc_mon_row.setSpacing(4)
        dc_mon_row.addWidget(QLabel("Mon:"))
        self._dc_mon_dev = NoScrollComboBox(); self._dc_mon_dev.setStyleSheet("font-size:10px;")
        self._dc_mon_dev.currentIndexChanged.connect(lambda: self._on_mon_dev_changed("dc"))
        dc_mon_row.addWidget(self._dc_mon_dev, stretch=1)
        self._dc_mon_ch = NoScrollComboBox(); self._dc_mon_ch.setStyleSheet("font-size:10px;")
        dc_mon_row.addWidget(self._dc_mon_ch, stretch=1)
        dc_pgl.addLayout(dc_mon_row, 4, 0, 1, 4)
        self._dc_grp.setMaximumWidth(300)
        horiz.addWidget(self._dc_grp)

        fw_root.addLayout(horiz)
        self.field_w.setVisible(False)
        root.addWidget(self.field_w)
        self._on_submode_changed(0)   # apply initial highlight

        # ── TR-MOKE panel — DG645 front-panel style control ─────────────────
        self.trmoke_w = QWidget(); tr_root = QHBoxLayout(self.trmoke_w)
        tr_root.setContentsMargins(0, 0, 0, 0); tr_root.setSpacing(5)

        # ── Column 1: Channel selector + delay readback ──────────────────────
        tr_ch_grp = QGroupBox("DG645 Channels"); cg = QGridLayout(tr_ch_grp)
        cg.setSpacing(3); cg.setContentsMargins(6, 6, 6, 6)

        # Device row — path comes from Setup Defaults; shown read-only here
        cg.addWidget(QLabel("Device:"), 0, 0)
        self._tr_dev_lbl = QLabel("hpp-N42/delay/DG645")
        self._tr_dev_lbl.setStyleSheet(
            "color:#a6e3a1;font-size:10px;background:#181825;"
            "border:1px solid #313244;border-radius:4px;padding:2px 6px;")
        self._tr_dev_lbl.setMinimumWidth(140)
        cg.addWidget(self._tr_dev_lbl, 0, 1, 1, 5)
        tr_conn = QPushButton("Connect"); tr_conn.setFixedWidth(55)
        tr_conn.clicked.connect(self._tr_connect)
        cg.addWidget(tr_conn, 0, 6)

        self._tr_status = QLabel("Not connected")
        self._tr_status.setStyleSheet("color:#6c7086;font-size:9px;"); self._tr_status.setWordWrap(True)
        cg.addWidget(self._tr_status, 1, 0, 1, 7)

        # Channel selector buttons — styled like the DG645 front panel
        _CH_BTN = ("QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
                    "border-radius:3px;font-weight:bold;font-size:11px;padding:4px 8px;min-width:28px;}"
                    "QPushButton:hover{background:#45475a;}"
                    "QPushButton:checked{background:#89b4fa;color:#1e1e2e;border:1px solid #89b4fa;}")
        self._tr_ch_btns = {}
        ch_row = QHBoxLayout(); ch_row.setSpacing(2)
        for i, ch in enumerate(["A","B","C","D","E","F","G","H"]):
            btn = QPushButton(ch); btn.setCheckable(True); btn.setStyleSheet(_CH_BTN)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda checked, c=ch: self._tr_select_channel(c))
            self._tr_ch_btns[ch] = btn
            ch_row.addWidget(btn)
        ch_row.addStretch()
        cg.addLayout(ch_row, 2, 0, 1, 7)
        self._tr_ch_btns["A"].setChecked(True)
        self._tr_selected_ch = "A"

        # Selected channel display — large monospace readback
        self._tr_ch_label = QLabel("Channel A")
        self._tr_ch_label.setStyleSheet("color:#89b4fa;font-weight:bold;font-size:11px;")
        cg.addWidget(self._tr_ch_label, 3, 0, 1, 2)
        self._tr_ch_readback = QLabel("—")
        self._tr_ch_readback.setStyleSheet(
            "color:#a6e3a1;font-family:'Courier New',monospace;font-size:14px;"
            "font-weight:bold;background:#181825;border:1px solid #313244;"
            "border-radius:4px;padding:4px 8px;")
        self._tr_ch_readback.setMinimumWidth(160)
        cg.addWidget(self._tr_ch_readback, 3, 2, 1, 5)

        # Reference channel
        cg.addWidget(QLabel("Ref:"), 4, 0)
        self._tr_ref = NoScrollComboBox()
        self._tr_ref.addItems(["T0","T1","A","B","C","D","E","F","G","H"])
        self._tr_ref.setFixedWidth(55)
        cg.addWidget(self._tr_ref, 4, 1)
        tr_ref_set = QPushButton("Set"); tr_ref_set.setFixedWidth(32)
        tr_ref_set.clicked.connect(self._tr_write_ref)
        cg.addWidget(tr_ref_set, 4, 2)

        # Set delay
        cg.addWidget(QLabel("Delay:"), 4, 3)
        self._tr_delay_set = NoScrollDoubleSpinBox()
        self._tr_delay_set.setRange(0, 1e9); self._tr_delay_set.setDecimals(6)
        self._tr_delay_set.setValue(0.0); self._tr_delay_set.setFixedWidth(110)
        cg.addWidget(self._tr_delay_set, 4, 4)
        # Unit selector (shared with scan)
        self._tr_unit = NoScrollComboBox()
        self._tr_unit.addItems(["ns","ps","µs"]); self._tr_unit.setFixedWidth(42)
        cg.addWidget(self._tr_unit, 4, 5)
        tr_go = QPushButton("Go")
        tr_go.setFixedWidth(32)
        tr_go.setStyleSheet("background:#a6e3a1;color:#1e1e2e;font-weight:bold;border-radius:3px;")
        tr_go.clicked.connect(self._tr_write_delay)
        cg.addWidget(tr_go, 4, 6)

        # All-channel readback grid (compact)
        _RB_STYLE = "color:#94e2d5;font-family:'Courier New',monospace;font-size:9px;"
        self._tr_all_rb = {}
        rb_grid = QWidget(); rgl = QGridLayout(rb_grid)
        rgl.setSpacing(1); rgl.setContentsMargins(0, 2, 0, 0)
        for i, ch in enumerate(["A","B","C","D","E","F","G","H"]):
            r, c = divmod(i, 4)
            lbl = QLabel(f"{ch}: —")
            lbl.setStyleSheet(_RB_STYLE); lbl.setFixedWidth(85)
            self._tr_all_rb[ch] = lbl
            rgl.addWidget(lbl, r, c)
        cg.addWidget(rb_grid, 5, 0, 1, 5)
        tr_readall = QPushButton("Read All"); tr_readall.setFixedWidth(55)
        tr_readall.clicked.connect(self._tr_read_all_channels)
        cg.addWidget(tr_readall, 5, 5, 1, 2)

        tr_ch_grp.setMaximumWidth(400)
        tr_root.addWidget(tr_ch_grp)

        # ── Column 2: Output config + Delay Scan ─────────────────────────────
        col2 = QVBoxLayout(); col2.setSpacing(4)

        tr_out_grp = QGroupBox("Outputs"); og = QGridLayout(tr_out_grp)
        og.setSpacing(3); og.setContentsMargins(6, 6, 6, 6)

        # Output selector buttons
        _OUT_BTN = ("QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
                     "border-radius:3px;font-weight:bold;font-size:10px;padding:3px 6px;}"
                     "QPushButton:hover{background:#45475a;}"
                     "QPushButton:checked{background:#f9e2af;color:#1e1e2e;border:1px solid #f9e2af;}")
        self._tr_out_btns = {}
        out_row = QHBoxLayout(); out_row.setSpacing(2)
        for out in ["T0","AB","CD","EF","GH"]:
            btn = QPushButton(out); btn.setCheckable(True); btn.setStyleSheet(_OUT_BTN)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda checked, o=out: self._tr_select_output(o))
            self._tr_out_btns[out] = btn
            out_row.addWidget(btn)
        out_row.addStretch()
        og.addLayout(out_row, 0, 0, 1, 4)
        self._tr_out_btns["AB"].setChecked(True)
        self._tr_selected_out = "AB"

        og.addWidget(QLabel("Ampl:"), 1, 0)
        self._tr_ampl = NoScrollDoubleSpinBox()
        self._tr_ampl.setRange(0.5, 5.0); self._tr_ampl.setDecimals(2)
        self._tr_ampl.setValue(3.5); self._tr_ampl.setSuffix(" V"); self._tr_ampl.setFixedWidth(75)
        og.addWidget(self._tr_ampl, 1, 1)
        og.addWidget(QLabel("Offs:"), 1, 2)
        self._tr_offs = NoScrollDoubleSpinBox()
        self._tr_offs.setRange(-2.0, 2.0); self._tr_offs.setDecimals(2)
        self._tr_offs.setValue(0.0); self._tr_offs.setSuffix(" V"); self._tr_offs.setFixedWidth(75)
        og.addWidget(self._tr_offs, 1, 3)

        og.addWidget(QLabel("Pol:"), 2, 0)
        self._tr_pol = NoScrollComboBox()
        self._tr_pol.addItems(["Positive","Negative"]); self._tr_pol.setFixedWidth(75)
        og.addWidget(self._tr_pol, 2, 1)
        og.addWidget(QLabel("Presc:"), 2, 2)
        self._tr_out_prescale = NoScrollSpinBox()
        self._tr_out_prescale.setRange(1, 2**30-1); self._tr_out_prescale.setValue(1)
        self._tr_out_prescale.setFixedWidth(75)
        og.addWidget(self._tr_out_prescale, 2, 3)

        tr_out_apply = QPushButton("Apply Output"); tr_out_apply.setFixedWidth(85)
        tr_out_apply.clicked.connect(self._tr_write_output)
        og.addWidget(tr_out_apply, 3, 0, 1, 2)
        tr_out_read = QPushButton("Read"); tr_out_read.setFixedWidth(45)
        tr_out_read.clicked.connect(self._tr_read_output)
        og.addWidget(tr_out_read, 3, 2)
        self._tr_fmod = QLabel("f_mod: —")
        self._tr_fmod.setStyleSheet("color:#f9e2af;font-weight:bold;font-size:10px;"
                                    "font-family:'Courier New',monospace;")
        og.addWidget(self._tr_fmod, 3, 3)

        col2.addWidget(tr_out_grp)

        # Delay scan range (compact)
        tr_scan_grp = QGroupBox("Delay Scan"); sg = QGridLayout(tr_scan_grp)
        sg.setSpacing(3); sg.setContentsMargins(6, 6, 6, 6)

        # Row 0: Scan ch | Start | Stop  (all on one line)
        sg.addWidget(QLabel("Ch:"), 0, 0)
        self._tr_ch = NoScrollComboBox()
        self._tr_ch.addItems(["A","B","C","D","E","F","G","H"]); self._tr_ch.setFixedWidth(50)
        sg.addWidget(self._tr_ch, 0, 1)
        sg.addWidget(QLabel("Start:"), 0, 2)
        self._tr_start = NoScrollDoubleSpinBox()
        self._tr_start.setRange(0, 1e9); self._tr_start.setDecimals(3); self._tr_start.setValue(0.0)
        self._tr_start.setFixedWidth(80); self._tr_start.valueChanged.connect(self._tr_upd_info)
        sg.addWidget(self._tr_start, 0, 3)
        sg.addWidget(QLabel("Stop:"), 0, 4)
        self._tr_stop = NoScrollDoubleSpinBox()
        self._tr_stop.setRange(0, 1e9); self._tr_stop.setDecimals(3); self._tr_stop.setValue(10.0)
        self._tr_stop.setFixedWidth(80); self._tr_stop.valueChanged.connect(self._tr_upd_info)
        sg.addWidget(self._tr_stop, 0, 5)

        # Row 1: N / Δt toggle (same pattern as ActuatorGroup)
        ns_row = QWidget(); ns_lay = QHBoxLayout(ns_row)
        ns_lay.setContentsMargins(0, 0, 0, 0); ns_lay.setSpacing(4)
        self._tr_mode_bg = QButtonGroup(self)
        self._tr_rb_n    = QRadioButton("N:"); self._tr_rb_n.setChecked(True)
        self._tr_rb_step = QRadioButton("Δt:")
        self._tr_mode_bg.addButton(self._tr_rb_n, 0)
        self._tr_mode_bg.addButton(self._tr_rb_step, 1)
        self._tr_mode_bg.idClicked.connect(self._tr_on_mode)
        self._tr_npts = NoScrollSpinBox(); self._tr_npts.setRange(2, 100000); self._tr_npts.setValue(211)
        self._tr_npts.valueChanged.connect(self._tr_upd_info)
        self._tr_step = NoScrollDoubleSpinBox(); self._tr_step.setRange(1e-6, 1e9)
        self._tr_step.setDecimals(4); self._tr_step.setValue(0.050)
        self._tr_step.setVisible(False)
        self._tr_step.valueChanged.connect(self._tr_upd_info)
        self._tr_info = QLabel(); self._tr_info.setStyleSheet("color:#6c7086;font-size:10px;")
        for w in [self._tr_rb_n, self._tr_npts, self._tr_rb_step, self._tr_step, self._tr_info]:
            ns_lay.addWidget(w)
        ns_lay.addStretch()
        sg.addWidget(ns_row, 1, 0, 1, 6)

        col2.addWidget(tr_scan_grp)
        tr_root.addLayout(col2)

        # ── Column 3: Trigger + Burst ─────────────────────────────────────────
        col3 = QVBoxLayout(); col3.setSpacing(4)

        tr_trig_grp = QGroupBox("Trigger"); tg = QGridLayout(tr_trig_grp)
        tg.setSpacing(3); tg.setContentsMargins(6, 6, 6, 6)
        tg.addWidget(QLabel("Source:"), 0, 0)
        self._tr_trig_src = NoScrollComboBox()
        self._tr_trig_src.addItems(["Internal","Ext Rising","Ext Falling",
                                     "SS Ext Rise","SS Ext Fall","Single Shot","Line"])
        self._tr_trig_src.setCurrentIndex(1); self._tr_trig_src.setFixedWidth(100)
        tg.addWidget(self._tr_trig_src, 0, 1)
        tr_src_set = QPushButton("Set"); tr_src_set.setFixedWidth(32)
        tr_src_set.clicked.connect(self._tr_write_trig_source)
        tg.addWidget(tr_src_set, 0, 2)

        tg.addWidget(QLabel("Thresh:"), 1, 0)
        self._tr_trig_thr = NoScrollDoubleSpinBox()
        self._tr_trig_thr.setRange(-3.5, 3.5); self._tr_trig_thr.setDecimals(2)
        self._tr_trig_thr.setValue(1.0); self._tr_trig_thr.setSuffix(" V"); self._tr_trig_thr.setFixedWidth(90)
        tg.addWidget(self._tr_trig_thr, 1, 1)
        tr_thr_set = QPushButton("Set"); tr_thr_set.setFixedWidth(32)
        tr_thr_set.clicked.connect(self._tr_write_trig_threshold)
        tg.addWidget(tr_thr_set, 1, 2)

        tg.addWidget(QLabel("Rate:"), 2, 0)
        self._tr_rate_lbl = QLabel("—")
        self._tr_rate_lbl.setStyleSheet("color:#a6e3a1;font-weight:bold;font-size:10px;"
                                        "font-family:'Courier New',monospace;")
        tg.addWidget(self._tr_rate_lbl, 2, 1, 1, 2)

        tg.addWidget(QLabel("Holdoff:"), 3, 0)
        self._tr_holdoff = NoScrollDoubleSpinBox()
        self._tr_holdoff.setRange(0, 2e9); self._tr_holdoff.setDecimals(1)
        self._tr_holdoff.setValue(0); self._tr_holdoff.setSuffix(" ns"); self._tr_holdoff.setFixedWidth(90)
        tg.addWidget(self._tr_holdoff, 3, 1)
        tr_hold_set = QPushButton("Set"); tr_hold_set.setFixedWidth(32)
        tr_hold_set.clicked.connect(self._tr_write_holdoff)
        tg.addWidget(tr_hold_set, 3, 2)

        tg.addWidget(QLabel("Prescale:"), 4, 0)
        self._tr_prescale = NoScrollSpinBox()
        self._tr_prescale.setRange(1, 2**30-1); self._tr_prescale.setValue(1)
        self._tr_prescale.setFixedWidth(90)
        tg.addWidget(self._tr_prescale, 4, 1)
        tr_presc_set = QPushButton("Set"); tr_presc_set.setFixedWidth(32)
        tr_presc_set.clicked.connect(self._tr_write_prescale)
        tg.addWidget(tr_presc_set, 4, 2)
        col3.addWidget(tr_trig_grp)

        # Burst mode
        tr_burst_grp = QGroupBox("Burst Mode"); bg = QGridLayout(tr_burst_grp)
        bg.setSpacing(3); bg.setContentsMargins(6, 6, 6, 6)
        self._tr_burst_en = QCheckBox("Enable")
        self._tr_burst_en.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bg.addWidget(self._tr_burst_en, 0, 0)
        tr_burst_set = QPushButton("Set"); tr_burst_set.setFixedWidth(32)
        tr_burst_set.clicked.connect(self._tr_write_burst)
        bg.addWidget(tr_burst_set, 0, 1)
        bg.addWidget(QLabel("Count:"), 1, 0)
        self._tr_burst_cnt = NoScrollSpinBox()
        self._tr_burst_cnt.setRange(1, 2**31-1); self._tr_burst_cnt.setValue(1)
        self._tr_burst_cnt.setFixedWidth(80)
        bg.addWidget(self._tr_burst_cnt, 1, 1)
        bg.addWidget(QLabel("Period:"), 2, 0)
        self._tr_burst_per = NoScrollDoubleSpinBox()
        self._tr_burst_per.setRange(0.0001, 2000); self._tr_burst_per.setDecimals(4)
        self._tr_burst_per.setValue(0.001); self._tr_burst_per.setSuffix(" s")
        self._tr_burst_per.setFixedWidth(80)
        bg.addWidget(self._tr_burst_per, 2, 1)
        bg.addWidget(QLabel("Delay:"), 3, 0)
        self._tr_burst_dly = NoScrollDoubleSpinBox()
        self._tr_burst_dly.setRange(0, 2000); self._tr_burst_dly.setDecimals(6)
        self._tr_burst_dly.setValue(0.0); self._tr_burst_dly.setSuffix(" s")
        self._tr_burst_dly.setFixedWidth(80)
        bg.addWidget(self._tr_burst_dly, 3, 1)
        col3.addWidget(tr_burst_grp)

        # Single shot + Read All buttons
        tr_btns = QHBoxLayout(); tr_btns.setSpacing(4)
        tr_single = QPushButton("⚡ Single Shot")
        tr_single.setStyleSheet("background:#f9e2af;color:#1e1e2e;font-weight:bold;"
                                "border-radius:4px;padding:4px 8px;")
        tr_single.clicked.connect(self._tr_single_shot)
        tr_btns.addWidget(tr_single)
        tr_readall2 = QPushButton("🔄 Read All")
        tr_readall2.clicked.connect(self._tr_read_everything)
        tr_btns.addWidget(tr_readall2)
        col3.addLayout(tr_btns)
        col3.addStretch()

        tr_root.addLayout(col3)

        # ── Column 4: RTV40 Sync ──────────────────────────────────────────────
        col4 = QVBoxLayout(); col4.setSpacing(4)

        tr_rtv_grp = QGroupBox("RTV40 Sync"); rg = QGridLayout(tr_rtv_grp)
        rg.setSpacing(3); rg.setContentsMargins(6, 6, 6, 6)

        self._rtv40_en = QCheckBox("Enable RTV40 sync")
        self._rtv40_en.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        rg.addWidget(self._rtv40_en, 0, 0, 1, 3)

        rg.addWidget(QLabel("Device:"), 1, 0)
        self._rtv40_dev_lbl = QLabel("hpp-N42/pulser/RTV40")
        self._rtv40_dev_lbl.setStyleSheet(
            "color:#cba6f7;font-size:10px;background:#181825;"
            "border:1px solid #313244;border-radius:4px;padding:2px 6px;")
        rg.addWidget(self._rtv40_dev_lbl, 1, 1, 1, 2)

        rg.addWidget(QLabel("Tracking:"), 2, 0)
        self._rtv40_track_lbl = QLabel("sweep ch A")
        self._rtv40_track_lbl.setStyleSheet("color:#a6e3a1;font-size:10px;")
        rg.addWidget(self._rtv40_track_lbl, 2, 1, 1, 2)

        rg.addWidget(QLabel("Base width:"), 3, 0)
        self._rtv40_width = NoScrollDoubleSpinBox()
        self._rtv40_width.setRange(0.3, 20.0); self._rtv40_width.setDecimals(3)
        self._rtv40_width.setValue(1.0); self._rtv40_width.setSuffix(" ns")
        self._rtv40_width.setFixedWidth(85)
        rg.addWidget(self._rtv40_width, 3, 1)
        _rtv40_rd = QPushButton("Read"); _rtv40_rd.setFixedWidth(40)
        _rtv40_rd.clicked.connect(self._rtv40_read_width)
        rg.addWidget(_rtv40_rd, 3, 2)

        _sep = QLabel("── Device Settings ──")
        _sep.setStyleSheet("color:#6c7086;font-size:9px;")
        rg.addWidget(_sep, 4, 0, 1, 3)

        rg.addWidget(QLabel("Trig src:"), 5, 0)
        self._rtv40_trig_src = NoScrollComboBox()
        self._rtv40_trig_src.addItems(["Off", "External", "Internal"])
        self._rtv40_trig_src.setCurrentIndex(1)   # External
        self._rtv40_trig_src.setFixedWidth(90)
        rg.addWidget(self._rtv40_trig_src, 5, 1, 1, 2)
        self._rtv40_trig_src.currentIndexChanged.connect(self._rtv40_on_trig_src)

        self._rtv40_rate_lbl = QLabel("Trig rate:")
        rg.addWidget(self._rtv40_rate_lbl, 6, 0)
        self._rtv40_trig_rate = NoScrollDoubleSpinBox()
        self._rtv40_trig_rate.setRange(10, 100000); self._rtv40_trig_rate.setDecimals(0)
        self._rtv40_trig_rate.setValue(1000); self._rtv40_trig_rate.setSuffix(" Hz")
        self._rtv40_trig_rate.setFixedWidth(90)
        rg.addWidget(self._rtv40_trig_rate, 6, 1, 1, 2)

        rg.addWidget(QLabel("Polarity:"), 7, 0)
        self._rtv40_pol = NoScrollComboBox()
        self._rtv40_pol.addItems(["Negative", "Positive"])
        self._rtv40_pol.setCurrentIndex(1)   # Positive
        self._rtv40_pol.setFixedWidth(90)
        rg.addWidget(self._rtv40_pol, 7, 1, 1, 2)

        _rtv40_apply = QPushButton("Apply to Device")
        _rtv40_apply.setStyleSheet(
            "background:#cba6f7;color:#1e1e2e;font-weight:bold;"
            "border-radius:3px;padding:3px 6px;")
        _rtv40_apply.clicked.connect(self._rtv40_apply)
        rg.addWidget(_rtv40_apply, 8, 0, 1, 3)

        self._rtv40_status = QLabel("")
        self._rtv40_status.setStyleSheet("color:#6c7086;font-size:9px;")
        self._rtv40_status.setWordWrap(True)
        rg.addWidget(self._rtv40_status, 9, 0, 1, 3)

        self._rtv40_dev_path = ""

        col4.addWidget(tr_rtv_grp)
        col4.addStretch()
        tr_root.addLayout(col4)

        self._rtv40_on_trig_src()
        self._tr_ch.currentTextChanged.connect(
            lambda ch: self._rtv40_track_lbl.setText(f"sweep ch {ch}"))

        self.trmoke_w.setVisible(False)
        root.addWidget(self.trmoke_w)
        self._tr_upd_info()

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
        bot.addWidget(tg)

        # mg — Metadata group (MOKE-specific fields)
        mg = MokeMetadataGroup("Metadata")
        self.meta = mg
        bot.addWidget(mg)

        # hw — Hardware panel (Keithley current source + magnet/relay)
        self.hw = HardwarePanel(self._setup_getter, "Hardware")
        self.hw.setMaximumWidth(700)
        bot.addWidget(self.hw)

        root.addLayout(bot)
        self._on_axis_toggled()

        # save_dir is managed by the action bar, stored here for config persistence
        self._save_dir = os.path.expanduser("~/moke_data")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _on_type_changed(self):
        mode = self.scan_bg.checkedId()  # 0=Spatial, 1=Field, 2=TR-MOKE
        self.spatial_w.setVisible(mode == 0)
        self.field_w.setVisible(mode == 1)
        self.trmoke_w.setVisible(mode == 2)
        if mode == 0:
            self.scan_mode_changed.emit("SPATIAL")
        elif mode == 1:
            self.scan_mode_changed.emit("DC_HYST" if self._fsub_bg.checkedId() == 1 else "FIELD")
        else:
            self.scan_mode_changed.emit("TR_MOKE")

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
        self.scan_mode_changed.emit("DC_HYST" if mode_id == 1 else "FIELD")

    def _upd_dc_dur(self):
        """Show estimated total measurement time for DC hysteresis."""
        try:
            int_t   = self.dc_int_t.value()
            cycles  = self.dc_cycles.value()
            total_s = int_t * 2 * cycles
            dur = (f"≈ {total_s:.0f} s" if total_s < 120
                   else f"≈ {total_s/60:.1f} min" if total_s < 3600
                   else f"≈ {total_s/3600:.1f} h")
            self.dc_dur_lbl.setText(
                f"Est. {dur}  (2 × {int_t:.2g} s/half × {cycles} cycles)")
        except Exception:
            pass

    def _on_axis_toggled(self):
        x_on = self.act1_grp.scan_cb.isChecked()
        y_on = self.act2_grp.scan_cb.isChecked()
        time_mode = (not x_on and not y_on)
        both_on   = x_on and y_on

        # Zigzag container: only visible when both axes are active
        self.zigzag_w.setVisible(both_on)

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

    # ── Field / Hc convergence monitor ───────────────────────────────────────
    # ── Setup-defaults helpers (called from samba.py) ─────────────────────────
    def set_actuator_defaults(self, act1_dev: str, act1_attr: str,
                               act1_lbl: str, act1_unit: str,
                               act2_dev: str, act2_attr: str,
                               act2_lbl: str, act2_unit: str):
        """Update ActuatorGroup display fields when setup defaults change."""
        self.act1_grp.set_defaults(act1_dev, act1_attr, act1_lbl, act1_unit)
        self.act2_grp.set_defaults(act2_dev, act2_attr, act2_lbl, act2_unit)

    def set_trmoke_device(self, path: str):
        """Update the TR-MOKE device label from setup defaults."""
        if path:
            self._tr_dev_lbl.setText(path)

    def set_rtv40_device(self, path: str):
        """Update the RTV40 device label and stored path from setup defaults."""
        if path:
            self._rtv40_dev_path = path
            self._rtv40_dev_lbl.setText(path)

    def _rtv40_on_trig_src(self):
        """Show trigger-rate row only when Internal trigger is selected."""
        is_internal = self._rtv40_trig_src.currentIndex() == 2
        self._rtv40_rate_lbl.setVisible(is_internal)
        self._rtv40_trig_rate.setVisible(is_internal)

    def _rtv40_read_width(self):
        """Read PulseWidth from the RTV40 device and update the base-width spinbox."""
        path = self._rtv40_dev_path or self._rtv40_dev_lbl.text().strip()
        if not path:
            self._rtv40_status.setText("No device path set.")
            return
        try:
            from hardware import fresh_proxy, safe_read
            p, err = fresh_proxy(path)
            if err:
                self._rtv40_status.setText(f"⚠ {err}")
                return
            val, rerr = safe_read(p, "PulseWidth")
            if rerr:
                self._rtv40_status.setText(f"⚠ Read error: {rerr}")
                return
            if val is not None:
                self._rtv40_width.setValue(float(val))
                self._rtv40_status.setText(f"PulseWidth = {float(val):.3f} ns")
        except Exception as e:
            self._rtv40_status.setText(f"⚠ {e}")

    def _rtv40_apply(self):
        """Write TriggerSource, TriggerRate, and Polarity to the RTV40 device."""
        path = self._rtv40_dev_path or self._rtv40_dev_lbl.text().strip()
        if not path:
            self._rtv40_status.setText("No device path set.")
            return
        try:
            from hardware import fresh_proxy, safe_write
            p, err = fresh_proxy(path)
            if err:
                self._rtv40_status.setText(f"⚠ Connect: {err}")
                return
            src = self._rtv40_trig_src.currentIndex()   # 0=Off, 1=Ext, 2=Int
            pol = self._rtv40_pol.currentIndex()         # 0=Neg, 1=Pos
            errs = []
            e = safe_write(p, "TriggerSource", src)
            if e: errs.append(f"TrigSource: {e}")
            if src == 2:
                e = safe_write(p, "TriggerRate", float(self._rtv40_trig_rate.value()))
                if e: errs.append(f"TrigRate: {e}")
            e = safe_write(p, "Polarity", pol)
            if e: errs.append(f"Polarity: {e}")
            if errs:
                self._rtv40_status.setText("⚠ " + ", ".join(errs))
            else:
                _src_names = {0: "Off", 1: "External", 2: "Internal"}
                self._rtv40_status.setText(
                    f"Applied: src={_src_names[src]}, "
                    f"pol={'Pos' if pol else 'Neg'}")
        except Exception as e:
            self._rtv40_status.setText(f"⚠ {e}")

    def populate_monitor_combo(self, registry: list, preserve: bool = True):
        """Fill AC monitor, DC monitor, DC device, and AC device combos
        from the device registry.  Safe to call multiple times.

        preserve=False is used on setup switch so load_monitor_settings
        (called immediately after) exclusively controls the selection,
        rather than carrying over the previous setup's device name.
        """
        self._mon_registry = registry

        # ── AC + DC monitor dropdowns ─────────────────────────────────────────
        for dev_combo in (self._ac_mon_dev, self._dc_mon_dev):
            prev = dev_combo.currentText() if preserve else ""
            dev_combo.blockSignals(True); dev_combo.clear()
            for dev in registry:
                dev_combo.addItem(dev["name"], dev["tango_path"])
            if prev:
                idx = dev_combo.findText(prev)
                if idx >= 0: dev_combo.setCurrentIndex(idx)
            dev_combo.blockSignals(False)
        self._on_mon_dev_changed("ac")
        self._on_mon_dev_changed("dc")

        # ── DC hyst device combo (hysteresis-type devices preferred) ──────────
        hyst_devs = [d for d in registry if d.get("type") == "hysteresis"]
        show_dc = hyst_devs if hyst_devs else registry
        prev_dc = self.dc_dev_combo.currentData() or ""
        self.dc_dev_combo.blockSignals(True); self.dc_dev_combo.clear()
        for d in show_dc:
            self.dc_dev_combo.addItem(d["name"], d["tango_path"])
        if prev_dc:
            for i in range(self.dc_dev_combo.count()):
                if self.dc_dev_combo.itemData(i) == prev_dc:
                    self.dc_dev_combo.setCurrentIndex(i); break
        self.dc_dev_combo.blockSignals(False)

        # ── AC field device combo (magnet-type devices + setup-default option) ─
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

    # ── TR-MOKE / DG645 helpers ────────────────────────────────────────────
    _TR_UNIT_FACTORS = {"ps": 1e-12, "ns": 1e-9, "µs": 1e-6}

    def _tr_get_proxy(self):
        path = self._tr_dev_lbl.text().strip()
        if not path: return None, "No device"
        from hardware import fresh_proxy
        p, err = fresh_proxy(path)
        return p, err

    def _tr_connect(self):
        from hardware import is_sim_proxy, safe_read
        p, err = self._tr_get_proxy()
        if err and p is None:
            self._tr_status.setText(f"FAULT: {err}")
            self._tr_status.setStyleSheet("color:#f38ba8;font-size:9px;"); return
        if is_sim_proxy(p):
            self._tr_status.setText("Simulation mode")
            self._tr_status.setStyleSheet("color:#f9e2af;font-size:9px;"); return
        try:
            idn = p.command_inout("SendQuery", "*IDN?")
            self._tr_status.setText(str(idn))
            self._tr_status.setStyleSheet("color:#a6e3a1;font-size:9px;")
        except Exception:
            try:
                st = str(p.state())
                self._tr_status.setText(f"Connected ({st})")
            except Exception as e2:
                self._tr_status.setText(f"Err: {e2}")
            self._tr_status.setStyleSheet("color:#a6e3a1;font-size:9px;")
        self._tr_read_everything()

    # ── Channel selector ─────────────────────────────────────────────────
    def _tr_select_channel(self, ch):
        self._tr_selected_ch = ch
        for c, btn in self._tr_ch_btns.items():
            btn.setChecked(c == ch)
        self._tr_ch_label.setText(f"Channel {ch}")
        self._tr_read_delay()

    # ── Output selector ──────────────────────────────────────────────────
    def _tr_select_output(self, out):
        self._tr_selected_out = out
        for o, btn in self._tr_out_btns.items():
            btn.setChecked(o == out)
        self._tr_read_output()

    # ── Read / write delay for selected channel ──────────────────────────
    def _tr_read_delay(self, update_inputs=True):
        from hardware import safe_read
        p, _ = self._tr_get_proxy()
        if p is None: return
        ch = self._tr_selected_ch
        val, err = safe_read(p, f"Delay{ch}")
        unit = self._tr_unit.currentText()
        factor = self._TR_UNIT_FACTORS.get(unit, 1e-9)
        if val is not None:
            disp = val / factor
            self._tr_ch_readback.setText(f"{disp:+.6f} {unit}")
            if update_inputs:
                self._tr_delay_set.setValue(disp)
        else:
            self._tr_ch_readback.setText("read err")
        # Also read reference — only update combo when explicitly requested
        if update_inputs:
            ref_val, _ = safe_read(p, f"DelayRef{ch}")
            if ref_val is not None:
                refs = ["T0","T1","A","B","C","D","E","F","G","H"]
                try:
                    idx = int(ref_val)
                    if 0 <= idx < len(refs):
                        self._tr_ref.setCurrentText(refs[idx])
                except (ValueError, TypeError): pass

    def _tr_write_delay(self):
        from hardware import safe_write
        p, _ = self._tr_get_proxy()
        if p is None: return
        ch = self._tr_selected_ch
        unit = self._tr_unit.currentText()
        val_s = self._tr_delay_set.value() * self._TR_UNIT_FACTORS.get(unit, 1e-9)
        safe_write(p, f"Delay{ch}", val_s)
        self._tr_read_delay()

    def _tr_write_ref(self):
        from hardware import safe_write
        p, _ = self._tr_get_proxy()
        if p is None: return
        ch = self._tr_selected_ch
        refs = ["T0","T1","A","B","C","D","E","F","G","H"]
        ref_idx = refs.index(self._tr_ref.currentText()) if self._tr_ref.currentText() in refs else 0
        safe_write(p, f"DelayRef{ch}", ref_idx)

    # ── Read all channels ────────────────────────────────────────────────
    def _tr_read_all_channels(self):
        from hardware import safe_read
        p, _ = self._tr_get_proxy()
        if p is None: return
        unit = self._tr_unit.currentText()
        factor = self._TR_UNIT_FACTORS.get(unit, 1e-9)
        for ch in ["A","B","C","D","E","F","G","H"]:
            val, _ = safe_read(p, f"Delay{ch}")
            if val is not None:
                disp = val / factor
                self._tr_all_rb[ch].setText(f"{ch}: {disp:+.3f}")
            else:
                self._tr_all_rb[ch].setText(f"{ch}: err")

    # ── Output config read/write ─────────────────────────────────────────
    def _tr_read_output(self):
        from hardware import safe_read
        p, _ = self._tr_get_proxy()
        if p is None: return
        out = self._tr_selected_out
        if out == "T0": out_attr = "T0"
        else: out_attr = out
        amp, _ = safe_read(p, f"Amplitude{out_attr}")
        if amp is not None: self._tr_ampl.setValue(float(amp))
        off, _ = safe_read(p, f"Offset{out_attr}")
        if off is not None: self._tr_offs.setValue(float(off))
        pol, _ = safe_read(p, f"Polarity{out_attr}")
        if pol is not None:
            self._tr_pol.setCurrentText("Positive" if int(pol) == 1 else "Negative")
        pres, _ = safe_read(p, f"Prescale{out_attr}")
        if pres is not None: self._tr_out_prescale.setValue(int(pres))
        # Update f_mod
        self._tr_update_fmod()

    def _tr_write_output(self):
        from hardware import safe_write
        p, _ = self._tr_get_proxy()
        if p is None: return
        out = self._tr_selected_out
        out_attr = out if out != "T0" else "T0"
        safe_write(p, f"Amplitude{out_attr}", self._tr_ampl.value())
        safe_write(p, f"Offset{out_attr}", self._tr_offs.value())
        pol_val = 1 if self._tr_pol.currentText() == "Positive" else 0
        safe_write(p, f"Polarity{out_attr}", pol_val)
        safe_write(p, f"Prescale{out_attr}", self._tr_out_prescale.value())
        self._tr_update_fmod()

    def _tr_update_fmod(self):
        """Calculate and display f_mod based on trigger rate and prescaler."""
        # Try to get rate from readback label, else guess 100 MHz
        f_rep = 100e6
        try:
            txt = self._tr_rate_lbl.text()
            for sfx, mul in [("GHz",1e9),("MHz",1e6),("kHz",1e3),("Hz",1)]:
                if sfx in txt:
                    f_rep = float(txt.replace(sfx,"").strip()) * mul; break
        except Exception: pass
        trig_pres = self._tr_prescale.value()
        out_pres  = self._tr_out_prescale.value()
        f_mod = f_rep / max(1, trig_pres) / max(1, out_pres)
        if f_mod >= 1e6:   self._tr_fmod.setText(f"f_mod={f_mod/1e6:.1f} MHz")
        elif f_mod >= 1e3: self._tr_fmod.setText(f"f_mod={f_mod/1e3:.1f} kHz")
        else:              self._tr_fmod.setText(f"f_mod={f_mod:.0f} Hz")

    # ── Trigger config read/write ────────────────────────────────────────
    def _tr_write_trig_source(self):
        from hardware import safe_write
        p, _ = self._tr_get_proxy()
        if p is None: return
        safe_write(p, "TriggerSource", self._tr_trig_src.currentIndex())

    def _tr_write_trig_threshold(self):
        from hardware import safe_write
        p, _ = self._tr_get_proxy()
        if p is None: return
        safe_write(p, "TriggerLevel", self._tr_trig_thr.value())

    def _tr_write_holdoff(self):
        from hardware import safe_write
        p, _ = self._tr_get_proxy()
        if p is None: return
        safe_write(p, "TriggerHoldoff", self._tr_holdoff.value() * 1e-9)  # ns → s

    def _tr_write_prescale(self):
        from hardware import safe_write
        p, _ = self._tr_get_proxy()
        if p is None: return
        safe_write(p, "TriggerPrescale", self._tr_prescale.value())
        self._tr_update_fmod()

    # ── Burst mode ───────────────────────────────────────────────────────
    def _tr_write_burst(self):
        from hardware import safe_write
        p, _ = self._tr_get_proxy()
        if p is None: return
        safe_write(p, "BurstMode", 1 if self._tr_burst_en.isChecked() else 0)
        safe_write(p, "BurstCount", self._tr_burst_cnt.value())
        safe_write(p, "BurstPeriod", self._tr_burst_per.value())
        safe_write(p, "BurstDelay", self._tr_burst_dly.value())

    # ── Single shot ──────────────────────────────────────────────────────
    def _tr_single_shot(self):
        p, _ = self._tr_get_proxy()
        if p is None: return
        try:
            p.command_inout("SingleShot")
        except Exception:
            from hardware import safe_write
            safe_write(p, "TriggerSource", 5)  # single shot mode
            import time; time.sleep(0.05)
            safe_write(p, "TriggerSource", self._tr_trig_src.currentIndex())

    # ── Read everything (on connect) ─────────────────────────────────────
    def _tr_read_everything(self):
        """Read all DG645 settings — called after Connect."""
        from hardware import safe_read
        p, _ = self._tr_get_proxy()
        if p is None: return
        self._tr_read_delay()
        self._tr_read_all_channels()
        self._tr_read_output()
        # Trigger
        src, _ = safe_read(p, "TriggerSource")
        if src is not None and 0 <= int(src) < self._tr_trig_src.count():
            self._tr_trig_src.setCurrentIndex(int(src))
        thr, _ = safe_read(p, "TriggerLevel")
        if thr is not None: self._tr_trig_thr.setValue(float(thr))
        rate, _ = safe_read(p, "TriggerRate")
        if rate is not None:
            if rate >= 1e6:   self._tr_rate_lbl.setText(f"{rate/1e6:.3f} MHz")
            elif rate >= 1e3: self._tr_rate_lbl.setText(f"{rate/1e3:.3f} kHz")
            else:             self._tr_rate_lbl.setText(f"{rate:.1f} Hz")
        hold, _ = safe_read(p, "TriggerHoldoff")
        if hold is not None: self._tr_holdoff.setValue(float(hold) * 1e9)  # s → ns
        pres, _ = safe_read(p, "TriggerPrescale")
        if pres is not None: self._tr_prescale.setValue(int(pres))
        # Burst
        bm, _ = safe_read(p, "BurstMode")
        if bm is not None: self._tr_burst_en.setChecked(bool(int(bm)))
        bc, _ = safe_read(p, "BurstCount")
        if bc is not None: self._tr_burst_cnt.setValue(int(bc))
        bp, _ = safe_read(p, "BurstPeriod")
        if bp is not None: self._tr_burst_per.setValue(float(bp))
        bd, _ = safe_read(p, "BurstDelay")
        if bd is not None: self._tr_burst_dly.setValue(float(bd))
        self._tr_update_fmod()

    # ── Scan info ────────────────────────────────────────────────────────
    def _tr_on_mode(self, m):
        self._tr_npts.setVisible(m == 0)
        self._tr_step.setVisible(m == 1)
        self._tr_upd_info()

    def _tr_upd_info(self):
        span = abs(self._tr_stop.value() - self._tr_start.value())
        if self._tr_rb_n.isChecked():
            n = max(2, self._tr_npts.value())
            step = span / (n - 1) if n > 1 else span
            unit = self._tr_unit.currentText()
            self._tr_info.setText(f"Δt = {step:.4g} {unit}")
        else:
            step = max(1e-12, self._tr_step.value())
            n = max(2, int(round(span / step)) + 1)
            self._tr_info.setText(f"N = {n}")

    def _tr_get_npts(self) -> int:
        span = abs(self._tr_stop.value() - self._tr_start.value())
        if self._tr_rb_n.isChecked():
            return max(2, self._tr_npts.value())
        step = max(1e-12, self._tr_step.value())
        return max(2, int(round(span / step)) + 1)

    def tr_refresh(self):
        """Called by the 500ms poll timer — update readback label only, not input widgets."""
        if self.trmoke_w.isVisible():
            self._tr_read_delay(update_inputs=False)

    # ── Load / get config ─────────────────────────────────────────────────────
    def load_config(self, cfg: dict):
        scan_t   = cfg.get("scan_type", "SPATIAL")
        is_field = scan_t in ("FIELD", "DC_HYST")
        is_dc    = scan_t == "DC_HYST"
        is_tr    = scan_t == "TR_MOKE"

        # Select pill button: 0=Spatial, 1=Field, 2=TR-MOKE
        if is_tr:
            self.scan_bg.button(2).setChecked(True)
        elif is_field:
            self.scan_bg.button(1).setChecked(True)
        else:
            self.scan_bg.button(0).setChecked(True)

        if is_field:
            (self.rb_dc_hy if is_dc else self.rb_ac_sw).setChecked(True)
            self._on_submode_changed(1 if is_dc else 0)
        self.act1_grp.load("act1", cfg, enabled=cfg.get("scan_x", True))
        self.act2_grp.load("act2", cfg, enabled=cfg.get("scan_y", False))
        # AC field segments (backward-compat: derive from old single start/stop/npts)
        segs = cfg.get("field_segments")
        if segs:
            self._seg_list.load_segments(segs)
        else:
            self._seg_list.load_segments([[
                cfg.get("field_start_A", -1.0),
                cfg.get("field_stop_A",   1.0),
                cfg.get("field_npts",     101),
            ]])
        # AC field device
        field_dev = cfg.get("field_device", "")
        for i in range(self._ac_dev_combo.count()):
            if self._ac_dev_combo.itemData(i) == field_dev:
                self._ac_dev_combo.setCurrentIndex(i); break
        self.zigzag_cb.setChecked(cfg.get("zigzag", False))
        _fa_id = 1 if cfg.get("fast_axis", "act1") == "act2" else 0
        self.fast_axis_bg.button(_fa_id).setChecked(True)
        self.int_time.setValue(cfg.get("integration_time", 0.1))
        self.settle.setValue(  cfg.get("settle_time",      0.05))
        self.timeout.setValue( cfg.get("move_timeout",     15.0))
        self.meta.load_values(cfg)
        # DC hyst device
        hyst_path = cfg.get("hyst_device", "")
        for i in range(self.dc_dev_combo.count()):
            if self.dc_dev_combo.itemData(i) == hyst_path:
                self.dc_dev_combo.setCurrentIndex(i); break
        # DC hyst numeric params
        self.dc_field_V.setValue(cfg.get("hyst_field_V",  1.0))
        self.dc_int_t.setValue(  cfg.get("hyst_int_time", 2.0))
        self.dc_npts.setValue(   cfg.get("hyst_npts",     100))
        self.dc_cycles.setValue( cfg.get("hyst_cycles",   1))
        # TR-MOKE params — device path is set authoritatively by set_trmoke_device()
        # from setup data; do NOT read it from the scan config here as that value is stale.
        ch = cfg.get("trmoke_channel", "A")
        idx = self._tr_ch.findText(ch)
        if idx >= 0: self._tr_ch.setCurrentIndex(idx)
        unit = cfg.get("trmoke_unit", "ns")
        idx = self._tr_unit.findText(unit)
        if idx >= 0: self._tr_unit.setCurrentIndex(idx)
        factor = self._TR_UNIT_FACTORS.get(unit, 1e-9)
        self._tr_start.setValue(cfg.get("trmoke_start", 0.0))
        self._tr_stop.setValue(cfg.get("trmoke_stop", 10.0))
        self._tr_step.setValue(cfg.get("trmoke_step", 0.050))
        if "trmoke_npts" in cfg:
            self._tr_npts.setValue(int(cfg["trmoke_npts"]))
        self._tr_prescale.setValue(cfg.get("trmoke_prescale", 1))
        self._rtv40_en.setChecked(bool(cfg.get("rtv40_sync_enabled", False)))
        self._rtv40_width.setValue(float(cfg.get("rtv40_base_width_ns", 1.0)))
        self._rtv40_trig_src.setCurrentIndex(min(int(cfg.get("rtv40_trig_src", 1)), 2))
        self._rtv40_trig_rate.setValue(float(cfg.get("rtv40_trig_rate", 1000.0)))
        self._rtv40_pol.setCurrentIndex(min(int(cfg.get("rtv40_polarity", 1)), 1))
        self._rtv40_on_trig_src()
        self._tr_upd_info()
        self._save_dir = os.path.expanduser(
            self._setup_getter().get("save_dir", "~/moke_data"))
        self._on_type_changed(); self._on_axis_toggled()

    def get_config_partial(self) -> dict:
        mode_id  = self.scan_bg.checkedId()  # 0=Spatial, 1=Field, 2=TR-MOKE
        is_field = mode_id == 1
        is_dc    = is_field and (self._fsub_bg.checkedId() == 1)
        is_tr    = mode_id == 2

        if is_tr:
            # TR-MOKE: build a SPATIAL 1D config with DG645 delay as actuator
            unit = self._tr_unit.currentText()
            factor = self._TR_UNIT_FACTORS.get(unit, 1e-9)
            start_s = self._tr_start.value() * factor
            stop_s  = self._tr_stop.value()  * factor
            npts    = self._tr_get_npts()
            ch      = self._tr_ch.currentText()
            return {
                "scan_type":    "TR_MOKE",
                "scan_x":       True,
                "scan_y":       False,
                # act1_device / trmoke_dg645 injected from Setup Defaults in samba.py
                "act1_attr":    f"Delay{ch}",
                "act1_label":   f"Delay {ch}",
                "act1_unit":    unit,
                "act1_start":   start_s,
                "act1_stop":    stop_s,
                "act1_npts":    npts,
                "integration_time": self.int_time.value(),
                "settle_time":      0.001,   # DG645 updates instantly
                "move_timeout":     self.timeout.value(),
                # TR-MOKE persistence keys
                "trmoke_channel":   ch,
                "trmoke_unit":      unit,
                "trmoke_start":     self._tr_start.value(),
                "trmoke_stop":      self._tr_stop.value(),
                "trmoke_step":      self._tr_step.value(),
                "trmoke_npts":      self._tr_npts.value(),
                "trmoke_prescale":  self._tr_prescale.value(),
                # RTV40 sync
                "rtv40_sync_enabled":  self._rtv40_en.isChecked(),
                "rtv40_base_width_ns": self._rtv40_width.value(),
                "rtv40_trig_src":      self._rtv40_trig_src.currentIndex(),
                "rtv40_trig_rate":     self._rtv40_trig_rate.value(),
                "rtv40_polarity":      self._rtv40_pol.currentIndex(),
                **self.meta.get_values(),
            }

        if is_dc:
            return {
                "scan_type":    "DC_HYST",
                "scan_x": False, "scan_y": False,
                "hyst_device":   self.dc_dev_combo.currentData() or "",
                "hyst_field_V":  self.dc_field_V.value(),
                "hyst_int_time": self.dc_int_t.value(),
                "hyst_npts":     self.dc_npts.value(),
                "hyst_cycles":   self.dc_cycles.value(),
                "integration_time": self.int_time.value(),
                "settle_time":      self.settle.value(),
                "move_timeout":     self.timeout.value(),
                "ac_monitor_device": self._ac_mon_dev.currentText(),
                "ac_monitor_attr":   self._ac_mon_ch.currentData() or "",
                "dc_monitor_device": self._dc_mon_dev.currentText(),
                "dc_monitor_attr":   self._dc_mon_ch.currentData() or "",
                **self.meta.get_values(),
            }

        segs = self._seg_list.get_segments()
        p = {
            "scan_type": "FIELD" if is_field else "SPATIAL",
            "scan_x":    self.act1_grp.scan_cb.isChecked() if not is_field else False,
            "scan_y":    self.act2_grp.scan_cb.isChecked() if not is_field else False,
            "zigzag":    self.zigzag_cb.isChecked(),
            "fast_axis": "act2" if self.fast_axis_bg.checkedId() == 1 else "act1",
        }
        p.update(self.act1_grp.get_partial("act1"))
        p.update(self.act2_grp.get_partial("act2"))
        p.update({
            "field_segments":    segs,
            # Keep legacy keys for backward compat with scan.py fallback
            "field_start_A":     segs[0][0]                 if segs else -1.0,
            "field_stop_A":      segs[-1][1]                if segs else  1.0,
            "field_npts":        sum(int(s[2]) for s in segs) if segs else 101,
            "field_device":      self._ac_dev_combo.currentData() or "",
            # Beckhoff magnet: command current [A], read corrected field [mT]
            # (the Magnet device returns mT — matches the DC-Hyst convention).
            "field_x_label":     "Field",
            "field_x_unit":      "mT",
            "field_setpoint_unit": "A",
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


