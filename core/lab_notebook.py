"""
lab_notebook.py — per-setup CSV lab notebook, appended after each scan.

One CSV file per setup lives alongside the scan data in save_dir:
  ~/moke_data/lab_notebook_Green.csv
  ~/moke_data/lab_notebook_IR.csv
  ~/moke_data/lab_notebook_Cryo.csv

If the file doesn't exist it is created with a header row.
All errors are caught and logged — the notebook never crashes the UI.
"""
import csv
import logging
import os
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# Ordered column definitions: (csv_header, entry_key)
_COLUMNS = [
    ("Date",                     "_date"),
    ("Time",                     "_time"),
    ("Scan type",                "scan_type"),
    ("Config name",              "name"),
    ("Operator",                 "operator"),
    ("Sample ID",                "sample_id"),
    ("Notes",                    "notes"),
    ("Incidence",                "incidence"),
    ("Polarization",             "polarization"),
    ("lam2",                     "lam2"),
    ("lam4",                     "lam4"),
    ("noDC",                     "noDC"),
    ("Mirror shift (mm)",        "mirror_shift"),
    ("Integration time (s)",     "integration_time"),
    ("Settle time (s)",          "settle_time"),
    ("N points X",               "_n_x"),
    ("N points Y",               "_n_y"),
    ("Total points",             "_n_total"),
    ("Act1 start",               "act1_start"),
    ("Act1 stop",                "act1_stop"),
    ("Act1 step",                "_act1_step"),
    ("Act1 unit",                "act1_unit"),
    ("Act2 start",               "act2_start"),
    ("Act2 stop",                "act2_stop"),
    ("Act2 step",                "_act2_step"),
    ("Act2 unit",                "act2_unit"),
    ("Field start (A)",              "_field_start"),
    ("Field stop (A)",               "_field_stop"),
    ("Field step (A)",               "_field_step"),
    ("Temp sweep start (K)",         "_temp_sweep_start_K"),
    ("Temp sweep stop (K)",          "_temp_sweep_stop_K"),
    ("Temp sweep step (K)",          "_temp_sweep_step_K"),
    ("Keithley amplitude (mA)",  "hw_keithley_amplitude_mA"),
    ("Keithley frequency (Hz)",  "hw_keithley_frequency_Hz"),
    ("Keithley I out (mA)",      "hw_keithley_current_mA"),
    ("Keithley range",           "hw_keithley_range"),
    ("Keithley compliance (V)",  "hw_keithley_compliance_V"),
    ("ZI time constant (s)",     "hw_zi_tc_s"),
    ("ZI filter order",          "hw_zi_order"),
    ("ZI settling (s)",          "hw_zi_settling_s"),
    ("Relay state",              "hw_relay_state"),
    ("Field at start (mT)",      "hw_field_mT"),
    ("Magnet I at start (A)",    "hw_magnet_current_A"),
    ("Temperature (K)",          "hw_temperature_K"),
    ("VTI temperature (K)",      "hw_vti_temp_K"),
    ("Magnet temperature (K)",   "hw_magnet_temp_K"),
    ("Geometry",                 "geometry"),
    ("Stage type",               "stage_type"),
    ("File path",                "_hdf5_path"),
    ("Duration (s)",             "_duration_s"),
]

_HEADERS = [h for h, _ in _COLUMNS]
_KEYS    = [k for _, k in _COLUMNS]


def _compute_derived(entry: dict) -> dict:
    """Fill in derived _* keys from scan config fields."""
    out = dict(entry)

    # Date / Time from scan start timestamp
    ts = entry.get("_scan_start_time")
    if ts:
        dt = datetime.fromtimestamp(ts)
        out["_date"] = dt.strftime("%Y-%m-%d")
        out["_time"] = dt.strftime("%H:%M:%S")
    else:
        out.setdefault("_date", "")
        out.setdefault("_time", "")

    scan_type = entry.get("scan_type", "SPATIAL")
    # _is_temp_sweep is set explicitly by samba_cryo; fall back to checking
    # for the derived key so old entries without the flag still work.
    is_temp_sweep = bool(entry.get("_is_temp_sweep")) or "_temp_sweep_start_K" in entry
    # TIME scan: SPATIAL config with both axes deselected
    is_time    = (scan_type == "SPATIAL"
                  and not entry.get("scan_x", True)
                  and not entry.get("scan_y", False))
    is_field   = scan_type == "FIELD" and not is_temp_sweep
    is_dc_hyst = scan_type == "DC_HYST"
    is_spatial = scan_type not in ("FIELD", "DC_HYST") and not is_time

    # Override displayed scan type
    if is_temp_sweep:
        out["scan_type"] = "TEMP_SWEEP"
    elif is_time:
        out["scan_type"] = "TIME"

    # Point counts
    if is_field or is_temp_sweep or is_time:
        n_x = int(entry.get("act1_npts", entry.get("field_npts", 1))); n_y = 1
    elif is_dc_hyst:
        n_x = int(entry.get("hyst_npts", 1)); n_y = 1
    else:
        n_x = int(entry.get("act1_npts", 1))
        n_y = int(entry.get("act2_npts", 1))
    scan_2d = is_spatial and entry.get("scan_x", True) and entry.get("scan_y", False)
    out["_n_x"] = n_x
    out["_n_y"] = n_y if scan_2d else 1
    out["_n_total"] = out["_n_x"] * out["_n_y"]

    # Step sizes — only meaningful for spatial axes
    for pfx in ("act1", "act2"):
        if is_spatial:
            start = entry.get(f"{pfx}_start")
            stop  = entry.get(f"{pfx}_stop")
            npts  = int(entry.get(f"{pfx}_npts", 1))
            if start is not None and stop is not None and npts > 1:
                out[f"_{pfx}_step"] = (stop - start) / (npts - 1)
            else:
                out[f"_{pfx}_step"] = ""
        else:
            # Non-spatial / TIME scan: blank act range columns regardless of what
            # the cfg dict carries (trajectory panel may include stale values)
            out[f"_{pfx}_step"]    = ""
            out[f"{pfx}_start"]    = ""
            out[f"{pfx}_stop"]     = ""
            out[f"{pfx}_unit"]     = ""

    # Field sweep start/stop/step — real field scan only (not temp sweep)
    if is_field:
        segs = entry.get("field_segments", [])
        if segs:
            start_A = segs[0][0];  stop_A = segs[-1][1]
            total_pts = sum(max(1, int(s[2])) for s in segs)
            out["_field_start"] = start_A
            out["_field_stop"]  = stop_A
            out["_field_step"]  = (stop_A - start_A) / (total_pts - 1) if total_pts > 1 else ""
        else:
            out["_field_start"] = entry.get("field_start_A", "")
            out["_field_stop"]  = entry.get("field_stop_A", "")
            out["_field_step"]  = ""
    else:
        out["_field_start"] = ""
        out["_field_stop"]  = ""
        out["_field_step"]  = ""

    return out


def notebook_path(save_dir: str, setup_name: str) -> str:
    """Return the canonical notebook path for a given setup."""
    base = os.path.expanduser(save_dir)
    safe = setup_name.replace(" ", "_")
    return os.path.join(base, f"lab_notebook_{safe}.csv")


def _current_header(nb_path: str) -> list:
    """Read the first (header) row of an existing CSV; return [] on any error."""
    try:
        with open(nb_path, "r", newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            return next(reader, [])
    except Exception:
        return []


def append_measurement(nb_path: str, entry: dict) -> None:
    """Append one measurement row to the CSV lab notebook.

    Creates the file with a header row if it doesn't exist yet.
    If the file exists but has a different (stale) header — e.g. after a
    schema change that added columns — the old file is renamed to .bak and a
    fresh file is started so header and data rows stay aligned.

    ``entry`` is the scan cfg dict merged with extra ``_*`` keys:
      _scan_start_time, _hdf5_path, _duration_s.

    All errors are swallowed and logged — this must never crash the UI.
    """
    try:
        filled = _compute_derived(entry)
        row = {h: _fmt(filled.get(k)) for h, k in zip(_HEADERS, _KEYS)}

        os.makedirs(os.path.dirname(nb_path), exist_ok=True)

        need_header = not os.path.isfile(nb_path) or os.path.getsize(nb_path) == 0

        if not need_header and _current_header(nb_path) != _HEADERS:
            # Schema changed — back up the old file and start fresh
            bak = nb_path + ".bak"
            try:
                os.replace(nb_path, bak)
                log.warning("Lab notebook schema changed; old file backed up to %s", bak)
            except Exception:
                pass
            need_header = True

        with open(nb_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_HEADERS)
            if need_header:
                writer.writeheader()
            writer.writerow(row)

        log.debug("Lab notebook updated: %s", nb_path)

    except Exception:
        log.warning("Lab notebook update failed", exc_info=True)


def _fmt(v) -> str:
    """Format a value for CSV output; None → empty string."""
    if v is None:
        return ""
    if isinstance(v, float):
        # Avoid exponential notation for small-ish numbers
        return f"{v:.6g}"
    return str(v)
