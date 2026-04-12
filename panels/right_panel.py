"""
panels/right_panel.py — Samba v3
RightPanel — unified Devices + Plot panel (dropdown-based, registry-driven).
"""
from typing import List, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QGroupBox, QScrollArea, QStackedWidget
)
from PyQt6.QtCore import pyqtSignal

from config import COLORMAPS, X_NATURAL, X_TIME
from device_registry import load_registry
from panels._widgets import NoScrollComboBox
from panels.sensor_picker import SensorPickerRow


class RightPanel(QWidget):
    sensors_changed     = pyqtSignal()
    plot_config_changed = pyqtSignal()
    refresh_requested   = pyqtSignal()
    display_changed     = pyqtSignal(str, str)   # sensor_label, colormap
    x_axis_changed      = pyqtSignal(str, str)   # key, display_label

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self._registry: List[dict] = load_registry()
        self._sensor_rows: List[SensorPickerRow] = []

        lay = QVBoxLayout(self); lay.setContentsMargins(4, 6, 4, 4); lay.setSpacing(4)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QWidget(); hl = QHBoxLayout(hdr)
        hl.setContentsMargins(4, 0, 4, 0); hl.setSpacing(6)
        for txt, w in [("✓", 18), ("Device", 0), ("Channel", 100), ("Axis", 65)]:
            lb = QLabel(txt); lb.setStyleSheet("color:#6c7086;font-size:10px;")
            if w: lb.setFixedWidth(w)
            hl.addWidget(lb, stretch=(0 if w else 1))
        self._hdr_widget = hdr
        lay.addWidget(hdr)

        # ── Stacked widget: page 0 = normal sensors, page 1 = DC channels ───
        self._sensor_stack = QStackedWidget()

        # Page 0: Normal sensor picker
        page0 = QWidget(); p0l = QVBoxLayout(page0)
        p0l.setContentsMargins(0, 0, 0, 0); p0l.setSpacing(2)
        self._sensor_content = QWidget()
        self._sensor_vlayout = QVBoxLayout(self._sensor_content)
        self._sensor_vlayout.setContentsMargins(0, 0, 0, 0); self._sensor_vlayout.setSpacing(2)
        sensor_scroll = QScrollArea(); sensor_scroll.setWidgetResizable(True)
        sensor_scroll.setWidget(self._sensor_content)
        sensor_scroll.setStyleSheet("QScrollArea{border:none;}")
        p0l.addWidget(sensor_scroll, stretch=1)
        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        add_btn = QPushButton("+ Add channel"); add_btn.clicked.connect(self._add_sensor)
        ref_btn = QPushButton("↺ Refresh"); ref_btn.clicked.connect(self.refresh_requested)
        btn_row.addWidget(add_btn); btn_row.addWidget(ref_btn); btn_row.addStretch()
        p0l.addLayout(btn_row)
        self._sensor_stack.addWidget(page0)

        # Page 1: DC hysteresis channels (registry-based, same picker as page 0)
        page1 = QWidget(); p1l = QVBoxLayout(page1)
        p1l.setContentsMargins(0, 0, 0, 0); p1l.setSpacing(2)
        dc_hdr = QLabel("DC Hysteresis channels")
        dc_hdr.setStyleSheet("color:#89b4fa;font-weight:bold;font-size:11px;")
        p1l.addWidget(dc_hdr)
        self._dc_sensor_content = QWidget()
        self._dc_sensor_vlayout = QVBoxLayout(self._dc_sensor_content)
        self._dc_sensor_vlayout.setContentsMargins(0, 0, 0, 0); self._dc_sensor_vlayout.setSpacing(2)
        dc_scroll = QScrollArea(); dc_scroll.setWidgetResizable(True)
        dc_scroll.setWidget(self._dc_sensor_content)
        dc_scroll.setStyleSheet("QScrollArea{border:none;}")
        p1l.addWidget(dc_scroll, stretch=1)
        dc_btn_row = QHBoxLayout(); dc_btn_row.setSpacing(4)
        dc_add_btn = QPushButton("+ Add channel"); dc_add_btn.clicked.connect(self._add_dc_sensor)
        dc_btn_row.addWidget(dc_add_btn); dc_btn_row.addStretch()
        p1l.addLayout(dc_btn_row)
        self._dc_sensor_rows: List[SensorPickerRow] = []
        self._sensor_stack.addWidget(page1)

        lay.addWidget(self._sensor_stack, stretch=1)

        # ── Plot controls ─────────────────────────────────────────────────────
        pc = QGroupBox("Plot"); pcl = QGridLayout(pc)
        pcl.setSpacing(4); pcl.setContentsMargins(6, 6, 6, 6)

        pcl.addWidget(QLabel("2D sensor:"), 0, 0)
        self.disp_combo = NoScrollComboBox()
        self.disp_combo.currentTextChanged.connect(self._on_disp_changed)
        pcl.addWidget(self.disp_combo, 0, 1)

        pcl.addWidget(QLabel("Colormap:"), 0, 2)
        self.cmap_combo = NoScrollComboBox()
        for cm in COLORMAPS: self.cmap_combo.addItem(cm)
        self.cmap_combo.currentTextChanged.connect(self._on_disp_changed)
        pcl.addWidget(self.cmap_combo, 0, 3)

        pcl.addWidget(QLabel("X axis:"), 1, 0)
        self.x_combo = NoScrollComboBox(); self.x_combo.setToolTip("X axis for 1D plots")
        self._x_options: List[Tuple[str,str]] = [(X_NATURAL,"Natural x"), (X_TIME,"Time (s)")]
        for _, lbl in self._x_options: self.x_combo.addItem(lbl)
        self.x_combo.currentIndexChanged.connect(self._on_x_changed)
        pcl.addWidget(self.x_combo, 1, 1, 1, 3)

        lay.addWidget(pc)

    # ── Registry ──────────────────────────────────────────────────────────────
    def set_registry(self, registry: List[dict]):
        """Update the registry reference (called after registry edits)."""
        self._registry = registry
        for row in self._sensor_rows:
            row.update_registry(registry)
        for row in self._dc_sensor_rows:
            row.update_registry(registry)

    # ── Sensor management ─────────────────────────────────────────────────────
    def _add_sensor(self):
        self._make_picker_row()

    def _make_picker_row(self, device_name: str = "", channel_attr: str = "",
                         axis: str = "Y1", enabled: bool = False) -> SensorPickerRow:
        row = SensorPickerRow(self._registry, device_name, channel_attr,
                              axis, enabled)
        row.changed.connect(self._on_sensors_changed)
        row.delete_requested.connect(lambda: self._delete_row(row))
        self._sensor_vlayout.addWidget(row)
        self._sensor_rows.append(row)
        return row

    def _delete_row(self, row: SensorPickerRow):
        if row in self._sensor_rows:
            self._sensor_rows.remove(row)
            self._sensor_vlayout.removeWidget(row); row.hide(); row.deleteLater()
            self._on_sensors_changed()

    def _on_sensors_changed(self):
        self._refresh_disp_combo()
        self._refresh_x_sensor_items()
        self.sensors_changed.emit()
        self.plot_config_changed.emit()

    def _refresh_disp_combo(self):
        prev = self.disp_combo.currentText()
        self.disp_combo.blockSignals(True); self.disp_combo.clear()
        for r in self._sensor_rows:
            s = r.get()
            if s["enabled"]: self.disp_combo.addItem(s["label"])
        if prev and self.disp_combo.findText(prev) >= 0:
            self.disp_combo.setCurrentText(prev)
        self.disp_combo.blockSignals(False)

    def _refresh_x_sensor_items(self):
        prev_key = self.get_x_key()
        self.x_combo.blockSignals(True); self.x_combo.clear()
        self._x_options = [(X_NATURAL,"Natural x"), (X_TIME,"Time (s)")]
        for row in self._sensor_rows:
            s = row.get()
            if s["enabled"]: self._x_options.append((s["label"], s["label"]))
        for _, lbl in self._x_options: self.x_combo.addItem(lbl)
        keys = [k for k, _ in self._x_options]
        if prev_key in keys: self.x_combo.setCurrentIndex(keys.index(prev_key))
        self.x_combo.blockSignals(False)

    def _on_disp_changed(self):
        self.display_changed.emit(self.disp_combo.currentText(), self.cmap_combo.currentText())

    def _on_x_changed(self, idx):
        if 0 <= idx < len(self._x_options):
            key, lbl = self._x_options[idx]
            self.x_axis_changed.emit(key, lbl)

    # ── Public API ────────────────────────────────────────────────────────────
    def load_sensors(self, sensors: List[dict]):
        """Load sensor list from saved config.
        Uses device_name + channel_attr if saved (new format),
        falls back to tango path reverse lookup (old format)."""
        for row in self._sensor_rows:
            self._sensor_vlayout.removeWidget(row); row.hide(); row.deleteLater()
        self._sensor_rows = []

        # Build reverse lookup for old-format configs: tango_path → device name
        path_to_name = {}
        for d in self._registry:
            path_to_name[d["tango_path"]] = d["name"]
            # Also index lowercase for case-insensitive matching
            path_to_name[d["tango_path"].lower()] = d["name"]

        for s in sensors:
            # Prefer saved device_name (exact match)
            dev_name = s.get("device_name", "")
            if not dev_name:
                # Fallback: reverse lookup from tango path
                dev_path = s.get("device", "")
                dev_name = path_to_name.get(dev_path, "")
                if not dev_name:
                    dev_name = path_to_name.get(dev_path.lower(), "")

            # Prefer saved channel_attr, fallback to attribute
            ch_attr = s.get("channel_attr", s.get("attribute", ""))

            axis = s.get("plot_axis", s.get("y_axis", "Y1"))
            if not s.get("plot_visible", True) and axis not in ("X", "hidden"):
                axis = "hidden"
            enabled = s.get("enabled", False)

            self._make_picker_row(dev_name, ch_attr, axis, enabled)

        self._refresh_disp_combo()
        self._refresh_x_sensor_items()

    def get_sensors(self) -> List[dict]:
        return [r.get() for r in self._sensor_rows]

    def get_display_sensor(self) -> str:
        return self.disp_combo.currentText()

    def get_colormap(self) -> str:
        return self.cmap_combo.currentText()

    def get_x_key(self) -> str:
        # Check if any sensor has axis set to X
        for r in self._sensor_rows:
            s = r.get()
            if s["enabled"] and s.get("plot_axis") == "X":
                return s["label"]
        idx = self.x_combo.currentIndex()
        if 0 <= idx < len(self._x_options): return self._x_options[idx][0]
        return X_NATURAL

    def get_plot_sensors_meta(self) -> List[dict]:
        result = []
        for sr in self._sensor_rows:
            s = sr.get()
            if not s["enabled"]: continue
            axis = s.get("plot_axis", s.get("y_axis", "Y1"))
            if axis in ("hidden", "X"):
                axis = "—"
            result.append({"label": s["label"], "unit": s.get("unit",""), "axis": axis})
        return result

    def set_x_options(self, options: List[Tuple[str,str]]):
        prev_key = self.get_x_key()
        self.x_combo.blockSignals(True); self.x_combo.clear()
        self._x_options = options
        for _, lbl in self._x_options: self.x_combo.addItem(lbl)
        keys = [k for k, _ in self._x_options]
        if prev_key in keys: self.x_combo.setCurrentIndex(keys.index(prev_key))
        self.x_combo.blockSignals(False)

    def set_display(self, sensor: str, cmap: str):
        if self.disp_combo.findText(sensor) >= 0: self.disp_combo.setCurrentText(sensor)
        if self.cmap_combo.findText(cmap)   >= 0: self.cmap_combo.setCurrentText(cmap)

    # ── DC mode switching ────────────────────────────────────────────────────
    def set_dc_mode(self, dc: bool):
        """Switch between normal sensor picker (page 0) and DC channels (page 1)."""
        self._sensor_stack.setCurrentIndex(1 if dc else 0)
        self._hdr_widget.setVisible(not dc)

    def _add_dc_sensor(self):
        self._make_dc_picker_row()

    def _make_dc_picker_row(self, device_name: str = "", channel_attr: str = "",
                             axis: str = "Y1", enabled: bool = False) -> SensorPickerRow:
        row = SensorPickerRow(self._registry, device_name, channel_attr,
                              axis, enabled)
        row.changed.connect(self._on_dc_sensors_changed)
        row.delete_requested.connect(lambda: self._delete_dc_row(row))
        self._dc_sensor_vlayout.addWidget(row)
        self._dc_sensor_rows.append(row)
        return row

    def _delete_dc_row(self, row: SensorPickerRow):
        if row in self._dc_sensor_rows:
            self._dc_sensor_rows.remove(row)
            self._dc_sensor_vlayout.removeWidget(row); row.hide(); row.deleteLater()
            self._on_dc_sensors_changed()

    def _on_dc_sensors_changed(self):
        self.sensors_changed.emit()
        self.plot_config_changed.emit()

    def get_dc_channels(self) -> list:
        """Return hyst_channels list from the DC sensor picker rows."""
        return [r.get() for r in self._dc_sensor_rows]

    def load_dc_channels(self, chs: list):
        """Load DC channel config into the DC page using SensorPickerRow."""
        for row in self._dc_sensor_rows:
            self._dc_sensor_vlayout.removeWidget(row); row.hide(); row.deleteLater()
        self._dc_sensor_rows = []

        # Build reverse lookup for old-format configs
        path_to_name = {d["tango_path"]: d["name"] for d in self._registry}
        path_to_name.update({d["tango_path"].lower(): d["name"] for d in self._registry})

        for s in chs:
            dev_name = s.get("device_name", "")
            if not dev_name:
                dev_path = s.get("device", "")
                dev_name = path_to_name.get(dev_path, path_to_name.get(dev_path.lower(), ""))
            ch_attr = s.get("channel_attr", s.get("attribute", s.get("attr", "")))
            axis = s.get("plot_axis", s.get("y_axis", "Y1"))
            if not s.get("plot_visible", True) and axis not in ("X", "hidden"):
                axis = "hidden"
            enabled = s.get("enabled", False)
            self._make_dc_picker_row(dev_name, ch_attr, axis, enabled)


