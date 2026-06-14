"""
config.py — Samba v3
Constants, hardware defaults, scan config schema, and JSON persistence.
"""
import copy, json, logging
from pathlib import Path
from typing import Dict, List, TypedDict, Optional

log = logging.getLogger(__name__)

# Current schema version — bump when adding new fields
SCHEMA_VERSION = 3

# ─────────────────────────────────────────────────────────────────────────────
# UI / plot constants
# ─────────────────────────────────────────────────────────────────────────────
LEFT_COLORS  = ['#89b4fa','#74c7ec','#89dceb','#a6e3a1','#94e2d5']
RIGHT_COLORS = ['#f38ba8','#fab387','#f9e2af','#cba6f7','#eba0ac']
COLORMAPS    = [
    # Diverging (signed MOKE data) — listed first
    'RdBu_r','seismic','bwr','coolwarm','PuOr_r','RdYlBu_r','Spectral_r','PiYG','BrBG','twilight','twilight_shifted',
    # Sequential
    'viridis','plasma','inferno','magma','cividis','turbo',
    # Classic / misc
    'gray','hot','cool','copper','jet','rainbow','nipy_spectral',
]
SETUP_NAMES  = ["Green", "IR"]

# Sentinel keys used as x-axis identifiers in the live 1D plot
X_NATURAL    = "_natural_"   # scan's natural x: position (nm) or field (T)
X_TIME       = "_time_"      # elapsed time in seconds

# ─────────────────────────────────────────────────────────────────────────────
# Hardware device paths and attribute names, per setup
# ─────────────────────────────────────────────────────────────────────────────
KEITHLEY_RANGES = ["2mA", "20mA", "100mA"]

SETUP_HW_DEFAULTS: Dict[str, dict] = {
    "Green": {
        "magnet_device":         "hpp-N42/beckhoff/magnet",
        "magnet_current_attr":   "current_longitudinal",
        "magnet_field_attr":     "field_longitudinal_corr",
        "relay_device":          "hpp-N42/current/PyRelais",
        "relay_attr":            "switchvar",
        "keithley_device":       "hpp-N42/current/PyKeithley",
        "keithley_output_attr":      "amplitude",
        "keithley_amplitude_attr":  "amplitude",
        "keithley_frequency_attr":  "frequency",
        "keithley_range_attr":      "range",
        "keithley_compliance_attr": "compliance",
        "zi_device":             "hpp-N42/measure/ZI2",
        "zi_tc_attr":            "timeconstant",
        "zi_order_attr":         "filterorder",
        "zi_settling_attr":      "settlingtime",
        "z_device":              "smaract2/control/IR-controller",
        "z_attr":                "position0",
        "z_label":               "Z",
        "z_unit":                "nm",
        "focus_averagein":       "hpp-N42/beckhoff/averageIn2",
        "focus_attr":            "Value",
        "save_dir":              "~/moke_data/Data_Samba_Green",
        "notebook_dir":          "~/moke_data",
        "server_sync_dir":       "",
        "act1_device":           "smaract2/control/IR-controller",
        "act1_attr":             "x",
        "act1_label":            "X",
        "act1_unit":             "nm",
        "act2_device":           "smaract2/control/IR-controller",
        "act2_attr":             "y",
        "act2_label":            "Y",
        "act2_unit":             "nm",
        "trmoke_dg645":          "hpp-N42/delay/DG645",
        "rtv40_device":          "hpp-N42/pulser/RTV40",
        "field_settle_rate":     2.0,    # mT — max |Δfield_polar_corr| per 0.5 s
        "field_settle_timeout":  300.0,  # seconds
    },
    "IR": {
        "magnet_device":         "hpp-N42/beckhoff/magnet",
        "magnet_current_attr":   "current_polar",
        "magnet_field_attr":     "field_polar_corr",
        "relay_device":          "hpp-N42/current/PyRelais",
        "relay_attr":            "switchvar",
        "keithley_device":       "hpp-N42/current/PyKeithley2",
        "keithley_output_attr":      "amplitude",
        "keithley_amplitude_attr":  "amplitude",
        "keithley_frequency_attr":  "frequency",
        "keithley_range_attr":      "range",
        "keithley_compliance_attr": "compliance",
        "zi_device":             "hpp-N42/measure/ZI2",
        "zi_tc_attr":            "timeconstant",
        "zi_order_attr":         "filterorder",
        "zi_settling_attr":      "settlingtime",
        "z_device":              "smaract2/control/IR-controller",
        "z_attr":                "z",
        "z_label":               "Z",
        "z_unit":                "nm",
        "focus_averagein":       "hpp-N42/beckhoff/averageIn2",
        "focus_attr":            "Value",
        "save_dir":              "~/moke_data/Data_Samba_IR",
        "notebook_dir":          "~/moke_data",
        "server_sync_dir":       "",
        "act1_device":           "smaract2/control/IR-controller",
        "act1_attr":             "x",
        "act1_label":            "X",
        "act1_unit":             "nm",
        "act2_device":           "smaract2/control/IR-controller",
        "act2_attr":             "y",
        "act2_label":            "Y",
        "act2_unit":             "nm",
        "trmoke_dg645":          "hpp-N42/delay/DG645",
        "rtv40_device":          "hpp-N42/pulser/RTV40",
        "field_settle_rate":     2.0,    # mT — max |Δfield_polar_corr| per 0.5 s
        "field_settle_timeout":  300.0,  # seconds
    },
    "Cryo": {
        "magnet_device":         "",
        "magnet_current_attr":   "current_polar",
        "magnet_field_attr":     "field_polar_corr",
        "relay_device":          "",
        "relay_attr":            "switchvar",
        "keithley_device":       "hpp-N42/current/PyKeithley2",
        "keithley_output_attr":      "amplitude",
        "keithley_amplitude_attr":  "amplitude",
        "keithley_frequency_attr":  "frequency",
        "keithley_range_attr":      "range",
        "keithley_compliance_attr": "compliance",
        "zi_device":             "hpp-N42/measure/ZI2",
        "zi_tc_attr":            "timeconstant",
        "zi_order_attr":         "filterorder",
        "zi_settling_attr":      "settlingtime",
        "z_device":              "smaract2/control/IR-controller",
        "z_attr":                "z",
        "z_label":               "Z",
        "z_unit":                "nm",
        "focus_averagein":       "hpp-N42/beckhoff/averageIn2",
        "focus_attr":            "Value",
        "save_dir":              "~/moke_data/Data_Samba_Cryo",
        "notebook_dir":          "~/moke_data",
        "server_sync_dir":       "",
        "act1_device":           "smaract2/control/IR-controller",
        "act1_attr":             "x",
        "act1_label":            "X",
        "act1_unit":             "nm",
        "act2_device":           "smaract2/control/IR-controller",
        "act2_attr":             "y",
        "act2_label":            "Y",
        "act2_unit":             "nm",
        "trmoke_dg645":          "hpp-N42/delay/DG645",
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
        "_schema_version": SCHEMA_VERSION,
        "name": name,
        "scan_type": "SPATIAL",
        "scan_x": True, "scan_y": False,
        "act1_device": "smaract2/control/IR-controller",
        "act1_attr": "x", "act1_label": "X", "act1_unit": "nm",
        "act1_start": -10.0, "act1_stop": 10.0, "act1_npts": 51,
        "act2_device": "smaract2/control/IR-controller",
        "act2_attr": "y", "act2_label": "Y", "act2_unit": "nm",
        "act2_start": -10.0, "act2_stop": 10.0, "act2_npts": 51,
        "zigzag": True,
        "fast_axis": "act1",   # which axis is swept per line: act1 (X) or act2 (Y)
        "field_start_A": -1.0, "field_stop_A": 1.0, "field_npts": 101,
        "field_segments": [[-1.0, 1.0, 101]],   # multi-segment AC sweep
        "field_device":        "",               # "" = use setup's magnet_device
        "field_current_attr":  "",               # "" = use setup's magnet_current_attr
        "field_readback_attr": "",               # "" = use setup's magnet_field_attr
        "field_x_label":       "Field",
        "field_x_unit":        "mT",             # Beckhoff magnet returns mT
        "field_setpoint_unit": "A",              # current commanded in Ampere
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
        # TR-MOKE defaults
        "trmoke_dg645":     "hpp-N42/delay/DG645",
        "trmoke_channel":   "A",
        "trmoke_output":    "AB",
        "trmoke_unit":      "ns",
        "trmoke_ref":       "T0",
        "trmoke_prescale":  1,
        "trmoke_amplitude": 3.5,
        "trmoke_offset":    0.0,
        "trmoke_polarity":  "Positive",
        "trmoke_trig_src":  "Ext Rising",
        "trmoke_trig_thr":  1.0,
        "trmoke_reps":      1,
        "trmoke_sample":    "",
        "trmoke_pump":      "",
        "trmoke_probe":     "",
        "trmoke_notes":     "",
        "trmoke_field_A":   0.0,
        "trmoke_sensors":   [],
        # RTV40 pulse-width sync (TR-MOKE)
        "rtv40_sync_enabled":   False,
        "rtv40_base_width_ns":  1.0,
        "rtv40_trig_src":       1,      # 0=Off, 1=External, 2=Internal
        "rtv40_trig_rate":      1000.0,
        "rtv40_polarity":       1,      # 0=Negative, 1=Positive
    }

def make_default_setup(name: str) -> dict:
    return {**SETUP_HW_DEFAULTS[name], "active_idx": 0, "configs": [make_default_config()]}

# ─────────────────────────────────────────────────────────────────────────────
# Config persistence
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".config" / "moke_scan"

# ─────────────────────────────────────────────────────────────────────────────
# Versioned config migrations
# Each function migrates from version N-1 → N.  The chain runs until
# _schema_version == SCHEMA_VERSION.
# ─────────────────────────────────────────────────────────────────────────────

def _migrate_v0_to_v1(cfg: dict):
    """v0→v1: Canonicalize scan_type names, add DC hyst / field segment fields."""
    st = cfg.get("scan_type", "1D_SPATIAL")
    if st == "1D_SPATIAL":
        cfg["scan_type"] = "SPATIAL"; cfg.setdefault("scan_x", True);  cfg.setdefault("scan_y", False)
    elif st == "2D_XY":
        cfg["scan_type"] = "SPATIAL"; cfg.setdefault("scan_x", True);  cfg.setdefault("scan_y", True)
    elif st == "1D_FIELD":
        cfg["scan_type"] = "FIELD";   cfg.setdefault("scan_x", False); cfg.setdefault("scan_y", False)
    elif st == "DC_HYST":
        pass
    else:
        cfg.setdefault("scan_x", True); cfg.setdefault("scan_y", False)
    # DC hyst defaults
    cfg.setdefault("hyst_device",   "hpp-N42/beckhoff/hysteresis")
    cfg.setdefault("hyst_field_V",  1.0)
    cfg.setdefault("hyst_int_time", 2.0)
    cfg.setdefault("hyst_npts",     100)
    cfg.setdefault("hyst_cycles",   1)
    # Multi-segment field sweep
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
    # Sensor field normalization
    for s in cfg.get("sensors", []):
        s.setdefault("plot_visible", True)
        s.setdefault("device_name", "")
        s.setdefault("channel_attr", "")
        if not s.get("integ_time_attr"):
            s["integ_time_attr"] = "integrationtime"
        if not s.get("trigger_cmd") and s.get("integ_time_attr"):
            s["trigger_cmd"] = "Start"
        s.setdefault("settling_attr", "")
        if s.get("y_axis") == "Left Y":  s["y_axis"] = "Y1"
        elif s.get("y_axis") == "Right Y": s["y_axis"] = "Y2"
        else: s.setdefault("y_axis", "Y1")


def _migrate_v2_to_v3(cfg: dict):
    """v2→v3: Add RTV40 pulse-width sync defaults."""
    cfg.setdefault("rtv40_sync_enabled",  False)
    cfg.setdefault("rtv40_base_width_ns", 1.0)
    cfg.setdefault("rtv40_trig_src",      1)
    cfg.setdefault("rtv40_trig_rate",     1000.0)
    cfg.setdefault("rtv40_polarity",      1)


def _migrate_v1_to_v2(cfg: dict):
    """v1→v2: Add TR-MOKE defaults."""
    cfg.setdefault("trmoke_dg645",     "hpp-N42/delay/DG645")
    cfg.setdefault("trmoke_channel",   "A")
    cfg.setdefault("trmoke_output",    "AB")
    cfg.setdefault("trmoke_unit",      "ns")
    cfg.setdefault("trmoke_ref",       "T0")
    cfg.setdefault("trmoke_prescale",  1)
    cfg.setdefault("trmoke_amplitude", 3.5)
    cfg.setdefault("trmoke_offset",    0.0)
    cfg.setdefault("trmoke_polarity",  "Positive")
    cfg.setdefault("trmoke_trig_src",  "Ext Rising")
    cfg.setdefault("trmoke_trig_thr",  1.0)
    cfg.setdefault("trmoke_reps",      1)
    cfg.setdefault("trmoke_sample",    "")
    cfg.setdefault("trmoke_pump",      "")
    cfg.setdefault("trmoke_probe",     "")
    cfg.setdefault("trmoke_notes",     "")
    cfg.setdefault("trmoke_field_A",   0.0)
    cfg.setdefault("trmoke_sensors",   [])


# Ordered list of (target_version, migration_func)
_MIGRATIONS = [
    (1, _migrate_v0_to_v1),
    (2, _migrate_v1_to_v2),
    (3, _migrate_v2_to_v3),
]


def _migrate_config(cfg: dict):
    """Run all applicable migrations to bring cfg up to SCHEMA_VERSION."""
    v = cfg.get("_schema_version", 0)
    for target_v, fn in _MIGRATIONS:
        if v < target_v:
            fn(cfg)
            v = target_v
    cfg["_schema_version"] = SCHEMA_VERSION

def load_setup(name: str) -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"{name}.json"
    if path.exists():
        try:
            with open(path) as f:
                d = json.load(f)
            # Migrate: old paths → current per-setup data directory under moke_data
            _old_paths = {"~/moke_data", f"~/Data_Samba_{name}"}
            if d.get("save_dir") in _old_paths:
                d["save_dir"] = SETUP_HW_DEFAULTS[name]["save_dir"]
                log.info("Migrated save_dir → %s", d["save_dir"])
            d.setdefault("notebook_dir", "~/moke_data")
            d.setdefault("server_sync_dir", SETUP_HW_DEFAULTS[name]["server_sync_dir"])
            for k, v in SETUP_HW_DEFAULTS[name].items():
                if k in HW_WARN_KEYS:
                    saved = d.get(k)
                    if saved is None:
                        d[k] = v       # fill missing key from default
                    elif saved != v:
                        log.warning("Config [%s] '%s': using saved '%s' "
                                    "(default is '%s')", name, k, saved, v)
                else:
                    d.setdefault(k, v)
            if not d.get("configs"):
                d["configs"] = [make_default_config()]
            d.setdefault("active_idx", 0)
            for cfg in d["configs"]:
                _migrate_config(cfg)
            return d
        except Exception as e:
            log.error("Config load error (%s): %s", path, e)
    return make_default_setup(name)

def save_setup(name: str, data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_DIR / f"{name}.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.error("Config save error: %s", e)
