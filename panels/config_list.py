"""
panels/config_list.py — Samba v3
ConfigListPanel — config list with setup tabs.
"""
from typing import Dict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QListWidget, QListWidgetItem, QAbstractItemView, QInputDialog,
    QMessageBox
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QIcon

from config import SETUP_NAMES


class ConfigListPanel(QWidget):
    config_selected      = pyqtSignal(int)      # −1 means "copy current"
    new_config_requested = pyqtSignal()         # blank new config
    config_deleted       = pyqtSignal(int)
    config_renamed       = pyqtSignal(int, str)
    save_requested       = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4); lay.setSpacing(6)

        # ── Config management buttons ─────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(3)
        # Full style override — must include color so it isn't lost against the
        # dark background when a per-widget stylesheet is applied.
        _btn_style = ("QPushButton{background:#313244;border:1px solid #45475a;"
                      "border-radius:4px;padding:0;color:#cdd6f4;font-weight:bold;}"
                      "QPushButton:hover{background:#45475a;}"
                      "QPushButton:pressed{background:#585b70;}")

        def _cfg_btn(icon_name: str, fallback: str, tip: str) -> QPushButton:
            b = QPushButton()
            icon = QIcon.fromTheme(icon_name)
            if not icon.isNull():
                b.setIcon(icon)
            else:
                b.setText(fallback)
            b.setFixedSize(24, 24)
            b.setStyleSheet(_btn_style)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            return b

        new_btn = _cfg_btn("document-new",  "+", "New blank config (Spatial, X-axis)")
        new_btn.clicked.connect(self.new_config)
        btn_row.addWidget(new_btn)

        del_btn_cfg = _cfg_btn("list-remove", "-", "Delete config")
        del_btn_cfg.clicked.connect(self.del_config)
        btn_row.addWidget(del_btn_cfg)

        cpy_btn = _cfg_btn("edit-copy", "C", "Copy current config")
        cpy_btn.clicked.connect(self.copy_config)
        btn_row.addWidget(cpy_btn)

        ren_btn = _cfg_btn("document-edit", "R", "Rename config")
        ren_btn.clicked.connect(self.rename_config)
        btn_row.addWidget(ren_btn)

        sav_btn = _cfg_btn("document-save", "S", "Save config to disk")
        sav_btn.clicked.connect(self.save_requested.emit)
        btn_row.addWidget(sav_btn)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        # ── Setup tabs (tab bar hidden — switched from action bar) ────────────
        self.setup_tabs = QTabWidget()
        self.setup_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.setup_tabs.tabBar().setVisible(False)   # hidden — controlled externally
        self._tab_lists: Dict[str, QListWidget] = {}
        for sn in SETUP_NAMES:
            lw = QListWidget()
            lw.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            lw.itemSelectionChanged.connect(self._on_selection_changed)
            self._tab_lists[sn] = lw
            self.setup_tabs.addTab(lw, sn)
        self.setup_tabs.currentChanged.connect(self._on_tab_changed)
        lay.addWidget(self.setup_tabs, stretch=1)

    def load_setups(self, setups: Dict[str, dict]):
        for sn, data in setups.items():
            lw = self._tab_lists[sn]; lw.clear()
            for cfg in data.get("configs", []):
                lw.addItem(QListWidgetItem(cfg["name"]))
            idx = data.get("active_idx", 0)
            if 0 <= idx < lw.count():
                lw.setCurrentRow(idx)

    def active_setup_name(self) -> str: return SETUP_NAMES[self.setup_tabs.currentIndex()]
    def active_list(self)       -> QListWidget: return self._tab_lists[self.active_setup_name()]

    def _on_tab_changed(self, idx):
        lw = self._tab_lists[SETUP_NAMES[idx]]
        if lw.currentRow() >= 0: self.config_selected.emit(lw.currentRow())

    def _on_selection_changed(self):
        lw = self.active_list()
        if lw.currentRow() >= 0: self.config_selected.emit(lw.currentRow())

    def new_config(self):    self.new_config_requested.emit()
    def add_config(self):    self.config_selected.emit(-1)   # copy (legacy compat)
    def copy_config(self):   self.config_selected.emit(-1)
    def del_config(self):
        lw = self.active_list()
        if lw.count() <= 1:
            return
        it = lw.currentItem()
        name = it.text() if it else "this config"
        ans = QMessageBox.question(
            self, "Delete config",
            f'Delete "{name}"?\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            self.config_deleted.emit(lw.currentRow())
    def rename_config(self):
        lw = self.active_list(); it = lw.currentItem()
        if not it: return
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=it.text())
        if ok and name.strip(): self.config_renamed.emit(lw.currentRow(), name.strip())

    def remove_item(self, idx: int): self.active_list().takeItem(idx)
    def rename_item(self, idx: int, name: str):
        lw = self.active_list()
        if 0 <= idx < lw.count(): lw.item(idx).setText(name)
    def sync_name(self, idx: int, name: str):
        lw = self.active_list()
        if 0 <= idx < lw.count(): lw.item(idx).setText(name)
    def add_item(self, name: str) -> int:
        lw = self.active_list(); lw.addItem(QListWidgetItem(name))
        new_idx = lw.count() - 1; lw.setCurrentRow(new_idx); return new_idx


# ─────────────────────────────────────────────────────────────────────────────
