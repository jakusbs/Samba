"""
panels/setup_defaults.py — Samba v3
SetupDefaultsPanel — per-setup hardware device paths and attribute defaults.
Displayed as a tab next to Device Registry.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout,
    QLabel, QLineEdit, QGroupBox, QScrollArea
)
from PyQt6.QtCore import pyqtSignal

from panels._widgets import NoScrollComboBox


# ── Known attribute-name lists (fall-back when registry has no channels) ──────
_MAGNET_CUR_ATTRS = [
    "current_polar", "current_longitudinal", "current", "amplitude",
]
_MAGNET_FLD_ATTRS = [
    "field_polar_corr", "field_longitudinal_corr", "field", "field_polar",
]


class SetupDefaultsPanel(QWidget):
    """
    Editable per-setup hardware device paths and attribute defaults.

    Device combos are populated from the Device Registry; attribute combos
    are populated from the channels defined for the selected device.
    When a device+attr pair is selected the label and unit fields are
    auto-filled from the registry and shown read-only (they reflect the
    registry definition and cannot be edited manually).

    Emits `defaults_changed` whenever any value changes; the main window
    saves the new values into the active setup dict immediately.
    """
    defaults_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(2, 2, 2, 2)
        cl.setSpacing(6)

        # ── Stage Actuators ───────────────────────────────────────────────────
        stage_grp = QGroupBox("Stage Actuators")
        sg = QGridLayout(stage_grp)
        sg.setSpacing(4); sg.setContentsMargins(8, 10, 8, 8)
        sg.setColumnStretch(1, 1)

        def _hdr(text, row, col, **kw):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#6c7086;font-size:10px;font-weight:bold;")
            sg.addWidget(lbl, row, col, **kw)

        _hdr("Device (TANGO path)", 0, 1)
        _hdr("Attr",                0, 2)
        _hdr("Label",               0, 3)
        _hdr("Unit",                0, 4)

        sg.addWidget(QLabel("Act 1 (X):"), 1, 0)
        self.act1_dev  = _combo()
        self.act1_attr = _combo()
        self.act1_lbl  = _ro_field("X",  42)
        self.act1_unit = _ro_field("nm", 42)
        sg.addWidget(self.act1_dev,  1, 1)
        sg.addWidget(self.act1_attr, 1, 2)
        sg.addWidget(self.act1_lbl,  1, 3)
        sg.addWidget(self.act1_unit, 1, 4)

        sg.addWidget(QLabel("Act 2 (Y):"), 2, 0)
        self.act2_dev  = _combo()
        self.act2_attr = _combo()
        self.act2_lbl  = _ro_field("Y",  42)
        self.act2_unit = _ro_field("nm", 42)
        sg.addWidget(self.act2_dev,  2, 1)
        sg.addWidget(self.act2_attr, 2, 2)
        sg.addWidget(self.act2_lbl,  2, 3)
        sg.addWidget(self.act2_unit, 2, 4)

        # Z axis — device same as Act1, only attr is configured
        sg.addWidget(QLabel("Z (focus):"), 3, 0)
        self.z_attr = _combo()
        self.z_attr.setEditable(True)
        sg.addWidget(self.z_attr, 3, 2)
        sg.addWidget(QLabel("≡ Act 1 device"), 3, 1)

        cl.addWidget(stage_grp)

        # ── Current Source & Magnetics ────────────────────────────────────────
        hw_grp = QGroupBox("Current Source & Magnetics")
        hg = QGridLayout(hw_grp)
        hg.setSpacing(4); hg.setContentsMargins(8, 10, 8, 8)
        hg.setColumnStretch(1, 1); hg.setColumnStretch(3, 1)

        hg.addWidget(QLabel("Keithley:"),     0, 0)
        self.keithley_dev  = _combo()
        hg.addWidget(self.keithley_dev,        0, 1)
        hg.addWidget(_label("Output attr:"), 0, 2)
        self.keithley_attr = _combo()
        hg.addWidget(self.keithley_attr,       0, 3)

        hg.addWidget(QLabel("Magnet:"),        1, 0)
        self.magnet_dev    = _combo()
        hg.addWidget(self.magnet_dev,          1, 1, 1, 3)

        hg.addWidget(QLabel("Current attr:"),  2, 0)
        self.magnet_cur_attr = _attr_combo(_MAGNET_CUR_ATTRS)
        hg.addWidget(self.magnet_cur_attr,     2, 1)
        hg.addWidget(_label("Field attr:"),  2, 2)
        self.magnet_fld_attr = _attr_combo(_MAGNET_FLD_ATTRS)
        hg.addWidget(self.magnet_fld_attr,     2, 3)

        hg.addWidget(QLabel("Relay:"),         3, 0)
        self.relay_dev  = _combo()
        hg.addWidget(self.relay_dev,           3, 1)
        hg.addWidget(_label("Attr:"),        3, 2)
        self.relay_attr = _combo()
        hg.addWidget(self.relay_attr,          3, 3)

        cl.addWidget(hw_grp)

        # ── Calibration ───────────────────────────────────────────────────────
        cal_grp = QGroupBox("Calibration")
        cg = QGridLayout(cal_grp)
        cg.setSpacing(4); cg.setContentsMargins(8, 10, 8, 8)
        cg.setColumnStretch(1, 1); cg.setColumnStretch(3, 1)

        cg.addWidget(QLabel("Focus sensor:"), 0, 0)
        self.focus_dev  = _combo()
        cg.addWidget(self.focus_dev,           0, 1)
        cg.addWidget(_label("Attr:"),        0, 2)
        self.focus_attr = _combo()
        cg.addWidget(self.focus_attr,          0, 3)

        cl.addWidget(cal_grp)

        # ── TR-MOKE ───────────────────────────────────────────────────────────
        tr_grp = QGroupBox("TR-MOKE")
        tg = QGridLayout(tr_grp)
        tg.setSpacing(4); tg.setContentsMargins(8, 10, 8, 8)
        tg.setColumnStretch(1, 1)

        tg.addWidget(QLabel("DG645 device:"), 0, 0)
        self.trmoke_dg645 = _combo()
        tg.addWidget(self.trmoke_dg645,        0, 1)

        cl.addWidget(tr_grp)
        cl.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

        # ── Internal state ─────────────────────────────────────────────────────
        self._registry: list = []
        self._loading = False

        # Device-change → repopulate attr combos
        self.act1_dev.currentTextChanged.connect(
            lambda: self._on_dev_changed(self.act1_dev, self.act1_attr,
                                          self.act1_lbl, self.act1_unit,
                                          also_z=True))
        self.act2_dev.currentTextChanged.connect(
            lambda: self._on_dev_changed(self.act2_dev, self.act2_attr,
                                          self.act2_lbl, self.act2_unit))
        self.keithley_dev.currentTextChanged.connect(
            lambda: self._repop_attr_combo(self.keithley_dev, self.keithley_attr))
        self.relay_dev.currentTextChanged.connect(
            lambda: self._repop_attr_combo(self.relay_dev, self.relay_attr))
        self.focus_dev.currentTextChanged.connect(
            lambda: self._repop_attr_combo(self.focus_dev, self.focus_attr))

        # Attr-change → auto-fill label/unit for stage axes
        self.act1_attr.currentTextChanged.connect(
            lambda: self._autofill_label_unit(self.act1_dev, self.act1_attr,
                                               self.act1_lbl, self.act1_unit))
        self.act2_attr.currentTextChanged.connect(
            lambda: self._autofill_label_unit(self.act2_dev, self.act2_attr,
                                               self.act2_lbl, self.act2_unit))

        # Wire all combos to defaults_changed
        _all_combos = [
            self.act1_dev, self.act1_attr,
            self.act2_dev, self.act2_attr,
            self.z_attr,
            self.keithley_dev, self.keithley_attr,
            self.magnet_dev, self.magnet_cur_attr, self.magnet_fld_attr,
            self.relay_dev, self.relay_attr,
            self.focus_dev, self.focus_attr,
            self.trmoke_dg645,
        ]
        for w in _all_combos:
            w.currentTextChanged.connect(self._on_changed)

    # ── Registry ──────────────────────────────────────────────────────────────
    def set_registry(self, registry: list):
        self._registry = registry
        self._repopulate_all_device_combos()

    def _repopulate_all_device_combos(self):
        reg = self._registry

        def _paths(types):
            matched = [d["tango_path"] for d in reg if d.get("type") in types]
            return matched or [d["tango_path"] for d in reg]

        stage_paths    = _paths({"stage"})
        magnet_paths   = _paths({"magnet"})
        relay_paths    = _paths({"relay"})
        keithley_paths = _paths({"current", "keithley"})
        focus_paths    = _paths({"sensor", "beckhoff", "averageIn"})
        dg645_paths    = _paths({"dg645"})

        for combo, paths in [
            (self.act1_dev,     stage_paths),
            (self.act2_dev,     stage_paths),
            (self.magnet_dev,   magnet_paths),
            (self.relay_dev,    relay_paths),
            (self.keithley_dev, keithley_paths),
            (self.focus_dev,    focus_paths),
            (self.trmoke_dg645, dg645_paths),
        ]:
            _repop_combo(combo, paths)

        # Repopulate attr combos for all devices
        self._repop_attr_combo(self.act1_dev,     self.act1_attr)
        self._repop_attr_combo(self.act2_dev,     self.act2_attr)
        self._repop_z_attr()
        self._repop_attr_combo(self.keithley_dev, self.keithley_attr)
        self._repop_attr_combo(self.relay_dev,    self.relay_attr)
        self._repop_attr_combo(self.focus_dev,    self.focus_attr)

    def _repop_attr_combo(self, dev_combo: NoScrollComboBox,
                           attr_combo: NoScrollComboBox):
        """Populate attr_combo from the registry channels of the selected device."""
        if self._loading:
            return
        dev_path = dev_combo.currentText()
        cur_attr = attr_combo.currentText()
        attrs = []
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                attrs = [ch.get("attr", "") for ch in d.get("channels", []) if ch.get("attr")]
                break
        _repop_combo(attr_combo, attrs, keep=cur_attr)

    def _repop_z_attr(self):
        """Populate the Z attr combo from Act1's stage device channels."""
        if self._loading:
            return
        dev_path = self.act1_dev.currentText()
        cur_attr = self.z_attr.currentText()
        attrs = []
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                attrs = [ch.get("attr", "") for ch in d.get("channels", []) if ch.get("attr")]
                break
        _repop_combo(self.z_attr, attrs, keep=cur_attr)

    def _on_dev_changed(self, dev_combo, attr_combo, lbl_w, unit_w,
                         also_z: bool = False):
        """Handle device change: repopulate attrs and refresh label/unit."""
        self._repop_attr_combo(dev_combo, attr_combo)
        self._autofill_label_unit(dev_combo, attr_combo, lbl_w, unit_w)
        if also_z:
            self._repop_z_attr()

    def _autofill_label_unit(self, dev_combo, attr_combo, lbl_w, unit_w):
        """Set label/unit from registry when device+attr changes."""
        if self._loading:
            return
        dev_path = dev_combo.currentText()
        attr     = attr_combo.currentText()
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                for ch in d.get("channels", []):
                    if ch.get("attr") == attr:
                        lbl_w.setText(ch.get("label", attr))
                        unit_w.setText(ch.get("unit",  ""))
                        return
                break
        # Not found in registry — clear to avoid stale values

    # ── Load / Get ─────────────────────────────────────────────────────────────
    def load(self, setup_data: dict):
        """Populate all widgets from a setup data dict."""
        self._loading = True
        try:
            _set(self.act1_dev,        setup_data.get("act1_device",         ""))
            _set(self.act1_attr,       setup_data.get("act1_attr",           "x"))
            self.act1_lbl.setText(     setup_data.get("act1_label",          "X"))
            self.act1_unit.setText(    setup_data.get("act1_unit",           "nm"))

            _set(self.act2_dev,        setup_data.get("act2_device",         ""))
            _set(self.act2_attr,       setup_data.get("act2_attr",           "y"))
            self.act2_lbl.setText(     setup_data.get("act2_label",          "Y"))
            self.act2_unit.setText(    setup_data.get("act2_unit",           "nm"))

            _set(self.z_attr,          setup_data.get("z_attr",              "z"))

            _set(self.keithley_dev,    setup_data.get("keithley_device",     ""))
            _set(self.keithley_attr,   setup_data.get("keithley_output_attr","amplitude"))

            _set(self.magnet_dev,      setup_data.get("magnet_device",       ""))
            _set(self.magnet_cur_attr, setup_data.get("magnet_current_attr", "current_polar"))
            _set(self.magnet_fld_attr, setup_data.get("magnet_field_attr",   "field_polar_corr"))

            _set(self.relay_dev,       setup_data.get("relay_device",        ""))
            _set(self.relay_attr,      setup_data.get("relay_attr",          "switchvar"))

            _set(self.focus_dev,       setup_data.get("focus_averagein",     ""))
            _set(self.focus_attr,      setup_data.get("focus_attr",          "Value"))

            _set(self.trmoke_dg645,    setup_data.get("trmoke_dg645",        "intermag/dg645/1"))
        finally:
            self._loading = False

    def get_defaults(self) -> dict:
        """Return current widget values as a flat dict of setup keys."""
        return {
            "act1_device":         self.act1_dev.currentText(),
            "act1_attr":           self.act1_attr.currentText(),
            "act1_label":          self.act1_lbl.text(),
            "act1_unit":           self.act1_unit.text(),
            "act2_device":         self.act2_dev.currentText(),
            "act2_attr":           self.act2_attr.currentText(),
            "act2_label":          self.act2_lbl.text(),
            "act2_unit":           self.act2_unit.text(),
            "z_attr":              self.z_attr.currentText(),
            "keithley_device":     self.keithley_dev.currentText(),
            "keithley_output_attr":self.keithley_attr.currentText(),
            "magnet_device":       self.magnet_dev.currentText(),
            "magnet_current_attr": self.magnet_cur_attr.currentText(),
            "magnet_field_attr":   self.magnet_fld_attr.currentText(),
            "relay_device":        self.relay_dev.currentText(),
            "relay_attr":          self.relay_attr.currentText(),
            "focus_averagein":     self.focus_dev.currentText(),
            "focus_attr":          self.focus_attr.currentText(),
            "trmoke_dg645":        self.trmoke_dg645.currentText(),
        }

    def _on_changed(self):
        if not self._loading:
            self.defaults_changed.emit()


# ── Widget factory helpers ─────────────────────────────────────────────────────

def _combo() -> NoScrollComboBox:
    c = NoScrollComboBox()
    c.setEditable(True)
    c.setMinimumWidth(120)
    return c


def _attr_combo(items: list) -> NoScrollComboBox:
    c = NoScrollComboBox()
    c.setEditable(True)
    c.addItems(items)
    return c


def _ro_field(text: str, width: int) -> QLineEdit:
    """Read-only display field — value auto-filled from registry."""
    e = QLineEdit(text)
    e.setFixedWidth(width)
    e.setReadOnly(True)
    e.setStyleSheet(
        "background:#1e1e2e;color:#6c7086;border:1px solid #313244;"
        "border-radius:4px;padding:2px 4px;")
    return e


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#6c7086;font-size:10px;")
    return lbl


def _set(combo: NoScrollComboBox, value: str):
    """Set combo current text, adding as new item if not already present."""
    if not value:
        return
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setEditText(value)


def _repop_combo(combo: NoScrollComboBox, items: list, keep: str = ""):
    """Replace combo items while preserving the current text."""
    cur = keep or combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    for item in items:
        if item:
            combo.addItem(item)
    if cur:
        idx = combo.findText(cur)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(cur)
    combo.blockSignals(False)
