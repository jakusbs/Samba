"""
panels/setup_defaults.py — Samba v3
SetupDefaultsPanel — per-setup hardware device paths and attribute defaults.
Displayed as a tab next to Device Registry.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QGroupBox, QScrollArea,
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
_LOCKIN_TC_ATTRS     = ["timeconstant"]
_LOCKIN_ORDER_ATTRS  = ["filterorder"]
_LOCKIN_SETTLE_ATTRS = ["settlingtime"]


class SetupDefaultsPanel(QWidget):
    """
    Editable per-setup hardware device paths and attribute defaults.

    Device combos display friendly names from the Device Registry; the TANGO
    path is stored as item data so lookups are reliable.  Attribute combos are
    populated from the channels defined for the selected device.  When a
    device+attr pair is selected, label and unit fields are auto-filled from
    the registry and shown read-only (they cannot be edited manually).

    Emits `defaults_changed` whenever any value changes; the main window saves
    the new values into the active setup dict immediately.
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
        sg.setColumnStretch(1, 2); sg.setColumnStretch(2, 1)

        def _hdr(text, row, col, **kw):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#6c7086;font-size:10px;font-weight:bold;")
            sg.addWidget(lbl, row, col, **kw)

        _hdr("Device",  0, 1)
        _hdr("Attr",    0, 2)
        _hdr("Label",   0, 3)
        _hdr("Unit",    0, 4)

        sg.addWidget(QLabel("Act 1 (X):"), 1, 0)
        self.act1_dev  = _combo()
        self.act1_attr = _combo()
        self.act1_lbl  = _ro_field("X",  48)
        self.act1_unit = _ro_field("nm", 42)
        sg.addWidget(self.act1_dev,  1, 1)
        sg.addWidget(self.act1_attr, 1, 2)
        sg.addWidget(self.act1_lbl,  1, 3)
        sg.addWidget(self.act1_unit, 1, 4)

        sg.addWidget(QLabel("Act 2 (Y):"), 2, 0)
        self.act2_dev  = _combo()
        self.act2_attr = _combo()
        self.act2_lbl  = _ro_field("Y",  48)
        self.act2_unit = _ro_field("nm", 42)
        sg.addWidget(self.act2_dev,  2, 1)
        sg.addWidget(self.act2_attr, 2, 2)
        sg.addWidget(self.act2_lbl,  2, 3)
        sg.addWidget(self.act2_unit, 2, 4)

        sg.addWidget(QLabel("Z (focus):"), 3, 0)
        self.z_dev  = _combo()
        self.z_attr = _combo()
        self.z_lbl  = _ro_field("Z",  48)
        self.z_unit = _ro_field("nm", 42)
        sg.addWidget(self.z_dev,  3, 1)
        sg.addWidget(self.z_attr, 3, 2)
        sg.addWidget(self.z_lbl,  3, 3)
        sg.addWidget(self.z_unit, 3, 4)

        cl.addWidget(stage_grp)

        # ── Current Source & Magnetics — 3 side-by-side columns ───────────────
        hw_row = QHBoxLayout()
        hw_row.setSpacing(6)

        # --- Keithley ---
        keith_grp = QGroupBox("Keithley (Current Source)")
        kg = QGridLayout(keith_grp)
        kg.setSpacing(4); kg.setContentsMargins(8, 10, 8, 8)
        kg.setColumnStretch(1, 1)

        kg.addWidget(QLabel("Device:"),          0, 0)
        self.keithley_dev = _combo()
        kg.addWidget(self.keithley_dev,           0, 1)

        kg.addWidget(_label("Amplitude attr:"),  1, 0)
        self.keithley_amplitude_attr = _combo()
        kg.addWidget(self.keithley_amplitude_attr, 1, 1)

        kg.addWidget(_label("Frequency attr:"),  2, 0)
        self.keithley_frequency_attr = _combo()
        kg.addWidget(self.keithley_frequency_attr, 2, 1)

        kg.addWidget(_label("Range attr:"),      3, 0)
        self.keithley_range_attr = _combo()
        kg.addWidget(self.keithley_range_attr,    3, 1)

        kg.addWidget(_label("Compliance attr:"), 4, 0)
        self.keithley_compliance_attr = _combo()
        kg.addWidget(self.keithley_compliance_attr, 4, 1)

        hw_row.addWidget(keith_grp)

        # --- Magnet ---
        mag_grp = QGroupBox("Magnet")
        mg = QGridLayout(mag_grp)
        mg.setSpacing(4); mg.setContentsMargins(8, 10, 8, 8)
        mg.setColumnStretch(1, 1)

        mg.addWidget(QLabel("Device:"),          0, 0)
        self.magnet_dev = _combo()
        mg.addWidget(self.magnet_dev,             0, 1)

        mg.addWidget(_label("Current attr:"),    1, 0)
        self.magnet_cur_attr = _attr_combo(_MAGNET_CUR_ATTRS)
        mg.addWidget(self.magnet_cur_attr,        1, 1)

        mg.addWidget(_label("Field attr:"),      2, 0)
        self.magnet_fld_attr = _attr_combo(_MAGNET_FLD_ATTRS)
        mg.addWidget(self.magnet_fld_attr,        2, 1)

        hw_row.addWidget(mag_grp)

        # --- Relay ---
        relay_grp = QGroupBox("Relay")
        rg = QGridLayout(relay_grp)
        rg.setSpacing(4); rg.setContentsMargins(8, 10, 8, 8)
        rg.setColumnStretch(1, 1)

        rg.addWidget(QLabel("Device:"),    0, 0)
        self.relay_dev = _combo()
        rg.addWidget(self.relay_dev,       0, 1)

        rg.addWidget(_label("Attr:"),     1, 0)
        self.relay_attr = _combo()
        rg.addWidget(self.relay_attr,      1, 1)

        hw_row.addWidget(relay_grp)

        # --- Lock-in ---
        zi_grp = QGroupBox("Lock-in")
        zig = QGridLayout(zi_grp)
        zig.setSpacing(4); zig.setContentsMargins(8, 10, 8, 8)
        zig.setColumnStretch(1, 1)

        zig.addWidget(QLabel("Device:"),         0, 0)
        self.zi_dev = _combo()
        zig.addWidget(self.zi_dev,               0, 1)

        zig.addWidget(_label("TC attr:"),        1, 0)
        self.zi_tc_attr = _attr_combo(_LOCKIN_TC_ATTRS)
        zig.addWidget(self.zi_tc_attr,           1, 1)

        zig.addWidget(_label("Order attr:"),     2, 0)
        self.zi_order_attr = _attr_combo(_LOCKIN_ORDER_ATTRS)
        zig.addWidget(self.zi_order_attr,        2, 1)

        zig.addWidget(_label("Settling attr:"),  3, 0)
        self.zi_settling_attr = _attr_combo(_LOCKIN_SETTLE_ATTRS)
        zig.addWidget(self.zi_settling_attr,     3, 1)

        hw_row.addWidget(zi_grp)

        hw_wrap = QWidget()
        hw_wrap.setLayout(hw_row)
        cl.addWidget(hw_wrap)

        # ── Calibration ───────────────────────────────────────────────────────
        cal_grp = QGroupBox("Calibration")
        cg = QGridLayout(cal_grp)
        cg.setSpacing(4); cg.setContentsMargins(8, 10, 8, 8)
        cg.setColumnStretch(1, 1); cg.setColumnStretch(3, 1)

        cg.addWidget(QLabel("Focus sensor:"), 0, 0)
        self.focus_dev  = _combo()
        cg.addWidget(self.focus_dev,           0, 1)
        cg.addWidget(_label("Attr:"),         0, 2)
        self.focus_attr = _combo()
        cg.addWidget(self.focus_attr,          0, 3)

        # Lights (LED) device — plain path field (device may not be in the
        # registry); LED1 = green setup, LED2 = IR. Used by the Calibration tab's
        # LED on/off buttons. Leave blank to hide those buttons.
        cg.addWidget(QLabel("Lights (LED):"), 1, 0)
        self.lights_dev = QLineEdit()
        self.lights_dev.setPlaceholderText("e.g. hpp-N42/camera/lights — blank to disable")
        cg.addWidget(self.lights_dev, 1, 1, 1, 3)

        cl.addWidget(cal_grp)

        # ── TR-MOKE ───────────────────────────────────────────────────────────
        tr_grp = QGroupBox("TR-MOKE")
        tg = QGridLayout(tr_grp)
        tg.setSpacing(4); tg.setContentsMargins(8, 10, 8, 8)
        tg.setColumnStretch(1, 1)

        tg.addWidget(QLabel("DG645 device:"), 0, 0)
        self.trmoke_dg645 = _combo()
        tg.addWidget(self.trmoke_dg645,        0, 1)

        tg.addWidget(QLabel("RTV40 device:"), 1, 0)
        self.rtv40_dev = _combo()
        tg.addWidget(self.rtv40_dev,           1, 1)

        cl.addWidget(tr_grp)
        cl.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

        # ── Internal state ────────────────────────────────────────────────────
        self._registry: list = []
        self._loading = False

        # Device-change → repopulate attr combos + autofill label/unit
        self.act1_dev.currentIndexChanged.connect(
            lambda: self._on_dev_changed(self.act1_dev, self.act1_attr,
                                         self.act1_lbl, self.act1_unit))
        self.act2_dev.currentIndexChanged.connect(
            lambda: self._on_dev_changed(self.act2_dev, self.act2_attr,
                                         self.act2_lbl, self.act2_unit))
        self.z_dev.currentIndexChanged.connect(
            lambda: self._on_dev_changed(self.z_dev, self.z_attr,
                                         self.z_lbl, self.z_unit))
        self.keithley_dev.currentIndexChanged.connect(self._on_keithley_dev_changed)
        self.relay_dev.currentIndexChanged.connect(
            lambda: self._repop_attr_combo(self.relay_dev, self.relay_attr))
        self.focus_dev.currentIndexChanged.connect(
            lambda: self._repop_attr_combo(self.focus_dev, self.focus_attr))
        self.zi_dev.currentIndexChanged.connect(self._on_changed)

        # Attr-change → auto-fill label/unit for stage axes
        self.act1_attr.currentTextChanged.connect(
            lambda: self._autofill_label_unit(self.act1_dev, self.act1_attr,
                                              self.act1_lbl, self.act1_unit))
        self.act2_attr.currentTextChanged.connect(
            lambda: self._autofill_label_unit(self.act2_dev, self.act2_attr,
                                              self.act2_lbl, self.act2_unit))
        self.z_attr.currentTextChanged.connect(
            lambda: self._autofill_label_unit(self.z_dev, self.z_attr,
                                              self.z_lbl, self.z_unit))

        # Wire all combos to defaults_changed
        _all_combos = [
            self.act1_dev, self.act1_attr,
            self.act2_dev, self.act2_attr,
            self.z_dev, self.z_attr,
            self.keithley_dev,
            self.keithley_amplitude_attr, self.keithley_frequency_attr,
            self.keithley_range_attr,     self.keithley_compliance_attr,
            self.magnet_dev, self.magnet_cur_attr, self.magnet_fld_attr,
            self.relay_dev, self.relay_attr,
            self.focus_dev, self.focus_attr,
            self.trmoke_dg645,
            self.rtv40_dev,
            self.zi_tc_attr, self.zi_order_attr, self.zi_settling_attr,
        ]
        for w in _all_combos:
            w.currentTextChanged.connect(self._on_changed)
        self.lights_dev.editingFinished.connect(self._on_changed)

    # ── Registry ──────────────────────────────────────────────────────────────
    def set_registry(self, registry: list):
        self._registry = registry
        self._repopulate_all_device_combos()

    def _repopulate_all_device_combos(self):
        reg = self._registry

        def _entries(types):
            matched = [d for d in reg if d.get("type") in types]
            return matched or list(reg)

        stage_entries    = _entries({"stage"})
        magnet_entries   = _entries({"magnet"})
        relay_entries    = _entries({"relay"})
        keithley_entries = _entries({"current", "keithley"})
        focus_entries    = _entries({"sensor", "beckhoff", "averageIn"})
        dg645_entries    = _entries({"dg645"})
        pulser_entries   = _entries({"pulser"})
        lockin_entries   = _entries({"lockin"})

        for combo, entries in [
            (self.act1_dev,     stage_entries),
            (self.act2_dev,     stage_entries),
            (self.z_dev,        stage_entries),
            (self.magnet_dev,   magnet_entries),
            (self.relay_dev,    relay_entries),
            (self.keithley_dev, keithley_entries),
            (self.focus_dev,    focus_entries),
            (self.trmoke_dg645, dg645_entries),
            (self.rtv40_dev,    pulser_entries),
            (self.zi_dev,       lockin_entries),
        ]:
            _fill_dev_combo(combo, entries)

        # Repopulate attr combos after device combos are set
        self._repop_attr_combo(self.act1_dev,  self.act1_attr)
        self._repop_attr_combo(self.act2_dev,  self.act2_attr)
        self._repop_attr_combo(self.z_dev,     self.z_attr)
        self._on_keithley_dev_changed()
        self._repop_attr_combo(self.relay_dev, self.relay_attr)
        self._repop_attr_combo(self.focus_dev, self.focus_attr)

    def _repop_attr_combo(self, dev_combo: NoScrollComboBox,
                          attr_combo: NoScrollComboBox,
                          fallback_attrs: list = None):
        """Populate attr_combo from registry channels of the selected device.

        Uses currentData() to get the TANGO path regardless of the display name
        shown in the combo, which prevents the focus-sensor bug where ZI lock-in
        channels appeared because currentText() returned the name, not the path.
        """
        if self._loading:
            return
        dev_path = _get_path(dev_combo)   # path via currentData(), not display name
        cur_attr = attr_combo.currentText()
        attrs = []
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                attrs = [ch.get("attr", "") for ch in d.get("channels", [])
                         if ch.get("attr")]
                break
        if not attrs and fallback_attrs:
            attrs = list(fallback_attrs)
        _repop_simple_combo(attr_combo, attrs, keep=cur_attr)

    def _on_keithley_dev_changed(self):
        """Repopulate all four Keithley attr combos from the selected device's channels."""
        if self._loading:
            return
        dev_path = _get_path(self.keithley_dev)
        channels = []
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                channels = [ch.get("attr", "") for ch in d.get("channels", [])
                            if ch.get("attr")]
                break
        for combo, default_key in [
            (self.keithley_amplitude_attr,  "amplitude"),
            (self.keithley_frequency_attr,  "frequency"),
            (self.keithley_range_attr,      "range"),
            (self.keithley_compliance_attr, "compliance"),
        ]:
            cur = combo.currentText() or default_key
            _repop_simple_combo(combo, channels, keep=cur)

    def _on_dev_changed(self, dev_combo, attr_combo, lbl_w, unit_w):
        """Handle device change: repopulate attrs and refresh label/unit."""
        self._repop_attr_combo(dev_combo, attr_combo)
        self._autofill_label_unit(dev_combo, attr_combo, lbl_w, unit_w)

    def _autofill_label_unit(self, dev_combo, attr_combo, lbl_w, unit_w):
        """Set label/unit from registry when device+attr changes."""
        if self._loading:
            return
        dev_path = _get_path(dev_combo)
        attr     = attr_combo.currentText()
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                for ch in d.get("channels", []):
                    if ch.get("attr") == attr:
                        lbl_w.setText(ch.get("label", attr))
                        unit_w.setText(ch.get("unit",  ""))
                        return
                break

    # ── Load / Get ────────────────────────────────────────────────────────────
    def load(self, setup_data: dict):
        """Populate all widgets from a setup data dict."""
        self._loading = True
        try:
            _set_by_path(self.act1_dev,  setup_data.get("act1_device",           ""))
            _set(self.act1_attr,          setup_data.get("act1_attr",             "x"))
            self.act1_lbl.setText(        setup_data.get("act1_label",            "X"))
            self.act1_unit.setText(       setup_data.get("act1_unit",             "nm"))

            _set_by_path(self.act2_dev,  setup_data.get("act2_device",           ""))
            _set(self.act2_attr,          setup_data.get("act2_attr",             "y"))
            self.act2_lbl.setText(        setup_data.get("act2_label",            "Y"))
            self.act2_unit.setText(       setup_data.get("act2_unit",             "nm"))

            _set_by_path(self.z_dev,     setup_data.get("z_device",              ""))
            _set(self.z_attr,             setup_data.get("z_attr",               "z"))
            self.z_lbl.setText(           setup_data.get("z_label",              "Z"))
            self.z_unit.setText(          setup_data.get("z_unit",               "nm"))

            _set_by_path(self.keithley_dev, setup_data.get("keithley_device",   ""))
            _set(self.keithley_amplitude_attr,
                 setup_data.get("keithley_amplitude_attr",  "amplitude"))
            _set(self.keithley_frequency_attr,
                 setup_data.get("keithley_frequency_attr",  "frequency"))
            _set(self.keithley_range_attr,
                 setup_data.get("keithley_range_attr",      "range"))
            _set(self.keithley_compliance_attr,
                 setup_data.get("keithley_compliance_attr", "compliance"))

            _set_by_path(self.magnet_dev,  setup_data.get("magnet_device",      ""))
            _set(self.magnet_cur_attr,
                 setup_data.get("magnet_current_attr", "current_polar"))
            _set(self.magnet_fld_attr,
                 setup_data.get("magnet_field_attr",   "field_polar_corr"))

            _set_by_path(self.relay_dev,   setup_data.get("relay_device",       ""))
            _set(self.relay_attr,           setup_data.get("relay_attr",         "switchvar"))

            _set_by_path(self.focus_dev,   setup_data.get("focus_averagein",    ""))
            _set(self.focus_attr,           setup_data.get("focus_attr",         "Value"))
            self.lights_dev.setText(        setup_data.get("lights_device",      ""))

            _set_by_path(self.trmoke_dg645, setup_data.get("trmoke_dg645",     "hpp-N42/delay/DG645"))
            _set_by_path(self.rtv40_dev,    setup_data.get("rtv40_device",     "hpp-N42/pulser/RTV40"))

            _set_by_path(self.zi_dev,       setup_data.get("zi_device",        ""))
            _set(self.zi_tc_attr,           setup_data.get("zi_tc_attr",       "timeconstant"))
            _set(self.zi_order_attr,        setup_data.get("zi_order_attr",    "filterorder"))
            _set(self.zi_settling_attr,     setup_data.get("zi_settling_attr", "settlingtime"))
        finally:
            self._loading = False

        # Repopulate attr combos now that _loading is False
        self._repop_attr_combo(self.act1_dev,  self.act1_attr)
        self._repop_attr_combo(self.act2_dev,  self.act2_attr)
        self._repop_attr_combo(self.z_dev,     self.z_attr)
        self._on_keithley_dev_changed()
        self._repop_attr_combo(self.relay_dev, self.relay_attr)
        self._repop_attr_combo(self.focus_dev, self.focus_attr)

    def get_defaults(self) -> dict:
        """Return current widget values as a flat dict of setup keys."""
        return {
            "act1_device":              _get_path(self.act1_dev),
            "act1_attr":                self.act1_attr.currentText(),
            "act1_label":               self.act1_lbl.text(),
            "act1_unit":                self.act1_unit.text(),
            "act2_device":              _get_path(self.act2_dev),
            "act2_attr":                self.act2_attr.currentText(),
            "act2_label":               self.act2_lbl.text(),
            "act2_unit":                self.act2_unit.text(),
            "z_device":                 _get_path(self.z_dev),
            "z_attr":                   self.z_attr.currentText(),
            "z_label":                  self.z_lbl.text(),
            "z_unit":                   self.z_unit.text(),
            "keithley_device":          _get_path(self.keithley_dev),
            "keithley_amplitude_attr":  self.keithley_amplitude_attr.currentText(),
            "keithley_frequency_attr":  self.keithley_frequency_attr.currentText(),
            "keithley_range_attr":      self.keithley_range_attr.currentText(),
            "keithley_compliance_attr": self.keithley_compliance_attr.currentText(),
            # Keep legacy key for any code that still reads keithley_output_attr
            "keithley_output_attr":     self.keithley_amplitude_attr.currentText(),
            "magnet_device":            _get_path(self.magnet_dev),
            "magnet_current_attr":      self.magnet_cur_attr.currentText(),
            "magnet_field_attr":        self.magnet_fld_attr.currentText(),
            "relay_device":             _get_path(self.relay_dev),
            "relay_attr":               self.relay_attr.currentText(),
            "focus_averagein":          _get_path(self.focus_dev),
            "focus_attr":               self.focus_attr.currentText(),
            "lights_device":            self.lights_dev.text().strip(),
            "trmoke_dg645":             _get_path(self.trmoke_dg645),
            "rtv40_device":             _get_path(self.rtv40_dev),
            "zi_device":                _get_path(self.zi_dev),
            "zi_tc_attr":               self.zi_tc_attr.currentText(),
            "zi_order_attr":            self.zi_order_attr.currentText(),
            "zi_settling_attr":         self.zi_settling_attr.currentText(),
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
    """Combo pre-populated with a fixed list of attribute name strings."""
    c = NoScrollComboBox()
    c.setEditable(True)
    c.addItems(items)
    return c


def _ro_field(text: str, width: int) -> QLineEdit:
    """Read-only display field — value auto-filled from registry."""
    e = QLineEdit(text)
    e.setMinimumWidth(width)
    e.setReadOnly(True)
    e.setStyleSheet(
        "background:#1e1e2e;color:#6c7086;border:1px solid #313244;"
        "border-radius:4px;padding:2px 4px;")
    return e


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#6c7086;font-size:10px;")
    return lbl


# ── Path / name helpers for device combos ─────────────────────────────────────

def _get_path(combo: NoScrollComboBox) -> str:
    """Return the TANGO path for the current combo selection.

    When the user picks from the list the path is stored as item data;
    when they type a raw path directly currentData() is None so we fall
    back to currentText().
    """
    data = combo.currentData()
    return data if data else combo.currentText()


def _set_by_path(combo: NoScrollComboBox, path: str):
    """Select the combo item whose data matches *path*, or set as free text."""
    if not path:
        return
    for i in range(combo.count()):
        if combo.itemData(i) == path:
            combo.setCurrentIndex(i)
            return
    # Path not in list — let user see it as typed text
    combo.setEditText(path)


def _set(combo: NoScrollComboBox, value: str):
    """Set combo current text (simple-text combos without item data)."""
    if not value:
        return
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setEditText(value)


def _fill_dev_combo(combo: NoScrollComboBox, entries: list):
    """Populate a device combo with (friendly-name, tango-path) items.

    The current path is saved first and restored after repopulation so
    changing the registry does not reset user selections.
    """
    cur_path = _get_path(combo)
    combo.blockSignals(True)
    combo.clear()
    for d in entries:
        name = d.get("name", d.get("tango_path", ""))
        path = d.get("tango_path", "")
        if path:
            combo.addItem(name, path)
    # Restore previous selection by path
    if cur_path:
        for i in range(combo.count()):
            if combo.itemData(i) == cur_path:
                combo.setCurrentIndex(i)
                break
        else:
            combo.setEditText(cur_path)
    combo.blockSignals(False)


def _repop_simple_combo(combo: NoScrollComboBox, items: list, keep: str = ""):
    """Replace combo items (plain text, no item data) preserving current text."""
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
