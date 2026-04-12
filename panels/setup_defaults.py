"""
panels/setup_defaults.py — Samba v3
SetupDefaultsPanel — per-setup hardware device paths and calibration defaults.
Displayed as a tab next to Device Registry.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout,
    QLabel, QLineEdit, QGroupBox, QScrollArea
)
from PyQt6.QtCore import pyqtSignal

from panels._widgets import NoScrollComboBox


# Known magnet attribute names shown in the attr dropdowns
_MAGNET_CUR_ATTRS = [
    "current_polar", "current_longitudinal", "current", "amplitude",
]
_MAGNET_FLD_ATTRS = [
    "field_polar_corr", "field_longitudinal_corr", "field", "field_polar",
]


class SetupDefaultsPanel(QWidget):
    """
    Editable per-setup hardware device paths and calibration defaults.

    All device dropdowns are populated from the Device Registry.
    When any value changes the `defaults_changed` signal is emitted; the main
    window saves the new values back into the active setup dict immediately.
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

        def _hdr(text, row, col):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#6c7086;font-size:10px;font-weight:bold;")
            sg.addWidget(lbl, row, col)

        _hdr("Device (TANGO path)", 0, 1)
        _hdr("Attr",                0, 2)
        _hdr("Label",               0, 3)
        _hdr("Unit",                0, 4)

        sg.addWidget(QLabel("Act 1 (X):"), 1, 0)
        self.act1_dev  = _DevCombo()
        self.act1_attr = _DevCombo()
        self.act1_lbl  = _ShortEdit("X",  42)
        self.act1_unit = _ShortEdit("nm", 42)
        sg.addWidget(self.act1_dev,  1, 1)
        sg.addWidget(self.act1_attr, 1, 2)
        sg.addWidget(self.act1_lbl,  1, 3)
        sg.addWidget(self.act1_unit, 1, 4)

        sg.addWidget(QLabel("Act 2 (Y):"), 2, 0)
        self.act2_dev  = _DevCombo()
        self.act2_attr = _DevCombo()
        self.act2_lbl  = _ShortEdit("Y",  42)
        self.act2_unit = _ShortEdit("nm", 42)
        sg.addWidget(self.act2_dev,  2, 1)
        sg.addWidget(self.act2_attr, 2, 2)
        sg.addWidget(self.act2_lbl,  2, 3)
        sg.addWidget(self.act2_unit, 2, 4)

        cl.addWidget(stage_grp)

        # ── Current Source & Magnetics ────────────────────────────────────────
        hw_grp = QGroupBox("Current Source & Magnetics")
        hg = QGridLayout(hw_grp)
        hg.setSpacing(4); hg.setContentsMargins(8, 10, 8, 8)
        hg.setColumnStretch(1, 1); hg.setColumnStretch(3, 1)

        hg.addWidget(QLabel("Keithley:"),     0, 0)
        self.keithley_dev = _DevCombo()
        hg.addWidget(self.keithley_dev,       0, 1, 1, 3)

        hg.addWidget(QLabel("Magnet:"),       1, 0)
        self.magnet_dev = _DevCombo()
        hg.addWidget(self.magnet_dev,         1, 1, 1, 3)

        hg.addWidget(QLabel("Current attr:"), 2, 0)
        self.magnet_cur_attr = _AttrCombo(_MAGNET_CUR_ATTRS)
        hg.addWidget(self.magnet_cur_attr,    2, 1)
        hg.addWidget(QLabel("Field attr:"),   2, 2)
        self.magnet_fld_attr = _AttrCombo(_MAGNET_FLD_ATTRS)
        hg.addWidget(self.magnet_fld_attr,    2, 3)

        hg.addWidget(QLabel("Relay:"),        3, 0)
        self.relay_dev = _DevCombo()
        hg.addWidget(self.relay_dev,          3, 1, 1, 3)

        cl.addWidget(hw_grp)

        # ── Calibration ───────────────────────────────────────────────────────
        cal_grp = QGroupBox("Calibration")
        cg = QGridLayout(cal_grp)
        cg.setSpacing(4); cg.setContentsMargins(8, 10, 8, 8)
        cg.setColumnStretch(1, 1)

        cg.addWidget(QLabel("Z attribute:"),  0, 0)
        self.z_attr = QLineEdit()
        cg.addWidget(self.z_attr,             0, 1)

        cg.addWidget(QLabel("Focus sensor:"), 1, 0)
        self.focus_dev = _DevCombo()
        cg.addWidget(self.focus_dev,          1, 1)

        cl.addWidget(cal_grp)

        # ── TR-MOKE ───────────────────────────────────────────────────────────
        tr_grp = QGroupBox("TR-MOKE")
        tg = QGridLayout(tr_grp)
        tg.setSpacing(4); tg.setContentsMargins(8, 10, 8, 8)
        tg.setColumnStretch(1, 1)

        tg.addWidget(QLabel("DG645 device:"), 0, 0)
        self.trmoke_dg645 = _DevCombo()
        tg.addWidget(self.trmoke_dg645,       0, 1)

        cl.addWidget(tr_grp)
        cl.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll)

        # ── Internal state ────────────────────────────────────────────────────
        self._registry: list = []
        self._loading = False

        # Act1 device change → repopulate Act1 attr
        self.act1_dev.currentTextChanged.connect(
            lambda: self._repop_stage_attr(self.act1_dev, self.act1_attr))
        # Act2 device change → repopulate Act2 attr
        self.act2_dev.currentTextChanged.connect(
            lambda: self._repop_stage_attr(self.act2_dev, self.act2_attr))

        # Wire all widgets to defaults_changed
        _combos = [
            self.act1_dev, self.act1_attr,
            self.act2_dev, self.act2_attr,
            self.keithley_dev, self.magnet_dev,
            self.magnet_cur_attr, self.magnet_fld_attr,
            self.relay_dev, self.focus_dev, self.trmoke_dg645,
        ]
        _edits = [self.act1_lbl, self.act1_unit, self.act2_lbl, self.act2_unit, self.z_attr]
        for w in _combos:
            w.currentTextChanged.connect(self._on_changed)
        for w in _edits:
            w.textChanged.connect(self._on_changed)

    # ── Registry ──────────────────────────────────────────────────────────────
    def set_registry(self, registry: list):
        """Rebuild device combos from the Device Registry."""
        self._registry = registry
        self._repopulate_all_combos()

    def _repopulate_all_combos(self):
        reg = self._registry

        def _paths_for(types):
            matched = [d["tango_path"] for d in reg if d.get("type") in types]
            return matched if matched else [d["tango_path"] for d in reg]

        stage_paths    = _paths_for({"stage"})
        magnet_paths   = _paths_for({"magnet"})
        relay_paths    = _paths_for({"relay"})
        keithley_paths = _paths_for({"current", "keithley"})
        focus_paths    = _paths_for({"sensor", "beckhoff", "averageIn"})
        dg645_paths    = _paths_for({"dg645"})

        for combo, paths in [
            (self.act1_dev,    stage_paths),
            (self.act2_dev,    stage_paths),
            (self.magnet_dev,  magnet_paths),
            (self.relay_dev,   relay_paths),
            (self.keithley_dev,keithley_paths),
            (self.focus_dev,   focus_paths),
            (self.trmoke_dg645,dg645_paths),
        ]:
            _repop_combo(combo, paths)

        self._repop_stage_attr(self.act1_dev, self.act1_attr)
        self._repop_stage_attr(self.act2_dev, self.act2_attr)

    def _repop_stage_attr(self, dev_combo: "NoScrollComboBox",
                          attr_combo: "NoScrollComboBox"):
        """Populate an attr combo with channels from the selected stage device."""
        if self._loading:
            return
        dev_path = dev_combo.currentText()
        cur_attr = attr_combo.currentText()
        attrs = []
        for d in self._registry:
            if d.get("tango_path") == dev_path:
                attrs = [ch.get("attr", "") for ch in d.get("channels", []) if ch.get("attr")]
                break
        _repop_combo(attr_combo, attrs, cur_attr)

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

            _set(self.keithley_dev,    setup_data.get("keithley_device",     ""))
            _set(self.magnet_dev,      setup_data.get("magnet_device",       ""))
            _set(self.magnet_cur_attr, setup_data.get("magnet_current_attr", "current_polar"))
            _set(self.magnet_fld_attr, setup_data.get("magnet_field_attr",   "field_polar_corr"))
            _set(self.relay_dev,       setup_data.get("relay_device",        ""))
            _set(self.focus_dev,       setup_data.get("focus_averagein",     ""))
            self.z_attr.setText(       setup_data.get("z_attr",              "z"))
            _set(self.trmoke_dg645,    setup_data.get("trmoke_dg645",        "intermag/dg645/1"))
        finally:
            self._loading = False

    def get_defaults(self) -> dict:
        """Return current widget values as a dict of setup keys."""
        return {
            "act1_device":         self.act1_dev.currentText(),
            "act1_attr":           self.act1_attr.currentText(),
            "act1_label":          self.act1_lbl.text(),
            "act1_unit":           self.act1_unit.text(),
            "act2_device":         self.act2_dev.currentText(),
            "act2_attr":           self.act2_attr.currentText(),
            "act2_label":          self.act2_lbl.text(),
            "act2_unit":           self.act2_unit.text(),
            "keithley_device":     self.keithley_dev.currentText(),
            "magnet_device":       self.magnet_dev.currentText(),
            "magnet_current_attr": self.magnet_cur_attr.currentText(),
            "magnet_field_attr":   self.magnet_fld_attr.currentText(),
            "relay_device":        self.relay_dev.currentText(),
            "focus_averagein":     self.focus_dev.currentText(),
            "z_attr":              self.z_attr.text(),
            "trmoke_dg645":        self.trmoke_dg645.currentText(),
        }

    def _on_changed(self):
        if not self._loading:
            self.defaults_changed.emit()


# ── Small widget helpers ───────────────────────────────────────────────────────

def _DevCombo() -> NoScrollComboBox:
    c = NoScrollComboBox()
    c.setEditable(True)
    c.setMinimumWidth(130)
    return c


def _AttrCombo(items: list) -> NoScrollComboBox:
    c = NoScrollComboBox()
    c.setEditable(True)
    c.addItems(items)
    return c


def _ShortEdit(text: str, width: int) -> QLineEdit:
    e = QLineEdit(text)
    e.setFixedWidth(width)
    return e


def _set(combo: NoScrollComboBox, value: str):
    """Set combo to value; append as a new item if not already present."""
    if not value:
        return
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setEditText(value)


def _repop_combo(combo: NoScrollComboBox, items: list, keep: str = ""):
    """Replace combo items preserving the current text selection."""
    cur = keep or combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    for item in items:
        combo.addItem(item)
    if cur:
        idx = combo.findText(cur)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(cur)
    combo.blockSignals(False)
