"""
panels_cryo.py — Samba Cryo
Cryo-specific UI panels.

CryoHardwarePanel replaces the standard HardwarePanel for the Cryo setup:
  Left:  Keithley 6221 Current Source  (identical to standard)
  Right: AttoDRY cryostat controls     (replaces Field & Relay)

AttoDRY controls:
  • MagneticField (T)  — R/W spinbox
  • Temperature (K)    — R/W spinbox (setpoint)
  • toggleMagneticFieldControl / toggleFulltemperatureControl / togglePersistentMode
  • Readbacks: field, sample T, VTI T, magnet T
  • "Monitor" button → opens CryoMonitorDialog
"""
from typing import Optional

from PyQt6.QtWidgets import (
    QGroupBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QAbstractSpinBox, QCheckBox, QWidget
)
from PyQt6.QtCore import Qt

from hardware import get_proxy, fresh_proxy, safe_read, safe_write, is_sim_proxy
from config   import KEITHLEY_RANGES
from panels   import NoScrollComboBox   # reuse existing helper
from keithley_mixin import (
    KeithleyMixin, build_keithley_group, _make_spin,
    set_ok, set_err, set_sim,
)


class CryoHardwarePanel(KeithleyMixin, QGroupBox):
    """Hardware panel for the Cryo setup: Keithley (left) + AttoDRY (right)."""

    def __init__(self, setup_getter, title: str = "Hardware", parent=None):
        super().__init__(title, parent)
        self._setup_getter = setup_getter
        self._cryo_monitor = None   # lazy-created CryoMonitorDialog

        root = QHBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(6, 6, 6, 6)

        # ── Left: Current Source (Keithley 6221) — built by shared helper ─────
        cs = build_keithley_group(self)
        root.addWidget(cs)

        # ── Right: AttoDRY Cryostat ──────────────────────────────────────────
        ad = QGroupBox("AttoDRY Cryostat")
        adg = QGridLayout(ad)
        adg.setSpacing(3)
        adg.setContentsMargins(6, 6, 6, 6)

        row = 0
        self.ad_dev_lbl = QLabel("—")
        self.ad_dev_lbl.setStyleSheet("color:#6c7086;font-size:9px;")
        adg.addWidget(self.ad_dev_lbl, row, 0, 1, 4); row += 1

        # Field setpoint
        adg.addWidget(QLabel("Field (T):"), row, 0)
        self.field_sp = _make_spin(-9.0, 9.0, 4, " T", 110, self._write_field)
        adg.addWidget(self.field_sp, row, 1)
        self.field_rb = QLabel("— T")
        self.field_rb.setStyleSheet(
            "color:#89b4fa;font-weight:bold;font-size:13px;"
            "font-family:'Courier New',monospace;")
        adg.addWidget(self.field_rb, row, 2, 1, 2); row += 1

        # Temperature setpoint
        adg.addWidget(QLabel("T set (K):"), row, 0)
        self.temp_sp = _make_spin(0.0, 400.0, 2, " K", 110, self._write_temperature)
        adg.addWidget(self.temp_sp, row, 1)
        self.temp_rb = QLabel("— K")
        self.temp_rb.setStyleSheet(
            "color:#a6e3a1;font-weight:bold;font-size:13px;"
            "font-family:'Courier New',monospace;")
        adg.addWidget(self.temp_rb, row, 2, 1, 2); row += 1

        # Extra temperature readbacks (compact)
        self.vti_rb    = QLabel("VTI: —")
        self.mag_t_rb  = QLabel("Mag: —")
        for lbl in (self.vti_rb, self.mag_t_rb):
            lbl.setStyleSheet("color:#6c7086;font-size:10px;font-family:'Courier New',monospace;")
        adg.addWidget(self.vti_rb,   row, 0, 1, 2)
        adg.addWidget(self.mag_t_rb, row, 2, 1, 2); row += 1

        # Control toggle buttons
        _BTN_STYLE = (
            "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
            "border-radius:4px;padding:3px 8px;}"
            "QPushButton:checked{background:#a6e3a1;color:#1e1e2e;font-weight:bold;"
            "border:1px solid #a6e3a1;}"
            "QPushButton:hover{border:1px solid #89b4fa;}"
        )
        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        self.btn_mag_ctrl = QPushButton("Mag Ctrl")
        self.btn_mag_ctrl.setToolTip(
            "Toggle active magnetic field control on the superconducting magnet.\n"
            "ON: magnet actively ramps to the field setpoint.\n"
            "OFF: magnet stays at current field without active regulation.")
        self.btn_mag_ctrl.setCheckable(True)
        self.btn_mag_ctrl.setStyleSheet(_BTN_STYLE)
        self.btn_mag_ctrl.clicked.connect(self._toggle_mag_ctrl)
        btn_row.addWidget(self.btn_mag_ctrl)

        self.btn_temp_ctrl = QPushButton("Temp Ctrl")
        self.btn_temp_ctrl.setToolTip(
            "Toggle full temperature control (PID controller).\n"
            "ON: temperature is actively regulated to the setpoint.\n"
            "OFF: heaters are off, temperature drifts freely.")
        self.btn_temp_ctrl.setCheckable(True)
        self.btn_temp_ctrl.setStyleSheet(_BTN_STYLE)
        self.btn_temp_ctrl.clicked.connect(self._toggle_temp_ctrl)
        btn_row.addWidget(self.btn_temp_ctrl)

        self.btn_persist = QPushButton("Persistent Mode")
        self.btn_persist.setToolTip(
            "Toggle persistent mode for the superconducting magnet.\n"
            "ON (persistent): magnet holds field without current — lower heat load.\n"
            "OFF (driven): magnet is actively driven; required before changing the field.")
        self.btn_persist.setCheckable(True)
        self.btn_persist.setStyleSheet(_BTN_STYLE)
        self.btn_persist.clicked.connect(self._toggle_persistent)
        btn_row.addWidget(self.btn_persist)

        self.btn_monitor = QPushButton("📈 Monitor")
        self.btn_monitor.setToolTip(
            "Open the Cryo Monitor window — real-time rolling plots (60 s window)\n"
            "showing temperatures (K), pressures (mbar), and heater powers (W).")
        self.btn_monitor.clicked.connect(self._open_monitor)
        btn_row.addWidget(self.btn_monitor)

        adg.addLayout(btn_row, row, 0, 1, 4); row += 1

        self.ad_status = QLabel("")
        self.ad_status.setWordWrap(True)
        self.ad_status.setStyleSheet("font-size:9px;")
        adg.addWidget(self.ad_status, row, 0, 1, 4)
        root.addWidget(ad)

    def _setup(self):
        return self._setup_getter()

    def _update_dev_labels(self):
        s = self._setup()
        self.ks_dev_lbl.setText(s.get("keithley_device", "—") or "—")
        self.ad_dev_lbl.setText(s.get("attodry_device", "—") or "—")

    # Keithley methods (_read_keithley, _write_range, _write_amplitude,
    # _write_compliance, _write_frequency) are inherited from KeithleyMixin.

    # ── AttoDRY methods ──────────────────────────────────────────────────────
    def _ad_proxy(self):
        dev = self._setup().get("attodry_device", "")
        return fresh_proxy(dev)

    def _write_field(self):
        p, err = self._ad_proxy(); self._update_dev_labels()
        if err: set_err(self.ad_status, err); return
        if is_sim_proxy(p): set_sim(self.ad_status); return
        attr = self._setup().get("attodry_attr_field_set", "MagneticField")
        val  = self.field_sp.value()
        e = safe_write(p, attr, val)
        if e: set_err(self.ad_status, e[:60])
        else: set_ok(self.ad_status, f"Field → {val:.4f} T")

    def _write_temperature(self):
        p, err = self._ad_proxy(); self._update_dev_labels()
        if err: set_err(self.ad_status, err); return
        if is_sim_proxy(p): set_sim(self.ad_status); return
        attr = self._setup().get("attodry_attr_temp_set", "Temperature")
        val  = self.temp_sp.value()
        e = safe_write(p, attr, val)
        if e: set_err(self.ad_status, e[:60])
        else: set_ok(self.ad_status, f"T setpoint → {val:.2f} K")

    def _write_bool_ctrl(self, attr_key: str, cmd_key: str,
                         btn, label: str):
        """Write a boolean control attribute; fall back to toggle command on error."""
        p, err = self._ad_proxy()
        if err: set_err(self.ad_status, err); btn.setChecked(not btn.isChecked()); return
        if is_sim_proxy(p): set_sim(self.ad_status); return
        s   = self._setup()
        val = btn.isChecked()
        e   = safe_write(p, s.get(attr_key, ""), val)
        if e:
            # Fall back to toggle command
            cmd = s.get(cmd_key, "")
            if cmd:
                try:
                    p.command_inout(cmd)
                    set_ok(self.ad_status, f"Toggled {cmd}")
                    return
                except Exception as exc:
                    set_err(self.ad_status, str(exc)[:60])
            else:
                set_err(self.ad_status, e[:60])
            btn.setChecked(not val)   # revert button on total failure
        else:
            set_ok(self.ad_status, f"{label} → {'ON' if val else 'OFF'}")

    def _toggle_mag_ctrl(self):
        self._write_bool_ctrl("attodry_attr_mag_ctrl", "attodry_cmd_mag_ctrl",
                              self.btn_mag_ctrl, "Mag Ctrl")

    def _toggle_temp_ctrl(self):
        self._write_bool_ctrl("attodry_attr_temp_ctrl", "attodry_cmd_temp_ctrl",
                              self.btn_temp_ctrl, "Temp Ctrl")

    def _toggle_persistent(self):
        self._write_bool_ctrl("attodry_attr_persist", "attodry_cmd_persist",
                              self.btn_persist, "Persistent Mode")

    def _open_monitor(self):
        from cryo_monitor import CryoMonitorDialog
        dev = self._setup().get("attodry_device", "hpp-N42/attoDRY/attoDRY")
        # Recreate the dialog if the device path has changed since last open
        if self._cryo_monitor is not None and self._cryo_monitor._dev != dev:
            self._cryo_monitor.close()
            self._cryo_monitor = None
        if self._cryo_monitor is None:
            self._cryo_monitor = CryoMonitorDialog(
                attodry_device=dev,
                setup_getter=self._setup,
                parent=self.window(),
            )
        self._cryo_monitor.show()
        self._cryo_monitor.raise_()

    # ── Refresh / readback (called by polling timer) ─────────────────────────
    def refresh(self):
        """Read current values from Keithley + AttoDRY."""
        self._read_keithley()
        self._read_attodry()

    def _read_attodry(self):
        p, err = self._ad_proxy(); self._update_dev_labels()
        if err:
            set_err(self.ad_status, err); return

        s = self._setup()
        fld, e1 = safe_read(p, s.get("attodry_attr_field_rb",  "MagneticField"))
        tmp, e2 = safe_read(p, s.get("attodry_attr_temp_rb",   "Temperature"))
        vti, _  = safe_read(p, s.get("attodry_attr_vti_temp",  "VtiTemperature"))
        mgt, _  = safe_read(p, s.get("attodry_attr_mag_temp",  "MagnetTemperature"))

        if fld is not None:
            self.field_rb.setText(f"{fld:.4f} T")
        if tmp is not None:
            self.temp_rb.setText(f"{tmp:.2f} K")
        if vti is not None:
            self.vti_rb.setText(f"VTI: {vti:.2f} K")
        if mgt is not None:
            self.mag_t_rb.setText(f"Mag: {mgt:.2f} K")

        # Sync toggle button states from actual device boolean attrs
        for attr_key, btn in [
            ("attodry_attr_mag_ctrl",  self.btn_mag_ctrl),
            ("attodry_attr_temp_ctrl", self.btn_temp_ctrl),
            ("attodry_attr_persist",   self.btn_persist),
        ]:
            attr = s.get(attr_key, "")
            if attr:
                val, _ = safe_read(p, attr)
                if val is not None:
                    btn.setChecked(bool(val))

        if not e1 and not e2:
            set_ok(self.ad_status, "Connected")
        elif e1:
            set_err(self.ad_status, e1[:60])

    def update_field_readback(self, val_T: Optional[float]) -> None:
        """Called by the 500ms polling timer from samba_cryo.py."""
        if val_T is not None:
            self.field_rb.setText(f"{val_T:.4f} T")

    def update_cryo_readbacks(self, temp: Optional[float],
                              vti: Optional[float],
                              mag_t: Optional[float]) -> None:
        """Update temperature readbacks from polling timer."""
        if temp is not None:
            self.temp_rb.setText(f"{temp:.2f} K")
        if vti is not None:
            self.vti_rb.setText(f"VTI: {vti:.2f} K")
        if mag_t is not None:
            self.mag_t_rb.setText(f"Mag: {mag_t:.2f} K")
