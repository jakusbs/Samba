"""
scan.py — Samba v3
Scan execution logic: ScanRunner (pure Python, no Qt),
ScanWorker (single scan QThread), ScanlistWorker (N-scan list QThread).

v3.1 — Incremental HDF5 saving:
  • File is created and opened at scan start (datasets pre-filled with NaN).
  • Each data point is written to disk immediately after acquisition.
  • Periodic flush (every Y row, or every FLUSH_INTERVAL points for 1D).
  • A 'scan_status' root attribute tracks progress: "running" → "completed" | "aborted".
  • If the application crashes, the HDF5 file contains all data collected up to that point.

v3.2 — Hardware-gated synchronized acquisition:
  • At scan start, the GUI integration time is written to each sensor device's
    integration-time attribute (e.g. ZI "integrationtime", Beckhoff "integrationtime").
  • Per point: move actuator → settle → fire "Start" on ALL sensor devices
    → poll device states until none are RUNNING → batch read_attributes per device.
  • Sensors on the same device (e.g. ZI2 x1,y1,x2,y2) are read in a single
    read_attributes() call, eliminating inter-channel timing skew.
  • Devices without a trigger_cmd (e.g. simple analog inputs) are read after
    the triggered devices complete, ensuring the actuator has settled.

v3.3 — Async trigger + corrected timestamps:
  • Triggers fire via command_inout_asynch() — all Start commands are dispatched
    in a tight loop (~100 µs jitter vs ~5-10 ms with threads), so all devices
    begin integrating near-simultaneously.
  • Timestamp per point = t_trigger + int_time/2 (center of integration window),
    physically meaningful and identical for all channels on the same point.
  • 10 ms guard delay between state→done and readout prevents reading a stale
    output buffer on devices that report "not RUNNING" before registers update.
"""
import copy, os, re, time, traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import h5py
from datetime import datetime
from typing import Dict, List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

try:
    import tango
    TANGO_AVAILABLE = True
except ImportError:
    TANGO_AVAILABLE = False

from config import MAX_RETRIES, RETRY_DELAY, X_TIME
from hardware import get_proxy, fresh_proxy, safe_read, safe_write, demagnetize_magnet

# h5py on Python 3.13 raises TypeError when writing plain Python strings as
# variable-length UTF-8 attrs.  Use explicit string_dtype throughout.
import h5py as _h5py
_H5STR = _h5py.string_dtype()

def _wsa(target, key, val):
    """Write a string HDF5 attribute, compatible with all h5py/Python versions."""
    target.attrs.create(key, data=str(val), dtype=_H5STR)


def _make_filename(cfg: dict) -> str:
    """Return HDF5 filename: HHMMSS_SCANTYPE_SampleID_ConfigName.h5

    sample_id is omitted when empty.  Special characters are replaced with '_'.
    Temperature sweeps use TEMP_SWEEP instead of FIELD.
    """
    ts = datetime.now().strftime("%H%M%S")
    if cfg.get("_is_temp_sweep"):
        scan_type = "TEMP_SWEEP"
    elif (cfg.get("scan_type", "SPATIAL") == "SPATIAL"
          and not cfg.get("scan_x", True) and not cfg.get("scan_y", False)):
        scan_type = "TIME"
    else:
        scan_type = cfg.get("scan_type", "SPATIAL")
    sample_raw = cfg.get("sample_id", "").strip()
    sample     = re.sub(r"[^\w-]", "_", sample_raw).strip("_")
    name       = cfg.get("name", "scan")
    parts = [ts, scan_type]
    if sample:
        parts.append(sample)
    parts.append(name)
    return "_".join(parts) + ".h5"


def _write_hw_metadata(meta, cfg: dict) -> None:
    """Write hardware snapshot and temperature-sweep keys into an HDF5 group."""
    # hw_* keys: keithley, lock-in, relay, field, stage positions, temperature
    _HW_KEYS = [
        "hw_keithley_amplitude_mA", "hw_keithley_frequency_Hz",
        "hw_keithley_range",        "hw_keithley_compliance_V",
        "hw_zi_tc_s",               "hw_zi_order",
        "hw_zi_settling_s",         "hw_relay_state",
        "hw_field_mT",              "hw_act1_pos",
        "hw_act2_pos",              "hw_temperature_K",
    ]
    for k in _HW_KEYS:
        v = cfg.get(k)
        if v is not None:
            if isinstance(v, str):
                _wsa(meta, k, v)
            else:
                try:
                    meta.attrs[k] = v
                except Exception:
                    _wsa(meta, k, str(v))
    _TEMP_KEYS = {
        "_is_temp_sweep":      "is_temp_sweep",
        "_temp_sweep_start_K": "temp_sweep_start_K",
        "_temp_sweep_stop_K":  "temp_sweep_stop_K",
        "_temp_sweep_step_K":  "temp_sweep_step_K",
    }
    for src, dst in _TEMP_KEYS.items():
        v = cfg.get(src)
        if v is not None and v != "":
            if isinstance(v, str):
                _wsa(meta, dst, v)
            else:
                try:
                    meta.attrs[dst] = v
                except Exception:
                    _wsa(meta, dst, str(v))


# How often to flush to disk for 1D scans (every N points)
FLUSH_INTERVAL = 10
# How many consecutive sensor-read failures before auto-pausing
AUTO_PAUSE_THRESHOLD = 5
# Guard delay (ms) between device state leaving RUNNING and readout,
# to let output registers settle with final averaged values
READOUT_GUARD_MS = 10
# Phase-A timeout (ms): how long to wait for triggered devices to enter
# RUNNING after the async Start dispatch.  Normal ZI2 thread startup is
# <10 ms; 200 ms is a generous upper bound before we give up and proceed.
TRIGGER_START_GUARD_MS = 200
# Minimum lock-in filter settling wait (ms).  Prevents sub-threshold TC
# settings (e.g. TC=1.5 ms → 7 ms settling) from skipping meaningful
# settling altogether, which can produce noisy or inconsistent readings.
MIN_LOCKIN_SETTLING_MS = 50


# ─────────────────────────────────────────────────────────────────────────────
# ScanRunner — pure scan logic, no Qt dependencies
# ─────────────────────────────────────────────────────────────────────────────
class ScanRunner:
    def __init__(self, cfg: dict, setup: dict):
        self.cfg   = cfg
        self.setup = setup
        self._abort  = False
        self._paused = False

    def abort(self):  self._abort  = True
    def pause(self):  self._paused = True
    def resume(self): self._paused = False

    def run(self, cbs: dict) -> Optional[str]:
        """
        Execute the scan.  cbs is a dict of callbacks:
          point(ix, iy, x_actual, vals_dict)
          progress(count, total)
          status(msg_str)
          log(msg_str)
        Returns the HDF5 filename on success, None if aborted before any data.
        """
        cfg, setup = self.cfg, self.setup
        st = cbs.get('status',   lambda *a: None)
        lg = cbs.get('log',      lambda *a: None)
        pt = cbs.get('point',    lambda *a: None)
        pg = cbs.get('progress', lambda *a: None)

        scan_type = cfg.get("scan_type", "SPATIAL")
        scan_x    = cfg.get("scan_x",   True)
        scan_y    = cfg.get("scan_y",   False)

        # DC Hysteresis is handled entirely by its own method — exit early.
        if scan_type == "DC_HYST":
            return self._run_dc_hyst(cfg, setup, cbs)

        active    = [s for s in cfg["sensors"] if s["enabled"]]
        if not active:
            st("No sensors enabled."); return None

        devp: Dict[str, object] = {}
        # Collect unique device paths in order of first appearance
        _seen: set = set()
        unique_devs = [s["device"] for s in active
                       if s["device"] and not (_seen.__contains__(s["device"])
                                               or _seen.add(s["device"]))]
        # Connect to all devices concurrently — startup latency doesn't affect
        # trigger synchronization (triggers fire via a tight asynch loop later).
        with ThreadPoolExecutor(max_workers=max(len(unique_devs), 1)) as _ex:
            _fut_to_dev = {_ex.submit(fresh_proxy, dp): dp for dp in unique_devs}
            for _fut in as_completed(_fut_to_dev):
                dp = _fut_to_dev[_fut]
                fp, fp_err = _fut.result()
                devp[dp] = fp
                if fp_err:
                    lg(f"⚠ {dp}: using sim — {fp_err}")

        mag_cur_attr = setup.get("magnet_current_attr", "current_polar")
        mag_fld_attr = setup.get("magnet_field_attr",   "field_polar_corr")

        # ── Build scan axes ───────────────────────────────────────────────────
        if scan_type == "FIELD":
            segs = cfg.get("field_segments")
            if segs and len(segs) > 0:
                parts = [np.linspace(float(s[0]), float(s[1]), int(s[2])) for s in segs]
                x_plan = np.concatenate(parts)
            else:
                x_plan = np.linspace(cfg["field_start_A"], cfg["field_stop_A"],
                                     int(cfg["field_npts"]))
            y_plan = np.array([0.0])
            x_lbl  = cfg.get("field_x_label", "Field")
            x_unit = cfg.get("field_x_unit",  "T")
            act1_p, act2_p = None, None
            _field_dev = cfg.get("field_device", "") or setup.get("magnet_device", "")
            mag_p = get_proxy(_field_dev)
            # Per-config current/write attr override (falls back to setup)
            mag_cur_attr = (cfg.get("field_current_attr", "")
                            or setup.get("magnet_current_attr", "current_polar"))
            mag_fld_attr = setup.get("magnet_field_attr", "field_polar_corr")
            hdf_scan = "FIELD"
            fast_attr = None
        elif scan_type == "SPATIAL" and scan_x and scan_y:
            x_plan = np.linspace(cfg["act1_start"], cfg["act1_stop"], int(cfg["act1_npts"]))
            y_plan = np.linspace(cfg["act2_start"], cfg["act2_stop"], int(cfg["act2_npts"]))
            x_lbl, x_unit = cfg["act1_label"], cfg["act1_unit"]
            act1_p = get_proxy(cfg["act1_device"])
            act2_p = get_proxy(cfg["act2_device"])
            mag_p  = None
            hdf_scan = "SPATIAL_XY"
            fast_attr = cfg.get("act1_attr", "x")
        elif scan_type == "SPATIAL" and scan_y and not scan_x:
            x_plan = np.linspace(cfg["act2_start"], cfg["act2_stop"], int(cfg["act2_npts"]))
            y_plan = np.array([0.0])
            x_lbl, x_unit = cfg["act2_label"], cfg["act2_unit"]
            act1_p = get_proxy(cfg["act2_device"])
            act2_p, mag_p = None, None
            hdf_scan = "SPATIAL_Y"
            fast_attr = cfg.get("act2_attr", "y")
        elif scan_type == "SPATIAL" and not scan_x and not scan_y:
            n_pts  = int(cfg.get("act1_npts", 101))
            dt     = cfg.get("integration_time", 0.1)
            x_plan = np.arange(n_pts) * dt
            y_plan = np.array([0.0])
            x_lbl, x_unit = "Time", "s"
            act1_p, act2_p, mag_p = None, None, None
            hdf_scan = "TIME"
            fast_attr = None
        else:
            x_plan = np.linspace(cfg["act1_start"], cfg["act1_stop"], int(cfg["act1_npts"]))
            y_plan = np.array([0.0])
            x_lbl, x_unit = cfg["act1_label"], cfg["act1_unit"]
            act1_p = get_proxy(cfg["act1_device"])
            act2_p, mag_p = None, None
            hdf_scan = "SPATIAL_X"
            fast_attr = cfg.get("act1_attr", "x")

        n_x, n_y  = len(x_plan), len(y_plan)
        total     = n_x * n_y
        # In-memory buffers (still used for live plotting callbacks)
        data      = {s["label"]: np.full((n_y, n_x), np.nan) for s in active}
        x_actual  = np.zeros((n_y, n_x))
        t_actual  = np.zeros((n_y, n_x))

        base    = os.path.expanduser(setup.get("save_dir", "~/moke_data"))
        day_dir = os.path.join(base, datetime.now().strftime("%Y%m%d"))
        os.makedirs(day_dir, exist_ok=True)
        filename = os.path.join(day_dir, _make_filename(cfg))

        # ── Open HDF5 immediately — crash-safe from the first point ───────────
        hfile = self._open_hdf5(filename, x_plan, y_plan, active,
                                x_lbl, x_unit, hdf_scan, cfg)
        if hfile is None:
            err = getattr(self, '_hdf5_error', 'unknown error')
            st(f"⚠ Could not create {filename}: {err}"); return None

        st(f"Starting {hdf_scan}: {n_x}×{n_y} = {total} pts → {filename}")
        count = 0; t0 = time.time()
        consecutive_errors = 0

        # ── Configure integration time on all sensor devices ──────────────
        # Use fresh_proxy (not cached) to ensure a real connection.
        int_time = cfg["integration_time"]
        lg(f"── Integration time: {int_time:.4g} s — writing to devices ──")
        configured_devs = set()
        for s in active:
            dev_path = s["device"]
            it_attr  = s.get("integ_time_attr", "").strip()
            if not dev_path:
                continue
            if not it_attr:
                if dev_path not in configured_devs:
                    lg(f"  {dev_path}: no integ_time_attr configured — skipping")
                    configured_devs.add(dev_path)
                continue
            if dev_path in configured_devs:
                continue
            try:
                fp, fp_err = fresh_proxy(dev_path)
                if fp_err:
                    lg(f"  ⚠ {dev_path}: cannot connect — {fp_err}")
                    configured_devs.add(dev_path)
                    continue
                devp[dev_path] = fp
                err = safe_write(fp, it_attr, int_time)
                if err:
                    lg(f"  ⚠ {dev_path}/{it_attr}: write failed — {err}")
                    configured_devs.add(dev_path)
                    continue
                readback, rb_err = safe_read(fp, it_attr)
                if rb_err:
                    lg(f"  ✓ {dev_path}/{it_attr} ← {int_time:.4g} s (write OK, read-back failed: {rb_err})")
                elif readback is not None and abs(readback - int_time) > 1e-6:
                    lg(f"  ⚠ {dev_path}/{it_attr} ← {int_time:.4g} s but read-back = {readback:.4g} s")
                else:
                    lg(f"  ✓ {dev_path}/{it_attr} = {readback:.4g} s (verified)")
                configured_devs.add(dev_path)
            except Exception as e:
                lg(f"  ⚠ {dev_path}/{it_attr}: FAILED — {e}")
                configured_devs.add(dev_path)
        if not configured_devs:
            lg("  (no devices to configure)")
        lg(f"── Integration time configured on {len(configured_devs)} device(s) ──")

        # ── Read lock-in settling times from ZI devices ───────────────────
        lockin_settling: Dict[str, float] = {}
        lg("── Lock-in settling times: ──")
        for s in active:
            dev_path     = s["device"]
            settling_attr = s.get("settling_attr", "").strip()
            if not settling_attr or not dev_path or dev_path in lockin_settling:
                continue
            fp = devp.get(dev_path)
            if fp is None:
                lockin_settling[dev_path] = 0.0
                continue
            st_val, st_err = safe_read(fp, settling_attr)
            tc_val,  _     = safe_read(fp, "timeconstant")
            ord_val, _     = safe_read(fp, "filterorder")
            settling = float(st_val) if st_val is not None else 0.0
            lockin_settling[dev_path] = settling
            tc_s  = f"{tc_val:.4f}s"  if tc_val  is not None else "?"
            ord_s = str(int(ord_val)) if ord_val  is not None else "?"
            msg   = f"  {dev_path}: TC={tc_s}, order={ord_s}, settling={settling:.3f}s"
            if st_err:
                msg += f"  ⚠ ({st_err})"
            lg(msg)
        max_lockin_settling = max(lockin_settling.values()) if lockin_settling else 0.0
        if not lockin_settling:
            lg("  (no devices with settling_attr configured)")
        else:
            _floor = MIN_LOCKIN_SETTLING_MS / 1000.0
            if 0 < max_lockin_settling < _floor:
                lg(f"  ⚠ Max lock-in settling {max_lockin_settling*1000:.1f} ms is below "
                   f"the {MIN_LOCKIN_SETTLING_MS} ms floor — clamping up")
                max_lockin_settling = _floor
            if max_lockin_settling > 0:
                lg(f"── Max lock-in settling wait: {max_lockin_settling:.3f} s per point ──")
        try:
            hfile["metadata"].attrs["lockin_settling_time"] = max_lockin_settling
        except Exception:
            pass

        # ── Group sensors by device for batch read_attributes ─────────────
        dev_sensors: Dict[str, List[dict]] = defaultdict(list)
        for s in active:
            dev_sensors[s["device"]].append(s)

        # ── Collect unique devices that have a trigger command ────────────
        trigger_devs: Dict[str, str] = {}   # device_path → trigger_cmd
        for s in active:
            tcmd = s.get("trigger_cmd", "").strip()
            if tcmd and s["device"] and s["device"] not in trigger_devs:
                trigger_devs[s["device"]] = tcmd
        if trigger_devs:
            lg(f"── Triggered devices ({len(trigger_devs)}): ──")
            for dp, tc in trigger_devs.items():
                lg(f"  {dp} → command_inout('{tc}')")
            # ThreadZI's daq.poll() can block for up to (int_time + 5 s) before
            # returning.  The default 3 s TANGO client timeout fires first,
            # producing spurious TRANSIENT_CallTimedout on state() queries.
            # Set the proxy timeout to outlast the worst-case daq.poll() run.
            _zi_timeout_ms = max(15_000, int((int_time + 7.5) * 1000))
            for dp in trigger_devs:
                fp = devp.get(dp)
                if fp is not None and hasattr(fp, 'set_timeout_millis'):
                    try:
                        fp.set_timeout_millis(_zi_timeout_ms)
                        lg(f"  {dp}: state-poll timeout → {_zi_timeout_ms} ms")
                    except Exception:
                        pass
        else:
            lg("── No triggered devices — using timed integration (sleep) ──")

        # States that mean "still integrating"
        _RUNNING = {tango.DevState.RUNNING} if TANGO_AVAILABLE else set()
        _RUNNING.add("RUNNING")   # SimProxy returns a string

        # ── RTV40 pulse-width sync (TR-MOKE) ─────────────────────────────────
        # width_i = base_width − (delay_i − start_delay)
        # Keeps the END of the RTV40 pulse at a fixed time as the DG645 delay sweeps.
        # As delay increases (pulse start moves right), width decreases by the same amount.
        rtv40_p       = None
        rtv40_base_ns = None
        rtv40_ref_s   = None
        if (hdf_scan == "SPATIAL_X" and
                cfg.get("rtv40_sync_enabled") and
                cfg.get("rtv40_device", "").strip()):
            _rtv40_dev = cfg["rtv40_device"].strip()
            _rp, _rp_err = fresh_proxy(_rtv40_dev)
            if _rp_err:
                lg(f"⚠ RTV40 {_rtv40_dev}: {_rp_err} — sync disabled")
            else:
                rtv40_p       = _rp
                rtv40_base_ns = float(cfg.get("rtv40_base_width_ns", 1.0))
                rtv40_ref_s   = float(cfg.get("act1_start", 0.0))
                lg(f"── RTV40 sync: base={rtv40_base_ns:.3f} ns  "
                   f"ref={rtv40_ref_s * 1e9:.3f} ns ──")

        pt_retrace = cbs.get('point_retrace', lambda *a: None)
        self._retrace_filename = None   # set below if interleaved mode runs

        try:
          if cfg.get("_interleaved_2d") and hdf_scan == "SPATIAL_XY":
            # ── Interleaved 2D: per-row (or per-column) trace + retrace ─────
            interleave_axis = cfg.get("_interleave_axis", "x")
            retrace_cfg      = copy.deepcopy(cfg)
            retrace_cfg["name"] = cfg.get("_retrace_name",
                                          cfg["name"] + "_retrace")
            retrace_filename = os.path.join(day_dir, _make_filename(retrace_cfg))
            hfile2 = self._open_hdf5(retrace_filename, x_plan, y_plan, active,
                                     x_lbl, x_unit, hdf_scan, retrace_cfg)
            if hfile2 is None:
                err2 = getattr(self, '_hdf5_error', 'unknown')
                st(f"⚠ Could not create retrace file: {err2}")
                hfile2 = None
            else:
                self._retrace_filename = retrace_filename

            count_r   = 0
            total_all = total * 2   # trace + retrace combined progress
            move_t    = cfg["move_timeout"]
            settle    = cfg["settle_time"]

            try:
              if interleave_axis == "x":
                # Outer loop = Y (slow axis), inner = X+ (trace) then X- (retrace)
                retrace_x = x_plan[::-1]
                for iy, y_pos in enumerate(y_plan):
                    if self._abort: break
                    st(f"Moving {cfg['act2_label']} → {y_pos:.4g}")
                    self._move(act2_p, cfg["act2_attr"], y_pos, move_t, log=lg)

                    # ── Trace sweep (x+) ─────────────────────────────────────
                    for ix, x_pos in enumerate(x_plan):
                        if self._abort: break
                        while self._paused:
                            time.sleep(0.05)
                            if self._abort: break
                        x_read = self._move(act1_p, fast_attr, x_pos, move_t, log=lg)
                        if settle > 0: time.sleep(settle)
                        if max_lockin_settling > 0: time.sleep(max_lockin_settling)
                        vals, t_elapsed = self._trigger_poll_read(
                            devp, dev_sensors, trigger_devs, int_time,
                            t0, _RUNNING, move_t, lg)
                        x_actual[iy, ix] = x_read; t_actual[iy, ix] = t_elapsed
                        for s in active: data[s["label"]][iy, ix] = vals.get(s["label"], np.nan)
                        self._write_point(hfile, iy, ix, x_read, t_elapsed, vals, active, hdf_scan)
                        count += 1
                        pt(ix, iy, x_read, vals); pg(count + count_r, total_all)
                        st(f"[trace {count}/{total}]  x={x_read:.4g}{x_unit}")

                    # ── Retrace sweep (x-) ───────────────────────────────────
                    for j, x_pos in enumerate(retrace_x):
                        ix = n_x - 1 - j   # spatial index same as trace
                        if self._abort: break
                        while self._paused:
                            time.sleep(0.05)
                            if self._abort: break
                        x_read = self._move(act1_p, fast_attr, x_pos, move_t, log=lg)
                        if settle > 0: time.sleep(settle)
                        if max_lockin_settling > 0: time.sleep(max_lockin_settling)
                        vals, t_elapsed = self._trigger_poll_read(
                            devp, dev_sensors, trigger_devs, int_time,
                            t0, _RUNNING, move_t, lg)
                        if hfile2 is not None:
                            self._write_point(hfile2, iy, ix, x_read, t_elapsed, vals, active, hdf_scan)
                        count_r += 1
                        pt_retrace(ix, iy, x_read, vals); pg(count + count_r, total_all)
                        st(f"[retrace {count_r}/{total}]  x={x_read:.4g}{x_unit}")

                    try: hfile.flush()
                    except Exception: pass
                    if hfile2 is not None:
                        try: hfile2.flush()
                        except Exception: pass

              else:
                # interleave_axis == "y"
                # Outer loop = X (slow axis), inner = Y+ (trace) then Y- (retrace)
                retrace_y = y_plan[::-1]
                for ix, x_pos in enumerate(x_plan):
                    if self._abort: break
                    x_read = self._move(act1_p, fast_attr, x_pos, move_t, log=lg)
                    if settle > 0: time.sleep(settle)
                    st(f"Moving {cfg['act1_label']} → {x_pos:.4g}")

                    # ── Trace sweep (y+) ─────────────────────────────────────
                    for iy, y_pos in enumerate(y_plan):
                        if self._abort: break
                        while self._paused:
                            time.sleep(0.05)
                            if self._abort: break
                        self._move(act2_p, cfg["act2_attr"], y_pos, move_t, log=lg)
                        if settle > 0: time.sleep(settle)
                        if max_lockin_settling > 0: time.sleep(max_lockin_settling)
                        vals, t_elapsed = self._trigger_poll_read(
                            devp, dev_sensors, trigger_devs, int_time,
                            t0, _RUNNING, move_t, lg)
                        x_actual[iy, ix] = x_read; t_actual[iy, ix] = t_elapsed
                        for s in active: data[s["label"]][iy, ix] = vals.get(s["label"], np.nan)
                        self._write_point(hfile, iy, ix, x_read, t_elapsed, vals, active, hdf_scan)
                        count += 1
                        pt(ix, iy, x_read, vals); pg(count + count_r, total_all)
                        st(f"[trace {count}/{total}]  y={y_pos:.4g}")

                    # ── Retrace sweep (y-) ───────────────────────────────────
                    for j, y_pos in enumerate(retrace_y):
                        iy = n_y - 1 - j   # spatial index same as trace
                        if self._abort: break
                        while self._paused:
                            time.sleep(0.05)
                            if self._abort: break
                        self._move(act2_p, cfg["act2_attr"], y_pos, move_t, log=lg)
                        if settle > 0: time.sleep(settle)
                        if max_lockin_settling > 0: time.sleep(max_lockin_settling)
                        vals, t_elapsed = self._trigger_poll_read(
                            devp, dev_sensors, trigger_devs, int_time,
                            t0, _RUNNING, move_t, lg)
                        if hfile2 is not None:
                            self._write_point(hfile2, iy, ix, x_read, t_elapsed, vals, active, hdf_scan)
                        count_r += 1
                        pt_retrace(ix, iy, x_read, vals); pg(count + count_r, total_all)
                        st(f"[retrace {count_r}/{total}]  y={y_pos:.4g}")

                    try: hfile.flush()
                    except Exception: pass
                    if hfile2 is not None:
                        try: hfile2.flush()
                        except Exception: pass

            finally:
                if hfile2 is not None:
                    self._finalize_hdf5(hfile2, count_r, total,
                                        x_actual, t_actual, data, x_plan, y_plan,
                                        active, x_lbl, x_unit, hdf_scan, retrace_cfg)

          else:
            for iy, y_pos in enumerate(y_plan):
                if self._abort: break
                if hdf_scan == "SPATIAL_XY":
                    st(f"Moving {cfg['act2_label']} → {y_pos:.4g}")
                    self._move(act2_p, cfg["act2_attr"], y_pos, cfg["move_timeout"], log=lg)

                x_seq  = x_plan
                ix_seq = list(range(n_x))
                _prev_x_pos = None   # for adaptive settle tracking (reset each row)
                _adap_k     = float(cfg.get("adaptive_settle_k", 0.0)) if cfg.get("adaptive_settle_enabled") else 0.0
                if _adap_k > 0 and count == 0:
                    lg(f"── Adaptive settle: k = {_adap_k:.4f} s/µm ──")

                for ix, x_pos in zip(ix_seq, x_seq):
                    if self._abort: break
                    while self._paused:
                        time.sleep(0.05)
                        if self._abort: break

                    if hdf_scan == "FIELD":
                        _mag_err = safe_write(mag_p, mag_cur_attr, x_pos)
                        if _mag_err:
                            lg(f"⚠ Magnet write failed at {x_pos:.4g} A: {_mag_err}")
                        time.sleep(max(cfg["settle_time"], 0.05))
                        v, _ = safe_read(mag_p, mag_fld_attr)
                        x_read = v if v is not None else x_pos * 0.15
                    elif hdf_scan == "TIME":
                        x_read = time.time() - t0
                    else:
                        x_read = self._move(act1_p, fast_attr, x_pos, cfg["move_timeout"], log=lg)
                        if rtv40_p is not None:
                            delta_ns = (x_pos - rtv40_ref_s) * 1e9
                            new_width_ns = max(0.3, min(20.0, rtv40_base_ns - delta_ns))
                            _rw_err = safe_write(rtv40_p, "PulseWidth", new_width_ns)
                            if _rw_err and count == 0:
                                lg(f"⚠ RTV40 PulseWidth write: {_rw_err}")
                        if cfg["settle_time"] > 0:
                            time.sleep(cfg["settle_time"])
                        # Adaptive settle: extra wait proportional to position step
                        if _adap_k > 0 and _prev_x_pos is not None:
                            extra = _adap_k * abs(x_pos - _prev_x_pos)
                            if extra > 0:
                                time.sleep(extra)
                    _prev_x_pos = x_pos

                    x_actual[iy, ix] = x_read

                    # ── Lock-in filter settling ───────────────────────────────────
                    if max_lockin_settling > 0:
                        if count == 0:
                            lg(f"── Lock-in settling wait: {max_lockin_settling:.3f} s per point ──")
                        time.sleep(max_lockin_settling)

                    # ── 1. Fire trigger to all sensor devices (near-simultaneous) ──
                    trigger_failed = []
                    if trigger_devs:
                        # Try async dispatch (all Start commands in ~100 µs)
                        use_async = True
                        for dev_path, tcmd in trigger_devs.items():
                            try:
                                devp[dev_path].command_inout_asynch(tcmd)
                            except AttributeError:
                                # Device proxy doesn't support async — fall back
                                use_async = False
                                break
                            except Exception as e:
                                lg(f"⚠ Trigger {dev_path}.{tcmd}: {e}")
                                trigger_failed.append(dev_path)
                        t_trigger = time.time() - t0

                        if not use_async:
                            # Fallback: synchronous tight loop
                            for dev_path, tcmd in trigger_devs.items():
                                if dev_path in trigger_failed:
                                    continue
                                try:
                                    devp[dev_path].command_inout(tcmd)
                                except Exception as e:
                                    lg(f"⚠ Trigger {dev_path}.{tcmd}: {e}")
                                    trigger_failed.append(dev_path)
                            t_trigger = time.time() - t0
                        # No command_inout_reply() — we poll state() below instead
                    else:
                        t_trigger = time.time() - t0

                    # Only remove devices whose DISPATCH failed
                    for dp in trigger_failed:
                        lg(f"  → Removing {dp} from triggered devices")
                        trigger_devs.pop(dp, None)

                    # ── 2. Wait for ALL triggered devices to finish integration ──
                    if trigger_devs:
                        triggered = set(trigger_devs.keys()) - set(trigger_failed)

                        # Phase A — wait for every device to enter RUNNING.
                        # async-dispatched Start commands (e.g. ZI2) spawn a
                        # thread that sets state→RUNNING a few ms after Start()
                        # returns.  Without this phase the completion poll sees
                        # state=ON immediately and reads stale 0.0 values.
                        not_yet_running = set(triggered)
                        t_start = time.time()
                        while not_yet_running and (time.time() - t_start
                                                   < TRIGGER_START_GUARD_MS / 1000.0):
                            if self._abort: break
                            confirmed = set()
                            for dev_path in not_yet_running:
                                try:
                                    if devp[dev_path].state() in _RUNNING:
                                        confirmed.add(dev_path)
                                except Exception:
                                    confirmed.add(dev_path)
                            not_yet_running -= confirmed
                            if not_yet_running:
                                time.sleep(0.002)

                        # Phase B — wait for every device to leave RUNNING.
                        remaining = set(triggered)
                        t_wait = time.time()
                        timeout = cfg["move_timeout"]
                        _phase_b_fails = {dp: 0 for dp in remaining}
                        while remaining and (time.time() - t_wait < timeout):
                            if self._abort: break
                            done = set()
                            for dev_path in remaining:
                                try:
                                    dev_state = devp[dev_path].state()
                                    _phase_b_fails[dev_path] = 0  # reset on success
                                    if dev_state not in _RUNNING:
                                        done.add(dev_path)
                                except Exception as e:
                                    # Transient CORBA errors (IMP_LIMIT, TRANSIENT)
                                    # can occur if the device is briefly overloaded.
                                    # Retry a few times before giving up — treating
                                    # as "done" too early causes a stale-data read.
                                    _phase_b_fails[dev_path] += 1
                                    streak = _phase_b_fails[dev_path]
                                    if streak >= 5:
                                        lg(f"⚠ State poll failed {streak}× for "
                                           f"{dev_path}: {type(e).__name__} — giving up")
                                        done.add(dev_path)
                                    else:
                                        lg(f"⚠ State poll error for {dev_path} "
                                           f"(attempt {streak}/5): {type(e).__name__}"
                                           f" — retrying in 50 ms")
                                        time.sleep(0.05)
                            remaining -= done
                            if remaining:
                                time.sleep(0.01)
                        if remaining:
                            lg(f"⚠ Timeout waiting for: "
                               + ", ".join(remaining))
                    else:
                        time.sleep(int_time)

                    # ── 3. Guard delay — let device output registers settle ──────
                    time.sleep(READOUT_GUARD_MS / 1000.0)

                    # ── 4. Batch read_attributes per device (synchronized) ────
                    vals: Dict[str, float] = {}
                    point_had_error = False
                    for dev_path, sensors_on_dev in dev_sensors.items():
                        # Deduplicate attrs: two sensors may share the same
                        # attribute (e.g. two display channels both reading x1).
                        # Tango rejects read_attributes with repeated names.
                        unique_attrs = list(dict.fromkeys(
                            s["attribute"] for s in sensors_on_dev))
                        for attempt in range(MAX_RETRIES + 1):
                            try:
                                if len(unique_attrs) == 1:
                                    av = devp[dev_path].read_attribute(unique_attrs[0])
                                    raw = av.value
                                    attr_to_val = {unique_attrs[0]:
                                        float(raw[0]) if hasattr(raw, "__len__") else float(raw)}
                                else:
                                    attr_vals = devp[dev_path].read_attributes(unique_attrs)
                                    attr_to_val = {}
                                    for av, attr in zip(attr_vals, unique_attrs):
                                        raw = av.value
                                        attr_to_val[attr] = (
                                            float(raw[0]) if hasattr(raw, "__len__") else float(raw))
                                for s in sensors_on_dev:
                                    v = attr_to_val[s["attribute"]]
                                    vals[s["label"]] = v
                                    data[s["label"]][iy, ix] = v
                                break
                            except Exception as e:
                                if attempt == MAX_RETRIES:
                                    lg(f"⚠ Read {dev_path} {unique_attrs}: {e}")
                                    point_had_error = True
                                    for s in sensors_on_dev:
                                        vals[s["label"]] = np.nan
                                        data[s["label"]][iy, ix] = np.nan
                                else:
                                    time.sleep(RETRY_DELAY)

                    # Auto-pause on repeated failures
                    if point_had_error:
                        consecutive_errors += 1
                        if consecutive_errors >= AUTO_PAUSE_THRESHOLD and not self._paused:
                            self._paused = True
                            lg(f"⚠ Auto-paused after {consecutive_errors} consecutive errors. "
                               f"Fix the issue and press Resume.")
                            st(f"⚠ AUTO-PAUSED — {consecutive_errors} consecutive read errors")
                    else:
                        consecutive_errors = 0

                    # ── 5. Timestamp: center of integration window ────────────
                    t_elapsed = t_trigger + int_time / 2.0
                    t_actual[iy, ix] = t_elapsed
                    vals[X_TIME] = t_elapsed
                    # For TIME scans, x_read should be the same corrected timestamp
                    if hdf_scan == "TIME":
                        x_read = t_elapsed
                        x_actual[iy, ix] = x_read

                    # ── Write this point to HDF5 immediately ──────────────────
                    self._write_point(hfile, iy, ix, x_read, t_elapsed,
                                      vals, active, hdf_scan)

                    count += 1
                    # First-point diagnostic: confirm the full cycle works
                    if count == 1:
                        lg(f"── First point acquired ──")
                        lg(f"  Triggers active: {list(trigger_devs.keys()) if trigger_devs else '(none — using sleep)'}")
                        lg(f"  t_trigger={t_trigger:.3f}s  t_elapsed={t_elapsed:.3f}s")
                        for k, v in vals.items():
                            if not k.startswith("_"):
                                lg(f"  {k} = {v:.6g}")
                    pt(ix, iy, x_read, vals)
                    pg(count, total)
                    st(f"[{count}/{total}]  {x_lbl}={x_read:.4g}{x_unit}  t={t_elapsed:.1f}s  " +
                       "  ".join(f"{k}={v:.4g}" for k, v in vals.items()
                                  if not k.startswith("_")))

                    # Flush periodically for 1D scans
                    if n_y == 1 and count % FLUSH_INTERVAL == 0:
                        try: hfile.flush()
                        except Exception as e: lg(f"⚠ HDF5 flush failed: {e}")

                # Flush after each Y row (important for 2D scans)
                if n_y > 1:
                    try: hfile.flush()
                    except Exception as e: lg(f"⚠ HDF5 flush failed: {e}")

        finally:
            # ── Finalize: update status and close ─────────────────────────────
            self._finalize_hdf5(hfile, count, total, x_actual, t_actual,
                                data, x_plan, y_plan, active,
                                x_lbl, x_unit, hdf_scan, cfg)
            if rtv40_p is not None and rtv40_base_ns is not None:
                safe_write(rtv40_p, "PulseWidth", rtv40_base_ns)
                lg(f"── RTV40 reset to base width {rtv40_base_ns:.3f} ns ──")

        if count > 0:
            # Auto-demagnetize after FIELD scans — disabled for superconducting magnets
            # (set "demagnetize_after_scan": false in setup to suppress)
            if (hdf_scan == "FIELD" and mag_p is not None
                    and setup.get("demagnetize_after_scan", True)):
                st("Auto-demagnetizing magnet…")
                demagnetize_magnet(mag_p, mag_cur_attr, log_fn=lg)
            st(("Done ✓" if not self._abort else f"Aborted ({count}/{total} pts)") +
               f"  → {filename}")
            return filename
        st("Aborted — no data.")
        return None

    # ── DC Hysteresis: read arrays from device and emit to live plot ─────────
    def _read_and_emit_hyst_loop(self, hyst_p, active_ch: list, n_loop: int,
                                  pt, pg, cycle: int, cycles: int,
                                  lg, dl=None) -> Optional[Dict[str, np.ndarray]]:
        """
        Read field + result arrays from the PyHysteresis device and emit
        point callbacks so the live 1D plot refreshes with the current
        (cycle-averaged) hysteresis loop.

        Returns the result_arrays dict on success, None on unrecoverable failure.
        Re-emitting all n_loop points with the same ix indices overwrites the
        previous cycle's data in the Live1DWidget buffers — the plot sharpens
        after every cycle.

        Three retries per attribute.  Arrays shorter than 4 points are rejected
        as "device not ready yet" and retried.
        """
        _MIN_ARRAY_LEN = 4

        def _read_array(attr: str) -> Optional[np.ndarray]:
            for attempt in range(3):
                try:
                    raw = hyst_p.read_attribute(attr).value
                    if raw is None:
                        lg(f"  ⚠ {attr} returned None (attempt {attempt+1}/3)")
                        time.sleep(0.15)
                        continue
                    arr = np.asarray(raw, dtype=float).flatten()
                except Exception as e:
                    lg(f"  ⚠ {attr} attempt {attempt+1}/3: {e}")
                    time.sleep(0.15)
                    continue
                if len(arr) < _MIN_ARRAY_LEN:
                    lg(f"  ⚠ {attr} too short ({len(arr)} pts, need ≥{_MIN_ARRAY_LEN}) "
                       f"— device may not have finished (attempt {attempt+1}/3)")
                    time.sleep(0.2)
                    continue
                return arr
            return None

        # ── Read field axis ───────────────────────────────────────────────────
        field_arr = _read_array("field")
        if field_arr is None:
            lg(f"  ✗ field array unreadable after 3 attempts — skip cycle {cycle} plot")
            pg(cycle, cycles)
            return None
        lg(f"  cycle {cycle}: field len={len(field_arr)}  "
           f"range=[{field_arr.min():.1f}, {field_arr.max():.1f}] mT")

        # ── Read signal channels ──────────────────────────────────────────────
        result_arrays: Dict[str, np.ndarray] = {}
        for c in active_ch:
            arr = _read_array(c["attr"])
            if arr is None:
                lg(f"  ✗ {c['attr']} unreadable — using NaN")
                arr = np.full(n_loop, np.nan)
            else:
                lg(f"    {c['attr']} ({c['label']}): len={len(arr)}  "
                   f"mean={np.nanmean(arr):.4g}")
            result_arrays[c["label"]] = arr

        # ── Emit point-by-point to live plot ──────────────────────────────────
        n_actual = min(len(field_arr), n_loop)
        for i in range(n_actual):
            if self._abort:
                break
            vals: Dict[str, float] = {}
            for c in active_ch:
                a = result_arrays[c["label"]]
                vals[c["label"]] = float(a[i]) if i < len(a) else np.nan
            vals[X_TIME] = float(i)
            pt(i, 0, float(field_arr[i]), vals)

        pg(cycle, cycles)
        lg(f"  emitted {n_actual} pts for cycle {cycle}/{cycles}")

        # Fire dc_loop callback so the monitor canvas gets the full arrays directly.
        if dl is not None and n_actual > 0:
            try:
                dl(field_arr[:n_actual],
                   {c["label"]: result_arrays[c["label"]][:n_actual]
                    for c in active_ch if c["label"] in result_arrays})
            except Exception as e:
                lg(f"  ⚠ dc_loop callback failed: {e}")

        return result_arrays

    # ── DC Hysteresis scan ────────────────────────────────────────────────────
    def _run_dc_hyst(self, cfg: dict, setup: dict, cbs: dict) -> Optional[str]:
        """
        Execute a DC Hysteresis measurement via the PyHysteresis Tango device.

        The device handles all Beckhoff polling internally (at ~20 ms). From
        this side we only poll the *Tango* device state to track progress, at
        a rate derived from IntegrationTime — typically 200 ms–2 s.  This
        keeps the network subscription time low while the Beckhoff does the
        precision work.

        Flow:
          1. Write MagneticField / NumberOfPoints / Cycles / IntegrationTime.
          2. Issue Start command.
          3. Poll state + CycleReadback at  max(0.2, IntegrationTime/2)  s.
          4. On completion, read field + result arrays + scalar Hc/Hshift/Mr/Ms.
          5. Write to HDF5; emit point callbacks so the live 1D plot sees the
             full hysteresis loop.
        """
        st = cbs.get('status',   lambda *a: None)
        lg = cbs.get('log',      lambda *a: None)
        pt = cbs.get('point',    lambda *a: None)
        pg = cbs.get('progress', lambda *a: None)

        hyst_dev = cfg.get("hyst_device", "").strip()
        if not hyst_dev:
            st("No DC hysteresis device configured."); return None

        hyst_p, err = fresh_proxy(hyst_dev)
        if err:
            lg(f"⚠ {hyst_dev}: using sim — {err}")

        npts    = max(1, int(cfg.get("hyst_npts",     100)))
        cycles  = max(1, int(cfg.get("hyst_cycles",   1)))
        field_V = float(cfg.get("hyst_field_V",  1.0))
        int_t   = max(0.01, float(cfg.get("hyst_int_time", 2.0)))

        # Active channels (result1..result6)
        default_chs = [
            {"label": "MOKE (R1)", "attr": "result1", "enabled": True,  "y_axis": "Y1"},
            {"label": "R2",        "attr": "result2", "enabled": False, "y_axis": "Y2"},
            {"label": "R3",        "attr": "result3", "enabled": False, "y_axis": "Y2"},
            {"label": "R4",        "attr": "result4", "enabled": False, "y_axis": "Y2"},
            {"label": "R5 (field)","attr": "result5", "enabled": False, "y_axis": "Y2"},
            {"label": "R6",        "attr": "result6", "enabled": False, "y_axis": "Y2"},
        ]
        hyst_chs  = cfg.get("hyst_channels", default_chs)
        active_ch = [c for c in hyst_chs if c.get("enabled", True)]
        if not active_ch:
            st("No DC hysteresis channels enabled."); return None

        n_loop = 2 * npts   # full loop = positive half + negative half

        # ── HDF5 setup ────────────────────────────────────────────────────────
        base    = os.path.expanduser(setup.get("save_dir", "~/moke_data"))
        day_dir = os.path.join(base, datetime.now().strftime("%Y%m%d"))
        os.makedirs(day_dir, exist_ok=True)
        filename = os.path.join(day_dir, _make_filename(cfg))

        try:
            import json as _json
            hfile = h5py.File(filename, "w")

            # Root: minimal status + type
            _wsa(hfile, "scan_status", "running")
            _wsa(hfile, "scan_type",   "DC_HYST")
            _wsa(hfile, "timestamp",   datetime.now().isoformat())

            # /metadata/
            meta = hfile.create_group("metadata")
            _wsa(meta, "scan_name",   cfg.get("name", "dc_hyst"))
            _wsa(meta, "hyst_device", hyst_dev)
            meta.attrs["MagneticField_V"]  = field_V
            meta.attrs["NumberOfPoints"]   = npts
            meta.attrs["Cycles"]           = cycles
            meta.attrs["IntegrationTime"]  = int_t
            meta.attrs["n_loop"]           = n_loop
            _wsa(meta, "operator",    cfg.get("operator", ""))
            _wsa(meta, "sample_id",   cfg.get("sample_id", ""))
            _wsa(meta, "notes",       cfg.get("notes", ""))
            _wsa(meta, "incidence",   cfg.get("incidence", ""))
            _wsa(meta, "polarization",cfg.get("polarization", ""))
            meta.attrs["lam2"]             = bool(cfg.get("lam2",  False))
            meta.attrs["lam4"]             = bool(cfg.get("lam4",  False))
            meta.attrs["noDC"]             = bool(cfg.get("noDC",  False))
            meta.attrs["mirror_shift_mm"]  = float(cfg.get("mirror_shift", 0.0))
            meta.attrs["channels_json"]    = _json.dumps(hyst_chs)

            # Hardware snapshot + temperature-sweep keys
            _write_hw_metadata(meta, cfg)

            # Scalar results written at completion
            for s in ("Hc", "Hshift", "Mr", "Ms"):
                meta.attrs[s] = float("nan")

            # /data/
            data_grp = hfile.create_group("data")
            d = data_grp.create_dataset("actuator_field", data=np.full(n_loop, np.nan))
            _wsa(d, "label", "Field"); _wsa(d, "unit", "mT"); _wsa(d, "role", "x")

            for c in active_ch:
                key = self._hdf5_key(c["label"])
                ds  = data_grp.create_dataset(key, data=np.full(n_loop, np.nan))
                _wsa(ds, "label",           c["label"])
                _wsa(ds, "unit",            c.get("unit", "V"))
                _wsa(ds, "tango_attribute", c["attr"])
                _wsa(ds, "y_axis",          c.get("y_axis", "Y1"))
                _wsa(ds, "role",            "sensor")

            hfile.flush()
        except Exception as e:
            st(f"⚠ Could not create {filename}: {e}"); return None

        # ── Configure device ──────────────────────────────────────────────────
        lg(f"── Configuring {hyst_dev} ──")
        for attr, val in [("MagneticField",  field_V),
                           ("NumberOfPoints", npts),
                           ("Cycles",         cycles),
                           ("IntegrationTime", int_t)]:
            err = safe_write(hyst_p, attr, val)
            tag = "✓" if not err else "⚠"
            lg(f"  {tag} {attr} ← {val}" + (f"  ({err})" if err else ""))

        # Polling interval: 1/4 of a half-loop duration, minimum 200 ms.
        # This gives ~4 state-polls per half-loop — enough for smooth progress
        # without unnecessary Tango round-trips.
        poll_s = max(0.2, int_t / 4.0)
        lg(f"── Poll interval: {poll_s:.3f} s  "
           f"(IntegrationTime={int_t:.3g} s per half-loop) ──")

        t0 = time.time()
        _RUNNING = {tango.DevState.RUNNING} if TANGO_AVAILABLE else set()
        _RUNNING.add("RUNNING")

        result_fn = None
        try:
            # ── Start ─────────────────────────────────────────────────────────
            st(f"Starting DC Hyst: {npts} pts × {cycles} cycles, "
               f"field={field_V:.3f} V, int={int_t:.3g} s/half-loop")
            hyst_p.command_inout("Start")
            time.sleep(poll_s)   # give device time to enter RUNNING state

            last_cycle = 0
            result_arrays_live: Optional[Dict[str, np.ndarray]] = None
            while not self._abort:
                try:
                    dev_state = hyst_p.state()
                    if dev_state not in _RUNNING:
                        break
                except Exception as e:
                    lg(f"⚠ State poll error: {e}"); break

                # CycleReadback is a cheap scalar — read it for progress
                cycle_rb, _ = safe_read(hyst_p, "CycleReadback")
                if cycle_rb is not None:
                    c_int = int(cycle_rb)
                    if c_int != last_cycle and c_int > 0:
                        last_cycle = c_int
                        elapsed = time.time() - t0
                        rem = (elapsed / c_int * (cycles - c_int)) if c_int > 0 else 0
                        st(f"DC Hyst: cycle {c_int}/{cycles}  "
                           f"elapsed={elapsed:.1f} s  eta≈{rem:.0f} s")
                        # ── Live update: read current averaged loop and repaint ──
                        lg(f"  cycle {c_int} done — refreshing live plot…")
                        result_arrays_live = self._read_and_emit_hyst_loop(
                            hyst_p, active_ch, n_loop,
                            pt, pg, c_int, cycles, lg,
                            dl=cbs.get('dc_loop'))

                time.sleep(poll_s)

            # ── Abort path ────────────────────────────────────────────────────
            if self._abort:
                try:
                    hyst_p.command_inout("Abort")
                    lg("Sent Abort to hysteresis device.")
                except Exception as e:
                    lg(f"⚠ Abort command failed: {e}")
                st("DC Hyst aborted.")
                _wsa(hfile, "scan_status",   "aborted")
                _wsa(hfile, "timestamp_end", datetime.now().isoformat())
                # result_fn stays None; finally block closes the file

            else:
                # ── Final read: get definitive result (device may have updated
                #    after leaving RUNNING state — one final read ensures we have
                #    the fully converged N-cycle average) ─────────────────────
                elapsed = time.time() - t0
                lg(f"── DC Hyst complete ({elapsed:.1f} s) — reading final arrays ──")
                result_arrays = self._read_and_emit_hyst_loop(
                    hyst_p, active_ch, n_loop, pt, pg, cycles, cycles, lg,
                    dl=cbs.get('dc_loop'))
                # Fallback to last live result if final read fails
                if result_arrays is None:
                    result_arrays = result_arrays_live or {}

                # Read field axis directly (safe_read returns scalar — wrong for SPECTRUM)
                try:
                    field_raw = hyst_p.read_attribute("field").value
                    field_arr = np.asarray(field_raw, dtype=float).flatten()
                    if len(field_arr) < 4:
                        raise ValueError(f"field too short: {len(field_arr)} pts")
                except Exception as e:
                    lg(f"⚠ final field read failed ({e}) — using linear estimate")
                    field_arr = np.concatenate([
                        np.linspace(0, field_V * 1000 / 5.0, npts),
                        np.linspace(0, -field_V * 1000 / 5.0, npts)])
                n_actual = min(len(field_arr), n_loop)

                # Scalar results — safe_read is correct here (these ARE scalars)
                scalars: Dict[str, float] = {}
                for s in ("Hc", "Hshift", "Mr", "Ms"):
                    v, serr = safe_read(hyst_p, s)
                    if serr:
                        lg(f"⚠ {s}: {serr}")
                    scalars[s] = float(v) if v is not None else np.nan

                # ── Write to HDF5 ──────────────────────────────────────────────
                hfile["data"]["actuator_field"][:n_actual] = field_arr[:n_actual]
                for c in active_ch:
                    key = self._hdf5_key(c["label"])
                    arr = result_arrays.get(c["label"], np.full(n_loop, np.nan))
                    n_a = min(len(arr), n_loop)
                    hfile["data"][key][:n_a] = arr[:n_a]
                # Scalar results → metadata attrs
                for s, v in scalars.items():
                    hfile["metadata"].attrs[s] = v
                _wsa(hfile, "scan_status",   "completed")
                _wsa(hfile, "timestamp_end", datetime.now().isoformat())
                hfile.attrs["duration_seconds"] = elapsed
                hfile["metadata"].attrs["duration_seconds"] = elapsed
                hfile["metadata"].attrs["points_acquired"]  = n_actual

                hc     = scalars.get("Hc",     np.nan)
                hshift = scalars.get("Hshift", np.nan)
                mr     = scalars.get("Mr",     np.nan)
                ms     = scalars.get("Ms",     np.nan)
                st(f"DC Hyst done ✓  "
                   f"Hc={hc:.3f} mT  Hshift={hshift:.3f} mT  "
                   f"Mr={mr:.4g}  Ms={ms:.4g}  → {filename}")
                lg(f"  Hc={hc:.4f} mT  Hshift={hshift:.4f} mT  "
                   f"Mr={mr:.6g}  Ms={ms:.6g}  "
                   f"duration={elapsed:.2f} s")
                result_fn = filename

        except Exception:
            lg(f"⚠ DC Hyst exception:\n{traceback.format_exc()}")
            try:
                _wsa(hfile, "scan_status", "error")
            except Exception:
                pass
        finally:
            try:
                hfile.flush()
                hfile.close()
            except Exception as fe:
                lg(f"⚠ HDF5 close failed: {fe}")
        return result_fn

    # ── Shared trigger → poll → read sequence ────────────────────────────────
    def _trigger_poll_read(self, devp, dev_sensors, trigger_devs,
                           int_time, t0, _RUNNING, move_timeout, lg):
        """Fire async triggers, wait for completion (Phase A + B), read sensors.

        trigger_devs is modified in-place: devices whose trigger command fails
        are permanently removed so they don't block future points.
        Returns (vals_dict, t_elapsed_s).
        """
        trigger_failed = []
        if trigger_devs:
            use_async = True
            for dev_path, tcmd in trigger_devs.items():
                try:
                    devp[dev_path].command_inout_asynch(tcmd)
                except AttributeError:
                    use_async = False; break
                except Exception as e:
                    lg(f"⚠ Trigger {dev_path}.{tcmd}: {e}")
                    trigger_failed.append(dev_path)
            t_trigger = time.time() - t0

            if not use_async:
                for dev_path, tcmd in trigger_devs.items():
                    if dev_path in trigger_failed: continue
                    try:
                        devp[dev_path].command_inout(tcmd)
                    except Exception as e:
                        lg(f"⚠ Trigger {dev_path}.{tcmd}: {e}")
                        trigger_failed.append(dev_path)
                t_trigger = time.time() - t0
        else:
            t_trigger = time.time() - t0

        for dp in trigger_failed:
            lg(f"  → Removing {dp} from triggered devices")
            trigger_devs.pop(dp, None)

        if trigger_devs:
            triggered = set(trigger_devs.keys()) - set(trigger_failed)

            # Phase A — wait for entry into RUNNING
            not_yet_running = set(triggered)
            t_start = time.time()
            while not_yet_running and (time.time() - t_start
                                       < TRIGGER_START_GUARD_MS / 1000.0):
                if self._abort: break
                confirmed = set()
                for dp in not_yet_running:
                    try:
                        if devp[dp].state() in _RUNNING: confirmed.add(dp)
                    except Exception: confirmed.add(dp)
                not_yet_running -= confirmed
                if not_yet_running: time.sleep(0.002)

            # Phase B — wait for exit from RUNNING
            remaining = set(triggered)
            t_wait = time.time()
            _fails = {dp: 0 for dp in remaining}
            while remaining and (time.time() - t_wait < move_timeout):
                if self._abort: break
                done = set()
                for dp in remaining:
                    try:
                        ds = devp[dp].state()
                        _fails[dp] = 0
                        if ds not in _RUNNING: done.add(dp)
                    except Exception as e:
                        _fails[dp] += 1
                        if _fails[dp] >= 5:
                            lg(f"⚠ State poll failed {_fails[dp]}× for {dp}: "
                               f"{type(e).__name__} — giving up")
                            done.add(dp)
                        else:
                            time.sleep(0.05)
                remaining -= done
                if remaining: time.sleep(0.01)
            if remaining:
                lg(f"⚠ Timeout waiting for: " + ", ".join(remaining))
        else:
            time.sleep(int_time)

        time.sleep(READOUT_GUARD_MS / 1000.0)

        # Batch read per device
        vals: Dict[str, float] = {}
        for dev_path, sensors_on_dev in dev_sensors.items():
            unique_attrs = list(dict.fromkeys(s["attribute"] for s in sensors_on_dev))
            for attempt in range(MAX_RETRIES + 1):
                try:
                    if len(unique_attrs) == 1:
                        av  = devp[dev_path].read_attribute(unique_attrs[0])
                        raw = av.value
                        attr_to_val = {unique_attrs[0]:
                            float(raw[0]) if hasattr(raw, "__len__") else float(raw)}
                    else:
                        attr_vals = devp[dev_path].read_attributes(unique_attrs)
                        attr_to_val = {}
                        for av, attr in zip(attr_vals, unique_attrs):
                            raw = av.value
                            attr_to_val[attr] = (
                                float(raw[0]) if hasattr(raw, "__len__") else float(raw))
                    for s in sensors_on_dev:
                        vals[s["label"]] = attr_to_val[s["attribute"]]
                    break
                except Exception as e:
                    if attempt == MAX_RETRIES:
                        lg(f"⚠ Read {dev_path} {unique_attrs}: {e}")
                        for s in sensors_on_dev: vals[s["label"]] = np.nan
                    else:
                        time.sleep(RETRY_DELAY)

        t_elapsed = t_trigger + int_time / 2.0
        vals[X_TIME] = t_elapsed
        return vals, t_elapsed

    # ── Stage movement ────────────────────────────────────────────────────────
    def _move(self, proxy, attr: str, target: float, timeout: float,
              log=None) -> float:
        """
        Move a stage and verify arrival via position readback.

        Returns the actual position read back after movement.  If readback
        fails, returns the target value as a fallback.  Logs a warning if
        the readback deviates from the target by more than POSITION_TOLERANCE_FRAC
        of the scan range (default 1 %).
        """
        # Coerce to Python float — some pytango versions reject numpy scalars
        # with "unsupported data_format" when the C extension does type dispatch.
        err = safe_write(proxy, attr, float(target))
        if err:
            raise RuntimeError(f"Move failed on '{attr}' → {target:.6g}: {err}")
        if not TANGO_AVAILABLE:
            return target
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if proxy.state() != tango.DevState.MOVING: break
            except Exception: break
            time.sleep(0.005)

        # Read back actual position
        try:
            raw = proxy.read_attribute(attr).value
            actual = float(raw[0]) if hasattr(raw, "__len__") else float(raw)
        except Exception:
            # Readback failed — use target as fallback, no warning
            return target

        delta = abs(actual - target)
        # Warn if off by more than 1% of target or > 50 absolute units
        threshold = max(abs(target) * 0.01, 50.0)
        if delta > threshold and log:
            log(f"⚠ Position mismatch on '{attr}': "
                f"target={target:.4g}  actual={actual:.4g}  Δ={delta:.4g}")
        return actual

    # ── HDF5 key sanitization ─────────────────────────────────────────────────
    @staticmethod
    def _hdf5_key(label: str) -> str:
        key = label.strip()
        key = key.replace("/", "_")
        key = key.replace(" ", "_")
        key = key.replace("\\", "_")
        return key or "sensor"

    # ── Incremental HDF5: open ────────────────────────────────────────────────
    def _open_hdf5(self, fn, x_plan, y_plan, sensors,
                   x_lbl, x_unit, hdf_scan, cfg):
        """
        Create the HDF5 file and pre-allocate all datasets with NaN.

        Layout
        ------
        /                       root attrs: scan_status, scan_type, timestamp
        /metadata/              group attrs: all scan parameters, user fields,
                                    sensor configs (JSON), scan geometry
        /data/                  group: every measured array
            x           [n_x] or [n_y, n_x]  actual primary axis (pos/field/time)
            x_setpoint  [n_x]                 commanded primary axis (not TIME)
            time        [n_x] or [n_y, n_x]  elapsed time (not TIME scans)
            y_setpoint  [n_y]                 commanded Y setpoints (2D only)
            y           [n_y, n_x]            actual Y (pre-filled; 2D only)
            <chan_key>  [n_x] or [n_y, n_x]  per sensor channel

        Each dataset carries: label, unit, role, and full device provenance.
        """
        try:
            import json as _json
            n_x, n_y = len(x_plan), len(y_plan)
            is_2d    = n_y > 1
            is_field = hdf_scan == "FIELD"
            is_time  = hdf_scan == "TIME"
            shape    = (n_y, n_x) if is_2d else (n_x,)

            f = h5py.File(fn, "w")

            # ── Root: minimal status / timing attrs ───────────────────────────
            _wsa(f, "scan_status", "running")
            _wsa(f, "scan_type",   hdf_scan)
            _wsa(f, "timestamp",   datetime.now().isoformat())

            # ── /metadata/ ────────────────────────────────────────────────────
            meta = f.create_group("metadata")
            _wsa(meta, "scan_name",   cfg["name"])
            meta.attrs["n_x"]              = n_x
            meta.attrs["n_y"]              = n_y
            meta.attrs["points_planned"]   = n_x * n_y
            meta.attrs["points_acquired"]  = 0
            meta.attrs["integration_time"] = cfg["integration_time"]
            meta.attrs["settle_time"]      = cfg["settle_time"]
            meta.attrs["move_timeout"]     = float(cfg.get("move_timeout", 15.0))
            _wsa(meta, "operator",    cfg.get("operator", ""))
            _wsa(meta, "sample_id",   cfg.get("sample_id", ""))
            _wsa(meta, "notes",       cfg.get("notes", ""))
            _wsa(meta, "incidence",   cfg.get("incidence", ""))
            _wsa(meta, "polarization",cfg.get("polarization", ""))
            meta.attrs["lam2"]             = bool(cfg.get("lam2",  False))
            meta.attrs["lam4"]             = bool(cfg.get("lam4",  False))
            meta.attrs["noDC"]             = bool(cfg.get("noDC",  False))
            meta.attrs["mirror_shift_mm"]  = float(cfg.get("mirror_shift", 0.0))
            # Scan-geometry metadata — write the axis that is actually moving
            if is_field:
                segs = cfg.get("field_segments",
                               [[cfg.get("field_start_A",-1.0),
                                 cfg.get("field_stop_A",  1.0), n_x]])
                _wsa(meta, "field_segments_json", _json.dumps(segs))
                _wsa(meta, "field_device",       cfg.get("field_device", ""))
                _wsa(meta, "field_current_attr", cfg.get("field_current_attr", ""))
            elif not is_time:
                if hdf_scan == "SPATIAL_Y":
                    for k in ("device", "attr", "label", "unit"):
                        _wsa(meta, f"act2_{k}", cfg.get(f"act2_{k}", ""))
                    _wsa(meta, "axis_moving", "act2")
                else:
                    for pfx in (["act1"] + (["act2"] if is_2d else [])):
                        for k in ("device", "attr", "label", "unit"):
                            _wsa(meta, f"{pfx}_{k}", cfg.get(f"{pfx}_{k}", ""))
                    _wsa(meta, "axis_moving", "act1" if not is_2d else "both")
            _wsa(meta, "sensors_json", _json.dumps(
                [{k: v for k, v in s.items() if k != "plot_visible"}
                 for s in sensors]))

            # ── Step sizes ────────────────────────────────────────────────────
            if not is_field and not is_time:
                for pfx, npts_key in [("act1", n_x), ("act2", n_y if is_2d else None)]:
                    npts = npts_key
                    if npts and npts > 1:
                        start = cfg.get(f"{pfx}_start")
                        stop  = cfg.get(f"{pfx}_stop")
                        if start is not None and stop is not None:
                            meta.attrs[f"{pfx}_step"] = (stop - start) / (npts - 1)
                            _wsa(meta, f"{pfx}_step_unit", cfg.get(f"{pfx}_unit", ""))
            if is_field:
                segs = cfg.get("field_segments", [])
                total_pts = sum(max(1, int(s[2])) for s in segs) if segs else n_x
                if total_pts > 1 and segs:
                    span = segs[-1][1] - segs[0][0]
                    meta.attrs["field_step_A"] = span / (total_pts - 1)

            # ── Hardware snapshot + temperature-sweep keys ────────────────────
            _write_hw_metadata(meta, cfg)

            # ── /data/ ────────────────────────────────────────────────────────
            # Axis dataset key is derived from the scan label so the name is
            # self-describing: "actuator_x", "actuator_y", "actuator_field", …
            # TIME scans are the one exception — there the x IS time.
            if is_time:
                ax_key = "time"
            else:
                ax_key = "actuator_" + self._hdf5_key(x_lbl).lower()

            data = f.create_group("data")
            nan  = np.full(shape, np.nan)

            def _ds(name, arr, label, unit, role, **kw):
                d = data.create_dataset(name, data=arr)
                _wsa(d, "label", label)
                _wsa(d, "unit",  unit)
                _wsa(d, "role",  role)
                for k, v in kw.items():
                    _wsa(d, k, v) if isinstance(v, str) else d.attrs.__setitem__(k, v)
                return d

            if is_time:
                _ds("time", nan, "Time", "s", "x")
            elif is_field:
                _ds(ax_key + "_setpoint", x_plan, f"{x_lbl} (setpoint)", "A",     "x_setpoint")
                _ds(ax_key,               nan,    "Field",               "T",     "x")
                _ds("time",               nan,    "Time",                "s",     "time")
            else:
                _ds(ax_key + "_setpoint", x_plan, f"{x_lbl} (setpoint)", x_unit, "x_setpoint")
                _ds(ax_key,               nan,     x_lbl,                x_unit, "x")
                _ds("time",               nan,    "Time",                "s",    "time")
                if is_2d:
                    y2_lbl  = cfg.get("act2_label", "Y")
                    y2_unit = cfg.get("act2_unit",  "nm")
                    ay_key  = "actuator_" + self._hdf5_key(y2_lbl).lower()
                    _ds(ay_key + "_setpoint", y_plan, f"{y2_lbl} (setpoint)", y2_unit, "y_setpoint")
                    y_2d = np.tile(y_plan[:, np.newaxis], (1, n_x))
                    _ds(ay_key, y_2d, y2_lbl, y2_unit, "y")

            # Sensor channels — deduplicate keys if two sensors share a label
            _used_keys = set(data.keys())   # keys already in /data/
            for s in sensors:
                key = self._hdf5_key(s["label"])
                if key in _used_keys:
                    # Append suffix to make unique
                    n = 2
                    while f"{key}_{n}" in _used_keys:
                        n += 1
                    key = f"{key}_{n}"
                _used_keys.add(key)
                s["_hdf5_key"] = key      # store for _write_point lookup
                _ds(key, nan, s["label"], s.get("unit",""), "sensor",
                    device          = s["device"],
                    tango_attribute = s["attribute"],
                    trigger_cmd     = s.get("trigger_cmd", ""),
                    integ_time_attr = s.get("integ_time_attr", ""),
                    y_axis          = s.get("y_axis", "Y1"),
                    plot_axis       = s.get("plot_axis", s.get("y_axis", "Y1")))

            _wsa(f, "_x_key", ax_key)
            f.attrs["_is_2d"]   = is_2d
            f.attrs["_is_time"] = is_time
            f.attrs["_n_x"]     = n_x

            f.flush()
            return f

        except Exception as e:
            import traceback
            traceback.print_exc()
            try: f.close()
            except Exception: pass
            self._hdf5_error = str(e)
            return None

    # ── Incremental HDF5: write one point ─────────────────────────────────────
    def _write_point(self, f, iy, ix, x_read, t_elapsed, vals, sensors, hdf_scan):
        """Write a single data point into /data/ of the already-open HDF5 file."""
        try:
            is_2d = bool(f.attrs["_is_2d"])
            ax_key = str(f.attrs["_x_key"])
            d     = f["data"]

            if is_2d:
                d[ax_key][iy, ix] = x_read
                if "time" in d:
                    d["time"][iy, ix] = t_elapsed
                for s in sensors:
                    key = s.get("_hdf5_key", self._hdf5_key(s["label"]))
                    if key in d:
                        d[key][iy, ix] = vals.get(s["label"], np.nan)
            else:
                d[ax_key][ix] = x_read
                if "time" in d:
                    d["time"][ix] = t_elapsed
                for s in sensors:
                    key = s.get("_hdf5_key", self._hdf5_key(s["label"]))
                    if key in d:
                        d[key][ix] = vals.get(s["label"], np.nan)

            prev = int(f["metadata"].attrs.get("points_acquired", 0))
            f["metadata"].attrs["points_acquired"] = prev + 1

        except Exception:
            pass

    # ── Incremental HDF5: finalize ────────────────────────────────────────────
    def _finalize_hdf5(self, f, count, total, x_actual, t_actual,
                       data, x_plan, y_plan, sensors,
                       x_lbl, x_unit, hdf_scan, cfg):
        """Write final status/timing and close the file."""
        try:
            if count == 0:
                _wsa(f, "scan_status", "empty")
            elif self._abort:
                _wsa(f, "scan_status", "aborted")
            else:
                _wsa(f, "scan_status", "completed")

            duration = float(t_actual.max()) if count > 0 else 0.0
            _wsa(f, "timestamp_end",    datetime.now().isoformat())
            f.attrs["duration_seconds"] = duration

            f["metadata"].attrs["points_acquired"]  = count
            f["metadata"].attrs["duration_seconds"] = duration

            f.flush()
        except Exception:
            pass
        finally:
            try: f.close()
            except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# ScanWorker — runs a single scan on a QThread
# ─────────────────────────────────────────────────────────────────────────────
