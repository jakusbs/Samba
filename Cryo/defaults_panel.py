"""
defaults_panel.py — Samba Cryo
Setup Defaults tab: hardware device paths and attribute names stored once per
setup, not buried in every scan config or keithley_mixin.py.

Four sections:
  Stage Actuators  — Act 1 (X), Act 2 (Y), Z (focus): device, attr, label, unit
  Keithley 6221    — device + amplitude/frequency/compliance/range/current attrs
  AttoDRY Cryostat — device + field-set/field-rb/temp-set/temp-rb/vti/magnet attrs
  Calibration      — FL sensor device + attr

All combos are populated from the Device Registry (friendly name → tango_path;
channels → attrs).  Emits `defaults_changed` on any edit; the main window saves
the values into the setup dict and propagates them to panels.
"""
import logging
from typing import List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QGroupBox, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt

from panels import NoScrollComboBox

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
_RO_STYLE = ("background:#1e1e2e;color:#6c7086;border:1px solid #313244;"
             "border-radius:4px;padding:2px 4px;")


def _ro(text: str = "—") -> QLineEdit:
    w = QLineEdit(text); w.setReadOnly(True); w.setStyleSheet(_RO_STYLE)
    w.setMinimumWidth(55); return w


# ─────────────────────────────────────────────────────────────────────────────
# ActuatorDefaultRow — device combo + attr combo + label/unit RO
# ─────────────────────────────────────────────────────────────────────────────
class ActuatorDefaultRow(QWidget):
    changed = pyqtSignal()

    def __init__(self, registry: List[dict], parent=None):
        super().__init__(parent)
        self._registry = registry
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)

        self.dev_combo = NoScrollComboBox()
        self.dev_combo.setMinimumWidth(160)
        self.dev_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.ch_combo = NoScrollComboBox(); self.ch_combo.setMinimumWidth(70)

        self.lbl_edit  = _ro()
        self.unit_edit = _ro(); self.unit_edit.setMinimumWidth(45)

        lay.addWidget(self.dev_combo)
        lay.addWidget(self.ch_combo)
        lay.addWidget(QLabel("Label:")); lay.addWidget(self.lbl_edit)
        lay.addWidget(QLabel("Unit:"));  lay.addWidget(self.unit_edit)

        self.dev_combo.currentIndexChanged.connect(self._on_dev_changed)
        self.ch_combo.currentIndexChanged.connect(self._on_ch_changed)
        self._populate_devs()

    def set_registry(self, registry: List[dict]):
        cur_path = self.dev_combo.currentData() or ""
        cur_attr = self.ch_combo.currentData()  or ""
        self._registry = registry
        self._populate_devs(initial_path=cur_path, initial_attr=cur_attr)

    def _populate_devs(self, initial_path: str = "", initial_attr: str = ""):
        self.dev_combo.blockSignals(True); self.dev_combo.clear()
        stage_devs = [d for d in self._registry if d.get("type") == "stage"]
        show = stage_devs if stage_devs else self._registry
        for d in show:
            self.dev_combo.addItem(d["name"], d["tango_path"])
        if initial_path:
            for i in range(self.dev_combo.count()):
                if self.dev_combo.itemData(i) == initial_path:
                    self.dev_combo.setCurrentIndex(i); break
        self.dev_combo.blockSignals(False)
        self._populate_chs(initial_attr=initial_attr)

    def _populate_chs(self, initial_attr: str = ""):
        self.ch_combo.blockSignals(True); self.ch_combo.clear()
        dev_path = self.dev_combo.currentData() or ""
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                for ch in d.get("channels", []):
                    self.ch_combo.addItem(ch.get("attr", "?"), ch.get("attr", ""))
                break
        if initial_attr:
            idx = self.ch_combo.findData(initial_attr)
            if idx >= 0:
                self.ch_combo.setCurrentIndex(idx)
            elif self.ch_combo.findText(initial_attr) < 0:
                self.ch_combo.addItem(initial_attr, initial_attr)
                self.ch_combo.setCurrentText(initial_attr)
        self.ch_combo.blockSignals(False)
        self._update_label_unit()

    def _on_dev_changed(self, _=None):
        cur_attr = self.ch_combo.currentData() or ""
        self._populate_chs(initial_attr=cur_attr)
        self.changed.emit()

    def _on_ch_changed(self, _=None):
        self._update_label_unit(); self.changed.emit()

    def _update_label_unit(self):
        attr     = self.ch_combo.currentData() or self.ch_combo.currentText()
        dev_path = self.dev_combo.currentData() or ""
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                for ch in d.get("channels", []):
                    if ch.get("attr") == attr:
                        self.lbl_edit.setText(ch.get("label", attr))
                        self.unit_edit.setText(ch.get("unit", "nm"))
                        return
                break

    def get(self) -> dict:
        return {
            "device": self.dev_combo.currentData() or "",
            "attr":   self.ch_combo.currentData()  or self.ch_combo.currentText() or "",
            "label":  self.lbl_edit.text(),
            "unit":   self.unit_edit.text(),
        }

    def load(self, device: str, attr: str):
        self._populate_devs(initial_path=device, initial_attr=attr)


# ─────────────────────────────────────────────────────────────────────────────
# HardwareDeviceGroup — device combo + configurable attr combos
# Used for Keithley and AttoDRY where every attribute name should be editable.
# ─────────────────────────────────────────────────────────────────────────────
class HardwareDeviceGroup(QWidget):
    """
    A compact grid with:
      Row 0: Device [combo]
      Row 1: <attr_defs[0].label>  [combo or linedit]
      Row 2: <attr_defs[1].label>  [combo or linedit]
      ...

    attr_defs is a list of (label, setup_key, default, kind) tuples where
    kind is "attr" (registry combo) or "cmd" (plain QLineEdit).
    Attr combos are populated from the channels of the selected device.
    If the registry has no channels, the default value is added as a fallback.
    """
    changed = pyqtSignal()

    def __init__(self, attr_defs: list, filter_type: str = "",
                 registry: list = None, parent=None):
        super().__init__(parent)
        self._registry  = registry or []
        self._filter    = filter_type
        self._attr_defs = attr_defs   # [(label, setup_key, default, kind?), ...]

        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0); g.setSpacing(4)
        g.setColumnStretch(1, 1)

        g.addWidget(QLabel("Device:"), 0, 0)
        self.dev_combo = NoScrollComboBox()
        self.dev_combo.setMinimumWidth(180)
        self.dev_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        g.addWidget(self.dev_combo, 0, 1)

        self._attr_widgets: dict = {}   # key -> QComboBox (attr) or QLineEdit (cmd)
        for i, tup in enumerate(attr_defs, start=1):
            lbl_text, key, default = tup[0], tup[1], tup[2]
            kind = tup[3] if len(tup) > 3 else "attr"
            g.addWidget(QLabel(f"{lbl_text}:"), i, 0)
            if kind == "cmd":
                widget = QLineEdit(default)
                widget.setMinimumWidth(120)
                widget.textChanged.connect(lambda _=None, k=key: self.changed.emit())
            else:
                widget = NoScrollComboBox()
                widget.setMinimumWidth(120)
                widget.currentIndexChanged.connect(lambda _=None, k=key: self.changed.emit())
            self._attr_widgets[key] = widget
            g.addWidget(widget, i, 1)

        self.dev_combo.currentIndexChanged.connect(self._on_dev_changed)
        self._populate_devs()

    # ── Internals ─────────────────────────────────────────────────────────────
    def _on_dev_changed(self, _=None):
        self._populate_attrs()
        self.changed.emit()

    def _populate_devs(self, initial_path: str = ""):
        self.dev_combo.blockSignals(True); self.dev_combo.clear()
        filtered = ([d for d in self._registry if d.get("type") == self._filter]
                    if self._filter else self._registry)
        for d in filtered:
            self.dev_combo.addItem(d["name"], d["tango_path"])
        if initial_path:
            for i in range(self.dev_combo.count()):
                if self.dev_combo.itemData(i) == initial_path:
                    self.dev_combo.setCurrentIndex(i); break
        self.dev_combo.blockSignals(False)
        self._populate_attrs()

    def _populate_attrs(self, saved: dict = None):
        saved    = saved or {}
        dev_path = self.dev_combo.currentData() or ""
        channels: list = []
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                channels = d.get("channels", []); break

        for tup in self._attr_defs:
            lbl_text, key, default_attr = tup[0], tup[1], tup[2]
            kind = tup[3] if len(tup) > 3 else "attr"
            widget = self._attr_widgets[key]
            want   = saved.get(key, default_attr)
            if kind == "cmd":
                widget.blockSignals(True)
                widget.setText(want or default_attr)
                widget.blockSignals(False)
            else:
                widget.blockSignals(True); widget.clear()
                for ch in channels:
                    widget.addItem(ch.get("attr", "?"), ch.get("attr", ""))
                # Ensure both the default and the saved value are always present
                for val in dict.fromkeys([default_attr, want]):
                    if val and widget.findData(val) < 0:
                        widget.addItem(val, val)
                idx = widget.findData(want) if want else 0
                if idx >= 0: widget.setCurrentIndex(idx)
                widget.blockSignals(False)

    # ── Public API ─────────────────────────────────────────────────────────────
    def set_registry(self, registry: list):
        cur_path  = self.dev_combo.currentData() or ""
        cur_attrs = self.get_attrs()
        self._registry = registry
        self._populate_devs(initial_path=cur_path)
        self._populate_attrs(saved=cur_attrs)

    def get_attrs(self) -> dict:
        """Return {setup_key: value} for all rows (combos and lineedits)."""
        result = {}
        for tup in self._attr_defs:
            key  = tup[1]
            kind = tup[3] if len(tup) > 3 else "attr"
            w    = self._attr_widgets[key]
            result[key] = w.text() if kind == "cmd" else (w.currentData() or w.currentText() or "")
        return result

    def get(self) -> dict:
        return {"device": self.dev_combo.currentData() or "", **self.get_attrs()}

    def load(self, device: str, attrs: dict = None):
        self._populate_devs(initial_path=device)
        self._populate_attrs(saved=attrs or {})


# ─────────────────────────────────────────────────────────────────────────────
# Attribute-definition tables (label, setup_key, fallback_attr_name)
# ─────────────────────────────────────────────────────────────────────────────
KEITHLEY_ATTR_DEFS = [
    # (label, setup_key, default, kind)  kind: "attr" = registry combo, "cmd" = plain text
    ("Amplitude",    "keithley_attr_amplitude",   "amplitude",  "attr"),
    ("Frequency",    "keithley_attr_frequency",   "frequency",  "attr"),
    ("Compliance",   "keithley_attr_compliance",  "compliance", "attr"),
    ("Range",        "keithley_attr_range",        "range",     "attr"),
    ("Current (RB)", "keithley_attr_current",      "current",   "attr"),
]

LOCKIN_ATTR_DEFS = [
    ("TC attr",      "zi_tc_attr",       "timeconstant", "attr"),
    ("Order attr",   "zi_order_attr",    "filterorder",  "attr"),
    ("Settling attr","zi_settling_attr", "settlingtime", "attr"),
]

ATTODRY_ATTR_DEFS = [
    # ── Read/write attributes ─────────────────────────────────────────────
    ("Field set",        "attodry_attr_field_set",      "MagneticField",                "attr"),
    ("Field RB",         "attodry_attr_field_rb",       "MagneticField",                "attr"),
    ("Temp set",         "attodry_attr_temp_set",       "Temperature",                  "attr"),
    ("Temp RB",          "attodry_attr_temp_rb",        "Temperature",                  "attr"),
    ("VTI temp",         "attodry_attr_vti_temp",       "VtiTemperature",               "attr"),
    ("Magnet temp",      "attodry_attr_mag_temp",       "MagnetTemperature",            "attr"),
    ("Reservoir temp",   "attodry_attr_reservoir_temp", "ReservoirTemperature",         "attr"),
    ("Pressure In",      "attodry_attr_pressure_in",    "CryostatInPressure",           "attr"),
    ("Pressure Out",     "attodry_attr_pressure_out",   "CryostatOutPressure",          "attr"),
    ("Heater Sample",    "attodry_attr_heat_sample",    "SampleHeaterPower",            "attr"),
    ("Heater VTI",       "attodry_attr_heat_vti",       "VtiHeaterPower",               "attr"),
    ("Heater Reservoir", "attodry_attr_heat_reservoir", "ReservoirHeaterPower",         "attr"),
    # ── Boolean control state attributes ─────────────────────────────────
    ("Mag Ctrl state",   "attodry_attr_mag_ctrl",       "MagneticFieldControl",         "attr"),
    ("Temp Ctrl state",  "attodry_attr_temp_ctrl",      "FulltemperatureControl",       "attr"),
    ("Persist state",    "attodry_attr_persist",        "PersistentMode",               "attr"),
    # ── Toggle command names (fallback if bool attrs unavailable) ─────────
    ("Cmd: Mag Ctrl",    "attodry_cmd_mag_ctrl",        "toggleMagneticFieldControl",   "cmd"),
    ("Cmd: Temp Ctrl",   "attodry_cmd_temp_ctrl",       "toggleFulltemperatureControl", "cmd"),
    ("Cmd: Persist",     "attodry_cmd_persist",         "togglePersistentMode",         "cmd"),
]


# ─────────────────────────────────────────────────────────────────────────────
# SetupDefaultsPanel
# ─────────────────────────────────────────────────────────────────────────────
class SetupDefaultsPanel(QWidget):
    """
    Stores per-setup hardware device paths and attribute names so they don't
    need to live inside every scan config or be hardcoded in the source.

    Sections:
      Stage Actuators  — Act1/Act2/Z device + attr + label + unit
      Keithley 6221    — device + 5 writable/readable attribute names
      AttoDRY Cryostat — device + 6 attribute names (field, temp, readbacks)
      Calibration      — FL sensor device + attr
    """

    defaults_changed = pyqtSignal()

    def __init__(self, registry: List[dict] = None, parent=None):
        super().__init__(parent)
        self._registry = registry or []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        inner = QWidget()
        root  = QVBoxLayout(inner)
        root.setContentsMargins(10, 10, 10, 10); root.setSpacing(10)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # ── Stage Actuators — Faraday / Voigt × ANM200 / ANC300 ─────────────
        act_grp = QGroupBox("Stage Actuators")
        act_outer = QHBoxLayout(act_grp)
        act_outer.setSpacing(10); act_outer.setContentsMargins(8, 8, 8, 8)

        def _make_piezo_rows(grp_layout):
            """Add header + 3 ActuatorDefaultRow widgets to grp_layout.
            Returns (act1_row, act2_row, actz_row)."""
            g = grp_layout
            for col, hdr in enumerate(["", "Device", "Attr", "Label", "Unit"]):
                lbl = QLabel(hdr)
                lbl.setStyleSheet("color:#6c7086;font-size:10px;font-weight:bold;")
                g.addWidget(lbl, 0, col)
            rows = []
            for i, axis_lbl in enumerate(["Act 1 (X):", "Act 2 (Y):", "Z (focus):"], 1):
                lbl2 = QLabel(axis_lbl); lbl2.setStyleSheet("font-weight:bold;")
                row  = ActuatorDefaultRow(self._registry)
                g.addWidget(lbl2, i, 0)
                g.addWidget(row,  i, 1, 1, 4)
                rows.append(row)
            return rows

        def _make_geo_grp(geo_label):
            """Build one geometry column (Faraday or Voigt) with ANM200+ANC300."""
            geo_grp = QGroupBox(geo_label)
            geo_v   = QVBoxLayout(geo_grp)
            geo_v.setSpacing(6); geo_v.setContentsMargins(6, 6, 6, 6)
            anm_grp = QGroupBox("ANM200 (fine)")
            anm_g   = QGridLayout(anm_grp); anm_g.setSpacing(3)
            anc_grp = QGroupBox("ANC300 (coarse)")
            anc_g   = QGridLayout(anc_grp); anc_g.setSpacing(3)
            anm_rows = _make_piezo_rows(anm_g)
            anc_rows = _make_piezo_rows(anc_g)
            geo_v.addWidget(anm_grp)
            geo_v.addWidget(anc_grp)
            return geo_grp, anm_rows, anc_rows

        far_grp, far_anm, far_anc = _make_geo_grp("Faraday")
        voi_grp, voi_anm, voi_anc = _make_geo_grp("Voigt")

        (self.far_anm_act1, self.far_anm_act2, self.far_anm_actz) = far_anm
        (self.far_anc_act1, self.far_anc_act2, self.far_anc_actz) = far_anc
        (self.voi_anm_act1, self.voi_anm_act2, self.voi_anm_actz) = voi_anm
        (self.voi_anc_act1, self.voi_anc_act2, self.voi_anc_actz) = voi_anc

        for row in (self.far_anm_act1, self.far_anm_act2, self.far_anm_actz,
                    self.far_anc_act1, self.far_anc_act2, self.far_anc_actz,
                    self.voi_anm_act1, self.voi_anm_act2, self.voi_anm_actz,
                    self.voi_anc_act1, self.voi_anc_act2, self.voi_anc_actz):
            row.changed.connect(self.defaults_changed)

        act_outer.addWidget(far_grp)
        act_outer.addWidget(voi_grp)
        root.addWidget(act_grp)

        # ── Hardware: Keithley + AttoDRY side by side ─────────────────────────
        hw_row = QHBoxLayout(); hw_row.setSpacing(10)

        zi_grp = QGroupBox("Lock-in")
        zi_l   = QVBoxLayout(zi_grp); zi_l.setContentsMargins(8, 8, 8, 8)
        self.zi_hw = HardwareDeviceGroup(LOCKIN_ATTR_DEFS, filter_type="lockin",
                                         registry=self._registry)
        self.zi_hw.changed.connect(self.defaults_changed)
        zi_l.addWidget(self.zi_hw)
        hw_row.addWidget(zi_grp)

        ks_grp = QGroupBox("Keithley 6221")
        ks_l   = QVBoxLayout(ks_grp); ks_l.setContentsMargins(8, 8, 8, 8)
        self.ks_hw = HardwareDeviceGroup(KEITHLEY_ATTR_DEFS, registry=self._registry)
        self.ks_hw.changed.connect(self.defaults_changed)
        ks_l.addWidget(self.ks_hw)
        hw_row.addWidget(ks_grp)

        ad_grp = QGroupBox("AttoDRY Cryostat")
        ad_l   = QVBoxLayout(ad_grp); ad_l.setContentsMargins(8, 8, 8, 8)
        self.ad_hw = HardwareDeviceGroup(ATTODRY_ATTR_DEFS, registry=self._registry)
        self.ad_hw.changed.connect(self.defaults_changed)
        ad_l.addWidget(self.ad_hw)
        hw_row.addWidget(ad_grp)

        hw_w = QWidget(); hw_w.setLayout(hw_row)
        root.addWidget(hw_w)

        # ── Calibration ───────────────────────────────────────────────────────
        cal_grp = QGroupBox("Calibration")
        cal_g   = QGridLayout(cal_grp)
        cal_g.setSpacing(6); cal_g.setContentsMargins(8, 8, 8, 8)

        cal_g.addWidget(QLabel("FL sensor device:"), 0, 0)
        self.fl_dev_combo = NoScrollComboBox()
        self.fl_dev_combo.setMinimumWidth(200)
        self.fl_dev_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        cal_g.addWidget(self.fl_dev_combo, 0, 1)

        cal_g.addWidget(QLabel("Attribute:"), 0, 2)
        self.fl_attr_combo = NoScrollComboBox(); self.fl_attr_combo.setMinimumWidth(80)
        cal_g.addWidget(self.fl_attr_combo, 0, 3)

        self.fl_dev_combo.currentIndexChanged.connect(self._on_fl_dev_changed)
        self.fl_attr_combo.currentIndexChanged.connect(lambda _: self.defaults_changed.emit())

        self._populate_fl_dev()
        root.addWidget(cal_grp)
        root.addStretch()

    # ── FL sensor helpers ─────────────────────────────────────────────────────
    def _populate_fl_dev(self, initial_path: str = ""):
        self.fl_dev_combo.blockSignals(True); self.fl_dev_combo.clear()
        for d in self._registry:
            self.fl_dev_combo.addItem(d["name"], d["tango_path"])
        if initial_path:
            for i in range(self.fl_dev_combo.count()):
                if self.fl_dev_combo.itemData(i) == initial_path:
                    self.fl_dev_combo.setCurrentIndex(i); break
        self.fl_dev_combo.blockSignals(False)
        self._populate_fl_attr()

    def _populate_fl_attr(self, initial_attr: str = ""):
        self.fl_attr_combo.blockSignals(True); self.fl_attr_combo.clear()
        dev_path = self.fl_dev_combo.currentData() or ""
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                for ch in d.get("channels", []):
                    self.fl_attr_combo.addItem(ch.get("attr", "?"), ch.get("attr", ""))
                break
        if not self.fl_attr_combo.count():
            self.fl_attr_combo.addItem("Value", "Value")
        if initial_attr:
            idx = self.fl_attr_combo.findData(initial_attr)
            if idx >= 0: self.fl_attr_combo.setCurrentIndex(idx)
        self.fl_attr_combo.blockSignals(False)

    def _on_fl_dev_changed(self, _=None):
        self._populate_fl_attr()
        self.defaults_changed.emit()

    # ── Public API ─────────────────────────────────────────────────────────────
    def set_registry(self, registry: List[dict]):
        self._registry = registry
        for row in (self.far_anm_act1, self.far_anm_act2, self.far_anm_actz,
                    self.far_anc_act1, self.far_anc_act2, self.far_anc_actz,
                    self.voi_anm_act1, self.voi_anm_act2, self.voi_anm_actz,
                    self.voi_anc_act1, self.voi_anc_act2, self.voi_anc_actz):
            row.set_registry(registry)
        self.zi_hw.set_registry(registry)
        self.ks_hw.set_registry(registry)
        self.ad_hw.set_registry(registry)
        cur_fl = self.fl_dev_combo.currentData() or ""
        cur_fa = self.fl_attr_combo.currentData() or ""
        self._populate_fl_dev(initial_path=cur_fl)
        self._populate_fl_attr(initial_attr=cur_fa)

    def load(self, setup: dict):
        """Restore all selections from a setup dict."""
        def _load_piezo(row1, row2, rowz, blk):
            row1.load(blk.get("act1_device", ""), blk.get("act1_attr", "x"))
            row2.load(blk.get("act2_device", ""), blk.get("act2_attr", "y"))
            rowz.load(blk.get("z_device",    ""), blk.get("z_attr",    "z"))

        far = setup.get("stage_faraday", {})
        voi = setup.get("stage_voigt",   {})
        _load_piezo(self.far_anm_act1, self.far_anm_act2, self.far_anm_actz,
                    far.get("anm200", {}))
        _load_piezo(self.far_anc_act1, self.far_anc_act2, self.far_anc_actz,
                    far.get("anc300", {}))
        _load_piezo(self.voi_anm_act1, self.voi_anm_act2, self.voi_anm_actz,
                    voi.get("anm200", {}))
        _load_piezo(self.voi_anc_act1, self.voi_anc_act2, self.voi_anc_actz,
                    voi.get("anc300", {}))

        zi_attrs = {tup[1]: setup.get(tup[1], tup[2]) for tup in LOCKIN_ATTR_DEFS}
        self.zi_hw.load(setup.get("zi_device", ""), zi_attrs)

        ks_attrs = {tup[1]: setup.get(tup[1], tup[2]) for tup in KEITHLEY_ATTR_DEFS}
        self.ks_hw.load(setup.get("keithley_device", ""), ks_attrs)

        ad_attrs = {tup[1]: setup.get(tup[1], tup[2]) for tup in ATTODRY_ATTR_DEFS}
        self.ad_hw.load(setup.get("attodry_device", ""), ad_attrs)

        self._populate_fl_dev(initial_path=setup.get("focus_averagein", ""))
        self._populate_fl_attr(initial_attr=setup.get("focus_averagein_attr", "Value"))

    def get_values(self) -> dict:
        """Return current selections as a dict suitable for merging into setup."""
        def _piezo_block(r1, r2, rz) -> dict:
            a1, a2, az = r1.get(), r2.get(), rz.get()
            return {
                "act1_device": a1["device"], "act1_attr": a1["attr"],
                "act1_label":  a1["label"],  "act1_unit": a1["unit"],
                "act2_device": a2["device"], "act2_attr": a2["attr"],
                "act2_label":  a2["label"],  "act2_unit": a2["unit"],
                "z_device":    az["device"], "z_attr":    az["attr"],
                "z_label":     az["label"],  "z_unit":    az["unit"],
            }
        zi_attrs = self.zi_hw.get_attrs()
        ks_attrs = self.ks_hw.get_attrs()
        ad_attrs = self.ad_hw.get_attrs()
        return {
            "stage_faraday": {
                "anm200": _piezo_block(self.far_anm_act1, self.far_anm_act2, self.far_anm_actz),
                "anc300": _piezo_block(self.far_anc_act1, self.far_anc_act2, self.far_anc_actz),
            },
            "stage_voigt": {
                "anm200": _piezo_block(self.voi_anm_act1, self.voi_anm_act2, self.voi_anm_actz),
                "anc300": _piezo_block(self.voi_anc_act1, self.voi_anc_act2, self.voi_anc_actz),
            },
            "zi_device":   self.zi_hw.dev_combo.currentData() or "",
            **zi_attrs,
            "keithley_device": self.ks_hw.dev_combo.currentData() or "",
            **ks_attrs,
            "attodry_device":  self.ad_hw.dev_combo.currentData() or "",
            **ad_attrs,
            "focus_averagein":      self.fl_dev_combo.currentData() or "",
            "focus_averagein_attr": self.fl_attr_combo.currentData() or "Value",
        }
