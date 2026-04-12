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
import copy, os, time, traceback
from collections import defaultdict
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

# How often to flush to disk for 1D scans (every N points)
FLUSH_INTERVAL = 10
# How many consecutive sensor-read failures before auto-pausing
AUTO_PAUSE_THRESHOLD = 5
# Guard delay (ms) between device state leaving RUNNING and readout,
# to let output registers settle with final averaged values
READOUT_GUARD_MS = 10


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
        for s in active:
            dp = s["device"]
            if dp and dp not in devp:
                fp, fp_err = fresh_proxy(dp)
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
            x_lbl, x_unit = "Field", "T"
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
        filename = os.path.join(day_dir,
                                f"{cfg['name']}_{datetime.now().strftime('%H%M%S')}.h5")

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
        else:
            lg("── No triggered devices — using timed integration (sleep) ──")

        # States that mean "still integrating"
        _RUNNING = {tango.DevState.RUNNING} if TANGO_AVAILABLE else set()
        _RUNNING.add("RUNNING")   # SimProxy returns a string

        try:
            for iy, y_pos in enumerate(y_plan):
                if self._abort: break
                if hdf_scan == "SPATIAL_XY":
                    st(f"Moving {cfg['act2_label']} → {y_pos:.4g}")
                    self._move(act2_p, cfg["act2_attr"], y_pos, cfg["move_timeout"], log=lg)

                rev    = cfg.get("zigzag", False) and iy % 2 == 1 and hdf_scan == "SPATIAL_XY"
                x_seq  = x_plan[::-1] if rev else x_plan
                ix_seq = list(range(n_x-1, -1, -1)) if rev else list(range(n_x))

                for ix, x_pos in zip(ix_seq, x_seq):
                    if self._abort: break
                    while self._paused:
                        time.sleep(0.05)
                        if self._abort: break

                    if hdf_scan == "FIELD":
                        mag_p.write_attribute(mag_cur_attr, x_pos)
                        time.sleep(max(cfg["settle_time"], 0.05))
                        v, _ = safe_read(mag_p, mag_fld_attr)
                        x_read = v if v is not None else x_pos * 0.15
                    elif hdf_scan == "TIME":
                        x_read = time.time() - t0
                    else:
                        x_read = self._move(act1_p, fast_attr, x_pos, cfg["move_timeout"], log=lg)
                        if cfg["settle_time"] > 0:
                            time.sleep(cfg["settle_time"])

                    x_actual[iy, ix] = x_read

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
                        remaining = set(trigger_devs.keys())
                        t_wait = time.time()
                        timeout = cfg["move_timeout"]
                        while remaining and (time.time() - t_wait < timeout):
                            if self._abort: break
                            for dev_path in list(remaining):
                                try:
                                    dev_state = devp[dev_path].state()
                                    if dev_state not in _RUNNING:
                                        remaining.discard(dev_path)
                                except Exception:
                                    remaining.discard(dev_path)
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
                        attrs = [s["attribute"] for s in sensors_on_dev]
                        for attempt in range(MAX_RETRIES + 1):
                            try:
                                if len(attrs) == 1:
                                    av = devp[dev_path].read_attribute(attrs[0])
                                    raw = av.value
                                    v = float(raw[0]) if hasattr(raw, "__len__") else float(raw)
                                    vals[sensors_on_dev[0]["label"]] = v
                                    data[sensors_on_dev[0]["label"]][iy, ix] = v
                                else:
                                    attr_vals = devp[dev_path].read_attributes(attrs)
                                    for av, s in zip(attr_vals, sensors_on_dev):
                                        raw = av.value
                                        v = float(raw[0]) if hasattr(raw, "__len__") else float(raw)
                                        vals[s["label"]] = v
                                        data[s["label"]][iy, ix] = v
                                break
                            except Exception as e:
                                if attempt == MAX_RETRIES:
                                    lg(f"⚠ Read {dev_path} {attrs}: {e}")
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
                        except Exception: pass

                # Flush after each Y row (important for 2D scans)
                if n_y > 1:
                    try: hfile.flush()
                    except Exception: pass

        finally:
            # ── Finalize: update status and close ─────────────────────────────
            self._finalize_hdf5(hfile, count, total, x_actual, t_actual,
                                data, x_plan, y_plan, active,
                                x_lbl, x_unit, hdf_scan, cfg)

        if count > 0:
            # Auto-demagnetize after FIELD scans
            if hdf_scan == "FIELD" and mag_p is not None:
                st("Auto-demagnetizing magnet…")
                demagnetize_magnet(mag_p, mag_cur_attr, log=lg)
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
        filename = os.path.join(
            day_dir, f"{cfg['name']}_{datetime.now().strftime('%H%M%S')}.h5")

        try:
            import json as _json
            hfile = h5py.File(filename, "w")

            # Root: minimal status + type
            hfile.attrs["scan_status"] = "running"
            hfile.attrs["scan_type"]   = "DC_HYST"
            hfile.attrs["timestamp"]   = datetime.now().isoformat()

            # /metadata/
            meta = hfile.create_group("metadata")
            meta.attrs["scan_name"]        = cfg.get("name", "dc_hyst")
            meta.attrs["hyst_device"]      = hyst_dev
            meta.attrs["MagneticField_V"]  = field_V
            meta.attrs["NumberOfPoints"]   = npts
            meta.attrs["Cycles"]           = cycles
            meta.attrs["IntegrationTime"]  = int_t
            meta.attrs["n_loop"]           = n_loop
            meta.attrs["operator"]         = cfg.get("operator", "")
            meta.attrs["sample_id"]        = cfg.get("sample_id", "")
            meta.attrs["notes"]            = cfg.get("notes", "")
            meta.attrs["incidence"]        = cfg.get("incidence", "")
            meta.attrs["polarization"]     = cfg.get("polarization", "")
            meta.attrs["lam2"]             = bool(cfg.get("lam2",  False))
            meta.attrs["lam4"]             = bool(cfg.get("lam4",  False))
            meta.attrs["noDC"]             = bool(cfg.get("noDC",  False))
            meta.attrs["mirror_shift_mm"]  = float(cfg.get("mirror_shift", 0.0))
            meta.attrs["channels_json"]    = _json.dumps(hyst_chs)
            # Scalar results written at completion
            for s in ("Hc", "Hshift", "Mr", "Ms"):
                meta.attrs[s] = float("nan")

            # /data/
            data_grp = hfile.create_group("data")
            d = data_grp.create_dataset("actuator_field", data=np.full(n_loop, np.nan))
            d.attrs["label"] = "Field"; d.attrs["unit"] = "mT"; d.attrs["role"] = "x"

            for c in active_ch:
                key = self._hdf5_key(c["label"])
                ds  = data_grp.create_dataset(key, data=np.full(n_loop, np.nan))
                ds.attrs["label"]           = c["label"]
                ds.attrs["unit"]            = c.get("unit", "V")
                ds.attrs["tango_attribute"] = c["attr"]
                ds.attrs["y_axis"]          = c.get("y_axis", "Y1")
                ds.attrs["role"]            = "sensor"

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
                hfile.attrs["scan_status"] = "aborted"
                hfile.attrs["timestamp_end"] = datetime.now().isoformat()
                hfile.flush(); hfile.close()
                return None

            # ── Final read: get definitive result (device may have updated
            #    after leaving RUNNING state — one final read ensures we have
            #    the fully converged N-cycle average) ─────────────────────────
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

            # ── Write to HDF5 ─────────────────────────────────────────────────
            hfile["data"]["actuator_field"][:n_actual] = field_arr[:n_actual]
            for c in active_ch:
                key = self._hdf5_key(c["label"])
                arr = result_arrays.get(c["label"], np.full(n_loop, np.nan))
                n_a = min(len(arr), n_loop)
                hfile["data"][key][:n_a] = arr[:n_a]
            # Scalar results → metadata attrs
            for s, v in scalars.items():
                hfile["metadata"].attrs[s] = v
            hfile.attrs["scan_status"]      = "completed"
            hfile.attrs["timestamp_end"]    = datetime.now().isoformat()
            hfile.attrs["duration_seconds"] = elapsed
            hfile["metadata"].attrs["duration_seconds"] = elapsed
            hfile["metadata"].attrs["points_acquired"]  = n_actual
            hfile.flush()
            hfile.close()

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

            return filename

        except Exception:
            lg(f"⚠ DC Hyst exception:\n{traceback.format_exc()}")
            try:
                hfile.attrs["scan_status"] = "error"
                hfile.flush(); hfile.close()
            except Exception:
                pass
            return None

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
        proxy.write_attribute(attr, target)
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
            f.attrs["scan_status"] = "running"
            f.attrs["scan_type"]   = hdf_scan
            f.attrs["timestamp"]   = datetime.now().isoformat()

            # ── /metadata/ ────────────────────────────────────────────────────
            meta = f.create_group("metadata")
            meta.attrs["scan_name"]        = cfg["name"]
            meta.attrs["n_x"]              = n_x
            meta.attrs["n_y"]              = n_y
            meta.attrs["points_planned"]   = n_x * n_y
            meta.attrs["points_acquired"]  = 0
            meta.attrs["integration_time"] = cfg["integration_time"]
            meta.attrs["settle_time"]      = cfg["settle_time"]
            meta.attrs["move_timeout"]     = float(cfg.get("move_timeout", 15.0))
            meta.attrs["operator"]         = cfg.get("operator", "")
            meta.attrs["sample_id"]        = cfg.get("sample_id", "")
            meta.attrs["notes"]            = cfg.get("notes", "")
            meta.attrs["incidence"]        = cfg.get("incidence", "")
            meta.attrs["polarization"]     = cfg.get("polarization", "")
            meta.attrs["lam2"]             = bool(cfg.get("lam2",  False))
            meta.attrs["lam4"]             = bool(cfg.get("lam4",  False))
            meta.attrs["noDC"]             = bool(cfg.get("noDC",  False))
            meta.attrs["mirror_shift_mm"]  = float(cfg.get("mirror_shift", 0.0))
            # Scan-geometry metadata — write the axis that is actually moving
            if is_field:
                segs = cfg.get("field_segments",
                               [[cfg.get("field_start_A",-1.0),
                                 cfg.get("field_stop_A",  1.0), n_x]])
                meta.attrs["field_segments_json"] = _json.dumps(segs)
                meta.attrs["field_device"]        = cfg.get("field_device", "")
                meta.attrs["field_current_attr"]  = cfg.get("field_current_attr", "")
            elif not is_time:
                if hdf_scan == "SPATIAL_Y":
                    # Y-only scan: the moving axis is act2; store under act2_* keys
                    # so downstream analysis always finds the right device/attr/label.
                    for k in ("device", "attr", "label", "unit"):
                        meta.attrs[f"act2_{k}"] = cfg.get(f"act2_{k}", "")
                    meta.attrs["axis_moving"] = "act2"
                else:
                    # SPATIAL_X or SPATIAL_XY
                    for pfx in (["act1"] + (["act2"] if is_2d else [])):
                        for k in ("device", "attr", "label", "unit"):
                            meta.attrs[f"{pfx}_{k}"] = cfg.get(f"{pfx}_{k}", "")
                    meta.attrs["axis_moving"] = "act1" if not is_2d else "both"
            # Full sensor config for offline reconstruction
            meta.attrs["sensors_json"] = _json.dumps(
                [{k: v for k, v in s.items() if k != "plot_visible"}
                 for s in sensors])

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
                d.attrs["label"] = label
                d.attrs["unit"]  = unit
                d.attrs["role"]  = role
                for k, v in kw.items():
                    d.attrs[k] = v
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

            # Private write-time helpers (underscore prefix = internal)
            f.attrs["_x_key"]   = ax_key
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
                f.attrs["scan_status"] = "empty"
            elif self._abort:
                f.attrs["scan_status"] = "aborted"
            else:
                f.attrs["scan_status"] = "completed"

            duration = float(t_actual.max()) if count > 0 else 0.0
            f.attrs["timestamp_end"]    = datetime.now().isoformat()
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
