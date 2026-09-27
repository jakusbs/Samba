"""Map range, physical aspect, point readout, and line-profile controls."""
import numpy as np
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import (QToolButton, QMenu, QDialog, QFormLayout, QLineEdit,
                             QDialogButtonBox, QMessageBox, QVBoxLayout, QLabel)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from core.plot_geometry import physical_aspect


class MapControls(QObject):
    def __init__(self, owner, ax, canvas, get_data, set_limits):
        super().__init__(owner)
        self.ax, self.canvas = ax, canvas
        self.get_data, self.set_limits = get_data, set_limits
        self.selected = None
        self.button = QToolButton(owner)
        self.button.setText("Map options")
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.button)
        self.physical = self.menu.addAction("Physical aspect")
        self.physical.setCheckable(True)
        self.physical.toggled.connect(self.apply_aspect)
        self.menu.addAction("Set color limits…", self.edit_limits)
        self.menu.addAction("Line profiles at cursor…", self.profiles)
        self.button.setMenu(self.menu)
        self.readout = QLabel("Hover a map to read X, Y and signal; gray cells are unmeasured.", owner)
        self.readout.setWordWrap(True)
        self.readout.setStyleSheet("color:#a6adc8;font-size:9pt;padding:2px 6px;")
        self._dialogs = []
        self.canvas.mpl_connect("motion_notify_event", self.on_motion)
        self.canvas.mpl_connect("button_press_event", self.on_motion)

    def apply_aspect(self, *_):
        ratio = physical_aspect(self.ax.get_xlabel(), self.ax.get_ylabel())
        self.ax.set_aspect(ratio if self.physical.isChecked() and ratio is not None else "auto")
        self.canvas.draw_idle()

    def on_motion(self, event):
        if event.inaxes is not self.ax or event.xdata is None or event.ydata is None:
            return
        snapshot = self.get_data()
        if snapshot is None:
            return
        data, x, y, label = snapshot
        ix = int(np.argmin(abs(np.asarray(x) - event.xdata)))
        iy = int(np.argmin(abs(np.asarray(y) - event.ydata)))
        self.selected = iy, ix
        value = data[iy, ix]
        text = f"{value:.6g}" if np.isfinite(value) else "unmeasured"
        self.readout.setText(f"{self.ax.get_xlabel()}: {x[ix]:.6g} · "
                             f"{self.ax.get_ylabel()}: {y[iy]:.6g} · {label}: {text}")

    def edit_limits(self):
        if not self.ax.collections:
            return
        dialog = QDialog(self.button)
        dialog.setWindowTitle("Color limits — displayed signal units")
        form = QFormLayout(dialog)
        lo, hi = self.ax.collections[0].get_clim()
        lower, upper = QLineEdit(f"{lo:.8g}"), QLineEdit(f"{hi:.8g}")
        form.addRow("Minimum", lower); form.addRow("Maximum", upper)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        def apply():
            try:
                bounds = float(lower.text()), float(upper.text())
                if not all(np.isfinite(bounds)) or bounds[0] >= bounds[1]:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(dialog, "Invalid limits", "Enter finite numbers with minimum below maximum.")
                return
            self.set_limits(*bounds)
            dialog.accept()
        buttons.accepted.connect(apply); buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def profiles(self):
        snapshot = self.get_data()
        if snapshot is None:
            return
        data, x, y, label = snapshot
        iy, ix = self.selected or (len(y) // 2, len(x) // 2)
        iy, ix = min(iy, len(y)-1), min(ix, len(x)-1)
        dialog = QDialog(self.button)
        dialog.setWindowTitle("Line profiles — snapshot")
        dialog.resize(800, 500)
        layout = QVBoxLayout(dialog)
        fig = Figure(figsize=(8, 4), constrained_layout=True)
        axes = fig.subplots(1, 2)
        axes[0].plot(x, data[iy, :]); axes[0].set_xlabel(self.ax.get_xlabel())
        axes[0].set_title(f"Y = {y[iy]:.6g}")
        axes[1].plot(y, data[:, ix]); axes[1].set_xlabel(self.ax.get_ylabel())
        axes[1].set_title(f"X = {x[ix]:.6g}")
        for ax in axes:
            ax.set_ylabel(label); ax.grid(alpha=.2)
        layout.addWidget(FigureCanvasQTAgg(fig))
        from core.plot_interact import make_light_export_btn
        layout.addWidget(make_light_export_btn(lambda: fig, dialog))
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda *_: self._dialogs.remove(dialog))
        dialog.show()
