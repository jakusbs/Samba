"""
device_registry.py — Samba v3
Device Registry: define physical instruments once, reuse everywhere.

Each device entry stores the Tango path, trigger mechanism, integration time
attribute, and available channels (readable attributes with labels and units).
The scan config then references devices from the registry by name, rather
than having users manually configure Tango paths and trigger commands per
sensor row.

Persistence: ~/.config/moke_scan/device_registry.json
"""
import copy, json
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QComboBox,
    QListWidget, QListWidgetItem, QScrollArea, QCheckBox,
    QMessageBox, QInputDialog, QAbstractItemView
)
from PyQt6.QtCore import pyqtSignal, Qt

# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────
DEVICE_TYPES = ["lockin", "beckhoff_avg", "beckhoff_adc", "magnet", "hysteresis", "stage", "delay", "other"]

REGISTRY_DIR = Path.home() / ".config" / "moke_scan"
REGISTRY_FILE = REGISTRY_DIR / "device_registry.json"


def _default_registry() -> List[dict]:
    """Built-in device definitions for the Intermag lab."""
    return [
        {
            "name":            "ZI2",
            "tango_path":      "hpp-N42/measure/ZI2",
            "type":            "lockin",
            "trigger_cmd":     "Start",
            "integ_time_attr": "integrationtime",
            "channels": [
                {"attr": "x1", "label": "ZI2 x1", "unit": "V"},
                {"attr": "y1", "label": "ZI2 y1", "unit": "V"},
                {"attr": "x2", "label": "ZI2 x2", "unit": "V"},
                {"attr": "y2", "label": "ZI2 y2", "unit": "V"},
            ],
        },
        {
            "name":            "ZI1",
            "tango_path":      "hpp-N42/measure/ZI1",
            "type":            "lockin",
            "trigger_cmd":     "Start",
            "integ_time_attr": "integrationtime",
            "channels": [
                {"attr": "x1", "label": "ZI1 x1", "unit": "V"},
                {"attr": "y1", "label": "ZI1 y1", "unit": "V"},
                {"attr": "x2", "label": "ZI1 x2", "unit": "V"},
                {"attr": "y2", "label": "ZI1 y2", "unit": "V"},
            ],
        },
        {
            "name":            "FL (averageIn2)",
            "tango_path":      "hpp-N42/beckhoff/averageIn2",
            "type":            "beckhoff_avg",
            "trigger_cmd":     "Start",
            "integ_time_attr": "integrationtime",
            "channels": [
                {"attr": "Value", "label": "FL", "unit": "V"},
            ],
        },
        {
            "name":            "Mon (averageIn1)",
            "tango_path":      "hpp-N42/beckhoff/averageIn1",
            "type":            "beckhoff_avg",
            "trigger_cmd":     "Start",
            "integ_time_attr": "integrationtime",
            "channels": [
                {"attr": "Value", "label": "Mon", "unit": "V"},
            ],
        },
        {
            "name":            "averageIn3",
            "tango_path":      "hpp-N42/beckhoff/averageIn3",
            "type":            "beckhoff_avg",
            "trigger_cmd":     "Start",
            "integ_time_attr": "integrationtime",
            "channels": [
                {"attr": "Value", "label": "avgIn3", "unit": "V"},
            ],
        },
        {
            "name":            "DC diode (analogIn2)",
            "tango_path":      "hpp-N42/beckhoff/analogIn2",
            "type":            "beckhoff_adc",
            "trigger_cmd":     "",
            "integ_time_attr": "",
            "channels": [
                {"attr": "Value", "label": "DC diode", "unit": "V"},
            ],
        },
        {
            "name":            "Magnet",
            "tango_path":      "hpp-N42/beckhoff/magnet",
            "type":            "magnet",
            "trigger_cmd":     "",
            "integ_time_attr": "",
            "channels": [
                {"attr": "field_polar_corr",        "label": "Field polar",  "unit": "T"},
                {"attr": "field_longitudinal_corr", "label": "Field long.",  "unit": "T"},
            ],
        },
        {
            "name":            "Hyst Longitudinal",
            "tango_path":      "hpp-N42/beckhoff/pyhystlongi",
            "type":            "hysteresis",
            "trigger_cmd":     "",
            "integ_time_attr": "",
            "channels": [
                {"attr": "result1", "label": "MOKE (R1)", "unit": "V"},
                {"attr": "result2", "label": "R2",        "unit": "V"},
                {"attr": "result3", "label": "R3",        "unit": "V"},
                {"attr": "result4", "label": "R4",        "unit": "V"},
                {"attr": "result5", "label": "R5 (Hall)", "unit": "V"},
                {"attr": "result6", "label": "R6",        "unit": "V"},
                {"attr": "field",   "label": "Field",     "unit": "mT"},
            ],
        },
        {
            "name":            "IR Stage",
            "tango_path":      "smaract2/control/IR-controller",
            "type":            "stage",
            "trigger_cmd":     "",
            "integ_time_attr": "",
            "channels": [
                {"attr": "x", "label": "X", "unit": "nm"},
                {"attr": "y", "label": "Y", "unit": "nm"},
                {"attr": "z", "label": "Z", "unit": "nm"},
            ],
        },
        {
            "name":            "Green Stage",
            "tango_path":      "smaract2/control/Green-controller",
            "type":            "stage",
            "trigger_cmd":     "",
            "integ_time_attr": "",
            "channels": [
                {"attr": "x", "label": "X", "unit": "nm"},
                {"attr": "y", "label": "Y", "unit": "nm"},
                {"attr": "z", "label": "Z", "unit": "nm"},
            ],
        },
        {
            "name":            "Hyst Polar",
            "tango_path":      "hpp-N42/beckhoff/pyhystpolar",
            "type":            "hysteresis",
            "trigger_cmd":     "",
            "integ_time_attr": "",
            "channels": [
                {"attr": "result1", "label": "MOKE (R1)", "unit": "V"},
                {"attr": "result2", "label": "R2",        "unit": "V"},
                {"attr": "result3", "label": "R3",        "unit": "V"},
                {"attr": "result4", "label": "R4",        "unit": "V"},
                {"attr": "result5", "label": "R5 (Hall)", "unit": "V"},
                {"attr": "result6", "label": "R6",        "unit": "V"},
                {"attr": "field",   "label": "Field",     "unit": "mT"},
            ],
        },
        {
            "name":            "DG645",
            "tango_path":      "intermag/dg645/1",
            "type":            "delay",
            "trigger_cmd":     "",
            "integ_time_attr": "",
            "channels": [
                {"attr": "DelayA", "label": "Delay A", "unit": "s"},
                {"attr": "DelayB", "label": "Delay B", "unit": "s"},
                {"attr": "DelayC", "label": "Delay C", "unit": "s"},
                {"attr": "DelayD", "label": "Delay D", "unit": "s"},
                {"attr": "DelayE", "label": "Delay E", "unit": "s"},
                {"attr": "DelayF", "label": "Delay F", "unit": "s"},
                {"attr": "DelayG", "label": "Delay G", "unit": "s"},
                {"attr": "DelayH", "label": "Delay H", "unit": "s"},
            ],
        },
    ]


def load_registry() -> List[dict]:
    """Load device registry from disk, falling back to defaults."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    if REGISTRY_FILE.exists():
        try:
            with open(REGISTRY_FILE) as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                # Ensure all entries have required fields
                for d in data:
                    d.setdefault("channels", [])
                    d.setdefault("trigger_cmd", "")
                    d.setdefault("integ_time_attr", "")
                    d.setdefault("type", "other")
                return data
        except Exception as e:
            print(f"Registry load error: {e}")
    return _default_registry()


def save_registry(devices: List[dict]):
    """Save device registry to disk."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(REGISTRY_FILE, "w") as f:
            json.dump(devices, f, indent=2)
    except Exception as e:
        print(f"Registry save error: {e}")


def registry_to_sensors(registry: List[dict], selections: List[dict]) -> List[dict]:
    """
    Convert registry + user selections into the flat sensor list used by scan config.

    selections is a list of:
      {"device_name": "ZI2", "attr": "x1", "enabled": True, "axis": "Y1"}

    Returns a list of sensor dicts compatible with the existing scan engine.
    """
    # Build lookup: device_name → registry entry
    reg_map = {d["name"]: d for d in registry}
    sensors = []
    for sel in selections:
        dev = reg_map.get(sel.get("device_name"))
        if dev is None:
            continue
        # Find channel info
        ch = None
        for c in dev["channels"]:
            if c["attr"] == sel.get("attr"):
                ch = c
                break
        if ch is None:
            continue
        sensors.append({
            "label":           ch.get("label", f"{dev['name']} {ch['attr']}"),
            "device":          dev["tango_path"],
            "attribute":       ch["attr"],
            "unit":            ch.get("unit", ""),
            "enabled":         sel.get("enabled", True),
            "y_axis":          sel.get("axis", "Y1"),
            "plot_visible":    sel.get("axis", "Y1") != "hidden",
            "trigger_cmd":     dev.get("trigger_cmd", ""),
            "integ_time_attr": dev.get("integ_time_attr", ""),
        })
    return sensors


# ─────────────────────────────────────────────────────────────────────────────
# ChannelRowWidget — one channel in the device editor
# ─────────────────────────────────────────────────────────────────────────────
class ChannelRowWidget(QWidget):
    changed = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, ch: dict, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(4)

        self.attr_edit  = QLineEdit(ch.get("attr", ""))
        self.attr_edit.setFixedWidth(100)
        self.attr_edit.setPlaceholderText("attribute")
        self.attr_edit.textChanged.connect(lambda _: self.changed.emit())

        self.label_edit = QLineEdit(ch.get("label", ""))
        self.label_edit.setFixedWidth(120)
        self.label_edit.setPlaceholderText("label")
        self.label_edit.textChanged.connect(lambda _: self.changed.emit())

        self.unit_edit  = QLineEdit(ch.get("unit", ""))
        self.unit_edit.setFixedWidth(40)
        self.unit_edit.setPlaceholderText("unit")
        self.unit_edit.textChanged.connect(lambda _: self.changed.emit())

        del_btn = QPushButton("×")
        del_btn.setFixedWidth(22)
        del_btn.setFixedHeight(22)
        del_btn.setStyleSheet(
            "QPushButton{color:#f38ba8;font-weight:bold;border:1px solid #45475a;"
            "border-radius:3px;padding:0;background:#313244;}"
            "QPushButton:hover{background:#45475a;}")
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        del_btn.clicked.connect(self.delete_requested.emit)

        lay.addWidget(QLabel("attr:"))
        lay.addWidget(self.attr_edit)
        lay.addWidget(QLabel("label:"))
        lay.addWidget(self.label_edit)
        lay.addWidget(QLabel("unit:"))
        lay.addWidget(self.unit_edit)
        lay.addWidget(del_btn)
        lay.addStretch()

    def get(self) -> dict:
        return {
            "attr":  self.attr_edit.text().strip(),
            "label": self.label_edit.text().strip(),
            "unit":  self.unit_edit.text().strip(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# DeviceRegistryPanel — editor for the device database
# ─────────────────────────────────────────────────────────────────────────────
class DeviceRegistryPanel(QWidget):
    """
    Panel for editing the device registry.
    Left: device list.  Right: edit selected device properties + channels.
    """
    registry_changed = pyqtSignal()   # emitted when devices are added/removed/edited

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: List[dict] = load_registry()
        self._current_idx: int = -1
        self._channel_rows: List[ChannelRowWidget] = []
        self._loading: bool = False   # guard: don't save during load

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        # ── Left: device list ─────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(QLabel("Devices:"))
        self.dev_list = QListWidget()
        self.dev_list.setFixedWidth(180)
        self.dev_list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.dev_list, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        add_btn = QPushButton("+ Add")
        add_btn.setFixedHeight(26)
        add_btn.clicked.connect(self._add_device)
        dup_btn = QPushButton("Dup")
        dup_btn.setFixedHeight(26)
        dup_btn.clicked.connect(self._dup_device)
        del_btn = QPushButton("Delete")
        del_btn.setFixedHeight(26)
        del_btn.setStyleSheet("color:#f38ba8;")
        del_btn.clicked.connect(self._del_device)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(dup_btn)
        btn_row.addWidget(del_btn)
        left.addLayout(btn_row)
        root.addLayout(left)

        # ── Right: device editor ──────────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(6)

        # Properties
        pg = QGroupBox("Device properties")
        gl = QGridLayout(pg)
        gl.setSpacing(4)
        gl.setContentsMargins(8, 8, 8, 8)

        gl.addWidget(QLabel("Name:"), 0, 0)
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._on_prop_changed)
        gl.addWidget(self.name_edit, 0, 1)

        gl.addWidget(QLabel("Tango path:"), 1, 0)
        self.path_edit = QLineEdit()
        self.path_edit.textChanged.connect(self._on_prop_changed)
        gl.addWidget(self.path_edit, 1, 1)

        gl.addWidget(QLabel("Type:"), 2, 0)
        self.type_combo = QComboBox()
        self.type_combo.addItems(DEVICE_TYPES)
        self.type_combo.currentIndexChanged.connect(self._on_prop_changed)
        gl.addWidget(self.type_combo, 2, 1)

        gl.addWidget(QLabel("Trigger cmd:"), 3, 0)
        self.trigger_edit = QLineEdit()
        self.trigger_edit.setPlaceholderText("e.g. Start (leave empty if none)")
        self.trigger_edit.textChanged.connect(self._on_prop_changed)
        gl.addWidget(self.trigger_edit, 3, 1)

        gl.addWidget(QLabel("Integ time attr:"), 4, 0)
        self.integ_edit = QLineEdit()
        self.integ_edit.setPlaceholderText("e.g. integrationtime (leave empty if none)")
        self.integ_edit.textChanged.connect(self._on_prop_changed)
        gl.addWidget(self.integ_edit, 4, 1)

        right.addWidget(pg)

        # Channels
        cg = QGroupBox("Channels (readable attributes)")
        self._ch_layout = QVBoxLayout(cg)
        self._ch_layout.setSpacing(2)
        self._ch_layout.setContentsMargins(8, 8, 8, 8)

        self._ch_scroll_area = QScrollArea()
        self._ch_scroll_area.setWidgetResizable(True)
        self._ch_container = QWidget()
        self._ch_inner_layout = QVBoxLayout(self._ch_container)
        self._ch_inner_layout.setSpacing(2)
        self._ch_inner_layout.setContentsMargins(0, 0, 0, 0)
        self._ch_inner_layout.addStretch()
        self._ch_scroll_area.setWidget(self._ch_container)
        self._ch_layout.addWidget(self._ch_scroll_area, stretch=1)

        add_ch_btn = QPushButton("+ Add channel")
        add_ch_btn.setFixedHeight(24)
        add_ch_btn.clicked.connect(self._add_channel)
        self._ch_layout.addWidget(add_ch_btn)

        right.addWidget(cg, stretch=1)

        # Save button
        save_btn = QPushButton("Save registry")
        save_btn.setObjectName("start_btn")
        save_btn.setFixedHeight(30)
        save_btn.clicked.connect(self._save)
        right.addWidget(save_btn)

        root.addLayout(right, stretch=1)

        # Populate list
        self._refresh_list()

    # ── List management ───────────────────────────────────────────────────
    def _refresh_list(self):
        self.dev_list.blockSignals(True)
        self.dev_list.clear()
        for d in self._devices:
            self.dev_list.addItem(d.get("name", "(unnamed)"))
        self.dev_list.blockSignals(False)
        if self._devices:
            self.dev_list.setCurrentRow(0)

    def _on_select(self, idx: int):
        self._save_current()
        self._current_idx = idx
        self._load_current()

    def _save_current(self):
        """Save editor fields back into self._devices."""
        if self._loading:
            return
        if self._current_idx < 0 or self._current_idx >= len(self._devices):
            return
        d = self._devices[self._current_idx]
        d["name"]            = self.name_edit.text().strip()
        d["tango_path"]      = self.path_edit.text().strip()
        d["type"]            = self.type_combo.currentText()
        d["trigger_cmd"]     = self.trigger_edit.text().strip()
        d["integ_time_attr"] = self.integ_edit.text().strip()
        d["channels"]        = [r.get() for r in self._channel_rows]
        # Update list item text
        item = self.dev_list.item(self._current_idx)
        if item:
            item.setText(d["name"] or "(unnamed)")

    def _load_current(self):
        """Load device fields into editor."""
        self._loading = True
        try:
            # Clear channels
            for r in self._channel_rows:
                r.setParent(None)
            self._channel_rows.clear()

            if self._current_idx < 0 or self._current_idx >= len(self._devices):
                self.name_edit.clear()
                self.path_edit.clear()
                self.trigger_edit.clear()
                self.integ_edit.clear()
                return

            d = self._devices[self._current_idx]
            self.name_edit.setText(d.get("name", ""))
            self.path_edit.setText(d.get("tango_path", ""))
            t = d.get("type", "other")
            idx = self.type_combo.findText(t)
            self.type_combo.setCurrentIndex(idx if idx >= 0 else len(DEVICE_TYPES) - 1)
            self.trigger_edit.setText(d.get("trigger_cmd", ""))
            self.integ_edit.setText(d.get("integ_time_attr", ""))

            for ch in d.get("channels", []):
                self._insert_channel_row(ch)
        finally:
            self._loading = False

    def _on_prop_changed(self, *_):
        self._save_current()

    # ── Device add/dup/delete ─────────────────────────────────────────────
    def _add_device(self):
        new_dev = {
            "name": "New device",
            "tango_path": "",
            "type": "other",
            "trigger_cmd": "",
            "integ_time_attr": "",
            "channels": [],
        }
        self._save_current()
        self._devices.append(new_dev)
        self._refresh_list()
        self.dev_list.setCurrentRow(len(self._devices) - 1)

    def _dup_device(self):
        if self._current_idx < 0:
            return
        self._save_current()
        new_dev = copy.deepcopy(self._devices[self._current_idx])
        new_dev["name"] = new_dev["name"] + " (copy)"
        self._devices.append(new_dev)
        self._refresh_list()
        self.dev_list.setCurrentRow(len(self._devices) - 1)

    def _del_device(self):
        if self._current_idx < 0:
            return
        name = self._devices[self._current_idx].get("name", "")
        reply = QMessageBox.question(
            self, "Delete device",
            f"Remove '{name}' from registry?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        del self._devices[self._current_idx]
        self._current_idx = -1
        self._refresh_list()

    # ── Channel management ────────────────────────────────────────────────
    def _insert_channel_row(self, ch: dict):
        row = ChannelRowWidget(ch)
        row.changed.connect(self._save_current)
        row.delete_requested.connect(lambda r=row: self._remove_channel(r))
        # Insert before the stretch
        idx = self._ch_inner_layout.count() - 1
        self._ch_inner_layout.insertWidget(idx, row)
        self._channel_rows.append(row)

    def _add_channel(self):
        self._insert_channel_row({"attr": "", "label": "", "unit": ""})
        self._save_current()

    def _remove_channel(self, row: ChannelRowWidget):
        if row in self._channel_rows:
            self._channel_rows.remove(row)
            row.setParent(None)
            self._save_current()

    # ── Save / access ─────────────────────────────────────────────────────
    def _save(self):
        self._save_current()
        save_registry(self._devices)
        self.registry_changed.emit()

    def get_registry(self) -> List[dict]:
        self._save_current()
        return copy.deepcopy(self._devices)

    def get_device_names(self) -> List[str]:
        return [d.get("name", "") for d in self._devices]

    def get_device_by_name(self, name: str) -> Optional[dict]:
        for d in self._devices:
            if d.get("name") == name:
                return copy.deepcopy(d)
        return None
