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
