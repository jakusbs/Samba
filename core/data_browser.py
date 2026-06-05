"""
data_browser.py — Samba v3
Browse, load and overlay past HDF5 scan files.

Features:
  • Tree view organised by date folder → scan files
  • Metadata preview (scan type, points, sensors, status, duration)
  • Load into 1D or 2D plot
  • Overlay multiple 1D scans for comparison
  • Handles incomplete/aborted files (from incremental saving)
"""
import os, glob
import numpy as np
import h5py
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavToolbar
from matplotlib.figure import Figure

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QPushButton,
    QComboBox, QGroupBox, QTextEdit, QCheckBox,
    QFileDialog, QMessageBox, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from config import LEFT_COLORS, RIGHT_COLORS, COLORMAPS


# ─────────────────────────────────────────────────────────────────────────────
# Scan file reader — reads both old (end-of-scan) and new (incremental) HDF5
# ─────────────────────────────────────────────────────────────────────────────
class ScanFile:
    """Lightweight wrapper around one HDF5 scan file."""

    def __init__(self, path: str):
        self.path = path
        self.basename = os.path.basename(path)
        self.valid = False
        self.meta: Dict = {}
        self.sensor_keys: List[str] = []
        self.sensor_labels: Dict[str, str] = {}  # key → label
        self._read_meta()

    def _read_meta(self):
        try:
            with h5py.File(self.path, "r") as f:
                is_new = "data" in f          # new layout flag
                is_dc  = (f.attrs.get("scan_type", "?") == "DC_HYST")

                # ── Shared root attrs (same in both layouts) ──────────────────
                scan_type = str(f.attrs.get("scan_type", "?"))
                status    = str(f.attrs.get("scan_status", "completed"))
                timestamp = str(f.attrs.get("timestamp", ""))

                # ── Size / count params ───────────────────────────────────────
                if is_new:
                    meta_g = f.get("metadata", f)   # fallback to root if absent
                    n_loop       = int(meta_g.attrs.get("n_loop", 0))
                    n_x_raw      = n_loop if is_dc else int(meta_g.attrs.get("n_x", 0))
                    n_y_raw      = 1      if is_dc else int(meta_g.attrs.get("n_y", 0))
                    pts_acq      = int(meta_g.attrs.get("points_acquired", 0))
                    pts_plan     = int(meta_g.attrs.get("points_planned", n_loop if is_dc else 0))
                    int_time     = float(meta_g.attrs.get("integration_time",
                                         meta_g.attrs.get("IntegrationTime", 0)))
                    settle       = float(meta_g.attrs.get("settle_time", 0))
                    duration     = float(f.attrs.get("duration_seconds",
                                         meta_g.attrs.get("duration_seconds", 0)))
                    operator     = str(meta_g.attrs.get("operator", ""))
                    sample_id    = str(meta_g.attrs.get("sample_id", ""))
                    notes        = str(meta_g.attrs.get("notes", ""))
                    scan_name    = str(meta_g.attrs.get("scan_name", "?"))
                else:
                    # ── Old layout: everything in root attrs ──────────────────
                    n_loop   = int(f.attrs.get("n_loop", 0))
                    n_x_raw  = n_loop if is_dc else int(f.attrs.get("n_x", 0))
                    n_y_raw  = 1      if is_dc else int(f.attrs.get("n_y", 0))
                    pts_acq  = int(f.attrs.get("points_acquired", 0))
                    pts_plan = int(f.attrs.get("points_planned", n_loop))
                    int_time = float(f.attrs.get("IntegrationTime" if is_dc
                                                  else "integration_time", 0))
                    settle   = float(f.attrs.get("settle_time", 0))
                    duration = float(f.attrs.get("duration_seconds", 0))
                    operator  = str(f.attrs.get("operator", ""))
                    sample_id = str(f.attrs.get("sample_id", ""))
                    notes     = str(f.attrs.get("notes", ""))
                    scan_name = str(f.attrs.get("scan_name", "?"))

                self.meta = {
                    "scan_type":        scan_type,
                    "scan_name":        scan_name,
                    "timestamp":        timestamp,
                    "scan_status":      status,
                    "points_acquired":  pts_acq,
                    "points_planned":   pts_plan,
                    "n_x":              n_x_raw,
                    "n_y":              n_y_raw,
                    "integration_time": int_time,
                    "settle_time":      settle,
                    "duration_seconds": duration,
                    "operator":         operator,
                    "sample_id":        sample_id,
                    "notes":            notes,
                    "is_dc_hyst":       is_dc,
                }

                # ── Hardware snapshot & step-size attrs ───────────────────────
                _meta_src = f.get("metadata", f)
                for k in (
                    "hw_keithley_amplitude_mA", "hw_keithley_frequency_Hz",
                    "hw_keithley_range",        "hw_keithley_compliance_V",
                    "hw_keithley_current_mA",
                    "hw_zi_tc_s",               "hw_zi_order",
                    "hw_zi_settling_s",         "hw_relay_state",
                    "hw_field_mT",              "hw_magnet_current_A",
                    "hw_act1_pos",              "hw_act2_pos",
                    "hw_temperature_K",         "hw_vti_temp_K",
                    "hw_magnet_temp_K",
                    "act1_step", "act2_step",   "field_step_A",
                    "is_temp_sweep",
                    "temp_sweep_start_K", "temp_sweep_stop_K", "temp_sweep_step_K",
                    "geometry", "stage_type", "field_segments_json",
                ):
                    v = _meta_src.attrs.get(k)
                    if v is not None:
                        self.meta[k] = v

                # Normalise scan_type for display
                if self.meta.get("is_temp_sweep"):
                    self.meta["scan_type"] = "TEMP_SWEEP"

                # ── DC extra scalars ──────────────────────────────────────────
                if is_dc:
                    src = f.get("metadata", f)
                    for s in ("Hc", "Hshift", "Mr", "Ms"):
                        self.meta[s] = float(src.attrs.get(s, float("nan")))
                    self.meta["MagneticField_V"] = float(
                        src.attrs.get("MagneticField_V", float("nan")))
                    self.meta["Cycles"] = int(src.attrs.get("Cycles", 0))
                    self.meta["NumberOfPoints"] = int(
                        src.attrs.get("NumberOfPoints", 0))

                # ── Discover sensor keys ──────────────────────────────────────
                if is_new:
                    dg = f.get("data", {})
                    for key in dg:
                        role = str(dg[key].attrs.get("role", ""))
                        if role == "sensor":
                            self.sensor_keys.append(key)
                            self.sensor_labels[key] = str(
                                dg[key].attrs.get("label", key))
                elif "sensors" in f:
                    for key in f["sensors"]:
                        self.sensor_keys.append(key)
                        self.sensor_labels[key] = str(
                            f["sensors"][key].attrs.get("label", key))
                elif "measurement" in f:
                    for key in f["measurement"]:
                        if key not in _AXIS_NAMES:
                            self.sensor_keys.append(key)
                            self.sensor_labels[key] = str(
                                f["measurement"][key].attrs.get("label", key))

                self.valid = True
        except Exception:
            self.valid = False

    def read_1d(self, x_key: str = "auto", y_key: str = "auto") -> Optional[Dict]:
        """
        Read 1D data from the file.
        Supports both new layout (/data/) and old layout (/measurement/, /axes/, /sensors/).
        Returns dict with 'x', 'y', 'x_label', 'y_label', 'x_unit', 'y_unit'.
        """
        try:
            with h5py.File(self.path, "r") as f:
                is_new = "data" in f
                is_dc  = (f.attrs.get("scan_type", "") == "DC_HYST")

                if is_new:
                    dg = f["data"]

                    # Find the x-axis dataset by role attr — name-agnostic
                    x_ds_key = next(
                        (k for k in dg if str(dg[k].attrs.get("role","")) == "x"),
                        None)

                    if is_dc:
                        if x_ds_key is None:
                            return None
                        x_arr  = dg[x_ds_key][:]
                        x_lbl  = str(dg[x_ds_key].attrs.get("label", "Field"))
                        x_unit = str(dg[x_ds_key].attrs.get("unit",  "mT"))
                        if y_key == "auto":
                            y_key = self.sensor_keys[0] if self.sensor_keys else None
                        if y_key is None or y_key not in dg:
                            return None
                    else:
                        if x_ds_key is None:
                            return None
                        if x_key != "auto" and x_key in dg:
                            x_ds_key = x_key   # honour explicit caller choice
                        x_arr  = dg[x_ds_key][:].flatten()
                        x_lbl  = str(dg[x_ds_key].attrs.get("label", x_ds_key))
                        x_unit = str(dg[x_ds_key].attrs.get("unit",  ""))
                        if y_key == "auto":
                            y_key = self.sensor_keys[0] if self.sensor_keys else None
                        if y_key is None or y_key not in dg:
                            return None

                    y_arr  = dg[y_key][:].flatten()
                    y_lbl  = str(dg[y_key].attrs.get("label", y_key))
                    y_unit = str(dg[y_key].attrs.get("unit",  ""))
                    x_arr  = x_arr.flatten()

                else:
                    # ── Old layout ────────────────────────────────────────────
                    if is_dc:
                        if "axes" not in f or "sensors" not in f:
                            return None
                        x_arr  = f["axes"]["field_mT"][:]
                        x_lbl  = str(f["axes"]["field_mT"].attrs.get("label", "Field"))
                        x_unit = str(f["axes"]["field_mT"].attrs.get("unit",  "mT"))
                        if y_key == "auto":
                            y_key = self.sensor_keys[0] if self.sensor_keys else None
                        if y_key is None or y_key not in f["sensors"]:
                            return None
                        y_arr  = f["sensors"][y_key][:].flatten()
                        y_lbl  = str(f["sensors"][y_key].attrs.get("label", y_key))
                        y_unit = str(f["sensors"][y_key].attrs.get("unit",  "V"))
                        x_arr  = x_arr.flatten()
                    else:
                        if "measurement" not in f:
                            return None
                        meas = f["measurement"]
                        if x_key == "auto":
                            x_key = meas.attrs.get("axes", "x_actual")
                            if x_key not in meas:
                                for c in ["x_actual", "field_T", "time"]:
                                    if c in meas:
                                        x_key = c; break
                        if y_key == "auto":
                            y_key = meas.attrs.get("signal", "")
                            if y_key not in meas and self.sensor_keys:
                                y_key = self.sensor_keys[0]
                        if x_key not in meas or y_key not in meas:
                            return None
                        x_arr  = meas[x_key][:]
                        y_arr  = meas[y_key][:]
                        x_lbl  = str(meas[x_key].attrs.get("label", x_key))
                        y_lbl  = str(meas[y_key].attrs.get("label", y_key))
                        x_unit = str(meas[x_key].attrs.get("unit",  ""))
                        y_unit = str(meas[y_key].attrs.get("unit",  ""))

                x = x_arr.flatten(); y = y_arr.flatten()
                mask = np.isfinite(x) & np.isfinite(y)
                return {
                    "x": x[mask], "y": y[mask],
                    "x_label": x_lbl, "y_label": y_lbl,
                    "x_unit":  x_unit, "y_unit":  y_unit,
                }
        except Exception:
            return None
    def read_2d(self, sensor_key: str = "auto") -> Optional[Dict]:
        """
        Read 2D data.  Supports new (/data/) and old (/sensors/ + /axes/) layouts.
        Returns dict with 'data', 'x_arr', 'y_arr', 'x_label', 'y_label', 'sensor_label'.
        """
        try:
            with h5py.File(self.path, "r") as f:
                is_new = "data" in f
                if sensor_key == "auto":
                    sensor_key = self.sensor_keys[0] if self.sensor_keys else None
                if sensor_key is None:
                    return None

                if is_new:
                    dg = f["data"]
                    if sensor_key not in dg:
                        return None
                    data_arr = dg[sensor_key][:]
                    slbl = str(dg[sensor_key].attrs.get("label", sensor_key))
                    # Find x/y setpoints by role — name-agnostic
                    xset_key = next(
                        (k for k in dg if str(dg[k].attrs.get("role","")) == "x_setpoint"),
                        None)
                    yset_key = next(
                        (k for k in dg if str(dg[k].attrs.get("role","")) == "y_setpoint"),
                        None)
                    if xset_key:
                        x_arr = dg[xset_key][:]
                        x_lbl = str(dg[xset_key].attrs.get("label", "X"))
                        x_lbl = x_lbl.replace(" (setpoint)", "")
                    else:
                        x_arr = np.arange(data_arr.shape[-1]); x_lbl = "Index"
                    if yset_key:
                        y_arr = dg[yset_key][:]
                        y_lbl = str(dg[yset_key].attrs.get("label", "Y"))
                        y_lbl = y_lbl.replace(" (setpoint)", "")
                    else:
                        y_arr = np.arange(data_arr.shape[0]); y_lbl = "Row"
                else:
                    if "sensors" not in f or "axes" not in f:
                        return None
                    if sensor_key not in f["sensors"]:
                        return None
                    data_arr = f["sensors"][sensor_key][:]
                    slbl = str(f["sensors"][sensor_key].attrs.get("label", sensor_key))
                    x_col = "field_T" if "field_T" in f["axes"] else "x_actual"
                    if x_col in f["axes"]:
                        x_arr = f["axes"]["x_setpoint"][:]
                        x_lbl = str(f["axes"]["x_setpoint"].attrs.get("label", "X"))
                    else:
                        x_arr = np.arange(data_arr.shape[1]); x_lbl = "Index"
                    if "y" in f["axes"]:
                        y_arr = f["axes"]["y"][:]
                        y_lbl = str(f["axes"]["y"].attrs.get("label", "Y"))
                    else:
                        y_arr = np.arange(data_arr.shape[0]); y_lbl = "Row"

                return {
                    "data": data_arr, "x_arr": x_arr, "y_arr": y_arr,
                    "x_label": x_lbl, "y_label": y_lbl,
                    "sensor_label": slbl,
                }
        except Exception:
            return None

    def list_columns(self) -> List[Tuple[str, str]]:
        """Return [(key, display_label)] for all plottable columns."""
        cols = []
        try:
            with h5py.File(self.path, "r") as f:
                is_new = "data" in f
                is_dc  = (f.attrs.get("scan_type", "") == "DC_HYST")

                if is_new:
                    dg = f["data"]
                    for key in dg:
                        role  = str(dg[key].attrs.get("role", ""))
                        label = str(dg[key].attrs.get("label", key))
                        unit  = str(dg[key].attrs.get("unit",  ""))
                        # Expose the actual axis (role "x") and sensors as
                        # plottable; hide setpoints and the y/y_setpoint aux arrays
                        if role in ("x_setpoint", "y", "y_setpoint"):
                            continue
                        display = f"{label} ({unit})" if unit else label
                        cols.append((key, display))
                elif is_dc:
                    if "axes" in f and "field_mT" in f["axes"]:
                        lbl  = f["axes"]["field_mT"].attrs.get("label", "Field")
                        unit = f["axes"]["field_mT"].attrs.get("unit",  "mT")
                        cols.append(("field_mT", f"{lbl} ({unit})"))
                    if "sensors" in f:
                        for key in f["sensors"]:
                            lbl = f["sensors"][key].attrs.get("label", key)
                            cols.append((key, str(lbl)))
                elif "measurement" in f:
                    for key in f["measurement"]:
                        lbl = f["measurement"][key].attrs.get("label", key)
                        cols.append((key, str(lbl)))
        except Exception:
            pass
        return cols


# ─────────────────────────────────────────────────────────────────────────────
# Preview plot widget
# ─────────────────────────────────────────────────────────────────────────────
class BrowserPlotWidget(QWidget):
    """Standalone plot for the data browser — supports 1D overlay and 2D display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # constrained_layout keeps the axes filling the figure (with the
        # colorbar) across resizes — avoids the map shrinking to a narrow strip.
        self.fig    = Figure(figsize=(6, 4), dpi=100, facecolor="#1e1e2e",
                             constrained_layout=True)
        self.ax     = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.bar    = NavToolbar(self.canvas, None)
        self.bar.setStyleSheet("background:#1e1e2e;color:white;")

        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(6)
        top.addWidget(self.bar, stretch=1)

        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.canvas, stretch=1)
        self._cb     = None
        self._is_2d  = False
        self._style()

    def _style(self):
        self.ax.set_facecolor("#12121f")
        self.ax.tick_params(colors="#aaaacc", labelsize=9)
        for sp in self.ax.spines.values():
            sp.set_edgecolor("#3a3a5c")

    def clear(self):
        if self._cb:
            try: self._cb.remove()
            except Exception: pass
            self._cb = None
        self._is_2d = False
        self.ax.cla(); self._style()
        self.canvas.draw_idle()

    def plot_1d(self, datasets: List[Dict], title: str = ""):
        """
        Overlay multiple 1D datasets.
        Each dataset: {x, y, x_label, y_label, legend, color?}
        """
        self.clear()
        colors = LEFT_COLORS + RIGHT_COLORS
        for i, ds in enumerate(datasets):
            c = ds.get("color", colors[i % len(colors)])
            self.ax.plot(ds["x"], ds["y"], color=c, linewidth=1.5,
                         label=ds.get("legend", ds.get("y_label", f"#{i}")),
                         marker=".", markersize=3)
        if datasets:
            self.ax.set_xlabel(
                f"{datasets[0]['x_label']} ({datasets[0].get('x_unit','')})",
                color="#aaaacc")
            self.ax.set_ylabel(
                f"{datasets[0]['y_label']} ({datasets[0].get('y_unit','')})",
                color="#aaaacc")
        if title:
            self.ax.set_title(title, color="#ccccff", fontsize=10)
        self.ax.legend(fontsize=8, facecolor="#313244",
                       edgecolor="#45475a", labelcolor="#cdd6f4",
                       loc="best")
        self.canvas.draw_idle()

    def plot_2d(self, data: np.ndarray, x_arr, y_arr,
                x_label: str, y_label: str, sensor_label: str,
                cmap: str = "RdBu_r"):
        self.clear()
        ext = [x_arr[0], x_arr[-1], y_arr[0], y_arr[-1]]
        v   = data[np.isfinite(data)]
        vmin = v.min() if len(v) else 0
        vmax = v.max() if len(v) else 1
        if vmin == vmax: vmax = vmin + 1e-12
        img = self.ax.imshow(data, origin="lower", aspect="auto",
                             extent=ext, cmap=cmap, interpolation="nearest",
                             vmin=vmin, vmax=vmax)
        self._is_2d = True
        self._cb = self.fig.colorbar(img, ax=self.ax)
        self._cb.ax.yaxis.set_tick_params(color="#aaaacc", labelcolor="#aaaacc")
        self.ax.set_xlabel(x_label, color="#aaaacc")
        self.ax.set_ylabel(y_label, color="#aaaacc")
        self.ax.set_title(sensor_label, color="#ccccff", fontsize=10)
        self.canvas.draw_idle()


# ─────────────────────────────────────────────────────────────────────────────
# DataBrowserPanel — main panel for the bottom tabs
# ─────────────────────────────────────────────────────────────────────────────
class DataBrowserPanel(QWidget):
    """
    File browser + metadata preview + plot for past scans.
    Designed to be added as a tab in MainWindow.bottom_tabs.
    """
    file_loaded = pyqtSignal(str)  # emits filepath when a scan is loaded

    def __init__(self, save_dir_getter, parent=None):
        """save_dir_getter: callable returning the current save directory path."""
        super().__init__(parent)
        self._save_dir_getter = save_dir_getter
        self._loaded_files: Dict[str, ScanFile] = {}  # path → ScanFile
        self._overlay_paths: List[str] = []  # paths selected for overlay
        self._last_y_key: str = ""   # remember last viewed detector across files

        root = QHBoxLayout(self); root.setContentsMargins(4, 4, 4, 4); root.setSpacing(4)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: file tree + controls ────────────────────────────────────────
        left = QWidget(); left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0); left_l.setSpacing(4)

        ctrl_row = QHBoxLayout()
        refresh_btn = QPushButton("↻ Refresh"); refresh_btn.clicked.connect(self.refresh)
        refresh_btn.setToolTip("Rescan the save directory for new HDF5 files")
        ctrl_row.addWidget(refresh_btn)
        browse_btn = QPushButton("📂 Open…"); browse_btn.clicked.connect(self._open_file)
        browse_btn.setToolTip("Load a specific HDF5 scan file from anywhere on the filesystem")
        ctrl_row.addWidget(browse_btn)
        ctrl_row.addStretch()
        left_l.addLayout(ctrl_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Status", "Points"])
        self.tree.setColumnWidth(0, 180)
        self.tree.setColumnWidth(1, 60)
        self.tree.setColumnWidth(2, 60)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.itemSelectionChanged.connect(self._update_overlay_btn)
        left_l.addWidget(self.tree, stretch=1)

        # Overlay button
        ov_row = QHBoxLayout()
        self.overlay_btn = QPushButton("📊 Overlay selected")
        self.overlay_btn.clicked.connect(self._overlay_selected)
        self.overlay_btn.setEnabled(False)
        self.overlay_btn.setToolTip(
            "Plot all selected scan files on the same graph for direct comparison.\n"
            "Select multiple files with Ctrl+click or Shift+click.")
        ov_row.addWidget(self.overlay_btn)
        left_l.addLayout(ov_row)

        # Metadata panel
        meta_grp = QGroupBox("Metadata")
        meta_l = QVBoxLayout(meta_grp); meta_l.setContentsMargins(6, 6, 6, 6)
        self.meta_text = QTextEdit(); self.meta_text.setReadOnly(True)
        self.meta_text.setMaximumHeight(240)
        self.meta_text.setStyleSheet(
            "QTextEdit{background:#12121f;border:1px solid #313244;"
            "border-radius:4px;color:#a6e3a1;font-family:'Courier New',monospace;"
            "font-size:10px;}")
        meta_l.addWidget(self.meta_text)
        left_l.addWidget(meta_grp)

        # Column selectors
        self._populating_combos = False
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("X:"))
        self.x_combo = QComboBox(); self.x_combo.setMinimumWidth(90)
        self.x_combo.currentIndexChanged.connect(self._on_combo_changed)
        sel_row.addWidget(self.x_combo, stretch=1)
        sel_row.addWidget(QLabel("Y:"))
        self.y_combo = QComboBox(); self.y_combo.setMinimumWidth(90)
        self.y_combo.currentIndexChanged.connect(self._on_combo_changed)
        sel_row.addWidget(self.y_combo, stretch=1)
        # 2D-map mode: render the selected Y channel as a colour map.  Enabled
        # only for 2D scans; when on, the Y combo picks which channel is shown
        # and the map re-renders live instead of collapsing to a 1D plot.
        self.map2d_cb = QCheckBox("2D map")
        self.map2d_cb.setToolTip(
            "Show the selected Y channel as a 2D colour map (2D scans only).\n"
            "Uncheck for a 1D line plot of the selected X/Y columns.")
        self.map2d_cb.setEnabled(False)
        self.map2d_cb.toggled.connect(self._on_combo_changed)
        sel_row.addWidget(self.map2d_cb)
        # Colormap picker — applies to 2D colour maps.
        sel_row.addWidget(QLabel("Cmap:"))
        self.cmap_combo = QComboBox(); self.cmap_combo.setMinimumWidth(90)
        for cm in COLORMAPS:
            self.cmap_combo.addItem(cm)
        self.cmap_combo.setToolTip("Colormap for 2D colour maps")
        # Connect after populating so filling the list doesn't trigger a re-plot.
        self.cmap_combo.currentIndexChanged.connect(self._on_combo_changed)
        sel_row.addWidget(self.cmap_combo)
        plot_btn = QPushButton("Plot"); plot_btn.clicked.connect(self._plot_current)
        plot_btn.setToolTip("Plot the selected columns (or 2D map) from the current scan file")
        sel_row.addWidget(plot_btn)
        left_l.addLayout(sel_row)

        splitter.addWidget(left)

        # ── Right: plot ───────────────────────────────────────────────────────
        self.plot = BrowserPlotWidget()
        splitter.addWidget(self.plot)
        splitter.setSizes([320, 500]); splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

    # ── File discovery ────────────────────────────────────────────────────────
    def refresh(self):
        """Scan the save directory and populate the tree."""
        self.tree.clear()
        self._loaded_files.clear()
        base = os.path.expanduser(self._save_dir_getter())

        if not os.path.isdir(base):
            placeholder = QTreeWidgetItem(["No save directory found", "", ""])
            placeholder.setForeground(0, QColor("#6c7086"))
            self.tree.addTopLevelItem(placeholder)
            return

        # Find date directories (YYYYMMDD pattern)
        date_dirs = sorted(
            [d for d in os.listdir(base)
             if os.path.isdir(os.path.join(base, d)) and len(d) == 8 and d.isdigit()],
            reverse=True
        )

        for dd in date_dirs:
            date_path = os.path.join(base, dd)
            h5_files = sorted(glob.glob(os.path.join(date_path, "*.h5")), reverse=True)
            if not h5_files:
                continue

            # Format date nicely
            try:
                date_label = datetime.strptime(dd, "%Y%m%d").strftime("%Y-%m-%d (%a)")
            except Exception:
                date_label = dd

            date_item = QTreeWidgetItem([date_label, "", f"{len(h5_files)} files"])
            date_item.setExpanded(dd == date_dirs[0])  # expand today
            self.tree.addTopLevelItem(date_item)

            for fp in h5_files:
                sf = ScanFile(fp)
                if not sf.valid:
                    continue
                self._loaded_files[fp] = sf

                status = sf.meta["scan_status"]
                pts    = f"{sf.meta['points_acquired']}/{sf.meta['points_planned']}"
                item   = QTreeWidgetItem([sf.basename, status, pts])
                item.setData(0, Qt.ItemDataRole.UserRole, fp)

                # Color-code status
                if status == "completed":
                    item.setForeground(1, QColor("#a6e3a1"))
                elif status == "aborted":
                    item.setForeground(1, QColor("#fab387"))
                elif status == "running":
                    item.setForeground(1, QColor("#f38ba8"))
                else:
                    item.setForeground(1, QColor("#6c7086"))

                date_item.addChild(item)

        if self.tree.topLevelItemCount() == 0:
            placeholder = QTreeWidgetItem(["No scans found", "", ""])
            placeholder.setForeground(0, QColor("#6c7086"))
            self.tree.addTopLevelItem(placeholder)

    def _update_overlay_btn(self):
        """Enable overlay button only when at least one file is selected."""
        n = sum(1 for i in self.tree.selectedItems()
                if i.data(0, Qt.ItemDataRole.UserRole))
        self.overlay_btn.setEnabled(n > 0)
        self.overlay_btn.setToolTip(
            f"Plot {n} selected scan(s) on one graph" if n > 0
            else "Select one or more scans first")

    def _open_file(self):
        """Open a specific HDF5 file from anywhere."""
        fp, _ = QFileDialog.getOpenFileName(
            self, "Open HDF5 scan", os.path.expanduser("~"),
            "HDF5 files (*.h5 *.hdf5);;All files (*)")
        if not fp:
            return
        sf = ScanFile(fp)
        if not sf.valid:
            QMessageBox.warning(self, "Error", f"Could not read:\n{fp}")
            return
        self._loaded_files[fp] = sf
        self._show_file(sf)

    # ── Selection handling ────────────────────────────────────────────────────
    def _on_selection(self):
        items = self.tree.selectedItems()
        # Show metadata for the last selected file
        for item in reversed(items):
            fp = item.data(0, Qt.ItemDataRole.UserRole)
            if fp and fp in self._loaded_files:
                self._show_file(self._loaded_files[fp])
                return

    def _show_file(self, sf: ScanFile):
        """Display metadata and populate column combos."""
        m = sf.meta
        is_dc = m.get("is_dc_hyst", False)
        status_icon = {"completed": "✓", "aborted": "⚠", "running": "⏳"}.get(
            m["scan_status"], "?")

        lines = [
            f"Date:     {m['timestamp'][:19]}" if m["timestamp"] else "",
            f"Name:     {m['scan_name']}",
            f"Type:     {m['scan_type']}",
            f"Status:   {status_icon} {m['scan_status']}",
        ]
        if is_dc:
            lines += [
                f"Field:    {m.get('MagneticField_V', float('nan')):.3f} V  "
                f"× {m.get('NumberOfPoints', 0)} pts/half  "
                f"× {m.get('Cycles', 0)} cycles",
                f"Int.time: {m['integration_time']:.3g} s/half-loop",
                f"Points:   {m['points_acquired']} / {m.get('n_x', 0)}",
                f"Duration: {m.get('duration_seconds', 0):.1f} s",
                f"Channels: {', '.join(sf.sensor_labels.values())}",
            ]
            # Scalar results — only shown if finite
            def _fmtval(v, fmt=".3f"):
                return f"{v:{fmt}}" if np.isfinite(v) else "—"
            hc     = m.get("Hc",     float("nan"))
            hshift = m.get("Hshift", float("nan"))
            mr     = m.get("Mr",     float("nan"))
            ms     = m.get("Ms",     float("nan"))
            lines += [
                f"Hc:       {_fmtval(hc)} mT",
                f"Hshift:   {_fmtval(hshift)} mT",
                f"Mr:       {_fmtval(mr, '.4g')}",
                f"Ms:       {_fmtval(ms, '.4g')}",
            ]
        else:
            lines += [
                f"Grid:     {m['n_x']}×{m['n_y']}",
                f"Points:   {m['points_acquired']} / {m['points_planned']}",
                f"Time:     {m.get('duration_seconds', 0):.1f} s",
                f"Int/Sett: {m['integration_time']:.3f} / {m['settle_time']:.3f} s",
                f"Sensors:  {', '.join(sf.sensor_labels.values())}",
            ]
        if m.get("operator"):  lines.append(f"Operator: {m['operator']}")
        if m.get("sample_id"): lines.append(f"Sample:   {m['sample_id']}")
        if m.get("notes"):     lines.append(f"Notes:    {m['notes']}")
        if m.get("geometry") or m.get("stage_type"):
            parts = []
            if m.get("geometry"):   parts.append(m["geometry"])
            if m.get("stage_type"): parts.append(m["stage_type"])
            lines.append(f"Geometry: {' / '.join(parts)}")

        def _gfmt(v): return f"{v:.4g}" if isinstance(v, (int, float)) else str(v)

        # Temperature sweep range (temp sweeps only — suppress redundant field step)
        if m.get("is_temp_sweep"):
            t0 = m.get("temp_sweep_start_K", "?")
            t1 = m.get("temp_sweep_stop_K",  "?")
            ts = m.get("temp_sweep_step_K",  "?")
            lines.append(f"Temp range: {_gfmt(t0)} → {_gfmt(t1)} K  (step {_gfmt(ts)} K)")

        # Field scan range (real field scans only)
        is_field_scan = (m.get("scan_type") == "FIELD")
        if is_field_scan:
            segs_json = m.get("field_segments_json", "")
            if segs_json:
                try:
                    import json as _json
                    segs = _json.loads(segs_json)
                    f0 = segs[0][0];  f1 = segs[-1][1]
                    step = m.get("field_step_A")
                    step_str = f"  (step {_gfmt(step)} A)" if step is not None else ""
                    lines.append(f"Field range: {_gfmt(f0)} → {_gfmt(f1)} A{step_str}")
                except Exception:
                    if m.get("field_step_A") is not None:
                        lines.append(f"Field step: {_gfmt(m['field_step_A'])} A")

        # Spatial step sizes
        step_rows = []
        for key, label in (("act1_step", "Act1 step"), ("act2_step", "Act2 step")):
            v = m.get(key)
            if v is not None and v != "":
                step_rows.append(f"{label}: {_gfmt(v)}")
        if step_rows:
            lines.append("─" * 28)
            lines.extend(step_rows)

        # Hardware snapshot
        _HW_DISPLAY = [
            ("hw_keithley_amplitude_mA", "Keithley amp",   "mA"),
            ("hw_keithley_frequency_Hz", "Keithley freq",  "Hz"),
            ("hw_keithley_current_mA",   "Keithley I out", "mA"),
            ("hw_keithley_range",        "Keithley range", ""),
            ("hw_keithley_compliance_V", "Keithley compl", "V"),
            ("hw_zi_tc_s",               "ZI TC",          "s"),
            ("hw_zi_order",              "ZI order",       ""),
            ("hw_zi_settling_s",         "ZI settling",    "s"),
            ("hw_relay_state",           "Relay",          ""),
            ("hw_field_mT",              "Field @start",   "mT"),
            ("hw_magnet_current_A",      "Magnet I @start","A"),
            ("hw_temperature_K",         "Temp @start",    "K"),
            ("hw_vti_temp_K",            "VTI temp",       "K"),
            ("hw_magnet_temp_K",         "Magnet temp",    "K"),
            ("hw_act1_pos",              "Act1 @start",    ""),
            ("hw_act2_pos",              "Act2 @start",    ""),
        ]
        hw_rows = []
        for key, label, unit in _HW_DISPLAY:
            v = m.get(key)
            if v is not None and v != "":
                val = f"{v:.4g}" if isinstance(v, (int, float)) else str(v)
                hw_rows.append(f"{label}: {val}{' ' + unit if unit else ''}")
        if hw_rows:
            lines.append("─" * 28)
            lines.append("Hardware at scan start:")
            lines.extend(hw_rows)

        self.meta_text.setPlainText("\n".join(l for l in lines if l))

        # Populate column combos (guard auto-replot while we rebuild them)
        self._populating_combos = True
        cols = sf.list_columns()
        self.x_combo.clear(); self.y_combo.clear()
        for key, lbl in cols:
            self.x_combo.addItem(lbl, key)
            self.y_combo.addItem(lbl, key)

        # Smart defaults — find the x-axis dataset key by role for new files,
        # fall back to known names for old files
        x_default = None
        for i in range(self.x_combo.count()):
            key = self.x_combo.itemData(i)
            if key and (key.startswith("actuator_") or key in ("field_mT", "x_actual", "time")):
                x_default = key; break
        if is_dc and x_default is None:
            x_default = "field_mT"
        # Prefer the detector the user last looked at (sticky across files) if
        # this file also has it; otherwise fall back to the first sensor.
        y_default = ""
        if self._last_y_key:
            for i in range(self.y_combo.count()):
                if self.y_combo.itemData(i) == self._last_y_key:
                    y_default = self._last_y_key; break
        if not y_default:
            y_default = sf.sensor_keys[0] if sf.sensor_keys else ""
        if x_default:
            for i in range(self.x_combo.count()):
                if self.x_combo.itemData(i) == x_default:
                    self.x_combo.setCurrentIndex(i); break
        for i in range(self.y_combo.count()):
            if self.y_combo.itemData(i) == y_default:
                self.y_combo.setCurrentIndex(i); break

        # 2D-map mode is available only for non-DC scans with a real Y axis.
        is_2d = (not is_dc) and m["n_y"] > 1
        self.map2d_cb.blockSignals(True)
        self.map2d_cb.setEnabled(is_2d)
        self.map2d_cb.setChecked(is_2d)   # default to the map for 2D scans
        self.map2d_cb.blockSignals(False)
        self._populating_combos = False

        # Auto-plot in the resolved mode (DC/1D → line, 2D → colour map)
        self._plot_current()

    def _on_combo_changed(self, *_):
        """Live re-plot when the user changes a column selector or the 2D-map
        toggle — but not while the combos are being repopulated in _show_file."""
        if self._populating_combos:
            return
        # Remember the chosen detector so the next file opened defaults to it
        yk = self.y_combo.currentData()
        if yk:
            self._last_y_key = yk
        self._plot_current()

    def _plot_current(self):
        """Plot with the user's column selection — a 2D colour map when the
        2D-map toggle is on, otherwise a 1D line plot."""
        # Find the last selected file
        fp = None
        for item in reversed(self.tree.selectedItems()):
            candidate = item.data(0, Qt.ItemDataRole.UserRole)
            if candidate and candidate in self._loaded_files:
                fp = candidate
                break
        if not fp:
            self.meta_text.setPlainText("⚠ Select a scan file first.")
            return
        x_key = self.x_combo.currentData()
        y_key = self.y_combo.currentData()
        sf = self._loaded_files[fp]
        is_dc = sf.meta.get("is_dc_hyst", False)

        # ── 2D colour map of the selected Y channel ───────────────────────────
        if self.map2d_cb.isEnabled() and self.map2d_cb.isChecked() and not is_dc:
            sensor_key = y_key or "auto"
            result = sf.read_2d(sensor_key=sensor_key)
            if result and np.asarray(result["data"]).ndim == 2:
                self.plot.plot_2d(result["data"], result["x_arr"], result["y_arr"],
                                  result["x_label"], result["y_label"],
                                  result["sensor_label"],
                                  cmap=self.cmap_combo.currentText())
            else:
                self.meta_text.append("\n⚠ Could not read a 2D map for the selected channel.")
            return

        # ── 1D line plot ──────────────────────────────────────────────────────
        if not y_key or (not x_key and not is_dc):
            self.meta_text.append("\n⚠ Select X and Y columns above, then click Plot.")
            return
        if is_dc:
            result = sf.read_1d(y_key=y_key)
        else:
            result = sf.read_1d(x_key, y_key)
        if result:
            result["legend"] = sf.basename
            self.plot.plot_1d([result], title=sf.meta["scan_name"])
        else:
            self.meta_text.append("\n⚠ Could not read selected columns from file.")

    def _overlay_selected(self):
        """Overlay all selected scan files on one 1D plot."""
        items = self.tree.selectedItems()
        datasets = []
        title_parts = []
        for item in items:
            fp = item.data(0, Qt.ItemDataRole.UserRole)
            if fp and fp in self._loaded_files:
                sf = self._loaded_files[fp]
                x_key = self.x_combo.currentData() or "auto"
                y_key = self.y_combo.currentData() or "auto"
                result = sf.read_1d(x_key, y_key)
                if result:
                    result["legend"] = sf.basename
                    datasets.append(result)
                    if sf.meta["scan_name"] not in title_parts:
                        title_parts.append(sf.meta["scan_name"])

        if datasets:
            self.plot.plot_1d(datasets, title=f"Overlay: {', '.join(title_parts[:3])}")
        else:
            QMessageBox.information(self, "No data",
                "Select one or more scan files (not date folders) to overlay.")
