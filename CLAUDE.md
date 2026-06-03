# SAMBA — Developer Documentation

**Scanning MOKE Acquisition & Measurement Application**
ETH Zürich — Intermag Lab | Creator: Jakub Strnad | Collaborator: Tobias Goldenberg
April 2026

---

## 1. Overview

SAMBA is a PyQt6 desktop application for controlling scanning Magneto-Optical Kerr Effect
(MOKE) measurements. It supports spatial scans, AC field sweeps, DC hysteresis, TR-MOKE
(time-resolved), and temperature sweeps across three experimental setups: Green, IR, and Cryo.

**Stack:** Python 3.13, PyQt6, matplotlib (QtAgg), HDF5 (h5py, NXdata for PyMca), TANGO Controls (pytango), JSON config, Catppuccin Mocha dark theme.

---

## 2. Repository Layout

```
Samba/
├── core/                    # Shared modules (scan engine, hardware, plotting, etc.)
│   ├── scan/
│   │   ├── runner.py        # ScanRunner — pure Python, no Qt dependency
│   │   └── workers.py       # ScanWorker / ScanlistWorker — QThread wrappers
│   ├── hardware.py          # Proxy cache, safe_read/write, SimProxy
│   ├── plot_widgets.py      # Live1DWidget, Live2DWidget
│   ├── data_browser.py      # HDF5 file browser
│   ├── calibration.py       # Autofocus, time scan plotting
│   ├── device_registry.py   # Device/channel definitions, registry editor UI
│   ├── script_console.py    # Embedded Python console
│   └── play_intro.py        # Splash screen
│
├── Samba_main/              # Green + IR setups
│   ├── samba.py             # MainWindow entry point
│   ├── config.py            # SETUP_HW_DEFAULTS, config migration, persistence
│   ├── setup_lock.py        # Client-side setup locking (acquire/release)
│   ├── panels/              # UI panels package
│   │   ├── trajectory.py    # Scan type, actuators, field segments, DG645
│   │   ├── right_panel.py   # Sensor picker, colormap, display sensor
│   │   ├── sensor_picker.py # SensorPickerRow — device+channel dropdown
│   │   ├── hardware_panel.py# Keithley, field/relay, lock-in readback
│   │   ├── setup_defaults.py# Per-setup device paths, lock-in attr config
│   │   ├── config_list.py   # Config list sidebar
│   │   ├── scanlist.py      # Scanlist panel
│   │   └── _widgets.py      # NoScroll widgets, MokeMetadataGroup
│   └── tango_devices/       # Tango device server source code
│       ├── ZurichInstruments_lockin/
│       │   ├── ZI.py / ZI2.py           # ZI MFLI device servers
│       │   ├── ThreadZI_DAQ.py / ThreadZI2_DAQ.py  # poll()+numpy threads
│       │   └── install_ZI_DAQ.sh / install_ZI2_DAQ.sh
│       ├── RTV40/
│       │   ├── RTV40_Pulser.py          # Kentech RTV40/RTV30 pulse generator
│       │   ├── install_RTV40.sh         # pip-installable package installer
│       │   └── RTV_30_manual.pdf        # Hardware manual
│       └── SetupLock/
│           └── SetupLock.py              # Setup lock Tango device server
│
├── Cryo/                    # Cryo setup (separate entry point)
│   ├── samba_cryo.py        # CryoMainWindow — single "Cryo" setup
│   ├── config.py            # Cryo-specific defaults
│   ├── panels_cryo.py       # CryoHardwarePanel (AttoDRY + Keithley)
│   ├── cryo_monitor.py      # Rolling temperature/pressure plots
│   ├── keithley_mixin.py    # Shared Keithley 6221 UI code
│   ├── setup_lock.py        # Copy of setup_lock client
│   ├── defaults_panel.py    # Setup defaults for Cryo
│   └── scan/ → imports core/scan/
│
└── CLAUDE.md                # This file
```

**Code sharing:** Samba_main and Cryo both import shared modules from `core/`. Each
directory adds its own UI panels and setup-specific configuration. The `scan/` directories
in Samba_main and Cryo re-export from `core/scan/`.

---

## 3. Hardware & Device Map

### Stages (SmarAct Positioners)

| Setup | TANGO Path | Attributes | Units |
|-------|-----------|------------|-------|
| IR | `smaract2/control/IR-controller` | x, y, z | nm |
| Green | (similar path) | x, y, z | nm |
| Cryo | (similar path) | x, y, z | nm |

### Lock-In Amplifiers (Zurich Instruments MFLI)

| Device | TANGO Path | Serial | Attributes |
|--------|-----------|--------|------------|
| ZI (Green) | `hpp-N42/measure/ZI` | dev4855 | x1–x4, y1–y4 |
| ZI2 (IR) | `hpp-N42/measure/ZI2` | dev30933 | x1–x4, y1–y4 |

### Beckhoff PLC Devices

| Device | Example Path | Purpose |
|--------|-------------|---------|
| DoubleInBeckhoff | `hpp-N42/beckhoff/analogIn2` | Focus diode (DC) |
| DoubleInBeckhoffAverage | `hpp-N42/beckhoff/averageIn1` | Averaged balanced diode |
| Magnet | `hpp-N42/beckhoff/magnet` | Field readback (corrected) |
| PyHysteresis | `hpp-N42/beckhoff/pyhystlongi` | DC hysteresis controller |

### Other Devices

| Device | TANGO Path | Purpose |
|--------|-----------|---------|
| DG645 | `intermag/dg645/1` | Delay generator (TR-MOKE) |
| Keithley 6221 | `hpp-N42/current/PyKeithley2` | AC excitation current source |
| Relay | `hpp-N42/current/PyRelais` | Optical relay switching |
| AttoDRY | `hpp-N42/attoDRY/attoDRY` | Cryostat (Cryo only) |
| Setup Lock | `hpp-N42/samba/lock` | Multi-computer scan mutex |
| RTV40 | `hpp-N42/pulser/RTV40` | Kentech RTV40/RTV30 pulse generator |

---

## 4. Config & Setup Structure

### Two-level hierarchy

**Setup dict** — one per physical rig (Green, IR, Cryo). Contains hardware device paths
and a list of scan configs. Persisted at `~/.config/moke_scan/{SetupName}.json`.

**Scan config dict** — one per measurement preset within a setup. Contains scan type,
axes, ranges, points, sensor list, integration time, settle time, metadata.

### Key setup fields (from `SETUP_HW_DEFAULTS`)

```
magnet_device, magnet_current_attr, magnet_field_attr
relay_device, relay_attr
keithley_device, keithley_amplitude_attr, keithley_frequency_attr, ...
zi_device, zi_tc_attr, zi_order_attr, zi_settling_attr
act1_device, act1_attr, act2_device, act2_attr
z_device, z_attr (focus axis)
save_dir
```

### Key scan config fields (from `make_default_config`)

```
scan_type: SPATIAL | FIELD | DC_HYST | TR_MOKE
scan_x, scan_y: which axes are active
act1_start, act1_stop, act1_npts: X axis range
act2_start, act2_stop, act2_npts: Y axis range
integration_time: seconds (written to ZI device before scan)
settle_time: seconds (post-move wait per point)
sensors: list of sensor dicts (see §8)
field_segments: [[start, stop, npts], ...] for multi-segment sweeps
hyst_*: DC hysteresis parameters
trmoke_*: TR-MOKE / DG645 parameters
```

### Schema migration

Configs are versioned with `_schema_version`. On load, `_migrate_config()` runs a chain:
- **v0→v1:** Canonicalize scan type names, add DC hyst / field segment defaults, normalize sensor fields (add `settling_attr`, `plot_visible`, etc.)
- **v1→v2:** Add TR-MOKE defaults
- **v2→v3:** Add RTV40 sync defaults (`rtv40_sync_enabled`, `rtv40_base_width_ns`, `rtv40_trig_src`, `rtv40_trig_rate`, `rtv40_polarity`)

---

## 5. Scan Engine

**Files:** `core/scan/runner.py` (pure-Python scan logic), `core/scan/workers.py` (QThread wrappers)

### Scan types

| Type | X axis | Y axis | Movement | Notes |
|------|--------|--------|----------|-------|
| SPATIAL | Stage actuator 1 | Stage actuator 2 (optional) | SmarAct nm positioning | 1D or 2D raster |
| FIELD | Magnet current (A) | — | No physical motion | Multi-segment current sweeps |
| DC_HYST | Delegated to PyHysteresis device | — | — | Full hysteresis loops via Beckhoff |
| TR_MOKE | DG645 delay | — | — | Time-resolved pump-probe |
| TIME | Elapsed seconds | — | No movement | Repeated acquisition at fixed position |

### Per-point acquisition sequence (SPATIAL / FIELD / TIME)

Each point runs a **6-phase sequence** in `_run_point()`:

```
┌──────────────────────────────────────────────────────────────┐
│ 1. MOVE        Write setpoint to actuator / magnet           │
│ 2. SETTLE      time.sleep(settle_time)                       │
│ 3. ZI SETTLE   time.sleep(max(lockin_settling values))       │
│ 4. TRIGGER     command_inout_asynch("Start") on all devices  │
│ 5. PHASE A     Poll state until RUNNING  (≤200 ms timeout)   │
│ 6. PHASE B     Poll state until NOT RUNNING (move_timeout)   │
│ 7. GUARD       time.sleep(10 ms)                             │
│ 8. READ        read_attribute(s) per device, batch per device │
└──────────────────────────────────────────────────────────────┘
```

**Phase details:**

1. **Move** — For SPATIAL: `safe_write(act_proxy, act_attr, x_pos)`. For FIELD: `safe_write(mag_proxy, mag_cur_attr, x_pos)`. For TIME: no-op.

2. **Settle** — Post-movement mechanical settling. FIELD scans enforce a minimum of 50 ms (`max(settle_time, 0.05)`). TIME scans skip entirely (`settle = 0`).

3. **Lock-in settling** — The scan engine reads `settling_attr` from each sensor device at scan start and stores the values in a `lockin_settling` dict. Before each trigger, it sleeps for the **maximum** of all settling values. This ensures the ZI low-pass filter has settled after any field/position change. Example: ZI2 with TC=0.1 s, order=6 → settling = 13.1 × 0.1 = 1.31 s.

4. **Trigger** — Fires `command_inout_asynch("Start")` to all sensor devices that have a `trigger_cmd`. Near-simultaneous dispatch (~100 µs jitter). Falls back to synchronous loop if async is unavailable. Records `t_trigger = time.time() - t0`.

5. **Phase A (entry polling)** — Polls every 2 ms until each triggered device's `state()` becomes `RUNNING`. Timeout: 200 ms. This phase exists because the ZI device's `Start()` spawns a background thread that transitions to RUNNING a few ms after the command returns. Without Phase A, Phase B would see the device still `ON` and immediately read stale zeros.

6. **Phase B (completion polling)** — Polls every 10 ms until each device leaves `RUNNING` (returns to `ON`). Timeout: `cfg["move_timeout"]`. Logs a warning if timeout is exceeded.

7. **Readout guard** — 10 ms sleep after state change. Lets device output registers settle with final averaged values.

8. **Read** — Sensors grouped by device for batch reading. Uses `read_attributes([names])` for multi-attribute devices, `read_attribute(name)` for single. **Deduplication:** `dict.fromkeys()` removes duplicate attribute names (e.g., two display channels reading "x1") while preserving order. The raw value is mapped back to each sensor's label.

### Timestamp calculation

```python
t_elapsed = t_trigger + int_time / 2.0   # center of integration window
```

This is physically meaningful: the timestamp represents the midpoint of the averaging window, not the readout moment.

### Integration time configuration

Written once at scan start to every sensor device via its `integ_time_attr` (e.g., `"integrationtime"`). Uses `fresh_proxy()` (uncached) for reliability. Reads back the value and logs a warning if the readback differs by >1e-6 s.

### Device grouping and trigger set

```python
# Group sensors by TANGO device path
dev_sensors: Dict[str, List[dict]] = defaultdict(list)
for s in active_sensors:
    dev_sensors[s["device"]].append(s)

# One trigger command per device (first sensor's trigger_cmd wins)
trigger_devs: Dict[str, str] = {}
for s in active_sensors:
    tcmd = s.get("trigger_cmd", "").strip()
    if tcmd and s["device"] not in trigger_devs:
        trigger_devs[s["device"]] = tcmd
```

If no trigger devices exist, the engine falls back to `time.sleep(int_time)` instead of state polling.

### FIELD scan specifics

- Magnet current written per point: `safe_write(mag_proxy, mag_cur_attr, x_pos)`
- Field readback: `safe_read(mag_proxy, mag_fld_attr)` with fallback estimate `0.15 × current`
- Segmented ranges: `field_segments = [[start, stop, npts], ...]` concatenated via `np.concatenate([linspace(...)])`
- Auto-demagnetize after scan completes (unless `demagnetize_after_scan == False` for superconducting magnets)

### DC_HYST scan flow

Entirely delegated to the PyHysteresis Beckhoff device:

1. Write parameters: `MagneticField` (V), `NumberOfPoints`, `Cycles`, `IntegrationTime`
2. Send `Start` command
3. Poll device state every `max(0.2, int_time / 4.0)` seconds
4. Read arrays: `field`, `result1`–`result6` (mapped from `hyst_channels`)
5. Emit per-point callbacks for live plotting; re-emit each cycle as the N-cycle average accumulates
6. Read scalar results: Hc, Hshift, Mr, Ms after completion

### TIME scan specifics

- No movement, no settle phase
- X axis = elapsed seconds: `x_read = time.time() - t0`
- Timestamp corrected to integration midpoint: `t_elapsed = t_trigger + int_time / 2.0`
- Runs for `n_x` points with trigger/integration at each

### HDF5 output

- File created/opened immediately at scan start (crash-safe: partial data survives)
- Data written per-point as it arrives
- NXdata format for PyMca compatibility
- Metadata attributes include all config fields, lock-in settling time, timestamps

---

## 6. ZI / ZI2 Tango Device Servers

**Files:** `Samba_main/tango_devices/ZurichInstruments_lockin/ZI.py`, `ZI2.py`, `ThreadZI_DAQ.py`, `ThreadZI2_DAQ.py`

### Purpose

TANGO device servers wrapping Zurich Instruments MFLI lock-in amplifiers. Each server manages 4 demodulators (channels 1–4), providing averaged X/Y output via a poll-and-average approach.

### ZI vs ZI2

| Aspect | ZI (Green) | ZI2 (IR) |
|--------|-----------|----------|
| Serial | dev4855 | dev30933 |
| Default host | 192.168.1.62 | 192.168.1.144 |
| Demod 4 harmonic | 4 | 1 (changed 04.05.2024) |
| Thread class | ThreadZI | ThreadZI2 |
| Integration logic | Identical | Identical |

### Key attributes

**Read-only (per demodulator):**

| Attribute | Type | Description |
|-----------|------|-------------|
| `x1`–`x4` | DevDouble | Averaged X component (µV, ×√2) |
| `y1`–`y4` | DevDouble | Averaged Y component (µV, ×√2) |
| `timeconstant` | DevDouble | Current low-pass filter TC (seconds) |
| `filterorder` | DevLong | Filter order (1–8) |
| `settlingtime` | DevDouble | 99% settling = settle_99[order] × TC |
| `phase1`–`phase4` | DevDouble | Demodulator phase shift |
| `frequency` | DevDouble | Oscillator frequency |
| `samplingrate` | DevDouble | Demodulator sampling rate |

**Read-write:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `integrationtime` | DevDouble | Collection duration (seconds, written by Samba) |
| `Amplitude` | DevDouble | Signal output amplitude |

### Settling time computation

The `settlingtime` attribute is computed on-device from the low-pass filter parameters:

```python
_SETTLE_99 = {
    1: 4.6,   2: 6.6,   3: 8.4,   4: 10.0,
    5: 11.6,  6: 13.1,  7: 14.6,  8: 16.0
}
settlingtime = _SETTLE_99[filterorder] * timeconstant
```

These are the factors for 99% settling of a Butterworth filter cascade. Example: order=6, TC=0.1 s → settling = 1.31 s. Samba reads this value at scan start and sleeps for it before each trigger (see §5, phase 3).

### Commands

| Command | Description |
|---------|-------------|
| `Start` | Begin integration: spawns poll thread, sets state → RUNNING |
| `SetIntegTime(float)` | Alternative way to set integration time |

### Poll-and-average mechanism (`ThreadZI_DAQ` / `ThreadZI2_DAQ`)

When `Start()` is called:

1. Thread reads stored `integrationtime` value
2. **Flush** — polls DAQ for ~100 ms to discard stale buffered samples
3. **Collect** — polls DAQ for exactly `integrationtime` seconds, accumulating samples
4. **Average** — `value = np.mean(samples) * 1e6 * sqrt(2)` (converts to µV RMS)
5. Writes averaged values to `x1`–`x4`, `y1`–`y4` output attributes
6. Sets device state → ON

The `poll()` call returns whatever samples the MFLI has buffered since the last poll. The numpy averaging ensures noise reduction proportional to √N. The `× 1e6 × √2` scaling converts from V peak to µV RMS.

### State machine

```
INIT → ON (idle, ready for trigger)
     → RUNNING (integration in progress, thread collecting samples)
     → ON (integration complete, results in x1–x4/y1–y4)
     → FAULT (connection lost to MFLI)
```

The scan engine's two-phase polling (§5) relies on this: Phase A waits for ON→RUNNING, Phase B waits for RUNNING→ON.

---

## 7. Setup Lock

**Server:** `Samba_main/tango_devices/SetupLock/SetupLock.py`
**Client:** `Samba_main/setup_lock.py`, `Cryo/setup_lock.py`
**TANGO path:** `hpp-N42/samba/lock`

### Purpose

Prevents two computers from running scans on the same physical setup simultaneously. Three independent locks: Green, IR, Cryo.

### Server-side attributes

| Attribute | Type | Access | Description |
|-----------|------|--------|-------------|
| `greenbusy` | DevBoolean | RW | True while Green is scanning |
| `greeninfo` | DevString | RW | Stamp: "hostname:pid @ HH:MM:SS" |
| `irbusy` | DevBoolean | RW | True while IR is scanning |
| `irinfo` | DevString | RW | Stamp |
| `cryobusy` | DevBoolean | RW | True while Cryo is scanning |
| `cryoinfo` | DevString | RW | Stamp |

**Auto-clear:** Writing `busy = False` automatically clears the corresponding info string:

```python
@greenbusy.write
def greenbusy(self, value):
    self._green_busy = bool(value)
    if not value:
        self._green_info = ''   # auto-clear
```

**Server state:** `RUNNING` if any setup is busy, `ON` otherwise (via `always_executed_hook`).

### Client-side protocol (optimistic locking)

**`acquire_lock(setup_name)`** → `(bool, str)`:

1. Connect to lock device (1 s timeout)
2. Read `busy` attribute — if already True, return `(False, info)` (someone else has it)
3. Write info stamp: `"hostname:pid @ HH:MM:SS"`
4. Write `busy = True`
5. Sleep 50 ms (race window)
6. Re-read info — if stamp differs, another client won the race → release and return `(False, actual_info)`
7. Return `(True, "")`

**`release_lock(setup_name)`**: Write `busy = False`, `info = ""`. Silently ignores errors.

**`check_lock(setup_name)`** → `(bool, str)`: Read-only check without acquiring.

### Fail-open design

If the lock server is unreachable (network down, server not running, pytango not installed), all functions silently succeed. This ensures Samba always works even without the lock infrastructure. Failures are logged at WARNING level.

### Integration in samba.py / samba_cryo.py

```python
# Before scan start (_start_scan):
ok, who = acquire_lock(self._active_setup_name)
if not ok:
    QMessageBox.warning(self, "Setup busy",
        f"Setup '{self._active_setup_name}' is already in use:\n{who}")
    return

# After scan completes (_on_worker_finished):
release_lock(self._active_setup_name)
```

---

## 8. Device Registry & Sensor Flow

**File:** `core/device_registry.py`
**Persistence:** `~/.config/moke_scan/device_registry.json`

### Device entry structure

```python
{
    "name":            "ZI2",                          # Friendly display name
    "tango_path":      "hpp-N42/measure/ZI2",          # Full TANGO device path
    "type":            "lockin",                        # Category (see below)
    "trigger_cmd":     "Start",                         # Command to trigger read
    "integ_time_attr": "integrationtime",               # Integration time attribute
    "settling_attr":   "settlingtime",                  # Lock-in settling attribute
    "channels": [
        {"attr": "x1", "label": "ZI2 x1", "unit": "µV"},
        {"attr": "y1", "label": "ZI2 y1", "unit": "µV"},
        # ...
    ]
}
```

**Device types:** `lockin`, `beckhoff_avg`, `beckhoff_adc`, `magnet`, `hysteresis`, `stage`, `delay`, `cryostat`, `other`

### Sensor flow: registry → picker → scan engine

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────┐
│ Device Registry  │────▶│ SensorPickerRow  │────▶│ Scan Config │
│ (device_registry │     │ (sensor_picker.py)│     │ (JSON file) │
│  .json)          │     │                  │     │             │
│                  │     │ dev_combo ────┐  │     │ sensors: [  │
│ name ───────────▶│     │ ch_combo  ───┐│  │     │   {label,   │
│ channels[] ─────▶│     │ axis_combo─┐ ││  │     │    device,  │
│ trigger_cmd ────▶│     │ checkbox ┐ │ ││  │     │    attr,    │
│ integ_time_attr ▶│     │          ▼ ▼ ▼▼  │     │    ...}     │
│ settling_attr ──▶│     │  .get() ─────────▶────▶│ ]           │
└─────────────────┘     └──────────────────┘     └──────┬──────┘
                                                        │
                                                        ▼
                                                 ┌─────────────┐
                                                 │ ScanRunner   │
                                                 │ (runner.py)  │
                                                 │              │
                                                 │ Groups by    │
                                                 │ device path  │
                                                 │ Triggers     │
                                                 │ Reads attrs  │
                                                 └─────────────┘
```

### SensorPickerRow.get() output

The `get()` method returns a dict that serves **both** the scan engine and config persistence:

```python
{
    # Scan engine fields
    "label":           "ZI2 x1",
    "device":          "hpp-N42/measure/ZI2",    # TANGO path
    "attribute":       "x1",
    "unit":            "µV",
    "enabled":         True,
    "y_axis":          "Y1",                     # Y1, Y2
    "plot_visible":    True,                     # False if axis == "hidden"
    "trigger_cmd":     "Start",                  # From device registry
    "integ_time_attr": "integrationtime",        # From device registry
    "settling_attr":   "settlingtime",           # From device registry

    # Config persistence keys (used to restore dropdowns on load)
    "device_name":     "ZI2",                    # Registry device name
    "channel_attr":    "x1",                     # Registry channel attr
}
```

The `device_name` and `channel_attr` fields enable reliable config restoration — the picker can re-select the correct dropdowns even if TANGO paths change.

### Registry editor UI (`DeviceRegistryPanel`)

Left panel: device list with Add/Duplicate/Delete buttons.
Right panel: property editor (name, path, type, trigger_cmd, integ_time_attr, settling_attr) + scrollable channel list (attr, label, unit per row).

Signal `registry_changed` propagates updates to: SensorPickerRow, SetupDefaultsPanel, trajectory monitor combo.

---

## 9. ETA Display & Pre-scan Estimate

### Runtime ETA (`_on_progress`)

Connected to `worker.progress(done, total)`. Uses linear extrapolation:

```python
elapsed = time.time() - self._scan_start_time
eta = (elapsed / done) * (total - done)
# Progress bar format: "123 / 456 pts  —  2m 34s elapsed  ~3m 21s left"
```

`_scan_start_time` is captured just before `worker.start()` in both `_start_scan` and `_start_calib_timescan`.

### Pre-scan estimate (`_update_estimate`)

Shown in the status label before starting a scan. Called at the end of `_save_active_config()` and `_on_worker_finished()`. Skipped if a scan is already running.

**Formula for standard scans (SPATIAL / FIELD / TIME):**

```
time_per_point = settle + zi_settle + integration_time
total_estimate = n_points × time_per_point
```

- `settle`: from config. TIME → 0, FIELD → max(settle, 0.05), SPATIAL → as-is
- `zi_settle`: read **live** from the ZI device's `settlingtime` attribute (500 ms Tango timeout). Falls back to 0 if device unreachable.
- `integration_time`: from config

**Formula for DC_HYST:**

```
total = integration_time × 2 × cycles
# (2 half-loops per cycle, each taking integration_time seconds)
```

**Output format examples:**

```
≈ 45 s   (10 pts × [0.1s settle + 4.5s ZI + 0.1s integ])
≈ 2.3 min  (51×51 pts × [0.05s settle + 0.1s integ] + moves)
≈ 4 s   (2 × 2.0s/half-loop × 1 cycle(s), 100 pts/half)
```

The breakdown shows each time component. Spatial scans add a "+ moves" note since stage travel time is not estimated.

---

## 10. Setup Defaults Panel

**File:** `Samba_main/panels/setup_defaults.py`

Editable per-setup hardware device paths and attribute names. Each section uses registry-driven combo boxes.

### Configurable sections

| Section | Fields | Registry type filter |
|---------|--------|---------------------|
| Stage Act 1/2/Z | device, attr, label (R/O), unit (R/O) | all |
| Keithley | device, amplitude/frequency/range/compliance attrs | "current", "keithley" |
| Magnet | device, current_attr, field_attr | "magnet" |
| Relay | device, attr | "relay" |
| Lock-in (ZI) | device, tc_attr, order_attr, settling_attr | "lockin" |
| Focus sensor | device, attr | "sensor", "beckhoff" |
| TR-MOKE | DG645 device | "dg645" |
| TR-MOKE | RTV40 device | "pulser" |

### Registry-driven combos

- **Device combos** display friendly names, store TANGO paths as item data
- **Attribute combos** populated from selected device's channels
- Fallback attribute lists (e.g., `_LOCKIN_TC_ATTRS = ["timeconstant", "tc", ...]`) used when device has no channels defined
- Label/Unit fields auto-fill from registry on device+attr selection

### Data flow

```
SetupDefaultsPanel.get_defaults()  →  flat dict with all keys
    ↓
setup.update(defaults)             →  merged into setup dict
    ↓
save_setup(name, setup)            →  persisted to ~/.config/moke_scan/{name}.json
    ↓
ScanRunner reads setup keys        →  zi_device, magnet_device, etc.
```

Signal `defaults_changed` triggers immediate save to disk.

---

## 11. Recent Changes (April 2026)

### RTV40 pulse-width sync for TR-MOKE
- Added "RTV40 Sync" as a 4th column in the TR-MOKE panel (trajectory.py)
- **Goal**: keep the END of the RTV40 high-voltage pulse at a fixed time while
  sweeping the DG645 delay. Formula per scan point:
  `width_i = base_width − (delay_i − start_delay)`
  As the DG645 delay increases (pulse start shifts right), the pulse width
  decreases by the same amount so the end stays fixed.
- **UI controls**: enable checkbox, device label (from Setup Defaults), tracking
  label (shows which sweep channel is followed), base-width spinbox + "Read"
  button, trigger source / rate / polarity dropdowns, "Apply to Device" button
- **Scan engine** (`core/scan/runner.py`): RTV40 proxy created at scan start;
  `PulseWidth` written after every DG645 move (clamped to hardware range
  0.3–20 ns); width reset to `base_width` in the `finally` block (covers
  normal completion and abort)
- **Pre-scan checks** (`samba.py`): warns if sweep range would push width
  outside 0.3–20 ns (clamped); warns if `TriggerSource ≠ External` (both
  dialogs are Yes/No — user can proceed)
- **Setup Defaults**: `rtv40_device` key added to `SETUP_HW_DEFAULTS` for
  Green and IR (defaults to `hpp-N42/pulser/RTV40`); device combo filtered to
  registry type `"pulser"`
- **Config schema v3**: added `rtv40_sync_enabled`, `rtv40_base_width_ns`,
  `rtv40_trig_src`, `rtv40_trig_rate`, `rtv40_polarity` defaults; migration
  `_migrate_v2_to_v3` backfills old configs

### DG645 device init fix (TR-MOKE)
- On startup, `load_config()` was overwriting the DG645 device label with a
  stale value from the scan config JSON (baked in by the v1→v2 migration
  default), immediately after `set_trmoke_device()` had correctly set it from
  setup data
- Fix: removed the `_tr_dev_lbl.setText` block from `load_config()`; the
  label is now set exclusively by `set_trmoke_device()` which is always called
  from setup data before `load_config()`

### RTV40 pulse generator TANGO device server
- Added `Samba_main/tango_devices/RTV40/RTV40_Pulser.py` and `install_RTV40.sh`
- **Protocol** (from RTV30 manual): PowerForth ASCII, 115200 baud, no flow control
  - Set: `<value> !<command><CR>` — device replies `<echo> ok<CR><LF>`
  - Query: `?<command><CR>` — device replies `<value> ok<CR><LF>`
- **Wire unit conversions**: amplitude in 0.1 V units (10–350), pulse width in ps (300–20000)
- **Trigger modes**: 0 = Off, 1 = External, 2 = Internal (not binary like original code assumed)
- **Threading model**: single background poll thread owns all serial reads; TANGO attribute
  `read_*()` methods return cached values only — no serial I/O on TANGO polls. This prevents
  command interleaving (`?rate\r?polarity` concatenation) when TANGO polls multiple attributes.
- Lock (`threading.Lock`) serializes poll thread reads and write method sends — never simultaneous
- `Connect` command triggers remote mode (sends `\r`, sleeps 1 s, discards banner), starts poll thread
- `Disconnect` stops poll thread, sends `local`, closes port
- Added `Local` and `ForceTrigger` commands; removed `OutputEnabled` (no hardware equivalent,
  use `TriggerSource=0` for off)
- **DG645 note**: Option 3 rear-panel BNC outputs have fixed TTL levels — amplitude/offset SCPI
  commands only affect front-panel outputs

### Setup lock integration
- Wired `acquire_lock()` / `release_lock()` from `setup_lock.py` into both `samba.py` and `samba_cryo.py`
- Lock acquired before scan start (with "Setup busy" dialog if locked)
- Lock released in `_on_worker_finished()`
- Added logging throughout `setup_lock.py` for debugging

### IR settling time fix
- `SensorPickerRow.get()` in `Samba_main/panels/sensor_picker.py` was missing `"settling_attr"` in its output dict
- Without this field, the scan engine couldn't read the ZI settling time for IR sensors
- One-line fix: added `"settling_attr": dev.get("settling_attr", "")`

### Pre-scan time estimate
- Added `_update_estimate()` to `samba.py` and `samba_cryo.py`
- Shows breakdown: settle + ZI settle + integration per point
- ZI settling read live from device (not cached)
- DC_HYST branch: `int_time × 2 × cycles`
- Called after every config save and scan completion

### Runtime ETA display
- Ported `_on_progress()` from Cryo to Samba_main
- Shows elapsed time and estimated time remaining in progress bar
- Added `_scan_start_time` capture in both `_start_scan` and `_start_calib_timescan`

### Cryo installer fix
- Replaced `conda install -c conda-forge pytango` (was hanging on dependency solving) with pip-based installation matching Samba_main's approach

### Config schema migration v1→v2
- Added TR-MOKE default fields to migration chain

### ZI/ZI2 device server fixes (prior session)
- Fixed `poll()` + numpy averaging to flush stale samples before collecting
- Corrected settling time factors (`_SETTLE_99`)
- Fixed `integrationtime` write-back and readback verification

---

## 12. Architecture Principles

1. **Hardware-gated synchronization** — Async trigger + state polling prevents timing drift between Samba and devices
2. **Fail-open** — Lock server, Tango connections, and device reads all fail gracefully. Samba always runs.
3. **Two-phase polling** — Phase A (entry) + Phase B (completion) guarantees data is ready before read
4. **Batch per device** — All sensors on the same device read in one call to minimize inter-channel skew
5. **Crash-safe persistence** — HDF5 written per-point; Cryo uses atomic file replacement for JSON configs
6. **Registry-driven UI** — Device/channel definitions in one place; UI combos auto-populate from registry
7. **Schema migration** — Versioned config chain ensures old configs load correctly after feature additions
8. **Catppuccin Mocha theme** — Consistent dark UI across all panels using the Catppuccin color palette
9. **Custom over Sardana** — Deliberate decision to build a custom scan engine rather than use the Sardana synchrotron framework, because the hardware is simpler and a lightweight custom solution is easier to maintain
10. **TR-MOKE as SPATIAL** — TR-MOKE scans are converted to SPATIAL by samba.py before passing to ScanRunner — the DG645 delay attribute becomes the actuator — requiring zero changes to the scan engine

---

## 13. DG645 Delay Generator & TR-MOKE

**Device server:** `Samba_main/tango_devices/DG645/` (separate repo)
**TANGO path:** `intermag/dg645/1`

### DG645 device server

Thread-safe TANGO wrapper around the Stanford DG645 via TCP socket with auto-reconnect.

**Channels:** 8 delay channels (A–H), each with a delay value and reference channel.
**Outputs:** 5 outputs (T0, AB, CD, EF, GH) with amplitude, offset, and polarity.
**Trigger:** 7 trigger modes (Internal, Ext Rising, Ext Falling, SS Ext Rising, SS Ext Falling, Single Shot, Line).
**Burst mode:** N bursts with configurable period and delay count.
**Persistence:** 9 settings store/recall slots. Raw `SendCommand`/`SendQuery` for arbitrary SCPI.

### TR-MOKE scan conversion trick

TR-MOKE scans are **not** a separate scan engine mode. Instead, `samba.py` converts them to SPATIAL scans before passing to `ScanRunner`:

1. The DG645 delay channel attribute (e.g., `delay_A`) becomes `act1_attr`
2. The DG645 device path becomes `act1_device`
3. Scan range (start/stop delay in seconds) → `act1_start` / `act1_stop`
4. `scan_type` is set to `SPATIAL`

The only TR-MOKE-specific logic in `samba.py` is the X-axis unit conversion in `_on_point`: seconds → ns/ps/µs for display.

### TR-MOKE UI

Front-panel-style widget in the trajectory panel:
- Clickable channel buttons (A–H, blue highlight) for selecting delay channel
- Large monospace delay readback display
- Output buttons (T0/AB/CD/EF/GH, gold highlight) with amplitude/offset/polarity
- Prescaler, f_mod display, burst mode controls
- Keithley section hidden when TR-MOKE is active

---

## 14. Cryo Architecture

**Entry point:** `Cryo/samba_cryo.py` → `CryoMainWindow`

### CryoMainWindow vs MainWindow

`CryoMainWindow` is **not** a subclass of `MainWindow`. It is an independent implementation that shares modules from `core/` but has its own UI layout, hardware panel, and config structure. Key differences:

| Aspect | Samba_main | Cryo |
|--------|-----------|------|
| Setups | Green, IR (tab-switching) | Cryo only (no tabs) |
| Magnet | Beckhoff (room-temp coils) | AttoDRY (superconducting, ±9 T) |
| Temperature | N/A | AttoDRY (0–400 K) |
| Demagnetization | Auto after field scan | Disabled (superconducting) |
| Config panel | QTabWidget (multi-setup) | QListWidget (single setup) |
| Relay | Optical relay switching | N/A |
| Accent color | Catppuccin Mocha palette | #0080FE blue branding |
| QSettings key | "Samba" | "SambaCryo" |

### Hardware panel injection

`TrajectoryPanel` and `ScanlistPanel` accept a `hw_panel_class` parameter (default `HardwarePanel`). `CryoMainWindow` passes `CryoHardwarePanel` instead. This replaced the earlier fragile `_replace_hw_panel()` approach.

### CryoHardwarePanel layout

**Left column** — Keithley 6221 controls via `KeithleyMixin`:
- Amplitude, frequency, range, compliance spin boxes
- `_make_spin()` factory for consistent styling
- `set_ok` / `set_err` / `set_sim` status helpers

**Right column** — AttoDRY cryostat controls:
- Field setpoint (±9 T), temperature setpoint (0–400 K)
- VTI and magnet temperature readbacks
- Toggle buttons: Magnetic Field Control, Temperature Control, Persistent Mode
- Monitor button → opens `CryoMonitorDialog`

### ReadbackWorker

QThread replacing GUI-thread polling for hardware readback:
- Emits signals: `attodry_readback`, `fallback_field`, `ac_monitor`, `stage_positions`
- GUI updates via 400 ms QTimer that reads latest values from the worker
- Cleanly stopped in `closeEvent()`

### CryoMonitorDialog

Rolling live plots for cryostat monitoring:
- 3 columns × 3 rows = 9 subplots
- **Temperatures:** Sample, VTI, Magnet, Reservoir
- **Pressures:** CryostatIn, CryostatOut
- **Heater powers:** Sample, VTI, Reservoir
- 60-second rolling window, 500 ms poll interval
- Uses `Line2D.set_data()` for incremental rendering (no redraw from scratch)
- `WA_DeleteOnClose = False` — dialog is hidden, not destroyed

### Temperature Sweep mode

Uses the FIELD scan engine with `act1_device` set to the AttoDRY and `act1_attr` set to the temperature setpoint attribute. After writing the setpoint, waits for settle (60–300 s) before reading sensors. Parameters: device, attribute, start/stop (K), N points, settle time.

### KeithleyMixin

Shared between `HardwarePanel` (Samba_main) and `CryoHardwarePanel` (Cryo):
- `build_keithley_group(owner)` — creates the QGroupBox with spin boxes
- `_read_keithley` / `_write_range` / `_write_amplitude` / `_write_compliance` / `_write_frequency`
- `_make_spin()` factory for NoScrollDoubleSpinBox with consistent range/decimals

### Cryo config specifics

- Atomic save: writes to `.json.tmp` → `os.fsync()` → `os.replace()` (crash-safe)
- `_sanitize()`: converts numpy/Qt types to JSON-safe Python types before serialization
- No demagnetization: alternating-decay demagnetization is not applicable to superconducting magnets

### Cryo import order

```
config ← hardware ← scan
config + hardware ← panels ← keithley_mixin ← panels_cryo + cryo_monitor
all ← samba_cryo
```

---

## 15. Extended Hardware Map

### Cryo stages (Attocube positioners)

| Device | TANGO Path | Attributes | Purpose |
|--------|-----------|------------|---------|
| ANM200 piezo scanner | `hpp-N42/attocube/ANM200` | x, y, z, scaling | Fine positioning (nm) |
| ANC300 stepper | `hpp-N42/attocube/ANC300` | fx/fy/fz, Vx/Vy/Vz, px/py/pz | Coarse positioning (steps) |

### Additional Beckhoff devices

| Device | TANGO Path | Purpose |
|--------|-----------|---------|
| PyHysteresis (polar) | `hpp-N42/beckhoff/pyhystpolar` | DC hyst, polar magnet |
| PyHysteresis (longi) | `hpp-N42/beckhoff/pyhystlongi` | DC hyst, longitudinal magnet |
| DoubleOutBeckhoff | `hpp-N42/beckhoff/analogOut` | Magnet coil current write |

### Magnet field readback attributes

| Attribute | Description |
|-----------|-------------|
| `current_polar` | Coil current for polar geometry |
| `current_longitudinal` | Coil current for longitudinal geometry |
| `field_polar_corr` | Corrected field (polar), mT |
| `field_longitudinal_corr` | Corrected field (longitudinal), mT |

### AttoDRY commands

| Command | Description |
|---------|-------------|
| `toggleMagneticFieldControl` | Enable/disable superconducting magnet PID |
| `toggleFulltemperatureControl` | Enable/disable temperature PID |
| `togglePersistentMode` | Enable/disable persistent mode (traps field in magnet) |

### Beckhoff trigger_cmd origin

The `trigger_cmd` pattern was discovered through `DoubleInBeckhoffAverage`: this device requires `Start()` → wait for `RUNNING→ON` → read `Value`. This handshake became the standard sensor trigger protocol used by all triggered devices (ZI, ZI2, BeckhoffAverage).

### AdsBridge architecture

The Beckhoff devices sit behind a two-layer bridge:
1. **AdsBridge** — TCP/ADS gateway translating TANGO commands into TwinCAT ADS protocol
2. **DoubleInBeckhoff / DoubleOutBeckhoff** — thin TANGO wrappers exposing individual PLC variables as attributes

SAMBA replaced the original C++ ScanServer from tango-controls.org with a Python-based scan engine.

---

## 16. UI Patterns & Conventions

### Scan naming (`MokeMetadataGroup.build_scan_name`)

Auto-generated scan filenames follow:
```
YYYYMMDD_SampleName_AmplitudemA_FreqHz_Config_Incidence_MirrorShift_Notes_noDC_lam2
```
Auto-updates when any metadata field changes.

### NoScroll widgets

`NoScrollComboBox`, `NoScrollSpinBox`, `NoScrollDoubleSpinBox` override `wheelEvent()` to prevent accidental value changes while scrolling the panel. Defined in `panels/_widgets.py`.

### Button icons (`_scan_btn` helper)

Uses `QIcon.fromTheme()` with freedesktop icon names and ASCII fallback text. Per-button `:disabled` styling for grayed-out state.

### CSS scoping

Action bar CSS uses `objectName` scoping (`#action_bar`) to prevent style cascade to child widgets. Early bug: `setStyleSheet("QWidget{...}")` cascaded to children.

### Catppuccin Mocha palette

| Color | Hex | Usage |
|-------|-----|-------|
| Green | `#a6e3a1` | Start button, success states |
| Peach | `#fab387` | Pause button, warnings |
| Red | `#f38ba8` | Abort button, errors, delete buttons |
| Blue | `#89b4fa` | Info text, Y1 axis color |
| Mauve | `#cba6f7` | Y2 axis color |
| Surface 0 | `#313244` | Widget backgrounds |
| Surface 1 | `#45475a` | Borders, hover states |
| Text | `#cdd6f4` | Primary text |
| Subtext 0 | `#a6adc8` | Secondary text |
| Base | `#1e1e2e` | Window background |
| Mantle | `#181825` | Sidebar/panel background |

### PyQt5 → PyQt6 migration notes

- `QAction` moved from `QtWidgets` to `QtGui`
- `exec_()` → `exec()`
- Enum-style flags (e.g., `Qt.AlignLeft` → `Qt.AlignmentFlag.AlignLeft`)
- Matplotlib backend: `Qt5Agg` → `QtAgg`
- `NavigationToolbar2QT` SIP TypeError fix: pass `None` as parent, then `addToolBar()` reparents

---

## 17. Known Issues & Future Work

### Known issues

- **Zigzag scan asymmetry** — 2D scans with zigzag show signal asymmetry due to piezo hysteresis. Workaround: increase settle time or disable zigzag.
- **ZI averaging is suboptimal** — The ZI device servers use `poll()` + numpy averaging (a digitizer pattern) instead of the MFLI's native hardware low-pass filter. The recommended alternative is `getSample()` with proper settling, which would give hardware-filtered results with lower noise.
- **Sequential sensor reads** — Sensors are read via individual TANGO RPCs with ms-scale gaps between devices. Not truly synchronized. Fine for slow scans, but introduces skew for fast ones.
- **TR-MOKE HDF5 x-axis** — Stores raw seconds, not the display unit (ns/ps). Post-processing must apply the conversion.
- **File versioning** — Stale project snapshot files may exist from earlier development.

### Planned future work

- **ZI hardware filtering** — Switch from poll-and-average to `getSample()` with settling for proper hardware-filtered lock-in output
- **Auto-focus before scanlist** — Run autofocus automatically before each scan in a scanlist
- **Scan history overlay** — Overlay previous scan data in the data browser for comparison

---

## 18. Installation & Running

### Requirements

```
pip install pytango PyQt6 matplotlib h5py numpy
```

For pytango, if pip fails, fall back to:
```
conda install -c conda-forge pytango
```

### Environment

```bash
export TANGO_HOST=192.168.1.1:10000
```

### Running

```bash
# Samba_main (Green + IR)
cd Samba_main && python samba.py

# Cryo
cd Cryo && python samba_cryo.py
```

### Simulation mode

If `pytango` is not installed, Samba falls back to `SimProxy` (defined in `core/hardware.py`) which returns dummy values. All Tango operations are wrapped in try/except with graceful degradation. This allows UI development without access to the lab hardware.

---

## 19. Recent Changes (May 2026) — Cryo Geometry & Stage Selection

### Faraday / Voigt geometry selection

`Cryo/config.py` — `SETUP_HW_DEFAULTS["Cryo"]` now holds two top-level stage blocks, each doubly-nested by piezo type:

```python
"stage_faraday": {
    "anm200": {
        "act1_device": "hpp-N42/attocube/ANM200", "act1_attr": "x", "act1_unit": "nm",
        "act2_device": "hpp-N42/attocube/ANM200", "act2_attr": "y", "act2_unit": "nm",
        "z_device":    "hpp-N42/attocube/ANM200", "z_attr":    "z", "z_unit":    "nm",
    },
    "anc300": {
        "act1_device": "hpp-N42/attocube/ANC300", "act1_attr": "px", "act1_unit": "steps",
        "act2_device": "hpp-N42/attocube/ANC300", "act2_attr": "py", "act2_unit": "steps",
        "z_device":    "hpp-N42/attocube/ANC300", "z_attr":    "pz", "z_unit":    "steps",
    },
},
"stage_voigt": { # same structure, same devices }
```

Each scan config now carries two extra keys:
- `"geometry"`: `"Faraday"` or `"Voigt"` (which stage block to read from)
- `"stage_type"`: `"anm200"` or `"anc300"` (fine nm scanner vs. coarse stepper)

`make_default_config()` sets both to `"Faraday"` / `"anm200"`. `_migrate_config()` back-fills them on old configs via `setdefault`.

### Config setup-level migration

`load_setup()` runs two migration passes before the per-config chain:
- **v0 → v1**: flat `act1_device` / `act2_device` / `z_device` keys at setup level are folded into `stage_faraday.anm200`
- **v1 → v2**: flat keys *inside* `stage_faraday` / `stage_voigt` are wrapped into the `anm200` sub-dict

### defaults_panel.py — stage actuator UI

`Cryo/defaults_panel.py` now shows Faraday and Voigt columns side-by-side, each containing ANM200 (fine) and ANC300 (coarse) sub-groups, each with Act1 / Act2 / Z rows — 12 `ActuatorDefaultRow` widgets total.

Widget attributes: `far_anm_act1/2/z`, `far_anc_act1/2/z`, `voi_anm_act1/2/z`, `voi_anc_act1/2/z`.

`get_values()` returns the full doubly-nested dict; `load()` reads it back with `far.get("anm200", {})` etc.

### samba_cryo.py — geometry & stage toggle buttons

Two pill-button pairs are injected directly into `traj_panel._type_row` (the same `QHBoxLayout` row as the Spatial / Field-Temperature scan-type buttons):

```
[ Spatial ]  [ Field / Temp ]  |  Geometry: [ Faraday ][ Voigt ]  |  Piezo: [ ANM200 ][ ANC300 ]
```

Implementation details:
- `traj_panel._type_row` is exposed in `Cryo/panels.py` with `self._type_row = type_row` after the scan-type pills are added
- Injection uses `tr.takeAt(tr.count() - 1)` to pop the trailing stretch, appends a `QFrame` VLine separator, label, and pill pair, then re-adds the stretch
- Pill CSS matches the scan-type pill style; Geometry uses mauve (`#cba6f7`), Piezo uses green (`#a6e3a1`)
- `_on_geometry_changed()` and `_on_stage_type_changed()` call `_persist_scan_profile()` which saves both keys to the active config and calls `_apply_defaults()` to push the correct device paths into the UI
- `_build_full_config()` resolves `setup["stage_{geo}"][stage_type]` and injects device/attr keys with `setdefault`
- `_load_active_config()` restores both toggles with `blockSignals` to avoid re-saving on load

### UI layout improvements

- **Geometry + Piezo pills inline**: placed in `_type_row` so they take no extra vertical space
- **Keithley range combo**: `setMinimumWidth(84)` (was `setFixedWidth(70)`); Set button `setFixedWidth(44)` (was 30) — dropdown no longer clipped
- **Field Sweep / Temperature Sweep groups**: removed `setMaximumWidth` caps, use `setMinimumWidth` so groups expand with window width
- **Right plotting panel**: initial `QSplitter` sizes changed from `[215, 760, 360]` to `[215, 640, 480]` for a wider measurement view

---

## 20. Recent Changes (May 2026) — Startup, Calibration, Installer & Bug Fixes

### Trace / Retrace scan directions (Cryo)

`Cryo/panels.py` — `ScanDirectionList` labels renamed from D1/D2 to Trace/Retrace:
- First row always labelled `"Trace:"`, second `"Retrace:"`; add button text changed to `"＋ Retrace"`
- File suffixes in `samba_cryo.py`: `_trace.h5` / `_retrace.h5` when more than one direction queued
- Progress bar label shows `trace` / `retrace` during multi-direction scans

### Calibration tab — stage positioning from setup defaults

**Problem:** The "Stage positioning" group was reading device/attribute from the scan config, which could be stale or missing (especially for Cryo's nested geometry structure).

**Fix:** New `configure_stage()` public method on `CalibrationPanel` (`core/calibration.py`):

```python
calib_panel.configure_stage(x_dev, x_attr, y_dev, y_attr, z_dev, z_attr)
```

- Stores axes in `self._stage_cfg`; `_get_axis_info()` returns this immediately if set, falls back to old scan-config reading otherwise
- Called in `Samba_main/samba.py` on setup load and defaults edit (uses flat `act1_device`/`z_device` setup keys)
- Called in `Cryo/samba_cryo.py` in `_apply_defaults()` (uses resolved `stage_{geo}[stage_type]` piezo block)
- Switching geometry/piezo in Cryo re-calls `_apply_defaults()` which updates the calibration stage config automatically
- `_start_autofocus()` updated to use `_get_axis_info()` instead of reading config directly

**Auto-read on tab click:** Both apps connect `live_tabs.currentChanged` to `_on_live_tab_changed()` which calls `calib_panel._read_all()` when the Calibration tab is selected.

**Non-blocking `_read_all`:** `_read_all()` now runs TANGO reads in a daemon thread and posts widget updates back to the GUI thread via `QTimer.singleShot(0, ...)` — prevents "not responding" freezes when devices are unreachable.

### Splash screen — parallel TANGO probe

**Problem:** On startup without a TANGO connection, `_probe_devices()` was called after the splash closed and ran sequentially on the GUI thread — each device timed out in ~9 s causing a complete freeze.

**Fix (both apps):** `_probe_devices(status_callback=None)` redesigned:
- Probes run in parallel **daemon threads** (one per device)
- When `status_callback` is provided, the GUI thread polls every 50 ms with `processEvents()` and invokes the callback as each thread finishes → splash shows live status lines (`✓ Stage: OK` / `⚠ AttoDRY: unavailable`)
- Called from `main()` after window construction, before `finish_splash(min_seconds=3)`
- Skipped entirely in simulation mode (pytango not installed)
- Cryo probes: Stage, AttoDRY, Keithley
- Samba_main probes: Stage, Lock-in, Magnet, Keithley (deduplicated across setups)

### numpy.float64 coercion in scan runner

`core/scan/runner.py` — `_move()` now coerces the target position to Python `float` before writing:

```python
err = safe_write(proxy, attr, float(target))
```

**Cause:** `np.linspace()` produces `numpy.float64` scalars. On machines where pytango runs in green mode (thread-pool executor with strict C-level type dispatch), `write_attribute(attr, numpy.float64)` raises `TypeError: unsupported data_format`. Python built-in `float` is accepted by all pytango versions.

### RTV40 panel bug fix

`Samba_main/panels/trajectory.py` — `_rtv40_read_width()` and `_rtv40_apply()` were calling `p, err = get_proxy(path)` but `get_proxy` returns a **single** proxy, not a tuple.

Python's tuple-unpack protocol falls back to `__getitem__`, so pytango's DeviceProxy was indexed as `proxy[0]` and `proxy[1]`, internally calling `read_attribute(0)` — producing the error `"incompatible function arguments ... invoked with: RTV40(...), 0"`.

**Fix:** Use `fresh_proxy(path)` which correctly returns `(proxy, error_string)`.

### Installer rewrite (Samba_main)

`Samba_main/install.sh` rewritten to match `Cryo/install.sh`:
- Takes optional conda env name as argument (default: `base`), saved to `.install_config`
- Finds conda automatically across common install locations
- Creates the env if it doesn't exist; installs packages via `pip` inside the env
- Installs system Qt libs (`libxcb-*`) via `apt-get` when run as root
- Generates `launch_samba.sh` that activates the correct conda env before launching
- Desktop entry `Icon=` points directly to the project directory (avoids `cp` permission errors)
- Detects `$SUDO_USER` → uses real user's home for desktop/icon/config paths; `chown` fixes ownership

**Usage:** `bash install.sh Tango` or `sudo bash install.sh Tango`

---

## 21. Analysis Module — `Analysis/analyze_samba.py` (May 2026)

Post-acquisition pipeline for SAMBA HDF5 data. Replaces the legacy
`Jakub_methods.py` / Tobi reference scripts with one module that handles
Cryo, Green, and IR scanlists from the same entry point.

### Entry points

```python
from analyze_samba import analyze_cryo

# Trace + retrace (returns (res, None) for legacy single-direction data)
res_trace, res_retrace = analyze_cryo.import_analyze_both(SCANLIST)

# Single direction with explicit overrides
res = analyze_cryo.import_analyze_SOT(
    SCANLIST,
    see_channels = ('DC', 'ZI_x1'),   # None = auto-detect
    current_mA   = 12.5,              # None = HDF5 metadata → filename → 10 mA
    ignorLines   = (3,),              # 1-based, drop these scanlist rows
    fit_edge_offset = 8,
)
```

The class is also exported as `SambaSOTAnalysis` for backwards compatibility
with older measurement scripts.

### Auto-detection (no config needed)

| What | How |
|------|-----|
| `data_base_dir` | Inferred from scanlist location: `ScanLists_<X>` → `Data_Samba_<X>` (sibling folder). Multi-day scans handled by also trying `data_base_dir/<YYYYMMDD>/basename`. |
| `x_ch` | First available of `actuator_x` → `x_actual` → `x_setpoint` |
| Lock-in channels | Regex matches `ZI_x1`, `ZI2_x1`, and bare `x1` — all map to `zix1` |
| Intensity channel | First available of `DC` → `FL` → `Mon` |
| Sample name | HDF5 `/metadata/sample_id` → explicit arg → filename token |
| Current | HDF5 `hw_keithley_amplitude_mA` → filename regex `(\d+(?:[.p]\d+)?)\s*mA` → 10 mA |
| Direction | Filename markers `_trace` / `_retrace`; empty set = legacy single run |

### Output layout

```
<analysis_base>/<sample_name>/
  calibration.txt
  <YYYYMMDD_HHMMSS>_<scanlist-stem>[_<direction>]/
    intensity_<ch>.png
    phase_search.png
    sumdiff_<ch>.png  …  realimag_<ch>.png  …  negpos_<ch>.png
    fit_<ch>.png
    analyzed_data.csv
    results.json
```

Default `analysis_base` is `Z:\projects\MOKE_lab\Scanning\Analysis_Scripts`.
A timestamped subfolder per scan keeps re-runs separated. Override with
`save_dir=` (parent) or `save_subdir=False` (write directly into `save_dir`).

### Calibration file

`calibration.txt` lives in the sample folder; 4 data lines:

```
0.05 1.10 2.18 3.27 4.40 5.51   # 6 mV at µm ticks 0,5,10,15,20,25
1.0                              # R1 (NM/M)
1.0                              # R2 (M only, reference)
0.0                              # theta — 1st-harmonic phase offset (deg)
```

When the file is missing, `read_calibration()` prompts interactively for each
value and writes the file so subsequent runs skip the prompt. The slope from
the 6 mV calibration points is converted to `sln = (1/slope) × π/180 × 1e6`
(µrad/mV). Pass `use_calibration_file=False` to disable the prompt entirely
and use explicit `sln=`, `R=(R1,R2)`, `theta=` args.

### Per-channel data layout

`linescan_calc_cryo()` returns a dict keyed by mapped channel name:

```python
{
    'x'   : np.array,                   # position in µm
    'zix1': [x, diff, sum, err, pos, neg, n_pos],
    'ziy1': [...],
    'FL'  : [...],                      # intensity/reflection
    ...
}
```

The 7-element list is the standard format: half-difference `(pos−neg)/2`,
half-sum `(pos+neg)/2`, SEM-weighted error (quadrature of per-group SEMs),
mean of positive- and negative-polarity scans, and N for the positive group.
Polarity is `relay_sign × sign(field_T)` from columns 2–3 of the scanlist.

### Phase optimisation

`find_phase()` uses `scipy.optimize.minimize_scalar` with bounds `[-90°, 90°]`
to avoid the 180° degeneracy that an unbounded optimiser hits. Run per
polarity and averaged. Saved as `phase_search.png` when `do_plot=True`.

### Pipeline (`evaluate_data` modes)

- `sumdiff` / `sumdiff2nd` — half-sum vs. half-difference plot
- `negpos` — separate pos/neg traces
- `realimag` / `realimag2nd` — real and imaginary projections after phase
- `comp_1st_2nd` — 1st vs. 2nd harmonic comparison
- `thermoreflectance` — `(pos − neg) / mean(intensity)`
- `findphase` — diagnostic only

`eval_width_and_fit()` runs an `erf`-edge fit, computes device width, writes
`analyzed_data.csv` (semicolon-separated, includes 2ω columns when present)
and `results.json` (all metadata + fit parameters, numpy/bytes/Inf coerced
to JSON-safe).

### Key helper functions

| Function | Purpose |
|----------|---------|
| `_infer_data_base_dir(scanlist)` | `ScanLists_<X>` → `Data_Samba_<X>` sibling |
| `_resolve_path(path, base)` | literal → `base/file` → `base/<date>/file` |
| `_detect_channels(h5_path)` | returns `{x_ch, intensity, lockin, all}` |
| `_map_channel_name(name)` | normalises `ZI*`/bare `x1` → `zix1`, `DC`/`Mon` → `FL` |
| `first_h5_in_scanlist(sl, base)` | first resolvable H5 file (for metadata peek) |
| `read_h5_meta(h5_path)` | `/metadata` group attrs as plain dict |
| `read_calibration(folder)` | parses or interactively creates `calibration.txt` |
| `parse_current_from_name(s)` | regex `(\d+(?:[.p]\d+)?)\s*mA` |
| `detect_directions(sl)` | returns `{'trace','retrace'}` ∩ filename markers |
| `find_impurities_peaks(...)` | spline + peak detection, returns a mask |
| `data_load(filename, ch)` | handles Cryo, Green/IR new-SAMBA, and old `scan_*` formats |

### Constants

```python
DEFAULT_ANALYSIS_BASE     = r'Z:\projects\MOKE_lab\Scanning\Analysis_Scripts'
_X_CH_CANDIDATES          = ('actuator_x', 'x_actual', 'x_setpoint')
_INTENSITY_CH_CANDIDATES  = ('DC', 'FL', 'Mon')
_LI_CH_RE                 = r'^(?:ZI\d*_*)?([xy][1-4])$'  # case-insensitive
_SKIP_CH                  = {'actuator_x_setpoint', 'x_setpoint',
                             'time', 'Field', 'Temperature'}
_EXPECTED_X_UNITS         = {'µm', 'um', 'micrometer', ...}
```

### Known gotchas

- `data_load` warns once per missing channel/file but returns `np.zeros(1)`
  so the loop continues; check `Data keys loaded: [...]` for the actual set
- `_detect_channels` only inspects the *first* HDF5; if files in the same
  scanlist have different channel sets, only the first one's structure is
  used for x-axis detection
- The HDF5 x-axis unit is sanity-checked against `µm` and warns once if
  different — wrong units will silently produce wrong fit widths
- `data_calculation_cryo` skips files whose basename doesn't contain
  `_trace`/`_retrace` when `direction` is set; legacy scans must use
  `direction=None`

---

## 22. NAS Server Sync (May 2026)

### Overview

Both Samba_main and Cryo auto-upload data to the ETH NAS after every scan
and support a manual "↑ Sync" button. The NAS is accessed via the GVFS SMB
mount that GNOME Files creates automatically when the user browses to the share.

**File:** `core/server_sync.py` (shared by both apps via `Samba_main/server_sync.py` re-export)

### UI

A slim **"Server:" bar** sits directly below the action bar in both apps:

```
Server: [/run/user/1001/gvfs/smb-share:server=nas22.ethz.ch,...]  […]  [↑ Sync]
```

- The path field is editable; the `…` button opens a file dialog starting at
  `/run/user/<uid>/gvfs/` (the GVFS mount root)
- `↑ Sync` triggers an immediate manual sync in a background thread
- Auto-sync fires automatically after every scan (single scan and scanlist)
- Status label shows `Server sync complete` / `Server sync partial (see log)`

### Config key

`server_sync_dir` — stored per-setup in `~/.config/moke_scan/<Setup>.json`.
Set it once; it persists across restarts. Default is `""` (disabled).

### What gets synced

For setup name `Cryo` with `server_sync_dir = /run/user/1001/gvfs/smb-share:.../Data`:

| Local | Server |
|-------|--------|
| `~/moke_data/Data_Samba_Cryo/` | `<server>/Data_Samba_Cryo/` |
| `~/moke_data/ScanLists_Cryo/` | `<server>/ScanLists_Cryo/` |
| `~/moke_data/lab_notebook_Cryo.csv` | `<server>/lab_notebook_Cryo.csv` |

Lab notebooks are always overwritten with the local version (local is the
source of truth). Since notebooks only grow, the size check always detects
the change and uploads the updated file.

### Implementation notes

**`sync_setup(setup_name, setup, done_cb=None)`** — public entry point.
Reads `server_sync_dir`, `save_dir`, and `notebook_dir` from the setup dict,
derives the ScanLists path from the parent of `save_dir`, then starts a
daemon thread.

**Subprocess isolation** — all file I/O runs inside a child process
(`subprocess.run([sys.executable, '-c', ...], timeout=60)`). This is
necessary because GVFS/FUSE SMB mounts can block a thread indefinitely
inside a kernel syscall (e.g. `utime`) when the SMB connection times out.
Running in a subprocess allows `subprocess.run` to kill the child with
SIGKILL if it stalls, so `done_cb` is always called within 60 seconds.

**`shutil.copyfile`** (not `copy2`) is used because SMB mounts reject the
`utime` call that `copy2` makes after copying. `copyfile` transfers only
the raw bytes, which is sufficient for a backup.

**Skip condition** — a file is skipped if it already exists on the server
with the same byte count. This avoids re-uploading identical HDF5 files on
every sync.

### First-time setup on lab machine

1. Open Files (Nautilus) → connect to `smb://nas22.ethz.ch/matl_ips_intermag_s1`
   and navigate to `projects/MOKE_lab/Scanning/Data/` — this creates the
   GVFS mount under `/run/user/<uid>/gvfs/`
2. In Samba, click `…` next to the Server field and navigate to that path
3. The full path looks like:
   `/run/user/1001/gvfs/smb-share:server=nas22.ethz.ch,share=matl_ips_intermag_s1/projects/MOKE_lab/Scanning/Data`
4. Set once per setup; the value is saved to the setup JSON automatically

---

## 23. Recent Changes (June 2026) — Scan Engine Reliability & Bug Fixes

### Trigger recovery after ZI device restart mid-scan

**File:** `core/scan/runner.py` — `_do_acquire()`

Previously, the first `command_inout_asynch` failure on a sensor device permanently
removed it from `trigger_devs` for the rest of the scan. If the ZI lock-in server
crashed and was restarted mid-scan, it stopped receiving `Start` commands.

**Fix:** Per-device consecutive-failure counters (`_trigger_consec_fails`) persist
across points. On the first failure the proxy is refreshed via `fresh_proxy()` and
the trigger is retried immediately. The device is only permanently removed after
`AUTO_PAUSE_THRESHOLD` (5) consecutive failures. Counter resets to 0 on any success.

### Per-point retry loop with immediate pause

**File:** `core/scan/runner.py`

The acquire cycle (lock-in settling + trigger + Phase A/B + guard + read) is now
wrapped in a `while not self._abort` retry loop. If `_do_acquire` returns `ok=False`
(any sensor read NaN), the point is retried up to `AUTO_PAUSE_THRESHOLD` (5) times.

Key behaviour changes:
- **Immediate pause**: when all attempts fail, `self._paused = True` is set and a
  `while self._paused` wait loop runs **inside** the retry loop — the scan blocks on
  the failing point without advancing to the next one.
- **Same-point resume**: on Resume, the outer `while` iterates and retries the same
  point from scratch (5 fresh attempts) rather than advancing.
- **Abort-safe**: `if self._abort: break` exits both the inner `for` and outer `while`
  cleanly at any point.

### `_do_acquire()` extracted method

The trigger dispatch + Phase A (wait for RUNNING) + Phase B (wait for NOT RUNNING) +
guard delay + batch read is now a single method `_do_acquire()`. Returns
`(vals, t_trigger, ok)`. `trigger_devs` is modified in-place (removals persist).
This keeps the retry loop in `run()` readable and avoids deep indentation.

### Field-flip settle: rate-of-change instead of target-based polling

**File:** `core/scan/workers.py` — `ScanlistWorker._run_list()`

The old code read the field readback **after** writing the flipped current, so `v0`
was already mid-transition and `target_fld_est = -v0` was wrong.

**Fix:** Poll every 0.5 s and wait until `|Δfield|` between consecutive reads drops
below `field_settle_rate`. No target value assumed — works for any B-H curve.

| Setup | Attribute | Units | `field_settle_rate` | Physical threshold |
|---|---|---|---|---|
| Green / IR | `field_polar_corr` | mT | `2.0` | 2 mT / 0.5 s |
| Cryo | `MagneticField` | T | `0.002` | 2 mT / 0.5 s |

`field_settle_timeout` (300 s) and `field_settle_rate` are both overridable in the
setup JSON. A "Settling field…" line is always logged at settle start so fast coils
(< 500 ms) don't go silent in the log.

### `_trmoke_x_factor` AttributeError on scanlist start

**File:** `Samba_main/samba.py`

`_on_point` referenced `self._trmoke_x_factor` which was only assigned inside
`_start_scan()` and `_start_calib_timescan()`. Starting a scanlist directly (which
connects `sl_worker.point_done` → `_on_point` without going through `_start_scan()`)
caused an immediate `AttributeError` / core dump.

**Fix:** `self._trmoke_x_factor: Optional[float] = None` added to `__init__`.

### Unit tests

**File:** `test_runner.py` (repo root)

14 tests covering:
- `_do_acquire` happy path (correct values, `trigger_devs` unchanged, no-trigger fallback)
- Read failures with internal retries (NaN on persistent failure, recovery within budget)
- Trigger proxy refresh and permanent removal after `AUTO_PAUSE_THRESHOLD` failures
- Per-point retry loop (first-attempt success, recovery on Nth attempt, all-fail → pause, abort mid-retry)

Run with: `python test_runner.py -v` (no Qt, TANGO, or hardware needed).

**Note:** Tests must patch `runner.fresh_proxy` (the module's own binding) rather than
`hardware.fresh_proxy`, because `runner.py` uses `from hardware import fresh_proxy` which
creates a local binding at import time.

---

## 24. Recent Changes (June 2026) — UI Polish & Metadata

### Bug fixes (Samba_main + Cryo)

- **Scanlist pausable**: `_toggle_pause` now uses `self._worker or self._sl_worker` so
  the Pause button works during a scanlist run in both apps.
- **Scanlist abort**: `_sl_worker` is cleared to `None` in a dedicated
  `_on_sl_worker_finished` handler; `_abort_scanlist` is guarded by `_scan_running` to
  prevent stale-reference no-ops.
- **`_on_status` auto-pause detection**: now checks `_sl_worker` as fallback so the
  Pause→Resume button label updates correctly during a scanlist.
- **Samba_main only — setup-switch during scan**: `map2d.clear(); plot1d.clear()` are
  now guarded by `if not self._scan_running`, preventing plot buffer destruction when
  the user accidentally clicks Green↔IR during a measurement.
- **Samba_main only — stale field-sweep monitor**: `populate_monitor_combo` gained a
  `preserve: bool = True` parameter; called with `preserve=False` on config load to
  prevent a stale device/attribute carrying over after setup switch.

### New features (Samba_main + Cryo)

**Bidirectional metadata sync** — Trajectory and Scanlist tabs share a
`MokeMetadataGroup`; changes in either tab immediately update the other.
A `_meta_syncing` flag prevents feedback loops.

**Bidirectional timing sync** — The Timing group (Int / Settle / Timeout) on the
Scanlist tab stays in sync with the Trajectory tab via a `_timing_syncing` flag.

**Timing group moved into top row** — The Timing group (`QGroupBox`) now sits inline
in `top_row` between the "Active config" info widget and the Metadata group, saving a
row of vertical space.

**BD-calibration tab** — New tab between Scanlist and Data Browser:
- 6 editable mV spinboxes at λ/2 plate tick positions 0, 5, 10, 15, 20, 25
- Save / Load buttons; values persisted per-setup in the setup JSON
  (`bd_calibration`, `bd_calibration_date` keys)
- First time the tab is shown per setup per session, a dialog offers to reload the
  last saved calibration (`maybe_prompt`)
- On every scan the 6 mV values are injected into `cfg["bd_calibration"]` and written
  to HDF5 as `/data/calibration` (float64 array, 6 elements)
- Implementation lives in `core/bd_calibration.py`; `Samba_main/panels/bd_calibration.py`
  is a thin re-export wrapper

**Post-scan completion popups removed** — The "Scan complete" and "Scanlist complete"
`QMessageBox.information` dialogs replaced with color-coded log lines
(`✓ Scan complete — saved <path>`). The "Abort and quit?" close confirmation is
unchanged.

**MokeMetadataGroup additions** (both apps, `Samba_main/panels/_widgets.py` and
`Cryo/panels.py`):
- **Device ID field** (`meta_device`, key `"device_id"`) added to the right of the
  Sample field on the same row
- **R4W / R2W spinboxes** (keys `"r_4wire_kohm"`, `"r_2wire_kohm"`, range 0–10 000 kΩ,
  2 dp) placed in a new row below Sample/Device, above Notes
- `build_scan_name()` inserts device_id between sample and amplitude when non-empty
- All new fields emit `changed`, are round-tripped through `get_values`/`load_values`,
  and default gracefully on old configs (empty string / 0.0)

### Core engine additions (`core/scan/`)

- `ScanRunner.is_paused()` added (`runner.py`)
- `ScanlistWorker` gained `_paused` flag and `pause()`/`resume()`/`is_paused()` proxy
  methods delegating to `_runner` (`workers.py`)
- Field-flip settle loop in `ScanlistWorker._run_list` now respects `_paused`
- `_open_hdf5` writes BD calibration array to `/data/calibration` when
  `cfg["bd_calibration"]` is present
- `_move()` coerces `numpy.float64` targets to Python `float` before `safe_write` to
  avoid pytango type dispatch errors

### Cleanup

- `_running_scan_setup: str` instance variable removed from both `MainWindow` and
  `CryoMainWindow` — it was set but never read (the plot-buffer guard uses
  `_scan_running` instead)
- `QProgressBar` import and related dead CSS rules (`QProgressBar{...}` / `::chunk`)
  removed from both apps and `Samba_main/panels/scanlist.py`, `Cryo/panels.py`
- Orphaned `_, n_x, n_y = self._scan_dims(...)` locals removed (were computed but
  never used after the progress-bar removal)

---

## 25. Recent Changes (June 2026) — Bottom Status Bar

### Always-visible scan status bar

A `QStatusBar` strip sits at the very bottom of both `MainWindow` and `CryoMainWindow`,
visible at all times. It displays 7 fields in a single row:

```
Scan: 1/4  │  Start: 14:32:01  │  Elapsed: 0:42  │  Run left: 3:18  │  Scan left: 0:51  │  Dead: 12%  │  Done: 18%
```

**Implementation** (`samba.py` / `samba_cryo.py`):

- `_build_status_bar()` — creates the bar, three inner helper functions:
  - `_mk_field()` — value label (`color:#cdd6f4`, 12 px)
  - `_mk_caption(text)` — grey descriptor label (`color:#a6adc8`, 12 px)
  - `_mk_sep()` — `│` separator (`color:#45475a`, 12 px)
- `_refresh_status_bar()` — called from `_on_progress` and the 1 Hz `QTimer`; computes
  and writes all 7 fields:
  - **Scan** — `{_run_scans_done + 1} / {_run_scans_total}`
  - **Start** — wall-clock time of `_run_start_time` (HH:MM:SS)
  - **Elapsed** — `now - _run_start_time`
  - **Run left** — whole-run proportional estimate:
    `run_elapsed × (1 − frac) / frac` where `frac = _run_scans_done_frac + done/total * (1/_run_scans_total)`
  - **Scan left** — warmup-corrected per-scan estimate: measured from point 2 onward
    (`_scan_first_pt_time`); `(total − done) × rate_per_pt` where rate is
    `(now − _scan_first_pt_time) / (done − 1)`
  - **Dead%** — `(scan_elapsed − done × int_time) / scan_elapsed × 100`
  - **Done%** — `((_run_scans_done + done/total) / _run_scans_total) × 100`
- `_status_bar_run_start()` — called before the first `worker.start()`; initialises
  `_run_start_time`, `_run_scans_done = 0`, `_bar_int_time` from config
- `_status_bar_scan_done()` — increments `_run_scans_done`; called after each completed
  direction/scan within a multi-scan run
- `_status_bar_run_finish()` — called in `_on_sl_worker_finished` / run-end paths;
  resets all fields to `—`
- 1 Hz `QTimer` (`_sb_timer`) fires `_refresh_status_bar()` between `progress` signals
  so the Elapsed and Run/Scan-left counters tick smoothly

**`_run_scans_total` computation:**

| Context | Value |
|---------|-------|
| Single scan (Samba_main) | `1` |
| Scanlist (Samba_main) | `sl["n_scans"] × len(sl_worker.cfg_list)` |
| Single scan (Cryo, one direction) | `1` |
| Single scan (Cryo, trace+retrace) | `1 + len(_dir_queue)` (computed after queue assigned) |
| Scanlist (Cryo) | `sl["n_scans"] × len(cfg_list)` |

**Replaced UI elements:**

- `self.pbar` (`QProgressBar` in the action bar) removed from both apps — the status
  bar covers elapsed/done information more completely
- `self.list_bar` (`QProgressBar` in `ScanlistPanel`) removed — scanlist progress
  visible through the status bar's `Scan c/N` and `Done%` fields
- `status_lbl` (text label below the plotting area) is kept for per-point log messages
