# SAMBA — Developer Documentation

**S**trnad & Goldenberger **A**pplication for **M**agnetism **B**ased **A**nalysis
ETH Zürich — Intermag Lab | Creator: Jakub Strnad | Collaborator: Tobias Goldenberger
April 2026 — October 2026

> **Where things are.** This file is the architecture reference (§1–22) plus the
> distilled conventions at the end.  The dated change log — one section per batch
> of work, with the reasoning, the measurements and what was verified — is
> `docs/CHANGELOG.md` (§11, §19–20, §23 onwards).  **Section numbers are shared
> between the two files**, so a reference like "§67" means the same thing anywhere.
> Add a new batch as the next § in `docs/CHANGELOG.md`, not here.

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
├── pyproject.toml           # pip install -e .  →  `samba`, `samba-cryo` entry points
├── requirements.txt         # the same runtime deps, for the lab's conda installs
├── test_runner.py           # hardware-free unit suite (Qt + TANGO stubbed)
├── tests/                   # pytest: real in-process TANGO devices (fakes.py), sync, log
├── docs/CHANGELOG.md        # dated design notes, §11/§19–20/§23… (numbering shared)
├── Analysis/                # post-acquisition pipeline (analyze_samba.py, samba_io.py)
│
├── core/                    # Shared implementation for both applications
│   ├── version.py           # __version__ — the ONE place the app version is written
│   ├── launch.py            # console entry points (set up sys.path like `python samba.py`)
│   ├── scan_host.py         # ScanHostMixin — run lifecycle shared by both main windows
│   ├── scan/
│   │   ├── runner.py        # ScanRunner — pure Python, no Qt dependency
│   │   └── workers.py       # ScanWorker / ScanlistWorker — QThread wrappers
│   ├── hardware.py          # Proxy cache, safe_read/write, probe_alive, SimProxy
│   ├── bgcall.py            # BackgroundCall — panel writes off the GUI thread
│   ├── validation.py        # scan-config validation, point/travel limits
│   ├── setup_lock.py        # multi-computer setup lock client (stale-lock takeover)
│   ├── plot_widgets.py      # Live1DWidget, Live2DWidget
│   ├── plot_interact.py     # click readout, font-size spin, pills, light export
│   ├── theme.py             # Catppuccin tokens, validated curve palettes
│   ├── kerr.py              # Kerr-rotation (θ) display conversion
│   ├── data_browser.py      # HDF5 file browser (+ scan_index.py metadata cache)
│   ├── calibration.py       # Calibration tab: jog, autofocus, time scan, LEDs
│   ├── bd_calibration.py    # λ/2 (BD) calibration panel (+ bd_fit.py step fit)
│   ├── current_sweep.py     # current-sweep / refocus logic (Qt-free)
│   ├── current_sweep_ui.py  # CurrentSweepGroup, RefocusGroup, ThermalSettleWorker
│   ├── nstep.py, polarity.py# N+Δ coupling; AB/ABBA flip schedule
│   ├── lab_notebook.py      # CSV lab notebook (append-only columns)
│   ├── server_sync.py       # NAS sync (+ sync_worker.py subprocess)
│   ├── applog.py            # rotating file log, noisy-logger pinning
│   ├── device_registry.py   # Device/channel definitions, registry editor UI
│   ├── script_console.py    # Embedded Python console
│   ├── easter_egg.py        # Konami code
│   └── play_intro.py        # Splash screen
│
├── Samba_main/              # Green + IR setups (a package since §88; still a script dir)
│   ├── samba.py             # MainWindow(ScanHostMixin, QMainWindow) — entry point
│   ├── config.py            # SETUP_HW_DEFAULTS, config migration, persistence
│   ├── <mod>.py             # one-line re-export shims for every bare-imported core module
│   ├── panels/              # UI panels package
│   │   ├── trajectory.py    # Scan type, actuators, field segments, DG645
│   │   ├── right_panel.py   # Sensor picker, colormap, display sensor
│   │   ├── sensor_picker.py # SensorPickerRow — device+channel dropdown
│   │   ├── hardware_panel.py# Keithley, field/relay, lock-in readback
│   │   ├── setup_defaults.py# Per-setup device paths, lock-in attr config
│   │   ├── config_list.py   # Config list sidebar
│   │   ├── scanlist.py      # Scanlist panel (polarity, current sweep, refocus)
│   │   └── _widgets.py      # NoScroll widgets, MokeMetadataGroup
│   └── scan/ → re-exports core/scan/
│
├── Cryo/                    # Cryo setup (separate entry point)
│   ├── samba_cryo.py        # CryoMainWindow(ScanHostMixin, QMainWindow)
│   ├── config.py            # Cryo-specific defaults
│   ├── panels.py            # Cryo copies of the panels (own ActuatorGroup, ScanlistPanel …)
│   ├── panels_cryo.py       # CryoHardwarePanel (AttoDRY + Keithley)
│   ├── cryo_monitor.py      # Rolling temperature/pressure plots
│   ├── keithley_mixin.py    # Shared Keithley 6221 UI code
│   ├── defaults_panel.py    # Setup defaults for Cryo
│   ├── <mod>.py             # re-export shims, as in Samba_main
│   └── scan/ → re-exports core/scan/
│
└── CLAUDE.md                # This file
```

**Code sharing:** Samba_main and Cryo both import shared modules from `core/`. Each
directory adds its own UI panels and setup-specific configuration. The `scan/` directories
in Samba_main and Cryo re-export from `core/scan/`.  The two main windows are separate
classes with their own layouts, but the whole **run lifecycle** — setup lock, status bar,
pause/abort, engine errors, refocus, the current-sweep sequencer, close — is one
implementation, `ScanHostMixin` in `core/scan_host.py`, which both inherit; each window
overrides only the hooks declared at the top of that class (§88).

**Running / installing:** `python samba.py` in `Samba_main/` (or `samba_cryo.py` in
`Cryo/`) as always, or `pip install -e .` in the repo root and then `samba` /
`samba-cryo`; both accept `--version`.  The version is written once, in
`core/version.py`, and read by both apps and `pyproject.toml`.

---

## 3. Hardware & Device Map

### Stages (SmarAct Positioners)

| Setup | TANGO Path | Attributes | Units |
|-------|-----------|------------|-------|
| IR | `smaract2/control/IR-controller` (MCS2) | x, y, z | **µm** (Conversion ±1e6 pm; X/Y direction-inverted — read from the TANGO DB, Aug 2026) |
| Green | old Smaract server | x, y, z | per Setup Defaults |
| Cryo | `hpp-N42/attocube/ANM200` (fine) / `ANC300` (coarse) | x, y, z | nm / steps |

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

**Files:** TANGO_Devices repo, `tango_servers_new/ZurichInstruments_lockin_correct_read/ZI.py` (resp. `..._lockin2_correct_read/ZI2.py`), `ThreadZI_DAQ.py` / `ThreadZI2_DAQ.py` — all device server sources live in the separate TANGO_Devices repository (the copies formerly in `Samba_main/tango_devices/` and `Cryo/tango_device_cryo/` were removed). The two files differ only in names, docstrings, the `DeviceId`/`ZI_Host` defaults and log prefixes, so **every fix must be applied to both** (and to the packaged `ZI_DAQ/`/`ZI2_DAQ/` copies the install scripts generate). A single-source version (`ZILockin` + thin `ZI`/`ZI2` subclasses) was prepared in October 2026 and parked at the lab's request — TANGO_Devices commit `7d141b7`, reverted on the same branch; see §88. The threading model below is the historic one — §29 made the servers thread-safe and §67 made `Start` raise outside ON/RUNNING; see `docs/CHANGELOG.md`.

### Purpose

TANGO device servers wrapping Zurich Instruments MFLI lock-in amplifiers. Each server manages 4 demodulators (channels 1–4), providing averaged X/Y output via a poll-and-average approach.

### ZI vs ZI2

| Aspect | ZI (Green) | ZI2 (IR) |
|--------|-----------|----------|
| Serial | dev4855 | dev30933 |
| Default host | 192.168.1.62 | 192.168.1.144 |
| `Harmonics` default | [1,2,3,1] | [1,2,3,1] (demod 4 at the fundamental since 04.05.2024) |
| Thread class | ThreadZI | ThreadZI2 (identical apart from name and log prefix) |
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
11. **One run lifecycle** — the two windows share `ScanHostMixin` (`core/scan_host.py`); a start/finish/pause/abort fix is one diff, and a unit test refuses any method defined near-identically in both windows again (§88)
12. **Refuse, don't guess** — unreachable devices refuse to start a scan, failed trigger replies fail the point, out-of-range targets are refused rather than clamped, unusable calibrations disable the display instead of showing zeros (§60, §66–67, §70, §86)

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

`CryoMainWindow` is **not** a subclass of `MainWindow`. It is an independent implementation that shares modules from `core/` but has its own UI layout, hardware panel, and config structure. Both inherit `ScanHostMixin` (`core/scan_host.py`, §88) for the run lifecycle, so the differences are in UI construction, config handling and the mixin's hooks (`_QSETTINGS_APP`, the Keithley setup-key spellings, `_stop_background_workers`, `_kerr_display_widgets`, zero-after-scan). Key differences:

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

```bash
pip install -e .            # from the repo root: deps + `samba` / `samba-cryo`
pip install -e ".[tango]"   # with pytango (otherwise simulation mode)
```

or just the packages: `pip install pytango PyQt6 matplotlib h5py numpy`.

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
cd Samba_main && python samba.py      # or: samba

# Cryo
cd Cryo && python samba_cryo.py       # or: samba-cryo
```

Both accept `--version`.

### Tests

```bash
python test_runner.py            # unit suite, Qt and TANGO stubbed (no hardware)
python -m pytest tests/ -q       # real in-process TANGO devices (tango.test_context), sync, log
ruff check --select F821,F822,F823,F811,E722 core Samba_main Cryo
```

CI (`.github/workflows/tests.yml`) runs all three on every push.  Offscreen Qt
harnesses (`QT_QPA_PLATFORM=offscreen`, a throwaway `SAMBA_CONFIG_DIR`) are used
per batch to drive the real windows but are not committed — PyQt6 is not in CI.

### Simulation mode

If `pytango` is not installed, Samba falls back to `SimProxy` (defined in `core/hardware.py`) which returns dummy values. All Tango operations are wrapped in try/except with graceful degradation. This allows UI development without access to the lab hardware.

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


## Conventions & lessons (distilled from the change log)

The rules below were each paid for once; the § points at the batch in
`docs/CHANGELOG.md` that explains why.

**Versions, branches, commits**
- Bump `core/version.py` on every regular commit (decimal part; major only for a
  release) and tag `v<version>`; the number is stamped into every scan file as
  `samba_version` (§58, §60, §88).  13.31 is skipped on purpose (§81).
- Add a new batch as the next `## NN.` section of `docs/CHANGELOG.md`: what was
  reported, the cause, what changed, how it was verified — with numbers.

**Module layout**
- Any new `core/<mod>.py` that other core modules or the apps import by bare name
  needs the one-line re-export shims `Samba_main/<mod>.py` and `Cryo/<mod>.py`
  (§46, §74).  Core modules import siblings by bare name; the two `config.py`
  files import `core.*` by package path (§68).
- Entry points **append** the repo root to `sys.path`, never prepend — a stale
  root-level module would silently shadow an app module (§74).
- The run lifecycle lives in `ScanHostMixin` (`core/scan_host.py`); app
  differences go through its hooks, never by copying a method into a window —
  `TestScanHost` fails on any near-identical pair (§88).
- The two apps spell the Keithley setup keys differently
  (`keithley_amplitude_attr` vs `keithley_attr_amplitude`); setup files on disk
  carry both — do not unify (§77).

**Never do these without asking**
- No abort/pause keyboard shortcuts (§62), no manual stage Stop button and no
  runaway guard (§70–71) — all were built and removed at the operators' request.
- Never construct a main window or run a diagnostic against the live
  `~/.config/moke_scan` — point `SAMBA_CONFIG_DIR` at a throwaway copy (§67).
- Only ever **append** lab-notebook columns at the end; a reorder restarts the
  file (§33, §41).

**Refuse, don't guess**
- With pytango present an unreachable stage, magnet or sensor refuses to start
  the scan (§60); the liveness probe is `probe_alive`, a DB entry is not a
  device (§86).
- A failed trigger reply, a device that never reached RUNNING, a FAULT state or a
  non-finite reading fails the point → retry → auto-pause naming the device (§42,
  §67, §86).  Replies of `command_inout_asynch` must be collected (§86).
- Out-of-range targets are refused, not clamped (§70); unusable calibrations
  disable the display rather than show zeros (§37, §79); an all-zero BD
  calibration is never written to HDF5 (§37).

**TANGO specifics**
- `safe_read` is numeric: a `DevString` through it returns its first character
  as a number — use `safe_read_str` (§67).
- A `MARSHAL_InvalidEnumValue` on a write almost always means a wrong attribute
  *name* (§77).
- `pytango` 10 freezes the `DeviceProxy` interface (no Python attributes on it);
  short nodb device names resolve only in the main process — hand other
  processes `ctx.get_device_access()`; fork start method for test fakes (§87).
- HDF5 string attributes are NUL-stripped (§59); NeXus attributes are additive
  on the existing `.h5` layout (§86); same-second file names get `_2`, `_3` (§88).

**Panels and threads**
- A panel's `load()` sets `_loading` and every change signal goes through one
  `_emit_changed()` that consults it — an echoed signal mid-load saves a
  half-restored panel over the setup file (§37, §67, §68).
- GUI updates from a thread go through a queued signal (`BackgroundCall`,
  `_post_to_main`), never `QTimer.singleShot` from the thread (§44, §87).
- `QGroupBox("A && B")` — a single `&` is a mnemonic (§61).  A checkable
  `QGroupBox` toggles on Space in `event()`, not `keyPressEvent()` (§68).
- When a widget's size comes from a `QScrollArea` sizeHint, state the hint
  explicitly (§54).  The bottom tab area cannot shrink below the tallest tab;
  spend width, not rows (§68).
- Colours come from `core/theme.py`; the curve palettes are validated for
  colour-vision deficiency — do not reorder (§46).

**Testing**
- `python test_runner.py` (Qt + TANGO stubbed) + `python -m pytest tests/`
  (real in-process devices) + `ruff --select F821,F822,F823,F811,E722` — all
  three in CI.  Patch `runner.fresh_proxy`, not `hardware.fresh_proxy` (§23).
- Offscreen Qt harnesses drive the real windows per batch and are not committed
  (CI has no PyQt6); recreate them — a harness anomaly is a finding, not an
  artifact (§54).

## Design-note index → `docs/CHANGELOG.md`

| Topic | Sections |
|---|---|
| Scan engine: retry, auto-pause, stale values, abort latency | §23, §42, §67, §82, §86 |
| 2D traversal (zigzag, fast axis), interleaved trace/retrace | §26, §30 |
| FIELD / temperature axis units, ramp wait, demagnetisation | §30, §60 |
| DC hysteresis: per-cycle data, sources, Ampere relabel, drift alignment | §31, §39, §53 |
| HDF5: calibration dataset, NUL guard, NeXus attrs, provenance, names | §32, §37, §59, §60, §86, §88 |
| BD (λ/2) calibration, Fit & Import, Kerr-rotation display | §24, §66, §67, §79 |
| Metadata sharing, lab notebook, scanlist metadata per scan | §32, §33, §41, §53, §85 |
| Scanlist: lock, polarity persistence, ABBA order, relay integrity | §53, §57, §78, §86 |
| Current sweep, thermal settle, refocus box, Z offset, autofocus | §43, §60, §68, §72, §73, §75, §76 |
| Calibration tab: jog, reinit, zero-in-place, LEDs, units | §34, §41, §44, §45, §48, §51, §52, §70 |
| Stage safety (IR Z runaway) and the device-side fixes | §69, §70, §71 |
| Plots: palettes, legends, eng ticks, light export, Recent scale | §27, §45, §46, §48, §49, §58, §63, §64 |
| Data browser: lazy load, search, metadata, index, dates | §40, §62, §65, §80 |
| Setup files: load failures, switch bugs, defaults round-trip | §35, §36, §37, §47, §86 |
| Hardware panel: readback, mirroring, background writes, pulse source | §50, §77, §87 |
| ZI / ZI2 servers: v5 thread safety, Start contract, one source | §29, §67, §88 |
| NAS sync, logging, module shadowing, NameError sweep | §22, §74, §82, §83, §84 |
| Layout: field row, Scanlist tab, action bar, window sizing | §27, §53, §54, §68, §81, §86 |
| Shared run lifecycle, packaging, docs split | §88 |
