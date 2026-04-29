"""
panels_cryo.py — Samba Cryo
Cryo-specific UI panels.

CryoHardwarePanel replaces the standard HardwarePanel for the Cryo setup:
  Left:  Keithley 6221 Current Source  (identical to standard)
  Right: AttoDRY cryostat controls     (replaces Field & Relay)

AttoDRY controls:
  • MagneticField (T)  — R/W spinbox
  • Temperature (K)    — R/W spinbox (setpoint)
  • Readbacks: field, sample T, VTI T, magnet T
  • "Monitor" button → opens CryoMonitorDialog (full control panel with toggles)
"""
from typing import Optional

from PyQt6.QtWidgets import (
    QGroupBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QAbstractSpinBox, QWidget
)
import threading
from PyQt6.QtCore import Qt, pyqtSignal

from hardware import get_proxy, fresh_proxy, safe_read, safe_write, is_sim_proxy
from config   import KEITHLEY_RANGES
from panels   import NoScrollComboBox   # reuse existing helper
from keithley_mixin import (
    KeithleyMixin, build_keithley_group, _make_spin,
    set_ok, set_err, set_sim,
)


class CryoHardwarePanel(KeithleyMixin, QGroupBox):
    """Hardware panel for the Cryo setup: Keithley (left) + AttoDRY (right)."""

    # Signals for thread-safe cross-thread UI updates.
    _zi_ok  = pyqtSignal(object, object, object)        # (tc, ord_, st)
    _zi_err = pyqtSignal(str)
    _ks_ok  = pyqtSignal(object, object, object, object) # (amp, frq, cpl, cur)
    _ks_err = pyqtSignal(str)
    _ad_ok  = pyqtSignal(object, object, object, object, object)  # fld,tmp,vti,mgt,err
    _ad_err = pyqtSignal(str)

    def __init__(self, setup_getter, title: str = "Hardware", parent=None):
        super().__init__(title, parent)
        self._setup_getter = setup_getter
        self._cryo_monitor = None   # lazy-created CryoMonitorDialog

        root = QHBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(6, 6, 6, 6)

        # ── Lock-in status ────────────────────────────────────────────────────
        li  = QGroupBox("Lock-in"); lig = QGridLayout(li)
        lig.setSpacing(3); lig.setContentsMargins(6, 6, 6, 6)

        row = 0
        self.zi_dev_lbl = QLabel("—")
        self.zi_dev_lbl.setStyleSheet("color:#6c7086;font-size:9px;")
        lig.addWidget(self.zi_dev_lbl, row, 0, 1, 2); row += 1

        lig.addWidget(QLabel("TC:"), row, 0)
        self.zi_tc_lbl = QLabel("—")
        self.zi_tc_lbl.setStyleSheet("color:#cdd6f4;font-weight:bold;")
        lig.addWidget(self.zi_tc_lbl, row, 1); row += 1

        lig.addWidget(QLabel("Order:"), row, 0)
        self.zi_ord_lbl = QLabel("—")
        self.zi_ord_lbl.setStyleSheet("color:#cdd6f4;font-weight:bold;")
        lig.addWidget(self.zi_ord_lbl, row, 1); row += 1

        lig.addWidget(QLabel("Settling:"), row, 0)
        self.zi_set_lbl = QLabel("—")
        self.zi_set_lbl.setStyleSheet("color:#89dceb;font-weight:bold;")
        lig.addWidget(self.zi_set_lbl, row, 1); row += 1

        self._btn_zi_read = QPushButton("🔄 Read"); self._btn_zi_read.clicked.connect(self._read_lockin)
        lig.addWidget(self._btn_zi_read, row, 0, 1, 2); row += 1

        self.zi_status = QLabel("")
        self.zi_status.setWordWrap(True); self.zi_status.setStyleSheet("font-size:9px;")
        lig.addWidget(self.zi_status, row, 0, 1, 2)
        root.addWidget(li)

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

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        self.btn_monitor = QPushButton("📈 Monitor && Control")
        self.btn_monitor.setToolTip(
            "Open the full AttoDRY monitor and control panel.\n"
            "Includes live plots, toggle controls, and system commands.")
        self.btn_monitor.clicked.connect(self._open_monitor)
        btn_row.addWidget(self.btn_monitor)

        adg.addLayout(btn_row, row, 0, 1, 4); row += 1

        self.ad_status = QLabel("")
        self.ad_status.setWordWrap(True)
        self.ad_status.setStyleSheet("font-size:9px;")
        adg.addWidget(self.ad_status, row, 0, 1, 4)
        root.addWidget(ad)

        # Wire signals → UI-update slots (guaranteed on main thread via Qt dispatch)
        self._zi_ok.connect(self._apply_lockin_readback)
        self._zi_err.connect(lambda e: set_err(self.zi_status, e))
        self._ks_ok.connect(self._apply_keithley_readback)
        self._ks_err.connect(lambda e: set_err(self.ks_status, e))
        self._ad_ok.connect(self._apply_attodry_readback)
        self._ad_err.connect(self._on_attodry_error)

    def _on_attodry_error(self, err: str):
        self._update_dev_labels()
        set_err(self.ad_status, err)

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

    # ── Lock-in ───────────────────────────────────────────────────────────────
    def _read_lockin(self):
        s = self._setup(); dev = s.get("zi_device", "")
        if not dev:
            self.zi_dev_lbl.setText("(not configured)")
            self.zi_status.setText("")
            return
        self.zi_dev_lbl.setText(dev)
        self.zi_status.setText("Reading…")

        tc_attr  = s.get("zi_tc_attr",       "timeconstant")
        ord_attr = s.get("zi_order_attr",    "filterorder")
        st_attr  = s.get("zi_settling_attr", "settlingtime")

        def _do():
            p, conn_err = fresh_proxy(dev)
            if conn_err:
                self._zi_err.emit(conn_err)
                return
            tc,   e1 = safe_read(p, tc_attr)
            ord_, e2 = safe_read(p, ord_attr)
            st,   e3 = safe_read(p, st_attr)
            errs = [e for e in [e1, e2, e3] if e]
            if errs:
                self._zi_err.emit(errs[0][:60])
                return
            self._zi_ok.emit(tc, ord_, st)

        threading.Thread(target=_do, daemon=True).start()

    def _apply_lockin_readback(self, tc, ord_, st):
        if tc is not None:
            if tc >= 1.0:
                self.zi_tc_lbl.setText(f"{tc:.4f} s")
            elif tc >= 1e-3:
                self.zi_tc_lbl.setText(f"{tc*1000:.3f} ms")
            else:
                self.zi_tc_lbl.setText(f"{tc*1e6:.1f} µs")
        else:
            self.zi_tc_lbl.setText("—")

        self.zi_ord_lbl.setText(str(int(ord_)) if ord_ is not None else "—")

        if st is not None:
            st_str = (f"{st:.3f} s" if st >= 1.0
                      else f"{st*1000:.1f} ms" if st >= 1e-3
                      else f"{st:.4f} s")
            self.zi_set_lbl.setText(f"{st_str}  (99%)")
        else:
            self.zi_set_lbl.setText("—")

        set_ok(self.zi_status, "OK")

    def set_scan_running(self, running: bool):
        """Disable/enable the Read buttons and guard refresh() during active scans.

        Concurrent hardware reads (ZI timeconstant, Keithley, AttoDRY) collide with
        the scan runner's state() polling on the same TANGO device and cause
        IMP_LIMIT CORBA errors.  Blocking the Read buttons prevents this.
        """
        self._scan_running = running
        tip = "Cannot read during an active scan" if running else ""
        for btn in (self._btn_zi_read, getattr(self, '_btn_ks_read', None)):
            if btn is not None:
                btn.setEnabled(not running)
                btn.setToolTip(tip)

    # ── Refresh / readback (called by polling timer) ─────────────────────────
    def refresh(self):
        """Read current values from Keithley + AttoDRY. Skipped during active scans."""
        if getattr(self, '_scan_running', False):
            return
        self._read_lockin()
        self._read_keithley()
        self._read_attodry()

    def _read_attodry(self):
        s = self._setup()
        dev   = s.get("attodry_device", "")
        fld_a = s.get("attodry_attr_field_rb", "MagneticField")
        tmp_a = s.get("attodry_attr_temp_rb",  "Temperature")
        vti_a = s.get("attodry_attr_vti_temp", "VtiTemperature")
        mgt_a = s.get("attodry_attr_mag_temp", "MagnetTemperature")
        self.ad_status.setText("Reading…")

        def _do():
            p, err = fresh_proxy(dev)
            if err:
                self._ad_err.emit(err)
                return
            fld, e1 = safe_read(p, fld_a)
            tmp, e2 = safe_read(p, tmp_a)
            vti, _  = safe_read(p, vti_a)
            mgt, _  = safe_read(p, mgt_a)
            self._ad_ok.emit(fld, tmp, vti, mgt, e1 or e2 or None)

        threading.Thread(target=_do, daemon=True).start()

    def _apply_attodry_readback(self, fld, tmp, vti, mgt, err_str):
        self._update_dev_labels()
        if fld is not None: self.field_rb.setText(f"{fld:.4f} T")
        if tmp is not None: self.temp_rb.setText(f"{tmp:.2f} K")
        if vti is not None: self.vti_rb.setText(f"VTI: {vti:.2f} K")
        if mgt is not None: self.mag_t_rb.setText(f"Mag: {mgt:.2f} K")
        if err_str:
            set_err(self.ad_status, err_str[:60])
        else:
            set_ok(self.ad_status, "Connected")

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
