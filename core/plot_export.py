"""Shared physical-size export specification for screen and browser figures."""
from dataclasses import dataclass
from datetime import datetime
import textwrap


@dataclass(frozen=True)
class ExportOptions:
    width_mm: float = 180.
    height_mm: float = 115.
    dpi: int = 300
    font_pt: float = 10.
    legend: bool = True
    caption: str = ""


def figure_caption(metadata):
    """Use stored acquisition metadata when available, never invent calibration."""
    parts = []
    for key in ("sample_id", "name", "scan_name", "timestamp", "samba_git_commit"):
        if metadata.get(key):
            parts.append(f"{key.replace('_', ' ')}: {metadata[key]}")
    if not metadata.get("timestamp"):
        parts.append("view: " + datetime.now().isoformat(timespec="seconds"))
    calibration = metadata.get("bd_calibration")
    if calibration is not None and any(float(v) != 0 for v in calibration):
        parts.append("λ/2 calibration stored with acquisition")
    return " · ".join(parts)


def prepare_export(fig, options):
    from core.plot_interact import render_light_figure, _MulticolorYLabel
    from matplotlib.text import Text
    if options.width_mm <= 0 or options.height_mm <= 0 or options.dpi <= 0:
        raise ValueError("Export dimensions and resolution must be positive")
    output, canvas = render_light_figure(fig)
    output.set_size_inches(options.width_mm / 25.4, options.height_mm / 25.4)
    output.set_layout_engine(None)
    for text in output.findobj(Text):
        text.set_fontsize(options.font_pt)
    for ax in output.axes:
        legend = ax.get_legend()
        if legend is not None:
            legend.set_visible(options.legend)
            legend.set_ncols(1 if options.width_mm < 110 else 3)
        for child in ax.get_children():
            if isinstance(child, _MulticolorYLabel):
                for text in child._texts:
                    text.set_fontsize(options.font_pt)
    caption_height = 0.
    if options.caption.strip():
        wrapped = textwrap.fill(options.caption.strip(), width=max(25, int(options.width_mm * .65)))
        caption_height = min(.35, (.05 + .04 * len(wrapped.splitlines())))
        output.text(.5, .015, wrapped, ha="center", va="bottom",
                    color="#4c4f69", fontsize=max(7, options.font_pt - 1))
    output.tight_layout(rect=(0, caption_height, 1, .97))
    return output, canvas


def ask_export_options(fig, parent=None):
    from PyQt6.QtWidgets import (QDialog, QFormLayout, QComboBox, QDoubleSpinBox,
        QSpinBox, QCheckBox, QPlainTextEdit, QDialogButtonBox)
    dialog = QDialog(parent); dialog.setWindowTitle("Export figure")
    form = QFormLayout(dialog)
    preset = QComboBox()
    preset.addItems(["Double column (180 mm)", "Single column (85 mm)", "Screen size", "Custom"])
    width, height = QDoubleSpinBox(), QDoubleSpinBox()
    for spin, value in ((width, 180), (height, 115)):
        spin.setRange(20, 1000); spin.setDecimals(1); spin.setSuffix(" mm"); spin.setValue(value)
    dpi = QSpinBox(); dpi.setRange(72, 1200); dpi.setValue(300); dpi.setSuffix(" dpi")
    font = QDoubleSpinBox(); font.setRange(6, 24); font.setValue(10); font.setSuffix(" pt")
    legend = QCheckBox("Include legend"); legend.setChecked(True)
    caption = QPlainTextEdit(getattr(fig, "samba_caption", "")); caption.setMaximumHeight(100)
    caption.setPlaceholderText("Optional sample, date, calibration and revision caption")
    for label, widget in [("Preset", preset), ("Width", width), ("Height", height),
                          ("Raster resolution", dpi), ("Text size", font),
                          ("", legend), ("Caption", caption)]:
        form.addRow(label, widget)
    def choose(index):
        sizes = [(180, 115), (85, 70), tuple(fig.get_size_inches() * 25.4)]
        if index < 3:
            width.setValue(sizes[index][0]); height.setValue(sizes[index][1])
    preset.currentIndexChanged.connect(choose)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    form.addRow(buttons); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return ExportOptions(width.value(), height.value(), dpi.value(), font.value(),
                         legend.isChecked(), caption.toPlainText())
