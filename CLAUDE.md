# SAMBA — Developer Documentation

**S**trnad & Goldenberger **A**pplication for **M**agnetism **B**ased **A**nalysis
ETH Zürich — Intermag Lab | Creator: Jakub Strnad | Collaborator: Tobias Goldenberger
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
- **Ramp wait:** after the settle sleep, the engine polls the magnet device and
  waits while it reports state `MOVING` (the AttoDRY holds MOVING until the
  written field/temperature setpoint is within tolerance), up to
  `field_settle_timeout` (setup key, default 300 s). `settle_time` is applied
  once more after arrival. Devices without MOVING feedback (Beckhoff magnet)
  cost exactly one `state()` call per point. Temperature sweeps use the same
  path and therefore also wait for arrival.
- Field readback: `safe_read(mag_proxy, mag_fld_attr)`. The readback attribute,
  the axis label/unit, and the setpoint unit are **config-driven** (not
  hardcoded), so the two magnets and temperature sweeps are labelled truthfully:
  - `field_readback_attr` — which attr to read as the actual x (default = setup
    `magnet_field_attr`). A **temperature sweep** sets this to the temperature
    attr so it reads temperature back, not `field_polar_corr` (which is what
    caused the old "weird x" — reading a Beckhoff field attr off the AttoDRY,
    failing, and plotting `setpoint × 0.15`).
  - `field_x_label` / `field_x_unit` — the plotted/stored actual axis.
    Samba_main field = `Field [mT]` (Beckhoff returns mT, matches DC-Hyst);
    Cryo field = `Field [T]` (AttoDRY); Cryo temperature = `Temperature [K]`.
  - `field_setpoint_unit` — unit of the commanded setpoint (`A` for current,
    `T`/`K` when reading back the same quantity). When setpoint and readback
    are the same quantity, a failed readback falls back to the **setpoint**
    itself, not `× field_per_amp`.
  - `_open_hdf5` and the live-plot x-axis both use these keys (previously both
    hardcoded `Field`/`T`/`A`).
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

**Files:** TANGO_Devices repo, `tango_servers_new/ZurichInstruments_lockin_correct_read/ZI.py` (resp. `..._lockin2_correct_read/ZI2.py`), `ThreadZI_DAQ.py` / `ThreadZI2_DAQ.py` — all device server sources live in the separate TANGO_Devices repository (the copies formerly in `Samba_main/tango_devices/` and `Cryo/tango_device_cryo/` were removed)

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

**Server:** TANGO_Devices repo, `tango_servers_new/SetupLock/SetupLock.py`
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
from analyze_samba import analyze_SOT

# Trace + retrace (returns (res, None) for legacy single-direction data)
res_trace, res_retrace = analyze_SOT.import_analyze_both(SCANLIST)

# Single direction with explicit overrides
res = analyze_SOT.import_analyze_SOT(
    SCANLIST,
    see_channels = ('DC', 'ZI_x1'),   # None = auto-detect
    current_mA   = 12.5,              # None = HDF5 metadata → filename → 10 mA
    ignorLines   = (3,),              # 1-based, drop these scanlist rows
    fit_edge_offset = 8,
)
```

`analyze_cryo` and `SambaSOTAnalysis` are kept as aliases (as are the old
`*_cryo` helper names) for backwards compatibility with older measurement
scripts — the module analyses Green, IR, and Cryo data alike.

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
  <current>mA <meas-date>/          # e.g. "15mA 20260326" — groups a measurement
    <run-date> <run-time>[_<direction>]/   # e.g. "20260702 105936"
      intensity_<ch>.png
      phase_search.png
      sumdiff_<ch>.png  …  realimag_<ch>.png  …  negpos_<ch>.png
      fit_<ch>.png
      analyzed_data.csv
      results.json
```

The mid folder groups every analysis of one measurement (same current +
measurement date); each run drops a fresh date-time subfolder inside it.
Measurement date is taken from the data file's `YYYYMMDD` sub-folder, else a
date token in the scanlist name, else today.

Default `analysis_base` is auto-set to `Analysis_Samba/` two levels above
the scanlist folder — scanlists in `<...>/Scanning/Data/ScanLists_<X>/` put
the analysis in `<...>/Scanning/Analysis_Samba/`; sample-name directories
are created inside it. Pass `analysis_base_dir=` to override.
A timestamped subfolder per scan keeps re-runs separated. Override with
`save_dir=` (parent) or `save_subdir=False` (write directly into `save_dir`).

### Calibration file

`calibration.txt` (v3) lives in the sample folder; line-based, 5 data lines:

```
# samba_calib v3  —  6 mV λ/2 sweep / Ms (A/m) / t_stack (nm) / t_FM (nm) / theta (deg)
0.05 1.10 2.18 3.27 4.40 5.51   # 6 mV λ/2 sweep at ticks 0,5,10,15,20,25
1.4e6                            # Ms — saturation magnetization (A/m); 0 = unset
8.0                              # t_stack — current-carrying stack thickness (nm); 0 = unset
3.0                              # t_FM — ferromagnet thickness (nm); 0 = unset
0.0                             # theta — 1st-harmonic phase offset (deg)
```

(v2 files without the t_FM line are still read and upgraded on the next write.)

The old R1/R2 (parallel-channel) lines were **dropped** — the SOT efficiency
uses geometry + Ms, not a resistance ratio. `read_calibration()` builds the
file from the HDF5 metadata and prompts only for what's missing, then writes
it back so later runs are silent:
- `sln` (µrad/mV): explicit `sln=`/`calibration=` → **HDF5 `/data/calibration`**
  (`read_h5_calibration()`) → the file's 6 mV line → prompt → default 1.0.
- `Ms` [A/m] and `t_stack` [nm]: explicit arg → file → prompt (blank/0 = unset,
  ξ_DL then skipped; not re-prompted).
- `theta`: never prompted (auto-detected by `get_theta`); file value or 0.
- `t_FM` [nm]: `t_fm_nm=` arg → HDF5 `fm_thickness_nm` metadata (Samba/Cryo
  metadata panel) → file → prompt (blank = unset).
Old-format (R1/R2) files are detected (no `samba_calib v2` marker) and rebuilt.
`results.json` records `sln`, `sln_source`, `bd_calibration_mV`, `device_id`,
`r_4wire_ohm`/`r_2wire_ohm` (Ω), and `fm_thickness_nm` from the metadata.

**SOT / spin-Hall efficiency** — with `Ms`, `t_stack` (from calibration.txt or
args) and `t_FM` available, `eval_width_and_fit` computes
`ξ_DL = (2e/ℏ)·μ₀·Ms·t_FM·(B_DL/μ₀) / J` with `J = Ic/(w·t_stack)` (w = the
fitted device width, Ic = the coefficient-corrected total current) and stores
`xi_DL`, `xi_DL_err`, `J_A_per_m2`, `Ms_A_per_m`, `t_stack_nm`, `t_fm_nm` in
`results.json`. `import_analyze_both` runs each direction independently and
prints the full traceback on a per-direction failure, keeping the other.

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
Polarity grouping is `relay_sign × sign(field_T)` from columns 2–3 of the
scanlist (on constant-relay data this reduces to the field sign). Note the
absolute DL sign is a labelling convention and may differ from the legacy
`data_calculation_new` (`-sign(field_T)` with its `#INVERTED!!` flip). The
error is the **standard error of the mean** (SEM = std/√N combined in
quadrature), not the original's plain STD — so bars here are ~√N tighter.
This is deliberate; drop the `/√n` factors in `data_calculation` to restore
STD-style bars.

### Phase optimisation

`find_phase()` uses `scipy.optimize.minimize_scalar` with bounds `[-90°, 90°]`
to avoid the 180° degeneracy that an unbounded optimiser hits. Run per
polarity and averaged. Saved as `phase_search.png` when `do_plot=True`.

**Edge detection** (`find_edges_width`) tries the innermost derivative-peak
pair first, then falls back to the left/right-half strategy, then to the
steepest gradient — the first result with width ≥ `min_width` (default 4)
wins. `get_edges` never aborts: if the width is still too small it warns
loudly and uses a central 15–85 % percentile window for the phase search
(the fit uses the Oersted edges, so it is unaffected). `import_analyze_both`
runs each direction in its own try/except and prints the full traceback on
failure, so one bad direction never silently loses the other.

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

---

## 26. Recent Changes (June 2026) — 2D Scan Traversal (Samba_main)

### Zigzag finally wired into the engine

The `zigzag` checkbox in `trajectory.py` had always saved `cfg["zigzag"]`, but
`core/scan/runner.py` never read it — 2D scans ran a plain forward raster with a
full fly-back between rows. The standard 2D loop now reverses the **physical** X
traversal on every odd Y row when `cfg["zigzag"]` is set (`SPATIAL_XY` only):

```python
if cfg.get("zigzag") and hdf_scan == "SPATIAL_XY" and iy % 2 == 1:
    x_seq  = x_plan[::-1]
    ix_seq = ix_seq[::-1]
```

The spatial index `ix` still maps to the correct data column, so the stored map
stays in ascending-X order regardless of sweep direction. The `x_seq`/`ix_seq`
zip pair was already set up for exactly this — only the reversal was missing.

### Fast (main) scanning axis selector

Samba_main 2D scans can now sweep **either** axis as the fast (inner) loop:

- **X-fast** (default): for each Y row, sweep all X — the historic behavior.
- **Y-fast**: for each X column, sweep all Y.

Data is stored identically as `[iy, ix]`, so the saved HDF5 map and the live
2D plot orientation are **the same** in both modes — only the physical traversal
order changes. This matters for drift/hysteresis: the user picks which axis gets
the continuous fine sweep.

**UI** (`Samba_main/panels/trajectory.py`): a "Fast axis: [X][Y]" pill pair
(green `#a6e3a1`, mutually exclusive `QButtonGroup`) lives in the same
`zigzag_w` container as the zigzag checkbox, shown only when both axes are on.
Persisted via `cfg["fast_axis"]` (`"act1"` = X / `"act2"` = Y) in
`get_config_partial()` / `load_config()`; default added to `make_default_config`
(`config.py`). Old configs default gracefully to `"act1"` (no migration needed).

**Engine** (`core/scan/runner.py`): a dedicated branch
`elif hdf_scan == "SPATIAL_XY" and cfg.get("fast_axis") == "act2":` implements
the X-outer / Y-inner traversal. It is kept **separate** from the battle-tested
X-fast loop (which still carries all the FIELD/TIME/RTV40/adaptive-settle
special-cases) to avoid disturbing it; the two loops share the new
`_acquire_point_retry()` helper.

### `_acquire_point_retry()` extracted

The per-point lock-in-settle + acquire + retry/auto-pause block (the
`while not self._abort:` loop) was extracted from the standard loop into
`ScanRunner._acquire_point_retry(...) -> (vals, t_trigger)`. Behavior is
identical (verified by the existing retry tests + `TestZigzag2D`); the
extraction lets the X-fast and Y-fast loops reuse one copy.

### Zigzag generalization for Y-fast

Zigzag in the Y-fast branch reverses the **Y** sweep on odd X columns — the
natural analog of reversing X on odd Y rows. `zigzag_cb` label updated to
"reverse direction on every fast line" to reflect that it follows the fast axis.

### Cryo untouched

Cryo's interleaved trace/retrace (`_interleaved_2d` / `_interleave_axis`) is a
separate engine path checked **first**, and Cryo never sets `fast_axis`. The
Cryo `TrajectoryPanel` is a different class with no fast-axis pills. All Cryo
behavior is unchanged.

### Tests

`test_runner.py` gains `TestZigzag2D` (4 tests) driving `run()` over a 3×2 grid
and capturing the point-callback order:
- X-fast zigzag reverses odd rows (`[2,1,0]`), even rows forward (`[0,1,2]`)
- X-fast without zigzag keeps every row forward
- Y-fast groups by column, sweeps Y inside each (`[(0,0),(1,0),(0,1),…]`), and
  writes every grid cell exactly once
- Y-fast + zigzag reverses the Y sweep on odd columns

Total suite: 18 tests, all passing (`python test_runner.py -v`).

---

## 27. Recent Changes (June 2026) — Plotting & UI Polish

### Live 1D legend shows from scan start (no manual refresh)

**File:** `core/plot_widgets.py` — `Live1DWidget.apply_config()`

The legend was created inside an `if visible:` block where `visible` required
`len(line.get_xdata()) > 0`. At scan start the lines are created empty
(`ax.plot([], [])`) with no data yet, so the legend was skipped — and
`_throttled_draw()` never (re)creates a legend. The user had to trigger a
config re-apply (the "refresh") *after* data existed to see it.

**Fix:** split the per-axis line list into `labelled` (any non-`_` label) and
`with_data` (has points). Y-limits still use `with_data`; the legend is now
drawn whenever `labelled` is non-empty — so it appears immediately at scan
start, before the first point. Shared module → fixes both Samba_main and Cryo.

### Screen-aware window sizing (both apps)

**Files:** `Samba_main/samba.py`, `Cryo/samba_cryo.py` — `_restore_geometry()`

The main window used a hard `setMinimumSize(1360, 920)` and opened with a plain
`show()`. On smaller laptop screens (e.g. 1366×768) the 920 px minimum height
exceeded the usable area, so the bottom status/action bar was clipped — and the
minimum prevented shrinking to fit.

**Fix:**
- Minimum lowered to `1180 × 640` (fits any modern laptop).
- `_restore_geometry()` now: restores saved geometry if present, else opens at
  the preferred `1360 × 920`; then **clamps** the size to the usable screen
  (`availableGeometry()` minus a small decoration margin) and **pulls the window
  back on-screen** if a saved position lands off the display (covers
  resolution / monitor changes). Falls through gracefully if no screen is
  reported.

### Data browser — switch channels on a 2D map (no collapse to 1D)

**File:** `core/data_browser.py` — `DataBrowserPanel`

Loading a 2D scan auto-plotted a 2D colour map, but the **Plot** button always
called `read_1d` + `plot_1d`, so changing the Y channel collapsed the map into a
1D line — there was no way to view a different channel *as a map*.

**Fix:** added a **"2D map"** checkbox next to the X/Y selectors:
- Auto-enabled and checked for non-DC scans with a real Y axis (`n_y > 1`);
  disabled for 1D / DC files.
- When on, the **Y combo selects which channel** is shown and `_plot_current()`
  renders `read_2d(sensor_key=y_key)` → `plot_2d`; uncheck for a 1D line/slice.
- Column selectors and the toggle now **re-plot live** via `_on_combo_changed`
  (guarded by `_populating_combos` so repopulating the combos in `_show_file`
  doesn't trigger spurious redraws).
- `_show_file` resolves the mode (DC/1D → line, 2D → map) and calls the unified
  `_plot_current()` instead of an inline auto-plot block.
- A `ndim == 2` guard prevents `imshow` from choking if a 1D column is picked
  while in map mode.

### Live-plot polish — view toggles (matplotlib retained)

Task 6 was scoped to keep matplotlib (right fit for scientific data + the
zoom/pan/save toolbar + HDF5/PyMca ecosystem) and add a few safe, user-driven
view controls. All changes are self-contained in the plot widgets — no
`samba.py` wiring.

**`core/plot_widgets.py`:**
- `Live1DWidget` — **"Auto-scale"** checkbox (default on) in the toolbar row.
  When unchecked, `_throttled_draw()` skips the per-frame x/y limit recompute,
  so a manual zoom/pan **survives live updates** instead of being reset every
  80 ms. Re-checking marks the widget dirty so it rescales immediately.
- `Live2DWidget` — **"Auto color"** (default on; gates the per-frame `clim`
  recompute) and **"Equal aspect"** (X/Y at the same scale — true proportions
  for spatial maps) toggles. Aspect is stored in `self._aspect` and applied in
  `_redraw()` and live via `_on_aspect_toggled()`.

**`core/data_browser.py`:**
- `BrowserPlotWidget` — **"Equal aspect"** toggle for past 2D maps. Tracks
  `self._is_2d` (set in `plot_2d`, cleared in `clear`) so toggling only affects
  colour maps, never 1D line plots; `plot_2d` honours `self._aspect`.

Shared modules → both Samba_main and Cryo get all of the above.

---

## 28. Recent Changes (June 2026) — Hardware Metadata, Data Browser & Layout

### Hardware metadata snapshot expansion

`_read_hw_snapshot()` (both apps) now captures the full hardware window at scan start:
- **Samba_main:** adds Keithley I-out readback (`hw_keithley_current_mA`) and magnet coil
  current (`hw_magnet_current_A`, skipped for FIELD scans where it is swept).
- **Cryo:** adds Keithley I-out, VTI temperature (`hw_vti_temp_K`) and magnet temperature
  (`hw_magnet_temp_K`) — the AttoDRY readbacks shown in the panel.
- New keys flow through the `runner.py` HDF5 metadata allowlist, the data-browser metadata
  preview (`_HW_DISPLAY` in `core/data_browser.py`) and the lab-notebook columns
  (`core/lab_notebook.py`). Recorded regardless of whether the device is in the measured list.

### Scanlist runs now save metadata + lab-notebook entries (bug fix)

Single scans called `_read_hw_snapshot()` and appended a notebook row on finish, but the
**scanlist** start paths did neither — so scanlist HDF5 files lacked `hw_*` metadata and 2D
map scans run via scanlist produced no CSV rows at all.
- Fix injects the hw snapshot into the scanlist config(s) before constructing
  `ScanlistWorker`, and adds a new `_on_sl_scan_done(idx, fn)` handler that appends one
  lab-notebook entry per finished scanlist file.
- Cryo's trace/retrace case indexes `cfg_list[idx % len(cfg_list)]` to log the matching
  per-direction config.

### Live 2D display-sensor switch (Samba_main)

- `_on_display_changed` now updates `_current_scan_cfg["display_sensor"]` so newly acquired
  points feed the newly-selected sensor (previously new points kept filling the original
  frozen sensor).
- `RightPanel.set_display` blocks signals so a programmatic restore (config load / setup
  switch) can't redirect the live map — only genuine user combo changes do.
- Cryo already read the display sensor live per-point, so it was unaffected.

### Data browser

- **Remember last detector:** tracks the last user-selected Y channel and defaults to it
  when opening another file that has it (falls back to first sensor otherwise).
- **Colormap picker:** a "Cmap:" combo (populated from `config.COLORMAPS`) sits next to the
  X/Y/2D-map controls; selecting one re-plots the current 2D map live via
  `_on_combo_changed` and is passed to `BrowserPlotWidget.plot_2d`.

### 2D plotting layout

- More colormaps in both `config.py` files (diverging set first for signed MOKE data, then
  sequential, then classic).
- `Live2DWidget` and `BrowserPlotWidget` switched to `constrained_layout` (aspect=auto, no
  `tight_layout`) so the map fills the window and no longer collapses to a narrow strip on
  resize; `clear()` also removes the stale colorbar.
- **Note (correction to §27):** the "Equal aspect" toggle added in §27 was removed here —
  `constrained_layout` supersedes it.

### Internal widget layout

- `ActuatorGroup` (both apps): Label/Unit/Attr fields changed from `setFixedWidth` to
  `setMinimumWidth` + column stretch so they expand and show full text; device-path tooltip
  added.
- `hardware_panel.py` / Cryo `panels.py`: range combo `setFixedWidth(70)` → `setMinimumWidth(84)`,
  Set button `30` → `44` (matches `keithley_mixin.py`).
- `setup_defaults.py` / `defaults_panel.py`: read-only label/unit fields use `setMinimumWidth`.
- Vertical splitter initial ratio `600/300` → `500/400` (and 0.55 → 0.50 resize ratio) in
  both apps, giving the trajectory / scanlist panel adequate default height.

---

## 29. Recent Changes (June 2026) — ZI/ZI2 Lock-in Server v5 Migration (thread-safe)

All four MFLI lock-in device servers — Samba_main **ZI** (dev4855, Green) + **ZI2**
(dev30933, IR) and **Cryo ZI1/ZI2** — were migrated from the old `PyTango.Device_4Impl`
(v4) to the modern `tango.server.Device` (v5) and made fully thread-safe. TANGO attribute
names (`x1`–`y4`, `settlingtime`, `integrationtime`, `Start`, …) are **unchanged**, so the
Samba client needs no changes. This supersedes the threading model described in §6 (the
poll-and-average acquisition logic there is otherwise still accurate).

### Root cause (the bug this fixes)

The class-level `ThreadZI.lock` was **defined but never used** — every `ziDAQServer`
(`daq.*`) call was unguarded. `ziDAQServer` is not thread-safe: concurrent access between
the acquisition thread's `poll()` and an attribute read (`settlingtime` / `timeconstant` /
`filterorder`, e.g. from Samba's HW-panel readback or Jive) corrupts the connection. That
single defect caused **both** reported symptoms:
- **Intermittent server crashes** — which is why Samba's client-side reads were disabled
  during scans in the first place.
- **Idle-server zero outputs** — once reads were disabled there was no API activity between
  `Start()` calls, so LabOne paused sample delivery (see below).

### v4 → v5 server changes (`cbe971e`, `62a1b7a`)

Each v5 server adds:
- `daq = None` init + a `_require_daq()` guard
- a real `self._daq_lock` serializing **all** `daq.*` paths
- `always_executed_hook` → `FAULT` state when disconnected
- `delete_device` cleanup
- a `Reconnect` command
- an `AllowVersionMismatch` property + `_connect_daq()` helper
- `_refresh_cached_settings()` warms the tc/order/settling caches at init and on `Reconnect`

### Idle-server zero-output fix (`bac5422`)

LabOne pauses sample delivery when the ziPython API connection has been idle. Without Jive
polling, the first `daq.poll()` after an idle period returns an **empty dict**; with short
integration times (< ~200 ms) the server doesn't catch up, all demod paths are missing, the
`KeyError` fallback fires, and outputs silently become `0`.
- **Fix:** a single `daq.getDouble('/<device>/demods/0/rate')` immediately **before** the
  flush poll in all six Thread files wakes the data server and guarantees streaming is active
  before flush+collect.
- A `warn_stream` now fires when the collect window returns no data at all, so the problem is
  visible in TANGO logs instead of producing silent zeros.

### Non-blocking filter reads (`b39cf81`)

`timeconstant` / `filterorder` / `settlingtime` are read-only filter-info attributes that are
**constant during a scan**, so the cached value is already correct.
- The three getters now use a **non-blocking** `acquire(blocking=False)`: refresh from hardware
  when the lock is free (idle / between points), otherwise return the last cached value
  immediately. A read during an active acquisition **never blocks**.
- **Writes** (`Amplitude` / frequency / samplingrate / phase) keep the **blocking** lock — a
  write must reach the hardware. `_settling_time()` (used by `integrationtime.write`
  validation) also stays blocking, as that path runs between scans.

### HW-panel reads during scans re-enabled (`28b7c57`)

The hardware panel used to disable its ZI/Keithley **Read** buttons and skip `refresh()`
during a scan — a workaround for the single-threaded v4 server (a read would block inside the
server while a poll was running, piling the scan's state-poller requests into `IMP_LIMIT`
CORBA errors). The v5 servers remove that root cause (lock-serialized + non-blocking filter
reads), so:
- `set_scan_running()` now just tracks the flag (no longer disables the buttons)
- `refresh()` no longer early-returns during a scan
- The hardware window stays **live and readable mid-measurement**
- Applied to both `HardwarePanel` (Samba_main) and `CryoHardwarePanel` (Cryo); Cryo's
  Keithley/AttoDRY are separate devices already polled live by `ReadbackWorker`

### Installers

- The `install_ZI_DAQ.sh` / `install_ZI2_DAQ.sh` sed patches were updated from the old
  `from Thread… import*` pattern (which no longer matched the v5 explicit import) to the
  robust `^from Thread… import Thread…$` → relative-import form.
- `zhinst` pinned to `>=24,<26` across all four install scripts.
- Packaged `ZI_DAQ/` / `ZI2_DAQ/` copies regenerated to match (relative import).

---

## 30. Recent Changes (June 2026) — Reliability, X-Axis Units, Hysteresis & Easter Egg

This batch came out of a full app review; all changes are on branch
`claude/app-review-suggestions-jozwry` and run hardware-free unit tests via
`python test_runner.py` (32 tests) + a GitHub Actions workflow.

### Scan-engine reliability (`core/scan/runner.py`, `workers.py`)
- **Sim-proxy guard**: actuator/magnet proxies use `fresh_proxy()`; when pytango
  is available and a configured stage/magnet is unreachable, the scan **refuses
  to start** instead of silently "moving" a cached `SimProxy` and recording fake
  data. `ScanlistWorker` does the same for relay/magnet when relay/field flip is on.
- **I/O timeout no longer starves**: `safe_read`/`safe_write` spawn one daemon
  thread per call (was a fixed 8-thread pool whose slots a hung device could
  permanently occupy).
- **Dedup**: deleted `_trigger_poll_read` (158-line near-copy of `_do_acquire`);
  the interleaved trace/retrace loops now use `_acquire_point_retry`, gaining the
  same per-point retry + auto-pause as every other scan type. Trigger-recovery
  factored into `_recover_trigger`.
- **Polarity integrity** (`ScanlistWorker`): field flip retries 3× then
  auto-pauses (was: logged + skipped → wrong polarity); failed field readback
  records **NaN** (not `0.0`, which `sign()` turned into corrupted pos/neg grouping).
- **HDF5 write failures** surface: `_write_point` logs the first failure and
  auto-pauses after 5 consecutive (disk full / broken handle) instead of an
  all-NaN file.
- **Unit-aware `_move` tolerance**: position-mismatch warning is now ½ the scan
  step in the axis' own units (was unit-blind `max(1 % target, 50)`).
- **`_finalize_hdf5`** signature trimmed to the 3 args it uses; `ScanWorker.run`
  emit-conditional unrolled.

### Stale-lock recovery (`core/setup_lock.py`, new — shared)
- Single shared implementation; `Samba_main/setup_lock.py` and `Cryo/setup_lock.py`
  are re-exports. Lock stamps carry a full date+time; a lock older than
  `STALE_LOCK_HOURS` (12 h) is treated as abandoned and taken over with a warning
  (was: setup locked out until manual Jive clear). Legacy stamps without a date
  are still honored as held.

### FIELD scan waits for ramping magnets (`runner.py`)
- After the settle sleep, FIELD scans poll the magnet device and wait while it
  reports state `MOVING` (the AttoDRY superconducting magnet holds MOVING until
  the written field/temperature setpoint is in tolerance), bounded by
  `field_settle_timeout` (default 300 s, abort/pause-aware), then re-apply
  `settle_time`. Beckhoff (no MOVING feedback) costs one `state()` call/point.
  Temperature sweeps use the same path. **Pairs with the AttoDRY server fix** that
  stops it being stuck-in-MOVING after a restart.

### Config-driven FIELD / temperature / hysteresis x-axis units (`runner.py` + both apps)
Fixes the "weird x-axis values". The FIELD path hardcoded `Field`/`T`/`A` in both
`_open_hdf5` and the live plot, and always read back `setup["magnet_field_attr"]`:
- Cryo temp sweep read `field_polar_corr` (a Beckhoff attr) off the AttoDRY →
  failed → plotted `setpoint × 0.15`.
- Samba_main field stored **mT** (Beckhoff returns mT) labelled as **T**.
Now per-scan, config-driven (the two apps use **different magnets**):
- `field_readback_attr` — attr read as the actual x (default = setup
  `magnet_field_attr`). Temp sweep → temp attr; Cryo field → `MagneticField`
  (AttoDRY R/W, read==write); Samba_main → `field_polar_corr` (mT).
- `field_x_label` / `field_x_unit` — Samba_main `Field [mT]`, Cryo `Field [T]`,
  Cryo temperature `Temperature [K]`. Used by both `_open_hdf5` and the live plot.
- `field_setpoint_unit` — `A` for current, `T`/`K` when read-back is the same
  quantity; same-quantity scans fall back to the setpoint (not `× field_per_amp`).
- Old configs auto-upgrade: Samba_main migration v3→v4 + Cryo `_migrate_config`
  backfill the keys with the right per-app values (scans always rebuild config
  from the live panel, so running scans were never wrong — this fixes the
  load-time label and any disk-replayed config).

### DC-hyst HDF5 dedup (`runner.py`)
- `_run_dc_hyst` deduplicates channel dataset names like `_open_hdf5` (suffix
  `_2`, …). Two enabled channels whose labels sanitize to the same key (two blank
  labels → `sensor`, identical names) no longer crash file creation with
  "Unable to create dataset (name already exists)".

### Device-server copies removed
- `Samba_main/tango_devices/` and `Cryo/tango_device_cryo/` (~39 MB of duplicated
  server source) deleted — all servers live in the separate **TANGO_Devices** repo,
  which is ahead. Verified nothing imports them; the copies held no code missing
  from TANGO_Devices.

### SAMBA acronym + easter egg
- The official backronym is documented in the `samba.py` / `samba_cryo.py` module
  headers and the `CLAUDE.md` header: **S**trnad & Goldenberger **A**pplication for
  **M**agnetism **B**ased **A**nalysis.
- `core/easter_egg.py`: the Konami code (↑↑↓↓←→←→) reveals the unofficial
  "Somewhat Adequate, Mostly Buggy Application". Application-wide event filter,
  observes only (never consumes keys); dedupes propagated key deliveries by
  `(key, timestamp)` and ignores auto-repeat; plain-text dialog (Linux Qt fonts
  lack emoji glyphs). `SAMBA_EGG_DEBUG=1` logs to stderr.

### Tests / CI
- `test_runner.py` grew to 32 tests (actuator guard, interleaved traversal,
  write-failure pause, FIELD ramp wait, field-axis units, DC-hyst dedup,
  lock-stamp parsing). `.github/workflows/tests.yml` runs them on push/PR
  (numpy + h5py only; Qt and tango are stubbed).

---

## 31. Recent Changes (June 2026) — DC-Hyst Per-Cycle Data & Source Selection

Item A from `CONTINUATION.md` (the PyHysteresis per-cycle feature). The TANGO
device already retains every cycle and can re-average excluding bad ones; this
batch wires Samba into it. A.1/A.3/A.4 done; A.2 (the interactive panel UX) is
still open — see `CONTINUATION.md` for the design note (use a compact
exclude-list, **not** N checkboxes).

### A.1 — Raw per-cycle half-loops saved to HDF5 (`core/scan/runner.py`)
- New `_save_hyst_cycles()`, called from `_run_dc_hyst` on completion:
  `GetNumberOfCycles` + `GetCycle(1..N)` → a **`/data/cycles` group** of
  per-quantity 2-D datasets, each `[n_cycles, n_loop]`:
  `field` (mT) + `result1`..`result6`, positive half then negative half.
  Group attr `n_cycles`; `field` carries `unit`, each `resultN` carries its
  display `label`.
- **Why a group of 2-D arrays, not one 3-D `[n_cycles,7,n_loop]` dataset:**
  PyMca could not open files where a 3-D dataset sat next to the 1-D averaged
  signals in `/data` (its NXdata auto-plot chokes on the rank mismatch — there
  is no `NX_class`, so it guesses from shapes). A subgroup of 2-D arrays is
  invisible to the signal detector and each `resultN` opens as a clean
  cycles×points image where a bad cycle is an obvious stripe.
- Best-effort: any failure is logged and swallowed; a device server without the
  per-cycle commands simply yields no group. The averaged result is already
  written, so the file is valid either way.

### A.3 — Analysis reads /data/cycles (`Analysis/samba_io.py`)
- `load_hyst_cycles(path)` → dict with `field`/`result1..6` `[n_cycles, n_loop]`
  arrays, a `valid` mask (all-NaN cycle = failed read), channel `labels`; `None`
  on old files. Reads the current group-of-2-D-arrays layout and transparently
  falls back to the legacy 3-D `[n_cycles,7,n_loop]` dataset.
- `hyst_cycle_average(cyc, exclude=())` — offline mirror of the device's
  `RecomputeAverage`: drop bad 1-based cycles and re-average (NaN-aware).
- `hyst_detect_outliers(cyc, channel, n_sigma)` — robust median+MAD flag of
  cycles whose loop deviates from the per-point median.
- `plot_hyst_cycles(...)` — faint per-cycle overlay + bold average (lazy mpl).
- The module-level `scipy.interpolate` import was made lazy (only `average_scans`
  uses it) so these numpy/h5py-only helpers — and the loaders — import without
  scipy, matching the CI environment.

### A.4 — Recorded-source selection (`config.py`, `right_panel.py`, `samba.py`, `runner.py`)
- New config key `hyst_sources` (6 ints; 1..6 = AnalogIn1..6, 11..16 = ELM1..6;
  default `[1..6]` preserves the old hard-wired behaviour). Schema bumped to v5
  with `_migrate_v4_to_v5` backfilling old configs.
- `RightPanel` DC page gains a compact "Recorded sources (PLC)" group: 6 combos
  (R1..R6) with `get_dc_sources()` / `load_dc_sources()`. Round-tripped through
  `_load_config` / `_save_active_config` / `get_config_partial`.
- `_run_dc_hyst` writes `source1..6` to the device just after the base params,
  before measuring; an older server/PLC that rejects the attr is logged and
  tolerated (keeps AnalogIn1..6). `hyst_sources` is also stored in HDF5 metadata.
- Samba_main only (the Beckhoff DC-hyst path); Cryo is unaffected.

### Tests / CI
- `test_runner.py` grew from 32 → 42 tests: `TestDcHystCycleSave` (4),
  `TestHystCycleRoundTrip` (4, writer↔reader via `Analysis/samba_io`),
  `TestDcHystSourceWrite` (2). Still numpy + h5py only.

---

## 32. Recent Changes (July 2026) — Calibration on Every Scan & Setup-Level Shared Metadata

Branch `claude/moke-sot-scan-fixes-11x8y9`. Hardware-free (`python test_runner.py`,
now 48 tests).

### BD (λ/2) calibration now written for *every* scan type
The 6 mV λ/2 calibration array (`/data/calibration`) was only written by
`_open_hdf5` (SPATIAL / FIELD / TIME) **and** only injected into the config by
`_start_scan` / `_start_scanlist`.  So two paths silently lost it:
- **DC hysteresis** — `_run_dc_hyst` builds its own HDF5 file and never wrote the
  calibration dataset.
- **Calibration time scans** — `_start_calib_timescan` never injected
  `cfg["bd_calibration"]` (only the two other start routes did).

Fixes (Samba_main + Cryo):
- Injection moved into **`_build_full_config()`** — the single build path shared by
  `_start_scan`, `_start_scanlist` and `_start_calib_timescan` — so the panel's
  6 mV values reach the config for *all* scan types.  The redundant per-route
  injections were removed (Cryo's per-cycle deepcopies inherit it).
- `_run_dc_hyst` (`core/scan/runner.py`) now writes `/data/calibration`
  (float64, `unit=mV`, `role=calibration`) right after the channel datasets,
  matching `_open_hdf5`.  The Analysis module's `read_h5_calibration()` reads the
  same path, so DC-hyst files now feed SOT calibration like the others.

### Setup-level shared metadata (same sample across scan types)
Metadata (operator / sample / device / notes / incidence / polarization / mirror
shift / R4W / R2W / FM thickness) was stored **per scan config**, so switching
between configs of different scan types (a map vs. a line scan vs. a field sweep)
reset the sample identity.  Metadata describes the *physical sample*, which is
constant across scan types.

Fix (both apps): the whole `MokeMetadataGroup` is now stored **once per setup**
(`setup["metadata"]`):
- `_save_active_config()` persists `setup["metadata"] = traj_panel.meta.get_values()`.
- `_load_active_config()` re-applies `setup["metadata"]` into both the trajectory
  and scanlist metadata groups **after** `load_config()`, overriding the per-config
  copy — so switching configs keeps "the same sample".
- Per-setup isolation is preserved: `_on_setup_changed` saves before switching, so
  Green / IR / Cryo keep their own metadata.  Old setups without the shared block
  fall back to the config's own metadata and populate it on the first save.
- Per-config metadata copies are still written (via `get_config_partial`) so HDF5
  files stay correct; the shared block just wins on load.

### Tests
- `test_runner.py` +2 → 48: `TestDcHystCalibration` (calibration written to the
  DC-hyst HDF5; absent when no `bd_calibration` key).

---

## 33. Recent Changes (July 2026) — Data Browser, Naming, Lab Notebook & Plot Interaction

Branch `claude/moke-sot-scan-fixes-11x8y9`. Hardware-free
(`python test_runner.py`, 51 tests). Batch of small UI/quality improvements.

### Data browser — point-by-point (index) x-axis (`core/data_browser.py`)
- New sentinel `INDEX_KEY = "__index__"` and an **"Index (point #)"** entry
  appended to the X-axis combo. When selected, `read_1d` ignores the stored
  actuator/field/time axis and plots the signal vs. its sample index
  (`np.arange`), keeping every finite-y sample. Works for line scans and DC
  hyst; forced to the 1D path even when the 2D-map toggle is on.

### Scan/file naming
- **Polarization token** added to `MokeMetadataGroup.build_scan_name` (both
  `Samba_main/panels/_widgets.py` and `Cryo/panels.py`): `s → Spol`,
  `p → Ppol`, `45° → 45deg`, else the sanitized custom string. Inserted between
  incidence and mirror-shift; empty polarization contributes nothing.
- **Scanlist filename** (`core/scan/workers.py`): the redundant second date was
  dropped. `list_name` already begins with a `YYYYMMDD` date (from
  `build_scan_name`), so the scanlist `.txt` now appends only `_HHMMSS` (was
  `_YYYYMMDD_HHMMSS`). A time is kept so two scanlists the same day don't collide.

### Lab notebook — scanlist column + in-place migration (`core/lab_notebook.py`)
- New **"Scanlist"** column (last column, key `_scanlist_name`): records the
  scanlist name for scanlist scans, blank for single scans. Set at the scanlist
  append sites in `samba.py` / `samba_cryo.py` from `ScanlistWorker.list_name`.
- `append_measurement` now **migrates an existing notebook in place** when the
  on-disk header is a strict prefix of the current headers (columns only
  appended): it rewrites the file with the new header and pads old rows with
  blanks, so old measurements keep their column alignment in the same file.
  Only a non-prefix change (reorder/rename/remove) still falls back to the
  `.bak` backup-and-restart. **Only ever append columns at the end.**

### Plot interaction — click-to-read + text size (`core/plot_interact.py`, new)
- Shared `ClickReadout`: left-click a line plot to annotate the nearest data
  point (label + x/y); right-click or a config change clears it. Ignores clicks
  while a nav-toolbar tool (pan/zoom) is active; fully fail-soft.
- Shared `make_fontsize_spin`: a 6–32 pt spinbox for on-plot text size (labels,
  ticks, legend, readout) — so numbers are readable from across the room during
  alignment.
- Wired into **`Live1DWidget`** (`core/plot_widgets.py`) and the data-browser
  **`BrowserPlotWidget`**: both gain a "Text:" spinbox and the click readout.
  Font size flows into tick labels, axis labels, legend, title and colorbar.

### Tests
- `test_runner.py` +3 → 51: `TestLabNotebookScanlistColumn` (scanlist value +
  blank default, append-only in-place migration, non-prefix backup). The
  `ClickReadout` nearest-point math was sanity-checked headless with an Agg
  canvas (matplotlib/Qt aren't in the CI env, so that check isn't committed).

### Follow-up
- The **IR SmarAct reinit button** and **Calibration-tab LED buttons** noted here
  as pending are implemented in §34.

---

## 34. Recent Changes (July 2026) — Stage Reinit & Calibration-Tab LEDs

Branch `claude/moke-sot-scan-fixes-11x8y9` (SAMBA) +
`claude/scan-calibration-metadata-sharing-scpsvm` (TANGO_Devices).

### IR SmarAct stage reinitialise
The IR SmarAct axes occasionally wedge after manual use with the hand
controller; the fix is to re-initialise each axis (the standard TANGO `Init`,
which re-runs the motor's `init_device` and re-establishes the MCS2 connection
— distinct from Home / CalibrateAxis).
- **TANGO_Devices** (`SmarActMCS2Stage.py`): new **`Initialise`** command on the
  stage (IR-controller) device propagates `Init` to each of the three underlying
  motor devices (X/Y/Z), then refreshes the stage's cached proxies. All axes are
  attempted; errors are collected and raised together. **Needs redeploy.**
- **SAMBA** (`core/calibration.py`): a **"⟲ Reinitialise"** button in the
  Calibration tab's Stage-positioning group calls the stage device's `Initialise`
  command, falling back to the standard `Init` if the server predates the new
  command. Uses the stage device from `configure_stage` (`_stage_cfg["x"]`).
  Generic — works for the Cryo Attocube stage too (its `Init` reconnects).

### Calibration-tab LED buttons
The `Lights` TANGO server exposes `LED1ON/OFF`, `LED2ON/OFF` (LED1 = green setup,
LED2 = IR). A compact **"LEDs" row** (1 On / 1 Off / 2 On / 2 Off) was added to
the Calibration tab's Stage-positioning group.
- Shown only when a Lights device is configured (`set_lights_device(path)`);
  hidden otherwise, so Cryo (no lights_device) never shows it.
- New setup key **`lights_device`** (`Samba_main/config.py`, Green + IR, default
  `hpp-N42/light/lights` — a **guess**; correct it in Setup Defaults). Round-tripped
  through the new **"Lights (LED)"** field in `setup_defaults.py` (a plain path
  field, since the Lights device may not be in the registry). Existing setups pick
  up the default via `load_setup`'s `setdefault`.
- Wired in `samba.py` `_load_active_config` + `_on_defaults_changed`
  (`set_lights_device`). LED commands are fail-soft (status line on error).

---

## 35. Recent Changes (July 2026) — LED Toggle, Metadata Layout, Setup-Switch Config Bug

Branch `claude/moke-sot-scan-fixes-11x8y9`. Hardware-free
(`python test_runner.py`, 51 tests).

### Calibration-tab LED buttons show state (`core/calibration.py`)
The LED On/Off buttons are now a toggle pair per LED: the active state is
highlighted (On → green `#a6e3a1`, Off → red `#f38ba8`), the inactive one stays
grey. State is tracked client-side (`_led_state`, updated on a successful
command) since the Lights server has no read-back; `_style_led(led)` restyles.
`_led(led, on)` builds the `LED{n}ON/OFF` command.

### Metadata: t_FM + t_Stack on the operator row (`_widgets.py`, Cryo `panels.py`)
- `t_FM` moved off its own column (it was pushing the panel out) onto the
  **operator row** as a compact fixed-width spinbox, alongside a new **`t_S`
  (full stack thickness)** spinbox. The row is a QHBoxLayout spanning the same
  grid width as the Notes field, so the operator field stretches and the two
  thickness fields stay inside the panel.
- New metadata key **`t_stack_nm`** (round-tripped through `get_values` /
  `load_values`; defaults 0.0 on old configs). Written to HDF5 metadata next to
  `fm_thickness_nm` in `_write_hw_metadata` (`runner.py`) for the SOT analysis
  (`J = Ic/(w·t_stack)`).

### Setup-switch config-selection corruption (`samba.py`, config_list.py)
**Bug:** switching setups (Green↔IR) made the *previous* setup adopt the *other*
setup's config selection, and could surface as wrong labels/units (the wrong
config loaded). **Cause:** `setup_tabs.currentChanged` drives two slots —
`ConfigListPanel._on_tab_changed` (connected first) and
`MainWindow._on_setup_changed`. On a switch `_on_tab_changed` fires first, while
`_active_setup_name` is still the OLD setup, and emits
`config_selected(new_row)` → `_on_config_selected` writes the new list's row into
the **old** setup's `active_idx`.
**Fix:** a `_switching_setup` guard set around `setup_tabs.setCurrentIndex` in
`_action_bar_setup_clicked`; `_on_config_selected` early-returns while it's set,
so `_on_setup_changed` is the sole authority. `_on_setup_changed` also sets the
new setup's list row (blockSignals) so the highlight is correct.

### Spatial/field axis labels authoritative from setup (`samba.py`)
`_build_full_config` now injects `act1_label/act1_unit/act2_label/act2_unit` from
the setup defaults for non-TR-MOKE scans (matching how device/attr are already
injected), so a stale panel label can't leak wrong labels/units into the scan or
the saved HDF5 axis. Cryo already injects labels via its piezo block.

---

## 36. Recent Changes (July 2026) — Block Setup Switch During a Scan

Branch `claude/moke-sot-scan-fixes-11x8y9`.

Switching the setup (Green↔IR) **while a scan or scanlist is running** reloaded
the other setup's config into the panels and live display over the running one
(label/unit mixing), and would retarget the setup lock that the running scan
still holds. `_action_bar_setup_clicked` (the single choke point for both the
pill buttons and the hidden tab bar) now refuses the switch when
`_scan_running` and the target differs from the running setup: it bounces the
tab-bar + pill back to the running setup via the new `_resync_setup_ui(idx)`
and shows "Finish or abort the current scan before switching setup." in the
status label. Same-setup clicks and all not-running switches are unaffected.

---

## 37. Recent Changes (July 2026) — Config-List Routing by Setup Name (Name Transport Fix)

Branch `claude/moke-sot-scan-fixes-11x8y9` (`python test_runner.py`, 52 tests).

### Config name transported into the other setup on switch
**Bug:** with a config selected, switching Green↔IR wrote that config's *name*
into the other setup's config list (same row). **Cause:** `_on_setup_changed`
fires from `setup_tabs.currentChanged`, i.e. *after* the tab index already
points at the NEW setup — and its first act is `_save_active_config()`, which
saves the OLD setup's data but called `cfg_list.sync_name(idx, name)`, which
routed by **tab index** (`active_list()`) → old name written into the new
setup's list. Same class as the §35 active_idx bug (UI-derived vs.
authoritative setup identity), different symptom.

**Fix:** every ConfigListPanel mutator (`sync_name` / `add_item` /
`remove_item` / `rename_item`) now takes an optional `setup_name` resolved via
`_list(setup_name)`; all samba.py call sites pass the authoritative
`_active_setup_name`, so a call landing inside the switch window can never hit
the wrong list. `load_setups` also blockSignals around its `setCurrentRow`
(previously only harmless due to signal-connection ordering at startup).

### Audit of the same bug class (UI-derived setup identity / stale-if-absent loads)
- **Clean:** no remaining `setup_tabs.currentIndex()`-derived identity in
  samba.py (the one `active_list()` left is in `_on_setup_changed` *after*
  `_active_setup_name` is updated); setup-lock, notebook, server-sync, BD
  save/load, `maybe_prompt` are all name-based; `setup_defaults.load()` has a
  `_loading` guard so it can't echo defaults during a switch; shared metadata
  and sensors are unconditionally re-loaded per config (no stale panel value
  can survive a switch).
- **Fixed — BD calibration leak:** a setup with *no saved* `bd_calibration`
  kept showing (and injecting into every scan's HDF5!) the previous setup's
  6 mV values. `_load_active_config` now clears the panel to zeros with a
  "No BD calibration saved for setup 'X' yet" status, and both HDF5 writers
  (`_open_hdf5`, `_run_dc_hyst`) skip an **all-zero** calibration so the
  analysis falls back to `calibration.txt` instead of reading zeros as a real
  λ/2 sweep.
- **Theoretical only (not changed):** `if hyst_chs:` in `_load_active_config`
  would keep the previous DC-channel rows if a config had an empty
  `hyst_channels` — unreachable in practice because `_migrate_config`
  `setdefault`s a non-empty list on every config at load.

### Tests
- `test_runner.py` +1 → 52: all-zero BD calibration is not written to HDF5.

---

## 38. Recent Changes (July 2026) — Scanlist Pause Fix (Stale Worker Reference)

Branch `claude/moke-sot-scan-fixes-11x8y9` (52 tests).

**Bug:** the Pause button did nothing during a scanlist. **Cause:**
`_toggle_pause` (and `_on_status`'s auto-pause detection) pick their target via
`self._worker or self._sl_worker` — but `_on_worker_finished` never cleared
`self._worker`, so after **any** earlier single scan the stale finished
ScanWorker won the `or` and all pause/resume/is_paused calls went to its dead
runner instead of the running scanlist. (A scanlist started as the first action
of a session paused fine — which made it look intermittent.)

**Fix (both apps):** `_on_worker_finished` now sets `self._worker = None` in
its terminal path, mirroring what `_on_sl_worker_finished` already did for
`_sl_worker`. In Cryo the clear is only in the terminal branch — the
`_dir_queue` branch re-assigns `self._worker` for the next trace/retrace
direction and returns early. All other `self._worker` consumers already guard
against `None` (`_abort_scan`, closeEvent worker loop).

**Note on DC hysteresis:** a running DC-hyst measurement itself cannot be
paused — the Beckhoff PLC runs the loop autonomously and the TANGO side only
polls it (only Abort is possible mid-loop). Pausing during a DC-hyst scanlist
item takes effect at the next scan boundary (`_run_list`'s between-scan
`while self.is_paused()` wait).

---

## 39. Recent Changes (July 2026) — DC-Hyst Per-Half-Loop Drift Alignment (Analysis)

Branch `claude/moke-sot-scan-fixes-11x8y9` (56 tests).

Long DC-hyst runs suffer slow balanced-diode drift. Measured on a real
1000-cycle file (Pt(8)CoFe(3) ETH1, 53 min): loop amplitude ≈ 0.62 mV but the
per-cycle baseline wanders ≈ 2 mV p2p (>3× the signal) — individual cycles look
terrible — while *within* one ~3 s cycle drift is only ~1–2 % of amplitude,
showing up as a small systematic offset between the up- and down-sweep branches
of the averaged loop.

### `hyst_align_cycles(cyc, tail_frac=0.10)` (`Analysis/samba_io.py`)
For every cycle and every **half-loop independently**, the saturated tails
(the `tail_frac` most-positive/most-negative-field points of that half) give
the half's two saturation levels; the half is shifted so their midpoint is 0.
Physics pins the Kerr signal at saturation to the same levels in every cycle
and both sweep directions, so any difference there is diode drift. This
centres all cycles on a common baseline **and** zeroes the up/down branch
offset, while leaving each half's amplitude (→ Ms, Mr, Hc) untouched. The
absolute signal level is discarded (meaningless for a drifting balanced
diode). Assumes the loop saturates within the swept range.

- `hyst_cycle_average(cyc, exclude=(), align=False, tail_frac=0.10)` — pass
  `align=True` to align before averaging (combines with `exclude`).
- `plot_hyst_cycles(..., align=True)` — aligned overlay.
- On the real file: cycle-to-cycle spread 392 µV → 29 µV (13×); the averaged
  loop's up/down branch offset closes; a handful of genuinely distorted cycles
  become visible in the aligned overlay (drop via `hyst_detect_outliers` +
  `exclude`).

### Tests
- `test_runner.py` +4 → 56: `TestHystAlign` (per-cycle offsets removed, branch
  offset closed in average, amplitude preserved, NaN cycle passthrough).

---

## 40. Recent Changes (July 2026) — Lazy Data-Browser Refresh (Setup-Switch Freeze)

Branch `claude/moke-sot-scan-fixes-11x8y9` (56 tests).

`DataBrowserPanel.refresh()` constructed a `ScanFile` — which opens the HDF5
and reads all metadata — for **every file in every date folder**, synchronously
on the GUI thread. It runs on every setup switch (when the browser tab has
been shown) and after every scan, so with months of data the switch froze for
~2 s.

Now only the **newest (auto-expanded) date folder** reads metadata eagerly;
older folders get name-only rows (status column "…") whose metadata is filled
on first expand (`itemExpanded` → `_on_date_expanded`). `ScanFile`s are also
created lazily on selection via the new `_get_scanfile(fp)` cache helper, so
selecting a file in a not-yet-populated folder still works; unreadable files
are greyed out with their click target removed. Shared column/colour logic
extracted into `_fill_item_meta`.

---

## 41. Recent Changes (July 2026) — Scanlist Hygiene, Field Re-Apply, LED Readback & New-Sample Popup

Branch `claude/moke-sot-scan-fixes-11x8y9` (SAMBA, 56 tests) +
`claude/scan-calibration-metadata-sharing-scpsvm` (TANGO_Devices). Six-item
user batch.

### Lab notebook — Scanlist column moved to 8th position (`core/lab_notebook.py`)
`("Scanlist", "_scanlist_name")` moved from the last column to position 8
(index 7, after "Notes", before "Incidence"). This is a deliberate one-time
**reorder** — existing notebooks whose header doesn't match are backed up to
`.bak` and restarted (the append-only in-place migration only covers appended
columns). The user planned to delete the old lab book anyway.

### Scanlist txt — aborted scans excluded (`core/scan/workers.py`)
`ScanlistWorker._run_list` no longer records a scan in `results` (and no longer
emits `scan_done`) when `self._abort` is set — an aborted scan's partial HDF5
file stays on disk but never enters the scanlist `.txt`, so the analysis never
averages a truncated line scan.

### Scanlist txt — no trailing timestamp (`core/scan/workers.py`)
The `.txt` filename is now just `{list_name}.txt` (the `_HHMMSS` suffix from
§33 removed as well). Same-day name collisions are handled with a `_2`, `_3`, …
dedupe loop instead.

### Field setpoint re-applied at scan start (`hardware_panel.py`, `samba.py`)
After an aborted scanlist zeroes the magnet, the hardware panel's field-write
spinbox still shows the old setpoint — but starting a new scan didn't re-apply
it, so scans silently ran at 0 field. New `HardwarePanel.apply_field_setpoint()`
writes the spinbox value to the magnet current attr (returns `(value, err)`;
"simulation" / no-device are non-errors). `MainWindow._apply_field_setpoint_for_scan`
calls it at the start of `_start_scan`, `_start_scanlist` (which uses the
Scanlist tab's own hw panel) and `_start_calib_timescan`, **skipping FIELD and
DC_HYST scans** (those own the magnet themselves). Success/failure is logged to
the status line. Samba_main only (Cryo's AttoDRY field control is separate).
The manual **Zero field** button also resets the write spinbox to 0 (via
`_on_zero_field_clicked`; `setValue` doesn't write to hardware — the demag
does the zeroing) so a later scan start doesn't re-apply the old setpoint.
Automatic zeroes (post-DC-hyst `demagnetize()`) leave the spinbox untouched.

### LED state readback (`TANGO_Devices Lights.py` + `core/calibration.py`)
- **Lights server**: new read-only bool attributes `led1` / `led2` returning
  the live Beckhoff output state via AdsBridge2 `ReadBool`. **Needs redeploy.**
- **SAMBA**: `CalibrationPanel._refresh_led_state()` reads both attrs in a
  daemon thread and recolours the toggle buttons via `QTimer.singleShot`;
  called from `set_lights_device()` (setup load / defaults change) and
  `_read_all()` (Calibration tab opened). Fail-soft: an old server without the
  attributes, a sim proxy, or an unreachable device just leaves the buttons
  grey (state unknown) as before.

### New-sample popup → fresh BD calibration (both apps)
Editing the **Sample** field (Trajectory or Scanlist metadata group) to a new
non-empty value pops "Sample changed to 'X' — start a new BD calibration?".
Yes → the BD panel's 6 mV values are cleared to zeros, a status hint is shown,
and the app jumps to the BD Calibration tab with its first-open reload prompt
suppressed (new `BDCalibrationPanel.suppress_prompt(setup)` — the prompt would
offer the old sample's calibration right back). `_last_sample_id` is
initialised on every config load from the (shared) metadata, and programmatic
`setText` (config load, meta sync between tabs) doesn't emit
`editingFinished`, so only genuine user edits trigger the popup. All-zero
calibrations are already skipped by the HDF5 writers (§37), so a scan started
before entering the new values falls back to `calibration.txt` in the analysis.

---

## 42. Recent Changes (July 2026) — Stale-Value Auto-Pause + Error Popup

Branch `claude/moke-sot-scan-fixes-11x8y9` (57 tests). User report: a failing
TANGO device no longer paused the measurement — the scan continued, logged
errors, and the data file contained incorrect values.

### Root cause — two engine paths recorded stale values with `ok=True`
Only an outright **read** failure (NaN) fed the per-point retry/auto-pause
machinery. Two other failure modes slipped through `_do_acquire`:
1. **Trigger failures**: a device whose `Start` command failed (e.g. server
   in FAULT — commands rejected, attribute reads still fine) was still read
   afterwards; the read succeeded and returned the **previous point's stale
   values** → point recorded, scan continued. After `AUTO_PAUSE_THRESHOLD`
   (5) consecutive failures the device was **permanently removed** from
   `trigger_devs` (§23 behaviour) — the whole rest of the scan silently
   recorded stale data.
2. **Phase B state-poll give-up**: after 5 failed `state()` calls the device
   was treated as done and read anyway → same stale-value recording.

### Fix (`core/scan/runner.py`)
- `_recover_trigger` now reports **every** unrecovered dispatch failure via
  `trigger_failed` (not only at the 5-fail threshold) and devices are **never
  removed** from `trigger_devs`.
- `_do_acquire` collects `bad_devs` = unrecovered trigger failures ∪ Phase B
  state-poll give-ups; after the batch read, sensors of those devices are
  **forced to NaN** and `ok=False` — so a "successful" stale read can never
  be recorded. The existing `_acquire_point_retry` machinery then retries the
  point (with proxy refresh) up to 5× and **auto-pauses on the same point**;
  Resume retries it from scratch. Phase B *timeout* (device slow, not
  unreachable) keeps its historical log-and-proceed behaviour.

### Error popup on auto-pause (both apps)
`_on_status` shows a `QMessageBox.warning` ("Measurement paused") when a
status message carrying the engine's **"AUTO-PAUSED"** marker arrives — all
engine auto-pause paths (acquire failure, HDF5 write failure, scanlist field
flip) emit it; a manual Pause never does. `_autopause_notified` fires it once
per pause event (reset on resume via the not-paused status branch and re-armed
in `_status_bar_run_start`). The popup states that no data was recorded for
the failing point and that Resume retries the same point.

### Tests
- `test_persistent_trigger_failure_removes_device` →
  `test_persistent_trigger_failure_fails_point_not_removed` (device stays in
  `trigger_devs`, every attempt returns ok=False + NaN).
- New `test_state_poll_failure_fails_point` (trigger OK, `state()` raising →
  ok=False + NaN). Suite: 57 tests.

---

## 43. Recent Changes (July 2026) — Autofocus Rework (Sweep Instead of Hill-Climb)

Branch `claude/moke-sot-scan-fixes-11x8y9` (57 tests). User report: autofocus
"only sweeps down the z position" and behaves erratically.

### Root causes (old `AutofocusWorker` hill-climb, `core/calibration.py`)
1. The climb always started downward (`sign = -1`) and only reversed on an
   intensity **drop sharper than `Int0/50`**. On a flat or noisy focus curve
   the change never crossed that threshold, so the branch that shrinks the
   step fired every time and the sign **never flipped** — the stage just
   crawled downward with ever-smaller steps.
2. The stage was **never moved to the best Z found** — the loop ended and
   left the stage at the last (usually worst) position; `focus_found` only
   updated the plot and the jog display.
3. Each step compared against the *previous* point, not the best, making the
   walk noise-driven.

### New algorithm (same UI knobs)
1. **Coarse sweep**: Z₀ ± `Max range`, step `dz`, capped at `Max points`
   (label renamed from "Max tries"; if the range needs more points the
   coarse step widens — the fine sweep restores resolution). Single-direction
   traversal (low → high) minimises backlash.
2. **Fine sweep**: 9 points over ± one coarse step around the coarse peak
   (clamped to the range).
3. **Parabolic vertex** through the fine maximum and its neighbours for
   sub-step accuracy (only used when curvature is a true maximum and the
   vertex lies between the neighbours).
4. **Final move to the found focus** + one confirmation measurement (emitted
   to the plot; its value reported as the focus FL).
- `_measure_fl()` waits for the FL device to leave RUNNING (BeckhoffAverage
  `Start` handshake, 2 s timeout) instead of a fixed 0.3 s sleep; falls back
  cleanly for devices without state feedback.
- Abort or an all-failed sweep returns Z to Z₀ and always restores the scan
  axis (`try/finally`).

### Verification
Headless simulation (Qt/matplotlib/hardware stubbed, synthetic Gaussian
focus curve + noise): finds the true focus starting above/below/near it
(±0.02 µm quiet, ±0.06 µm at 2 % rms noise), point budget respected, stage
moved to the result, abort restores Z₀ + scan axis. Not committed (needs the
stub scaffolding); `python test_runner.py` (57) unaffected.

---

## 44. Recent Changes (July 2026) — LED Readback Delivery & Diagnosability

Branch `claude/moke-sot-scan-fixes-11x8y9` (57 tests). User report: Lights
server redeployed with working led1/led2 attributes, but the Calibration-tab
buttons still start grey.

### `core/calibration.py`
- **Guaranteed GUI delivery**: `CalibrationPanel` gains a `_gui_apply`
  pyqtSignal(object) connected to `lambda fn: fn()`; background reader
  threads emit their apply-callable through it. Replaces
  `QTimer.singleShot(0, …)` in `_refresh_led_state` **and** `_read_all` —
  singleShot from a plain Python thread has Qt/PyQt-version-dependent
  delivery; a queued signal is delivered to the GUI thread unconditionally.
- **Grey is now diagnosable**: when the led1/led2 read fails, the reason is
  shown in the status line ("LED state unavailable (…)") and set as the
  LED buttons' tooltip ("State read failed: … old Lights server without
  led1/led2, or device unreachable"). Success clears the tooltip. Verified
  headlessly (stubbed Qt/hardware): live server → correct colors;
  old server → grey + visible reason.
- Note: the SAMBA side of the readback shipped in §41 commit `97ebf12` —
  a lab installation must be pulled to at least that commit (plus this one)
  for the buttons to reflect the device state.

---

## 45. Recent Changes (July 2026) — Calibration-Tab Config & Plot, 1D Plot Layout

Branch `claude/moke-sot-scan-fixes-11x8y9` (57 tests). Four-item user batch.

### Calibration tab — its own hidden time-scan config (`core/calibration.py` + both apps)
The calibration time scan (▶ Start while the Calibration tab is open) used the
scan config selected in the left panel (its `act1_npts` / integration time).
Now the tab carries its **own** settings — a "Time scan (this tab's own
settings)" group with Points + Int time spinboxes:
- `_start_calib_timescan` (both apps) overrides `act1_npts` and
  `integration_time` from `calib_panel.get_timescan_settings()`.
- Persisted per setup under `setup["calib_timescan"]` (a hidden config —
  never in the config list): `timescan_changed` →
  `_on_calib_timescan_changed` saves; `load_timescan_settings` restores on
  setup/config load (blockSignals). Old setups default to 300 pts / 0.1 s.
- Sensors still come from the right panel (needed for multi-sensor plotting).

### Calibration plot — text size + click readout (`FocusPlotWidget`)
The §33 plot-interaction upgrade never reached the calibration plot. Its
toolbar row now has the same "Text:" spinbox (ticks, axis labels, title,
legend) and the left-click nearest-point readout.

### Calibration plot — Y1/Y2 twin axis (corrected per user feedback)
The real complaint was "select focus line on Y1 + balanced diode on Y2 and
only one gets plotted": the calibration plot had a **single y-axis**, so both
sensors were drawn on one scale and the small signal flattened into an
invisible line — the Y1/Y2 assignment was ignored. (A first attempt added
all-sensors + visibility checkboxes; reverted — not what was asked.)
`setup_timescan` now honours the sensor panel: Y1 sensors on the left axis,
Y2 sensors on a **right twin axis** (`_ts_ax2`, created per scan, removed in
`clear()`), each autoscaled independently (X shared). Cool palette left /
warm right; axis titles carry "name (unit)" (curve color when single,
axis color when several); one combined legend on the top axes. Verified
headlessly with real Agg rendering: a 5 V Y1 signal and a 1 mV Y2 signal
each fill their own scale; Y1-only scans create no right axis; autofocus
plotting is unaffected after `clear()`.

### Live 1D plot — legend & axis-title layout (`core/plot_widgets.py`)
- **Legends can no longer sit on the data**: anchored *above* the axes
  (Y1's above-left, Y2's above-right, `ncol≤3`). New `_layout()` runs
  `tight_layout`, then measures the real legend heights from a draw and
  `subplots_adjust(top=…)` to reserve exactly the strip they need — correct
  at any font size. Called from `apply_config` and on font-size change
  (which previously never re-solved the layout → axis titles overlapping
  tick numbers at larger fonts).
- **Y-axis titles show sensor name + unit** (e.g. "ZI2 x1 (µV)"), not just
  the unit. One sensor on an axis → title takes the **curve's color**;
  several → joined "name (unit)" list in the axis color (legend maps
  name → color). Rebuilt on every `apply_config`, so it follows what is
  plotted.
- **Y2 title/ticks forced to the right side** (`set_label_position("right")`
  + `tick_right()` in `_style_axes`) — `cla()` on a twinx can reset them to
  the left where they collide with Y1's.
- Verified with a real Agg render (mpl 3.11) across 9/14/20 pt and 1–6
  sensors per axis: legends fully above the data area and inside the
  figure, labels on the correct sides, y-titles clear of tick numbers.

---

## 46. Recent Changes (July 2026) — Style Batch: Palettes, Zero-Centred Maps, Eng Ticks, Light Export, State Tint

Branch `claude/moke-sot-scan-fixes-11x8y9` (57 tests). Style-review batch;
user opted out of gridlines and a global UI font size.

### `core/theme.py` (new)
Central home for the Catppuccin Mocha tokens, plot surfaces, the validated
curve palettes, `DIVERGING_CMAPS`, and the Mocha→Latte mapping used by the
light export. New code imports from here; scattered hex values migrate
opportunistically.
**Fix-up:** core modules import it by bare name (`from theme import …`), so
each app directory needs the usual re-export shim — `Samba_main/theme.py` and
`Cryo/theme.py` (`from core.theme import *`) were added after a lab-machine
`ModuleNotFoundError` (same convention as `plot_interact.py`, §33). **Any new
`core/<mod>.py` that other core modules bare-import needs both shims.**

### Curve palettes reordered (validated) — both `config.py` + calibration plot
The old Y1 order put blue `#89b4fa` next to sapphire `#74c7ec` (ΔE ≈ 6 normal
vision, 4.5 deutan — nearly indistinguishable) and sky/teal similarly. New
orders validated on the dark surface with the dataviz palette checker:
- `LEFT_COLORS  = blue, green, lavender, teal, sky`
- `RIGHT_COLORS = red, yellow, mauve, peach, maroon`
`FocusPlotWidget._TS_*_COLORS` now alias `theme.PLOT_*_COLORS`. Order is part
of the validation — do not reshuffle.

### Zero-centred diverging colormaps (`Live2DWidget`, `BrowserPlotWidget`)
A diverging map (RdBu_r…) scaled min→max puts its neutral midpoint at the
middle of the data range, not at zero — "no signal" showed as light red.
When the selected cmap is in `theme.DIVERGING_CMAPS` **and** the data spans
zero, the colour range is made symmetric (±max|data|), so white = 0 exactly.
Single-signed data keeps min→max (aesthetic use of RdBu stays contrasty).
Applied in the live map's autocolor path and the browser's `plot_2d`.

### SI engineering ticks (`plot_interact.eng_axis`)
All signal y-axes + both colorbars now use `EngFormatter(unit="", sep="")` —
ticks read "24µ" instead of nice-looking numbers with a `1e-5` offset hiding
at the axis top (classic misreading source; an additive offset is worse).
Applied: Live1D (both axes), calibration plot (autofocus + time scan, both
axes), browser 1D, Live2D + browser colorbars. X-axes unchanged.

### Light-mode figure export (`plot_interact.render_light_figure` + button)
"⬇ Light" button in the toolbar row of all four plot widgets (Live1D, Live2D,
calibration, browser): exports the CURRENT plot restyled for white paper —
white surfaces, dark ink for ticks/labels/legend, and every curve colour
mapped Mocha→Latte (pastels are unreadable on white; Latte is the saturated
counterpart). Implementation pickles the figure (deep copy — the on-screen
plot is untouched), restyles the copy, saves via file dialog (PNG/PDF/SVG,
200 dpi, bbox_inches tight).

### Nav-toolbar icons visible (`plot_interact.fix_toolbar_icons`)
matplotlib's dark-gray toolbar icons were nearly invisible on the dark
toolbar. The helper inverts each action icon's RGB (alpha preserved) →
light gray. Fail-soft; applied in all four plot widgets.

### Scan-state status-bar tint (both apps)
`_tint_status_bar(state)` styles the bottom QStatusBar: green top border +
dark-green tint while RUNNING, peach while PAUSED (manual or auto), neutral
when idle — machine state readable from across the room. Driven from
`_set_running`, `_toggle_pause`, and `_on_status`'s pause detection.

### Colormap list trimmed 24 → 7 (both `config.py`, follow-up)
The Cmap combo had grown to 24 entries. Curated set: `RdBu_r, seismic,
coolwarm, PuOr_r` (diverging, zero-centred) + `viridis, inferno` (sequential)
+ `gray`. Configs storing a removed name still render (matplotlib knows the
name; combo falls back to RdBu_r and migrates on next save), and
`theme.DIVERGING_CMAPS` keeps the removed diverging names so old scans still
zero-centre.

### Verification
- Palettes: dataviz validator (CVD + normal-vision separation) on #12121f.
- Headless real-Agg checks: eng ticks render "10µ…" with no offset text;
  RdBu_r map with data −1…5 gets clim (−5, 5) while viridis keeps (−1, 5)
  (browser + live widget); light export maps #89b4fa→#1e66f5 / #f38ba8→#d20f39,
  leaves the original figure untouched, and saves a valid PNG.
- Twin-axis timescan + LED checks re-run clean; `python test_runner.py` 57 OK.

---

## 47. Recent Changes (July 2026) — Setup-File Load Failures Made Visible & Non-Destructive

Branch `claude/moke-sot-scan-fixes-11x8y9` (61 tests). User report: after
copying `~/.config/moke_scan/` to a new computer, Samba_main started with
only the Green setup's scan configs.

### Root cause class
`load_setup()` fell back to `make_default_setup()` **silently** in both
failure modes — file unreadable (partial copy, permissions, encoding) and
file missing. Worse, the next auto-save then **overwrote the real
`<Setup>.json` with the defaults**, destroying the copied data. There was no
visible signal, so the user couldn't tell whether IR.json was missing,
unreadable, or clobbered.

### Fix (`Samba_main/config.py` + `Cryo/config.py`)
- **Unreadable file** → backed up to `<name>.json.bad` *before* any save can
  overwrite it (original left in place), and the returned default setup
  carries `_load_status = "error: …"`.
- **Missing file** → `_load_status = "missing"`; a healthy load → `"ok"`.
- `save_setup()` strips the transient `_load_status` key.

### Startup warnings (both apps)
- **Samba_main** (`_collect_setup_load_warnings` / `_show_setup_load_warnings`):
  a QMessageBox at startup lists (a) setups whose file could not be read
  (with the `.json.bad` backup path and the note that saving overwrites), and
  (b) setups whose file is **missing while other setups loaded fine** — the
  partial-copy case; all-missing is a normal first run and stays silent.
- **Cryo**: single setup — popup only for the unreadable case (missing =
  first run).

### Tests
- `test_runner.py` +4 → 61: `TestSetupLoadStatus` imports the real
  `Samba_main/config.py` against a temp CONFIG_DIR — valid file → "ok";
  corrupt file → "error", `.bad` backup byte-identical, original untouched;
  missing → "missing"; `save_setup` strips the key.

---

## 48. Recent Changes (July 2026) — Calibration Tab Full Hidden Config & 1D Legend Sizing

Branch `claude/moke-sot-scan-fixes-11x8y9` (61 tests). Three-item user batch.

### Calibration tab layout — Time scan under Autofocus
The Time-scan settings group moved from a third column into a **vertical
column 2** (`col2`): Autofocus on top, Time scan underneath with stretch, so
together they take the height of the Stage-positioning column.

### Calibration tab — its own sensors (completes the hidden config)
The calibration time scan no longer borrows `cfg["sensors"]` from the config
open in the left panel. The Time-scan group (retitled "…this tab's own
config") now contains its **own sensor picker rows**:
- `CalibrationPanel` gains `sensor_row_factory` — each app passes a lambda
  creating its own `SensorPickerRow` (identical API in Samba_main and Cryo)
  bound to the live registry via the new `MainWindow._registry_now()`
  (falls back to `load_registry()` before the registry panel exists).
- Rows: "＋" add button (max 6), per-row × delete, checkbox/device/channel/
  axis as in the right panel; `get_timescan_sensors()`;
  `get_timescan_settings()` now includes `"sensors"`, persisted in
  `setup["calib_timescan"]` through the existing save/load path.
  `load_timescan_settings` rebuilds rows (loading never re-emits); a fresh
  setup starts with one row; registry edits rebuild rows
  (`_on_registry_changed` → `load_timescan_settings`).
- `_start_calib_timescan` (both apps) uses the tab's sensors for
  `cfg["sensors"]` (falls back to the right panel only if the tab has no
  rows); the no-sensor warning names the calibration tab.
- Cryo passthrough: `CryoCalibrationPanel.__init__` forwards
  `sensor_row_factory`.

### Live 1D legend — fixed size, no dead band, resize-aware
- **Legend no longer scales with the Text spinbox** (`_LEGEND_PT = 9`,
  compact paddings) — a 20 pt legend used to eat half the plot.
- **Dead space above the plot removed**: `_layout()` previously *added* the
  legend strip to tight_layout's already-generous top margin (~40 px of
  emptiness); it now sets `top = 1 − legend_h − 8px`, filling the figure to
  the top (measured: 7 px headroom, 1 px gap to the axes).
- **Re-layout on canvas resize** (debounced 150 ms QTimer on
  `resize_event`): the reserved strip is a figure fraction, so growing the
  window used to stretch it into a large empty band (measured 36 px on a
  6×4→10×8 grow; now re-tightens to 7 px).
- Verified with real Agg rendering at 9/20 pt and across a resize;
  calib-sensor row logic verified headlessly (round-trip, add/remove emit
  once, default row, cap 6). Suite: 61 tests.

---

## 49. Recent Changes (July 2026) — Per-Sensor Colored Y-Axis Titles

Branch `claude/device-plot-legend-colors-yifm6d` (61 tests). User report: with
two sensors on one axis the curves get different colors but the axis title is
all one color (partially supersedes the §45 "several → axis color" behaviour).

### `set_multicolor_ylabel` + `_MulticolorYLabel` (`core/plot_interact.py`)
matplotlib's ylabel is a single `Text` and can only have one color, so the
title is now drawn as **stacked rotated segments, one per sensor, each in its
curve's color**:
- The real ylabel keeps the full `"A (µV), B (µV)"` string but is made
  invisible (`set_alpha(0.0)`) — it still reserves the correct
  tight_layout/`_layout()` space and gives the segments a position to follow.
- `_MulticolorYLabel` (an `Artist` added via `ax.add_artist`, so `cla()`
  removes it) re-reads the label's position, font size and rotation **on every
  draw** — segments track autoscaling tick-width changes, the Text spinbox and
  resizes with no extra wiring. `zorder=5` so it draws *after* the `YAxis`
  (zorder 1.5), which updates the label position during its own draw; at a
  lower zorder the segments sat at the previous frame's label position.
- Segments stack bottom→top (reading order of a 90°-rotated label), centred on
  the label's extent, `","` appended to non-final segments plus a ≈one-space
  gap. One sensor on an axis → plain ylabel in the curve color (unchanged);
  empty → label cleared.
- Module still imports without matplotlib (guarded `Artist` import — CI).
- `render_light_figure` maps the segment colors Mocha→Latte like curves (the
  segments are not in `ax.texts`, so the existing loops missed them); the
  artist survives the figure pickle.

### Call sites
- `Live1DWidget.apply_config` (`core/plot_widgets.py`) — Y1 + Y2.
- `FocusPlotWidget.setup_timescan` (`core/calibration.py`) — time-scan Y1 + Y2;
  autofocus mode and `clear()` restore the plain "Focus signal (V)" label.

### Verification
Headless offscreen-Qt run of the real widgets (mpl 3.11, 27 checks): segment
colors == line colors on both axes and in the calibration time scan; segments
horizontally/vertically centred on the (invisible) label after a real draw
with live data + autoscale; font-size change follows; re-apply/`clear()` never
duplicate or leak the artist; single-sensor and empty axes keep the old
behaviour; light export remaps segment colors and renders. `python
test_runner.py` 61 OK; `plot_interact` imports with matplotlib blocked.

---

## 50. Recent Changes (July 2026) — N+Δ Always Visible & Hardware-Panel Truth/Sync

Branch `claude/device-plot-legend-colors-yifm6d` (68 tests). Three-item user
batch.

### N and Δ-step both always visible, step as the base (`core/nstep.py`, new)
The N / Δ radio toggles (only one spinbox visible at a time, the other as a
grey computed label) are replaced everywhere by **both spinboxes always
visible and editable**, coupled through the new shared `NStepPair`
(+ `Samba_main/nstep.py` / `Cryo/nstep.py` shims):
- Editing one box derives the other from the current span; when start/stop
  change, the box the user edited **last** (the "anchor") keeps its value —
  and the default anchor is the **step size** (per user request), including
  after every config load.
- N stays the authoritative value handed to the engine (typing a step rounds
  to an integer point count); zero span (time scans) never clobbers N.
- Guarded `setValue` — external `valueChanged` listeners (config save,
  summary labels) still fire; Qt's no-change suppression prevents loops.
- Converted: Samba_main `ActuatorGroup` (X/Y spatial) + `FieldSegmentList`
  (per-segment Δ column, field sweeps); Cryo `ActuatorGroup` (direction-list
  span), `FieldSegmentList` (had a per-row radio toggle) and the temperature
  sweep group (N + ΔT; `temp_dT` stays persisted when the step is the anchor,
  loads via `set_step`). TR-MOKE's Δt toggle untouched.
- Dead code removed: `_on_field_mode` / `_upd_field_comp` / `_get_field_npts`
  in both apps referenced `self.fs/fe/fn/fd` widgets that were never created.

### Hardware panel — magnet current + relay read from the TANGO devices
`refresh()` (startup + every Trajectory/Scanlist tab switch) already
populated the Keithley spinboxes from the device, but the **magnet-current
write window** and **relay state** were client-side only — always 0 after a
restart. New `_read_magnet_relay()` (background thread + `_mr_ok` signal)
reads `magnet_current_attr` and `relay_attr` and fills the window/label:
- Skipped while a scan runs (a FIELD scan sweeps the current — capturing a
  mid-sweep value as the setpoint would be wrong) and for sim proxies.
- `setValue` on the write spinbox does not write hardware (only Return/Enter
  does) — the window now just shows device truth.

### Trajectory ↔ Scanlist hardware panels mirrored (`samba.py`)
**Bug (user report):** 1 A set on the Trajectory tab left the Scanlist tab's
magnet window at 0 A — and since §41, `apply_field_setpoint()` at scanlist
start writes the *Scanlist tab's* spinbox to the magnet (and
`get_settings()["magnet_current"]` feeds the field-flip magnitude), so the
scanlist really ran at 0 field. New `MainWindow._link_hw_panels()`
cross-connects the two `HardwarePanel`s:
- `amp/freq/compl/field` spinboxes and the range combo mirror bidirectionally
  (display only — writes still require Return/Enter in the edited panel);
  Qt's no-change signal suppression makes the cross-connection loop-free.
- New `HardwarePanel.relay_changed` signal (emitted on a successful/sim
  toggle and on device readback) mirrors the relay state; `set_relay_state`
  stays signal-free so the mirror can't loop.
- Samba_main only; Cryo's AttoDRY panel already live-reads via
  `ReadbackWorker` (linking its two tabs' panels is a possible follow-up).

### Tests / verification
- `test_runner.py` +7 → 68: `TestNStepPair` (load anchors step, step→N,
  N→step, span change preserves step by default / N after an N edit,
  zero-span safety, `set_step`).
- Offscreen-Qt run of the real widgets (27 checks): both apps' actuator
  groups and segment lists (derive/anchor/load round-trips), panel mirroring
  via the real unbound `MainWindow._link_hw_panels`, relay mirroring on a
  patched successful toggle, magnet/relay readback fills both tabs, sim
  refresh never clobbers values.

### Fix-up (same batch): OverflowError typing a step starting with "0"
Typing "0.5" into a Δ box emitted an intermediate "0" keystroke (clamped to
the spin minimum 1e-6); over a 50000 nm span the derived N exceeded Qt's
32-bit int range and `QSpinBox.setValue` raised OverflowError → core dump.
`NStepPair` now (a) clamps the derived N to the N box's own `maximum()`
(and 2^31−1) **before** `setValue`, and (b) sets
`setKeyboardTracking(False)` on both boxes, so derivation happens only on
commit (Enter / focus-out / arrows), never on intermediate keystrokes.
Regression test added (suite 69); verified against the real widgets with
QTest keystrokes offscreen.

---

## 51. Recent Changes (July 2026) — MCS2 Stage Home Button (Auto-Zero)

Branch `claude/device-plot-legend-colors-yifm6d` (SAMBA, 69 tests) + same
branch on TANGO_Devices. User report: after hand-controller use the IR MCS2
axes error with "movement finished, channel: 0 (invalid parameter)" until a
restart / axis Init; requested use of the SmarAct AutoZero referencing option
(position := 0 when the reference mark is found on Home). IR/MCS2 only — the
Green Smaract control must not be touched.

### Root cause of the sticky error (TANGO_Devices analysis)
The MCS2 Ctrl latches the **last** SA_CTL event per axis; the Motor's
`dev_state()` re-reads that latch on every State call and maps a failed
MOVEMENT_FINISHED to FAULT with exactly that message. Only a new axis event
(successful move/reference) clears it; motor `Init` recovers because
`init_device` re-sends the sensor configuration and rebuilds the connection.
The AutoZero chain (Motor `AutoZero` attr → Ctrl `SetAutoZero` →
`SA_CTL_PKEY_REFERENCING_OPTIONS`/`SA_CTL_REF_OPT_BIT_AUTO_ZERO`) already
existed — it was just unreachable from the stage device and SAMBA.

### TANGO_Devices — stage `Home` command (**needs redeploy**)
`SmarActMCS2Stage.py` gains `Home`: per axis (X→Y→Z sequentially) writes
`AutoZero = True` on the motor, runs the motor's `Home` (`SA_CTL_Reference`),
and waits (bounded by `MovementTimeout`) until not MOVING and `PositionKnown`
— tolerating transient FAULTs, since the stale latched event is only cleared
by the referencing's own events. Errors collected and raised together.

### SAMBA — "⌂ Home" button (`core/calibration.py`)
Next to "⟲ Reinitialise" in the Calibration tab's Stage-positioning group:
- Shown **only** when the stage device exposes both `Home` and `Initialise`
  commands — the SmarActMCS2Stage signature, probed in a background thread on
  every `configure_stage()`. The Green setup's old Smaract server and the
  Cryo Attocube stages never match, so they never see the button.
- Confirmation dialog first (the stage MOVES to its reference marks and
  positions read 0 there); runs in a daemon thread with a 120 s client
  timeout (3 axes × MovementTimeout); on success re-reads all positions,
  on failure shows the error in the status line; button disabled while
  running and re-enabled either way.
- Recommended recovery after hand-controller use: ⟲ Reinitialise, then
  ⌂ Home.

### Verification
Offscreen-Qt run of the real `CalibrationPanel` (9 checks): button hidden
initially / in sim mode / for a server without the command pair; shown for
the MCS2 signature; Yes → exactly one `Home` dispatched + positions re-read +
button re-enabled; No → nothing dispatched; failure path re-enables with an
error. Both servers `py_compile` clean; `python test_runner.py` 69 OK.
