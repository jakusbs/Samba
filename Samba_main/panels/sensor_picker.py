"""
panels/sensor_picker.py — Samba v3
SensorPickerRow — dropdown-based device/channel/axis picker.
"""
from typing import List

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QCheckBox
)
from PyQt6.QtCore import pyqtSignal, Qt

from panels._widgets import NoScrollComboBox, AXIS_OPTIONS


class SensorPickerRow(QWidget):
    changed          = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, registry: List[dict], device_name: str = "",
                 channel_attr: str = "", axis: str = "Y1",
                 enabled: bool = False, parent=None):
        super().__init__(parent)
        self._registry = registry
        lay = QHBoxLayout(self); lay.setContentsMargins(2, 2, 2, 2); lay.setSpacing(6)

        self.ck = QCheckBox(); self.ck.setChecked(enabled)
        self.ck.stateChanged.connect(lambda _: self.changed.emit())

        # Device dropdown
        self.dev_combo = NoScrollComboBox()
        self.dev_combo.setMinimumWidth(140)
        dev_names = [d["name"] for d in registry]
        self.dev_combo.addItems(dev_names)
        if device_name in dev_names:
            self.dev_combo.setCurrentText(device_name)
        elif dev_names:
            self.dev_combo.setCurrentIndex(0)
        self.dev_combo.currentIndexChanged.connect(self._on_device_changed)

        # Channel dropdown (populated based on selected device)
        self.ch_combo = NoScrollComboBox()
        self.ch_combo.setMinimumWidth(100)
        self._populate_channels(channel_attr)
        self.ch_combo.currentIndexChanged.connect(lambda _: self.changed.emit())

        # Axis dropdown
        self.axis_combo = NoScrollComboBox(); self.axis_combo.addItems(AXIS_OPTIONS)
        self.axis_combo.setCurrentText(axis if axis in AXIS_OPTIONS else "Y1")
        self.axis_combo.setFixedWidth(65)
        self.axis_combo.currentIndexChanged.connect(lambda _: self.changed.emit())

        # Delete button
        del_btn = QPushButton("×"); del_btn.setFixedWidth(22); del_btn.setFixedHeight(22)
        del_btn.setStyleSheet("QPushButton{color:#f38ba8;font-weight:bold;border:1px solid #45475a;"
                              "border-radius:3px;padding:0;background:#313244;}"
                              "QPushButton:hover{background:#45475a;}")
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        del_btn.clicked.connect(self.delete_requested.emit)

        lay.addWidget(self.ck)
        lay.addWidget(self.dev_combo, stretch=2)
        lay.addWidget(self.ch_combo, stretch=1)
        lay.addWidget(self.axis_combo)
        lay.addWidget(del_btn)

    def _on_device_changed(self, _):
        self._populate_channels()
        self.changed.emit()

    def _populate_channels(self, select_attr: str = ""):
        """Fill channel dropdown from the selected device's channels."""
        self.ch_combo.blockSignals(True)
        self.ch_combo.clear()
        dev = self._get_device()
        if dev:
            for ch in dev.get("channels", []):
                label = ch.get("label", ch.get("attr", "?"))
                self.ch_combo.addItem(label, ch.get("attr", ""))
            # Try to select the requested attr
            if select_attr:
                for i in range(self.ch_combo.count()):
                    if self.ch_combo.itemData(i) == select_attr:
                        self.ch_combo.setCurrentIndex(i)
                        break
        self.ch_combo.blockSignals(False)

    def _get_device(self) -> dict:
        """Look up the selected device in the registry."""
        name = self.dev_combo.currentText()
        for d in self._registry:
            if d["name"] == name:
                return d
        return {}

    def _get_channel(self) -> dict:
        """Look up the selected channel in the device."""
        dev = self._get_device()
        attr = self.ch_combo.currentData()
        for ch in dev.get("channels", []):
            if ch.get("attr") == attr:
                return ch
        return {}

    def get(self) -> dict:
        """Return a full sensor dict compatible with the scan engine.
        Includes device_name and channel_attr for config persistence."""
        dev = self._get_device()
        ch  = self._get_channel()
        axis = self.axis_combo.currentText()
        label = ch.get("label", self.ch_combo.currentText())
        return {
            "label":           label,
            "device":          dev.get("tango_path", ""),
            "attribute":       ch.get("attr", self.ch_combo.currentData() or ""),
            "unit":            ch.get("unit", ""),
            "enabled":         self.ck.isChecked(),
            "y_axis":          axis if axis in ("Y1", "Y2") else "Y1",
            "plot_visible":    axis != "hidden",
            "trigger_cmd":     dev.get("trigger_cmd", ""),
            "integ_time_attr": dev.get("integ_time_attr", ""),
            "settling_attr":   dev.get("settling_attr", ""),
            "plot_axis":       axis,
            # Registry keys — used to restore dropdowns on config load
            "device_name":     dev.get("name", ""),
            "channel_attr":    ch.get("attr", self.ch_combo.currentData() or ""),
        }

    def get_axis(self) -> str:
        return self.axis_combo.currentText()

    def update_registry(self, registry: List[dict]):
        """Update registry reference (e.g. after registry edit)."""
        self._registry = registry


