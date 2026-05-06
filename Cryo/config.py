"""
config.py — Samba v3
Constants, hardware defaults, scan config schema, and JSON persistence.
"""
import copy, json, os
import numpy as np
from pathlib import Path
from typing import Dict, List


def _sanitize(obj):
    """Convert *obj* to a JSON-safe structure using an iterative approach.

    Handles numpy scalars/arrays, circular references, Qt types, and
    arbitrary depth without hitting Python's recursion limit.
    """
    def _convert_scalar(v):
        """Convert a single non-container value to a JSON primitive."""
        if v is None or isinstance(v, bool):
            return v
        if isinstance(v, int):
            return int(v)          # also catches np.integer (subclass of int)
        if isinstance(v, float):
            return float(v)        # also catches np.floating
        if isinstance(v, str):
            return str(v)
        if isinstance(v, np.ndarray):
            return v.tolist()      # returns plain list of plain scalars
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        if isinstance(v, np.bool_):
            return bool(v)
        return None                # placeholder for containers, or fallback

    def _is_container(v):
        return isinstance(v, (dict, list, tuple))

    # Fast path — not a container at all
    if not _is_container(obj):
        s = _convert_scalar(obj)
        if s is None and obj is not None:
            try:
                return str(obj)
            except Exception:
                return None
        return s

    seen = set()
    # We'll build the result by processing containers iteratively.
    # Stack items: (source, dest_container, dest_key_or_index)
    # We first create the output root and then populate it.

    if isinstance(obj, dict):
        root = {}
    else:
        root = []

    # stack: (source_container, dest_container)
    stack = [(obj, root)]

    while stack:
        src, dst = stack.pop()
        src_id = id(src)

        if src_id in seen:
            # Circular — dst is already created empty, leave it
            continue
        seen.add(src_id)

        if isinstance(src, dict):
            for k, v in src.items():
                key = str(k)
                if v is None or isinstance(v, (bool, int, float, str)):
                    dst[key] = _convert_scalar(v)
                elif isinstance(v, np.ndarray):
                    dst[key] = v.tolist()
                elif isinstance(v, (np.integer,)):
                    dst[key] = int(v)
                elif isinstance(v, (np.floating,)):
                    dst[key] = float(v)
                elif isinstance(v, np.bool_):
                    dst[key] = bool(v)
                elif isinstance(v, dict):
                    child = {}
                    dst[key] = child
                    stack.append((v, child))
                elif isinstance(v, (list, tuple)):
                    child = [None] * len(v)
                    dst[key] = child
                    stack.append((v, child))
                else:
                    try:
                        dst[key] = str(v)
                    except Exception:
                        dst[key] = None

        elif isinstance(src, (list, tuple)):
            for i, v in enumerate(src):
                if v is None or isinstance(v, (bool, int, float, str)):
                    dst[i] = _convert_scalar(v)
                elif isinstance(v, np.ndarray):
                    dst[i] = v.tolist()
                elif isinstance(v, (np.integer,)):
                    dst[i] = int(v)
                elif isinstance(v, (np.floating,)):
                    dst[i] = float(v)
                elif isinstance(v, np.bool_):
                    dst[i] = bool(v)
                elif isinstance(v, dict):
                    child = {}
                    dst[i] = child
                    stack.append((v, child))
                elif isinstance(v, (list, tuple)):
                    child = [None] * len(v)
                    dst[i] = child
                    stack.append((v, child))
                else:
                    try:
                        dst[i] = str(v)
                    except Exception:
                        dst[i] = None

    return root

# ─────────────────────────────────────────────────────────────────────────────
# UI / plot constants
# ─────────────────────────────────────────────────────────────────────────────
LEFT_COLORS  = ['#89b4fa','#74c7ec','#89dceb','#a6e3a1','#94e2d5']
RIGHT_COLORS = ['#f38ba8','#fab387','#f9e2af','#cba6f7','#eba0ac']
COLORMAPS    = ['RdBu_r','seismic','bwr','viridis','plasma','inferno','gray','hot','coolwarm']
SETUP_NAMES  = ["Green", "IR", "Cryo"]

# Sentinel keys used as x-axis identifiers in the live 1D plot
X_NATURAL    = "_natural_"   # scan's natural x: position (nm) or field (T)
X_TIME       = "_time_"      # elapsed time in seconds

# ─────────────────────────────────────────────────────────────────────────────
# Hardware device paths and attribute names, per setup
# ─────────────────────────────────────────────────────────────────────────────
KEITHLEY_RANGES = ["2mA", "20mA", "100mA"]

SETUP_HW_DEFAULTS: Dict[str, dict] = {
    "Green": {
        "magnet_device":       "hpp-N42/beckhoff/magnet",
        "magnet_current_attr": "current_longitudinal",
        "magnet_field_attr":   "field_longitudinal_corr",
        "relay_device":        "hpp-N42/current/PyRelais",
        "keithley_device":     "hpp-N42/current/PyKeithley",
        "zi_device":           "hpp-N42/measure/ZI2",
        "zi_tc_attr":          "timeconstant",
        "zi_order_attr":       "filterorder",
        "zi_settling_attr":    "settlingtime",
        "z_attr":              "position0",
        "focus_averagein":     "hpp-N42/beckhoff/averageIn2",
        "save_dir":            "~/moke_data",
    },
    "IR": {
        "magnet_device":       "hpp-N42/beckhoff/magnet",
        "magnet_current_attr": "current_polar",
        "magnet_field_attr":   "field_polar_corr",
        "relay_device":        "hpp-N42/current/PyRelais",
        "keithley_device":     "hpp-N42/current/PyKeithley2",
        "zi_device":           "hpp-N42/measure/ZI2",
        "zi_tc_attr":          "timeconstant",
        "zi_order_attr":       "filterorder",
        "zi_settling_attr":    "settlingtime",
        "z_attr":              "z",
        "focus_averagein":     "hpp-N42/beckhoff/averageIn2",
        "save_dir":            "~/moke_data",
    },
    "Cryo": {
        "magnet_device":       "",
        "magnet_current_attr": "current_polar",
        "magnet_field_attr":   "field_polar_corr",
        "relay_device":        "",
        # AttoDRY uses a superconducting magnet — never demagnetize
        "demagnetize_after_scan": False,
        "keithley_device":     "hpp-N42/current/PyKeithley2",
        "zi_device":           "hpp-N42/measure/ZI2",
        "zi_tc_attr":          "timeconstant",
        "zi_order_attr":       "filterorder",
        "zi_settling_attr":    "settlingtime",
        "attodry_device":      "hpp-N42/attoDRY/attoDRY",
        # Stage actuators — two geometry presets × two piezo types
        "stage_faraday": {
            "anm200": {
                "act1_device": "hpp-N42/attocube/ANM200",
                "act1_attr": "x", "act1_label": "X", "act1_unit": "nm",
                "act2_device": "hpp-N42/attocube/ANM200",
                "act2_attr": "y", "act2_label": "Y", "act2_unit": "nm",
                "z_device": "hpp-N42/attocube/ANM200",
                "z_attr": "z", "z_label": "Z", "z_unit": "nm",
            },
            "anc300": {
                "act1_device": "hpp-N42/attocube/ANC300",
                "act1_attr": "px", "act1_label": "X", "act1_unit": "steps",
                "act2_device": "hpp-N42/attocube/ANC300",
                "act2_attr": "py", "act2_label": "Y", "act2_unit": "steps",
                "z_device": "hpp-N42/attocube/ANC300",
                "z_attr": "pz", "z_label": "Z", "z_unit": "steps",
            },
        },
        "stage_voigt": {
            "anm200": {
                "act1_device": "hpp-N42/attocube/ANM200",
                "act1_attr": "x", "act1_label": "X", "act1_unit": "nm",
                "act2_device": "hpp-N42/attocube/ANM200",
                "act2_attr": "y", "act2_label": "Y", "act2_unit": "nm",
                "z_device": "hpp-N42/attocube/ANM200",
                "z_attr": "z", "z_label": "Z", "z_unit": "nm",
            },
            "anc300": {
                "act1_device": "hpp-N42/attocube/ANC300",
                "act1_attr": "px", "act1_label": "X", "act1_unit": "steps",
                "act2_device": "hpp-N42/attocube/ANC300",
                "act2_attr": "py", "act2_label": "Y", "act2_unit": "steps",
                "z_device": "hpp-N42/attocube/ANC300",
                "z_attr": "pz", "z_label": "Z", "z_unit": "steps",
            },
        },
        "focus_averagein":     "hpp-N42/beckhoff/averageIn2",
        # Keithley attribute names (editable in Setup Defaults tab)
        "keithley_attr_amplitude":  "amplitude",
        "keithley_attr_frequency":  "frequency",
        "keithley_attr_compliance": "compliance",
        "keithley_attr_range":      "range",
        "keithley_attr_current":    "current",
        # AttoDRY attribute names (editable in Setup Defaults tab)
        "attodry_attr_field_set":      "MagneticField",
        "attodry_attr_field_rb":       "MagneticField",
        "attodry_attr_temp_set":       "Temperature",
        "attodry_attr_temp_rb":        "Temperature",
        "attodry_attr_vti_temp":       "VtiTemperature",
        "attodry_attr_mag_temp":       "MagnetTemperature",
        "attodry_attr_reservoir_temp": "ReservoirTemperature",
        "attodry_attr_pressure_in":    "CryostatInPressure",
        "attodry_attr_pressure_out":   "CryostatOutPressure",
        "attodry_attr_heat_sample":    "SampleHeaterPower",
        "attodry_attr_heat_vti":       "VtiHeaterPower",
        "attodry_attr_heat_reservoir": "ReservoirHeaterPower",
        # AttoDRY boolean control state attributes
        "attodry_attr_mag_ctrl":       "MagneticFieldControl",
        "attodry_attr_temp_ctrl":      "FulltemperatureControl",
        "attodry_attr_persist":        "PersistentMode",
        # AttoDRY command names (fallback if bool attrs unavailable)
        "attodry_cmd_mag_ctrl":        "toggleMagneticFieldControl",
        "attodry_cmd_temp_ctrl":       "toggleFulltemperatureControl",
        "attodry_cmd_persist":         "togglePersistentMode",
        "save_dir":            "~/moke_data",
    },
}

# These keys used to be forcibly overwritten from SETUP_HW_DEFAULTS on every
# load — but that silently reverted intentional path changes.  Now the saved
# value is respected, and a warning is printed when it disagrees with the
# compiled-in default so operators notice accidental drift.
HW_WARN_KEYS = {
    "magnet_device", "magnet_current_attr", "magnet_field_attr",
    "relay_device", "keithley_device",
}

# ─────────────────────────────────────────────────────────────────────────────
# Scan / sensor defaults
# ─────────────────────────────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAY = 0.05   # seconds between sensor read retries

DEFAULT_SENSORS: List[dict] = [
    {"label":"ZI2 x1",   "device":"hpp-N42/measure/ZI2",         "attribute":"x1",    "unit":"V","enabled":True, "y_axis":"Y1","plot_visible":True, "trigger_cmd":"Start", "integ_time_attr":"integrationtime", "settling_attr":"settlingtime"},
    {"label":"ZI2 y1",   "device":"hpp-N42/measure/ZI2",         "attribute":"y1",    "unit":"V","enabled":True, "y_axis":"Y1","plot_visible":True, "trigger_cmd":"Start", "integ_time_attr":"integrationtime", "settling_attr":"settlingtime"},
    {"label":"ZI2 x2",   "device":"hpp-N42/measure/ZI2",         "attribute":"x2",    "unit":"V","enabled":False,"y_axis":"Y2","plot_visible":True, "trigger_cmd":"Start", "integ_time_attr":"integrationtime", "settling_attr":"settlingtime"},
    {"label":"ZI2 y2",   "device":"hpp-N42/measure/ZI2",         "attribute":"y2",    "unit":"V","enabled":False,"y_axis":"Y2","plot_visible":True, "trigger_cmd":"Start", "integ_time_attr":"integrationtime", "settling_attr":"settlingtime"},
    {"label":"DC diode", "device":"hpp-N42/beckhoff/analogIn2",  "attribute":"Value", "unit":"V","enabled":False,"y_axis":"Y2","plot_visible":True, "trigger_cmd":"",      "integ_time_attr":"",               "settling_attr":""},
    {"label":"avgIn1",   "device":"hpp-N42/beckhoff/averageIn1", "attribute":"Value", "unit":"V","enabled":False,"y_axis":"Y2","plot_visible":True, "trigger_cmd":"Start", "integ_time_attr":"integrationtime", "settling_attr":""},
]

def make_default_config(name: str = "scan_x") -> dict:
    return {
        "name": name,
        "scan_type": "SPATIAL",
        "scan_x": True, "scan_y": False,
        "act1_device": "smaract2/control/IR-controller",
        "act1_attr": "x", "act1_label": "X", "act1_unit": "nm",
        "act1_start": -10.0, "act1_stop": 10.0, "act1_npts": 51,
        "act2_device": "smaract2/control/IR-controller",
        "act2_attr": "y", "act2_label": "Y", "act2_unit": "nm",
        "act2_start": -10.0, "act2_stop": 10.0, "act2_npts": 51,
        "geometry": "Faraday",
        "stage_type": "anm200",
        "act1_directions": [[-10.0, 10.0]],
        "act2_directions": [[-10.0, 10.0]],
        "adaptive_settle_enabled": False,
        "adaptive_settle_k": 0.05,
        "field_start_A": -1.0, "field_stop_A": 1.0, "field_npts": 101,
        "field_segments": [[-1.0, 1.0, 101]],   # multi-segment AC sweep
        "field_device":        "",               # "" = use setup's magnet_device
        "field_current_attr":  "",               # "" = use setup's magnet_current_attr
        "integration_time": 0.1, "settle_time": 0.05, "move_timeout": 15.0,
        "sensors": copy.deepcopy(DEFAULT_SENSORS),
        "display_sensor": "ZI2 x1", "colormap": "RdBu_r",
        # DC Hysteresis (PyHysteresis Tango device)
        "hyst_device":   "hpp-N42/beckhoff/pyhystlongi",
        "hyst_field_V":  1.0,      # peak field amplitude sent to power supply (V)
        "hyst_int_time": 2.0,      # integration time per half-loop on Beckhoff (s)
        "hyst_npts":     100,      # number of field points per half-loop
        "hyst_cycles":   1,        # number of loops to average
        "hyst_channels": [
            {"label": "MOKE (R1)", "attr": "result1", "enabled": True,  "y_axis": "Y1"},
            {"label": "R2",        "attr": "result2", "enabled": False, "y_axis": "Y2"},
            {"label": "R3",        "attr": "result3", "enabled": False, "y_axis": "Y2"},
            {"label": "R4",        "attr": "result4", "enabled": False, "y_axis": "Y2"},
            {"label": "R5 (Hall)", "attr": "result5", "enabled": False, "y_axis": "Y2"},
            {"label": "R6",        "attr": "result6", "enabled": False, "y_axis": "Y2"},
        ],
    }

def make_default_setup(name: str) -> dict:
    return {**SETUP_HW_DEFAULTS[name], "active_idx": 0, "configs": [make_default_config()]}

# ─────────────────────────────────────────────────────────────────────────────
# Config persistence
# ─────────────────────────────────────────────────────────────────────────────
_cfg_env   = os.environ.get("SAMBA_CONFIG_DIR", "")
CONFIG_DIR = Path(_cfg_env).expanduser() if _cfg_env else Path.home() / ".config" / "moke_scan"

def _migrate_config(cfg: dict):
    """Migrate old config fields to current schema in-place."""
    st = cfg.get("scan_type", "1D_SPATIAL")
    if st == "1D_SPATIAL":
        cfg["scan_type"] = "SPATIAL"; cfg.setdefault("scan_x", True);  cfg.setdefault("scan_y", False)
    elif st == "2D_XY":
        cfg["scan_type"] = "SPATIAL"; cfg.setdefault("scan_x", True);  cfg.setdefault("scan_y", True)
    elif st == "1D_FIELD":
        cfg["scan_type"] = "FIELD";   cfg.setdefault("scan_x", False); cfg.setdefault("scan_y", False)
    elif st == "DC_HYST":
        pass   # already canonical — ensure hyst fields have defaults
    else:
        cfg.setdefault("scan_x", True); cfg.setdefault("scan_y", False)
    # Ensure DC_HYST fields exist on all configs (harmless on non-DC scans)
    cfg.setdefault("hyst_device",   "hpp-N42/beckhoff/hysteresis")
    cfg.setdefault("hyst_field_V",  1.0)
    cfg.setdefault("hyst_int_time", 2.0)
    cfg.setdefault("hyst_npts",     100)
    cfg.setdefault("hyst_cycles",   1)
    # Multi-segment field sweep (derive from old single start/stop/npts if absent)
    if "field_segments" not in cfg:
        cfg["field_segments"] = [[
            cfg.get("field_start_A", -1.0),
            cfg.get("field_stop_A",   1.0),
            cfg.get("field_npts",     101),
        ]]
    cfg.setdefault("field_device",       "")
    cfg.setdefault("field_current_attr", "")
    cfg.setdefault("hyst_channels", [
        {"label": "MOKE (R1)", "attr": "result1", "enabled": True,  "y_axis": "Y1"},
        {"label": "R2",        "attr": "result2", "enabled": False, "y_axis": "Y2"},
        {"label": "R3",        "attr": "result3", "enabled": False, "y_axis": "Y2"},
        {"label": "R4",        "attr": "result4", "enabled": False, "y_axis": "Y2"},
        {"label": "R5 (Hall)", "attr": "result5", "enabled": False, "y_axis": "Y2"},
        {"label": "R6",        "attr": "result6", "enabled": False, "y_axis": "Y2"},
    ])
    cfg.setdefault("geometry", "Faraday")
    cfg.setdefault("stage_type", "anm200")
    for s in cfg.get("sensors", []):
        s.setdefault("plot_visible", True)
        s.setdefault("device_name", "")       # registry device name (new format)
        s.setdefault("channel_attr", "")       # registry channel attr (new format)
        # Default to "integrationtime" — most devices support it.
        if not s.get("integ_time_attr"):
            s["integ_time_attr"] = "integrationtime"
        # If a device has an integration time attribute, it needs a trigger
        if not s.get("trigger_cmd") and s.get("integ_time_attr"):
            s["trigger_cmd"] = "Start"
        s.setdefault("settling_attr", "")
        if s.get("y_axis") == "Left Y":  s["y_axis"] = "Y1"
        elif s.get("y_axis") == "Right Y": s["y_axis"] = "Y2"
        else: s.setdefault("y_axis", "Y1")
    # Scan direction lists — backfill from legacy start/stop if absent
    if "act1_directions" not in cfg:
        cfg["act1_directions"] = [[cfg.get("act1_start", -10.0), cfg.get("act1_stop", 10.0)]]
    if "act2_directions" not in cfg:
        cfg["act2_directions"] = [[cfg.get("act2_start", -10.0), cfg.get("act2_stop", 10.0)]]
    cfg.setdefault("adaptive_settle_enabled", False)
    cfg.setdefault("adaptive_settle_k", 0.05)

def load_setup(name: str) -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"{name}.json"
    if path.exists():
        try:
            with open(path) as f:
                d = json.load(f)
            if name == "Cryo":
                _anc_default = copy.deepcopy(
                    SETUP_HW_DEFAULTS["Cryo"]["stage_faraday"]["anc300"])
                # v0 migration: very old configs stored flat act1/act2/z at setup level
                if "act1_device" in d and "stage_faraday" not in d:
                    old_block = {
                        "act1_device": d.pop("act1_device", ""),
                        "act1_attr":   d.pop("act1_attr",   "x"),
                        "act1_label":  d.pop("act1_label",  "X"),
                        "act1_unit":   d.pop("act1_unit",   "nm"),
                        "act2_device": d.pop("act2_device", ""),
                        "act2_attr":   d.pop("act2_attr",   "y"),
                        "act2_label":  d.pop("act2_label",  "Y"),
                        "act2_unit":   d.pop("act2_unit",   "nm"),
                        "z_device":    d.pop("z_device",    ""),
                        "z_attr":      d.pop("z_attr",      "z"),
                        "z_label":     d.pop("z_label",     "Z"),
                        "z_unit":      d.pop("z_unit",      "nm"),
                    }
                    d["stage_faraday"] = {"anm200": old_block, "anc300": _anc_default}
                    d["stage_voigt"]   = {
                        "anm200": copy.deepcopy(old_block), "anc300": _anc_default}
                # v1 migration: previous commit stored act1_device flat inside stage_faraday
                for geo_key in ("stage_faraday", "stage_voigt"):
                    blk = d.get(geo_key, {})
                    if "act1_device" in blk:
                        d[geo_key] = {"anm200": blk, "anc300": copy.deepcopy(_anc_default)}
            for k, v in SETUP_HW_DEFAULTS[name].items():
                if k in HW_WARN_KEYS:
                    saved = d.get(k)
                    if saved is None:
                        d[k] = v       # fill missing key from default
                    elif saved != v:
                        # Saved value differs from compiled-in default — keep
                        # the saved value but warn so operators notice drift.
                        print(f"Config [{name}] '{k}': using saved '{saved}' "
                              f"(default is '{v}')")
                    # else: saved == default, nothing to do
                else:
                    d.setdefault(k, v)
            if not d.get("configs"):
                d["configs"] = [make_default_config()]
            d.setdefault("active_idx", 0)
            for cfg in d["configs"]:
                _migrate_config(cfg)
            return d
        except Exception as e:
            print(f"Config load error ({path}): {e}")
    return make_default_setup(name)

def save_setup(name: str, data: dict):
    """Save setup config to JSON atomically (write-to-tmp, then rename).

    Writing directly to the final path risks partial-write corruption if the
    process is interrupted mid-write (power loss, OOM kill, etc.).  Using a
    temporary sibling file and os.replace() gives an atomic swap on POSIX
    systems, so the reader always sees either the old complete file or the new
    complete file — never a half-written one.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"{name}.json"
    tmp_path = CONFIG_DIR / f"{name}.json.tmp"
    clean = _sanitize(data)
    try:
        with open(tmp_path, "w") as f:
            json.dump(clean, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file if something went wrong before the rename
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
