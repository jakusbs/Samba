# SAMBA

**S**trnad & Goldenberger **A**pplication for **M**agnetism **B**ased **A**nalysis

A PyQt6 desktop application for controlling scanning Magneto-Optical Kerr Effect (MOKE)
measurements at the ETH Zürich Intermag lab. SAMBA drives three experimental setups —
**Green**, **IR**, and **Cryo** — through TANGO Controls device servers, records data to
HDF5, and ships with a post-acquisition analysis pipeline for spin-orbit-torque (SOT)
line scans and DC hysteresis loops.

*Creator: Jakub Strnad · Collaborator: Tobias Goldenberger · ETH Zürich, Intermag Lab*

> This README is the user/operator guide. Developer documentation — architecture
> internals, device-server details, and the full changelog — lives in
> [`CLAUDE.md`](CLAUDE.md).

---

## Contents

1. [What SAMBA does](#1-what-samba-does)
2. [Repository layout](#2-repository-layout)
3. [Installation](#3-installation)
4. [Running](#4-running)
5. [The two applications: Samba_main and Cryo](#5-the-two-applications-samba_main-and-cryo)
6. [Scan types](#6-scan-types)
7. [UI tour](#7-ui-tour)
8. [How a scan point is measured](#8-how-a-scan-point-is-measured)
9. [Reliability: retries, auto-pause, and the setup lock](#9-reliability-retries-auto-pause-and-the-setup-lock)
10. [Calibration workflows](#10-calibration-workflows)
11. [Data: HDF5 layout, lab notebook, NAS sync](#11-data-hdf5-layout-lab-notebook-nas-sync)
12. [Analysis pipeline](#12-analysis-pipeline)
13. [Configuration files](#13-configuration-files)
14. [Hardware map](#14-hardware-map)
15. [Simulation mode](#15-simulation-mode)
16. [Troubleshooting](#16-troubleshooting)
17. [Tests](#17-tests)

---

## 1. What SAMBA does

SAMBA orchestrates a scanning MOKE microscope: it moves a positioning stage (or sweeps a
magnet, a delay generator, or a temperature controller), triggers lock-in amplifiers and
other sensors at each point, waits for the hardware to finish integrating, reads the
averaged values, and streams everything into an HDF5 file with live plotting.

Key capabilities:

- **Spatial scans** — 1D line scans and 2D raster maps with a SmarAct or Attocube stage
  (zigzag traversal and selectable fast axis for 2D maps).
- **Field sweeps** — multi-segment magnet-current sweeps with true field readback.
- **DC hysteresis** — full loops delegated to a Beckhoff PLC hysteresis engine, with
  per-cycle raw data retention and offline re-averaging.
- **TR-MOKE** — time-resolved pump-probe scans sweeping a Stanford DG645 delay, with
  optional RTV40 pulse-width synchronization.
- **Temperature sweeps** (Cryo) — AttoDRY cryostat setpoint sweeps, 0–400 K.
- **Scanlists** — automated sequences of scans with relay switching and field flipping
  between scans, for polarity-resolved SOT measurements.
- **Hardware-gated timing** — every point waits for the lock-in filter to settle and for
  each sensor device to report acquisition complete before reading; nothing is read on a
  timer guess.
- **Crash-safe data** — the HDF5 file is created at scan start and written per point, so
  a crash or abort never loses acquired data.
- **Fail-soft design** — unavailable devices, a missing lock server, or a missing pytango
  install degrade gracefully; the app always starts.

**Stack:** Python 3.13, PyQt6, matplotlib (QtAgg), h5py (NXdata layout for PyMca),
pytango (TANGO Controls), JSON configs, Catppuccin Mocha dark theme.

The TANGO device servers themselves (ZI lock-ins, Beckhoff bridges, AttoDRY, Keithley,
stage controllers, …) live in the separate **TANGO_Devices** repository.

---

## 2. Repository layout

```
Samba/
├── core/                    # Shared modules used by both apps
│   ├── scan/
│   │   ├── runner.py        # ScanRunner — the scan engine (pure Python, no Qt)
│   │   └── workers.py       # ScanWorker / ScanlistWorker — QThread wrappers
│   ├── hardware.py          # TANGO proxy cache, safe_read/safe_write, SimProxy
│   ├── plot_widgets.py      # Live1DWidget, Live2DWidget (live plots)
│   ├── plot_interact.py     # Click readout, font-size spin, light export, eng ticks
│   ├── theme.py             # Catppuccin palette, plot colors, colormap sets
│   ├── data_browser.py      # HDF5 file browser tab
│   ├── calibration.py       # Calibration tab: autofocus, time scan, LEDs, stage jog
│   ├── bd_calibration.py    # BD (λ/2) calibration tab
│   ├── lab_notebook.py      # CSV lab notebook writer
│   ├── server_sync.py       # NAS auto-upload
│   ├── setup_lock.py        # Multi-computer scan mutex client
│   ├── device_registry.py   # Device/channel registry + editor UI
│   ├── script_console.py    # Embedded Python console
│   └── easter_egg.py        # Try the Konami code
│
├── Samba_main/              # Green + IR application
│   ├── samba.py             # Entry point (MainWindow)
│   ├── config.py            # Setup defaults, config schema + migration
│   ├── install.sh           # Installer (conda env + desktop entry)
│   └── panels/              # UI panels (trajectory, sensors, hardware, …)
│
├── Cryo/                    # Cryo application (separate entry point)
│   ├── samba_cryo.py        # Entry point (CryoMainWindow)
│   ├── config.py            # Cryo defaults (Faraday/Voigt stage blocks)
│   ├── panels_cryo.py       # AttoDRY + Keithley hardware panel
│   ├── cryo_monitor.py      # Rolling cryostat monitor plots
│   └── install.sh
│
├── Analysis/                # Post-acquisition analysis
│   ├── analyze_samba.py     # SOT line-scan pipeline (import_analyze_both, …)
│   └── samba_io.py          # HDF5 loaders, hysteresis cycle tools
│
├── test_runner.py           # Hardware-free unit tests (61 tests)
├── CLAUDE.md                # Developer documentation + changelog
└── README.md                # This file
```

**Code sharing:** both apps import shared modules from `core/`. Note for developers:
core modules import each other by bare name, so every `core/<mod>.py` used this way has
one-line re-export shims in `Samba_main/` and `Cryo/` (e.g. `Samba_main/theme.py` →
`from core.theme import *`). If you add a new core module, add both shims.

---

## 3. Installation

### Requirements

- Python ≥ 3.10 (lab machines run 3.13)
- Packages: `pytango PyQt6 matplotlib h5py numpy`
- A reachable TANGO database (`TANGO_HOST`) for real measurements — optional for
  UI development (see [Simulation mode](#15-simulation-mode))

### Quick install (pip)

```bash
pip install pytango PyQt6 matplotlib h5py numpy
```

If pip fails to build pytango, use conda instead:

```bash
conda install -c conda-forge pytango
```

### Lab-machine install (installer scripts)

Both apps ship an installer that sets up a conda environment, installs system Qt
libraries, and creates a desktop launcher:

```bash
# Samba_main — optional argument is the conda env name (default: base)
cd Samba_main && bash install.sh Tango
# or with system packages:
sudo bash install.sh Tango

# Cryo
cd Cryo && bash install.sh Tango
```

The installer writes `launch_samba.sh` (activates the right conda env) and a desktop
entry, and remembers the env name in `.install_config`.

### Environment

```bash
export TANGO_HOST=192.168.1.1:10000
```

---

## 4. Running

```bash
# Green + IR setups
cd Samba_main && python samba.py

# Cryo setup
cd Cryo && python samba_cryo.py
```

On startup a splash screen probes the key devices for the active setup **in parallel**
(stage, lock-in, magnet, Keithley — AttoDRY on Cryo) and shows a live ✓/⚠ status line
per device. Unreachable devices don't block startup.

---

## 5. The two applications: Samba_main and Cryo

The two entry points share the scan engine, plotting, data browser, calibration tab, and
persistence code from `core/`, but are independent applications tuned to their hardware:

| Aspect | Samba_main | Cryo |
|---|---|---|
| Setups | Green + IR (switchable) | Cryo only |
| Stage | SmarAct MCS2 (nm) | Attocube ANM200 (fine) / ANC300 (coarse steps) |
| Magnet | Beckhoff room-temperature coils (mT) | AttoDRY superconducting, ±9 T |
| Temperature | — | AttoDRY, 0–400 K, sweepable |
| Geometry | fixed | Faraday / Voigt selectable (per-geometry stage blocks) |
| Relay switching | optical relay (PyRelais) | — |
| Demagnetize after field scan | automatic | disabled (superconducting) |
| Scan directions | single | Trace / Retrace queue |
| DC hysteresis | Beckhoff PyHysteresis engine | — |
| TR-MOKE | DG645 + optional RTV40 sync | — |

Switching setups in Samba_main (Green ↔ IR pills) swaps the whole configuration —
device paths, scan configs, metadata, BD calibration. Switching is blocked while a scan
is running.

---

## 6. Scan types

| Type | Sweeps | Notes |
|---|---|---|
| **SPATIAL** | stage actuator 1 (X), optionally actuator 2 (Y) | 1D lines or 2D raster maps. 2D supports zigzag (reverse direction every fast line) and a selectable fast axis (X-fast or Y-fast); data is always stored in ascending axis order regardless of traversal. |
| **FIELD** | magnet current, multi-segment | Segments are `[start, stop, npts]` triples concatenated into one sweep. The engine waits for ramping magnets (devices reporting `MOVING`, e.g. the AttoDRY) before each point. X axis is the *measured* field readback, labelled in the correct unit (mT on Samba_main, T on Cryo). |
| **DC_HYST** | delegated to the Beckhoff PyHysteresis PLC engine | Full hysteresis loops measured autonomously on the PLC; SAMBA writes parameters, starts, polls, and reads back field + up to six result channels, N-cycle averaged. Per-cycle raw data is also saved (see §11). Recorded PLC sources (AnalogIn1–6 / ELM1–6) are selectable per config. Samba_main only. |
| **TR_MOKE** | DG645 delay channel | Internally converted to a SPATIAL scan with the delay attribute as the actuator. Optional RTV40 sync keeps the HV pulse *end* fixed while the delay sweeps. Samba_main only. |
| **TIME** | nothing — elapsed time | Repeated acquisition at a fixed position; x = seconds since start. Used by the Calibration tab's time scan. |
| **Temperature sweep** | AttoDRY temperature setpoint | Uses the FIELD engine with the temperature attribute; waits for arrival at each setpoint. Cryo only. |

### Scanlists

The Scanlist tab queues many scans of the active config with, per entry, a relay state
and a field polarity. Between scans the worker switches the relay and/or flips the
magnet current, then waits for the field to actually settle (rate-of-change polling — no
assumed target). The finished list is written as a `.txt` scanlist file (one line per
scan: filename, relay sign, field) which is the input to the analysis pipeline. Aborted
scans never enter the `.txt`, so the analysis never averages a truncated line.

---

## 7. UI tour

### Main window

- **Action bar** (top): Start / Pause / Abort, setup pills (Green/IR), Zero field.
- **Server bar**: NAS sync path + manual "↑ Sync" button (see §11).
- **Left sidebar**: per-setup config list. Each config is a named measurement preset
  (scan type, ranges, sensors, timing). Add / duplicate / rename / delete.
- **Center tabs**:
  - **Trajectory** — scan type pills, actuator ranges (start/stop/points), field
    segments, DC-hyst parameters, TR-MOKE front-panel widget, metadata group, timing
    group (integration / settle / timeout), hardware panel (Keithley, field write +
    readback, relay, lock-in readback).
  - **Scanlist** — the scan queue with per-row relay/field, its own metadata + timing
    groups (bidirectionally synced with the Trajectory tab), and its own hardware panel.
  - **BD Calibration** — six mV spinboxes for the λ/2-plate balanced-diode calibration
    at tick positions 0–25 (see §10).
  - **Data Browser** — browse past HDF5 scans by date folder; 1D lines, 2D maps with
    channel switching, colormap picker, index (point-#) x-axis, metadata preview.
  - **Setup Defaults** — per-setup device paths and attribute names, registry-driven.
  - **Device Registry** — the device/channel catalogue that feeds every sensor dropdown.
  - **Script console** — embedded Python console for ad-hoc TANGO access.
- **Right panel**: sensor picker rows (device + channel + Y1/Y2 axis + enable), display
  sensor for the 2D map, colormap choice, DC-hyst channels and recorded sources.
- **Live plots**: 1D multi-channel plot (twin Y axes, legend above the axes, click a
  curve to read out the nearest point, "Text:" size spinbox, light-mode export button)
  and a 2D map (auto color with zero-centred diverging colormaps, live display-sensor
  switching).
- **Status bar** (bottom): scan counter, start time, elapsed, run/scan time left, dead
  time %, done % — tinted green while running, peach while paused.

### Calibration tab

A self-contained tab for beam/sample alignment (see §10 for the workflows):

- **Stage positioning** — jog the X/Y/Z axes, read positions, "⟲ Reinitialise" the
  stage (recovers a wedged SmarAct axis), LED on/off toggle buttons with live state
  readback (Green/IR lights).
- **Autofocus** — sweep Z for maximum focus-diode intensity (see §10).
- **Time scan** — a repeated-acquisition scan with **its own hidden config**: points,
  integration time, and its own sensor rows, persisted per setup and completely
  independent of the config selected in the left panel. Y1/Y2 sensors plot on separate
  twin axes.

### Cryo specifics

Faraday/Voigt geometry pills and ANM200/ANC300 piezo pills sit inline with the scan-type
row; the hardware panel has AttoDRY field/temperature setpoints, PID toggle buttons,
persistent-mode control, and a rolling cryostat monitor dialog (temperatures, pressures,
heater powers). The Calibration tab gains an ANC300 stepper group (frequency, voltage,
ground per axis).

---

## 8. How a scan point is measured

Every SPATIAL / FIELD / TIME point runs the same hardware-gated sequence:

```
1. MOVE      write setpoint to actuator / magnet
2. SETTLE    sleep settle_time  (FIELD also waits for MOVING magnets to arrive)
3. ZI SETTLE sleep max(lock-in settling times)  — read from the device at scan start
4. TRIGGER   async "Start" to every sensor device
5. PHASE A   poll until each device reports RUNNING   (catches the trigger)
6. PHASE B   poll until each device leaves RUNNING    (integration complete)
7. GUARD     10 ms
8. READ      batch attribute read per device
```

The lock-in settling time (step 3) is computed **on the device** from the actual filter
time constant and order (99 % settling of the Butterworth cascade) and read once at scan
start — so the wait automatically matches the instrument settings.

The integration time from the config is written to every sensor device at scan start and
read back for verification.

---

## 9. Reliability: retries, auto-pause, and the setup lock

- **No stale data, ever.** A failed trigger, a device whose state can't be polled, or a
  failed read all mark that point's sensors as NaN and fail the point — a "successful"
  read of a device that was never triggered can never be recorded.
- **Per-point retry + auto-pause.** A failing point is retried up to 5 times (with proxy
  refresh). If all attempts fail the scan **pauses on that point** and a warning popup
  appears; pressing Resume retries the same point from scratch. The scan never advances
  past a failed point.
- **HDF5 write failures** (disk full, broken handle) also auto-pause after 5 consecutive
  failures instead of silently producing an all-NaN file.
- **Scan refuses to start** if the configured stage/magnet is unreachable while pytango
  is available — it will not silently "move" a simulated proxy and record fake data.
- **Setup lock.** A TANGO lock device (`hpp-N42/samba/lock`) prevents two computers from
  scanning the same setup simultaneously. Locks are acquired at scan start and released
  at the end; a lock older than 12 h is treated as abandoned and taken over with a
  warning. If the lock server is unreachable, SAMBA fails open (scans still run).
- **Field setpoint re-apply.** At scan start (except FIELD/DC-hyst scans, which own the
  magnet) the hardware panel's field-write value is re-applied, so a scan after an
  aborted scanlist doesn't silently run at zero field. The manual "Zero field" button
  also resets the write spinbox to 0.

---

## 10. Calibration workflows

### Autofocus

Sweeps the Z (focus) axis to maximize the focus-diode intensity:

1. **Coarse sweep** over Z₀ ± Max range in steps of dz (capped at Max points), low→high
   to minimize backlash.
2. **Fine sweep** — 9 points over ± one coarse step around the coarse peak.
3. **Parabolic vertex** through the fine maximum for sub-step accuracy.
4. **Move to the found focus** and take one confirmation measurement.

Abort (or an all-failed sweep) returns the stage to the starting Z.

### Calibration time scan

Point the beam at a feature, hit ▶ Start with the Calibration tab open: a TIME scan runs
with the tab's own points / integration time / sensors (persisted per setup, independent
of the selected config). Use it for polarizer alignment, checking signal stability, or
watching the balanced diode while turning the λ/2 plate.

### BD (λ/2) calibration

The balanced-diode calibration for converting mV to Kerr rotation:

1. When you mount a **new sample**, editing the Sample metadata field pops "start a new
   BD calibration?" — Yes clears the old values and jumps to the BD Calibration tab.
2. Turn the λ/2 plate to ticks 0, 5, 10, 15, 20, 25 and enter the balanced-diode mV
   reading at each into the six spinboxes. Save.
3. The six values are stored per setup and **written into every scan's HDF5 file** as
   `/data/calibration` (all scan types, including DC hysteresis and calibration time
   scans). The analysis pipeline reads it from there automatically.

An all-zero calibration is never written — the analysis then falls back to the sample
folder's `calibration.txt`.

---

## 11. Data: HDF5 layout, lab notebook, NAS sync

### File naming

Scan files are auto-named from the metadata:

```
YYYYMMDD_Sample_DeviceID_AmplitudemA_FreqHz_Config_Incidence_Polarization_MirrorShift_Notes[...].h5
```

and stored under `save_dir/<YYYYMMDD>/`. Cryo trace/retrace runs append
`_trace` / `_retrace`.

### HDF5 layout (NXdata, PyMca-compatible)

```
/data
  actuator_x            swept axis (position / field readback / delay / time)
  <sensor label>        one dataset per enabled sensor channel
  calibration           6-element λ/2 BD calibration (mV), if saved
  cycles/               DC-hyst only: per-cycle raw half-loops
    field               [n_cycles, n_loop]  (mT)
    result1..result6    [n_cycles, n_loop]  per recorded channel
/metadata               all config fields + hardware snapshot as attributes
                        (operator, sample, device_id, R4W/R2W, t_FM, t_stack,
                         Keithley/magnet/temperature readbacks, hw_* keys, …)
```

Data is written per point as it arrives; a partial file from a crash or abort is valid
and readable.

### Lab notebook

Every finished scan appends a row to a per-setup CSV
(`lab_notebook_<Setup>.csv`): timestamp, file, sample, device, notes, scanlist name,
incidence, hardware snapshot values, etc. Local file is the source of truth; it is
uploaded on every sync.

### NAS sync

A "Server:" bar below the action bar points at the GVFS SMB mount of the ETH NAS. After
every scan (and on the manual "↑ Sync" button) the data folder, scanlist folder, and lab
notebook are uploaded in the background; files that already exist with the same size are
skipped. First-time setup: mount the share once in GNOME Files, then pick the path with
the `…` button — it is remembered per setup.

---

## 12. Analysis pipeline

`Analysis/analyze_samba.py` processes SOT line-scan scanlists (Green, IR, and Cryo data
alike):

```python
from analyze_samba import analyze_SOT

# Trace + retrace in one call
res_trace, res_retrace = analyze_SOT.import_analyze_both(SCANLIST)

# Single direction with overrides
res = analyze_SOT.import_analyze_SOT(
    SCANLIST,
    see_channels=('DC', 'ZI_x1'),  # None = auto-detect
    current_mA=12.5,               # None = HDF5 metadata → filename → 10 mA
    ignorLines=(3,),               # 1-based scanlist rows to drop
)
```

Nearly everything is auto-detected: the data folder (from the scanlist location),
channels, sample name, current, trace/retrace direction. The pipeline groups scans by
polarity (relay × field sign), optimizes the lock-in phase, detects the device edges,
fits an erf-edge model for the device width, and — given Ms, t_stack and t_FM (from
`calibration.txt`, HDF5 metadata, or arguments) — computes the SOT efficiency ξ_DL.
Results land in a per-sample folder tree with plots, `analyzed_data.csv`, and
`results.json`.

`Analysis/samba_io.py` adds DC-hysteresis cycle tools: `load_hyst_cycles` (read the
`/data/cycles` group), `hyst_detect_outliers` (median+MAD flagging of bad cycles),
`hyst_align_cycles` (per-half-loop drift alignment — removes balanced-diode baseline
wander), and `hyst_cycle_average(…, exclude=…, align=True)` to re-average without
re-measuring.

---

## 13. Configuration files

All persistent state lives in `~/.config/moke_scan/`:

| File | Contents |
|---|---|
| `<Setup>.json` (Green / IR / Cryo) | device paths, scan configs, shared metadata, BD calibration, calibration-tab config, sync path |
| `device_registry.json` | the device/channel catalogue |

Configs are schema-versioned; old files are migrated automatically on load. Setup files
that fail to parse are backed up to `<name>.json.bad` and reported in a startup warning —
they are never silently overwritten. Metadata (sample, operator, thicknesses, …) is
stored **per setup**, so switching between scan configs keeps the same sample identity.

To move to a new computer, copy the whole `~/.config/moke_scan/` directory. If a file
didn't survive the copy, the startup warning will tell you which one.

---

## 14. Hardware map

All hardware is accessed through TANGO device servers (sources in the separate
**TANGO_Devices** repository):

| Device | Example TANGO path | Role |
|---|---|---|
| SmarAct MCS2 stage | `smaract2/control/IR-controller` | Green/IR positioning (nm) |
| Attocube ANM200 / ANC300 | `hpp-N42/attocube/ANM200`, `…/ANC300` | Cryo fine / coarse positioning |
| ZI MFLI lock-in (ZI / ZI2) | `hpp-N42/measure/ZI`, `…/ZI2` | Kerr signal, x1–x4 / y1–y4 |
| Beckhoff averaged input | `hpp-N42/beckhoff/averageIn1` | balanced diode (DC) |
| Beckhoff magnet | `hpp-N42/beckhoff/magnet` | room-temp coils, corrected field readback |
| PyHysteresis | `hpp-N42/beckhoff/pyhystpolar`, `…/pyhystlongi` | PLC DC-hysteresis engine |
| Keithley 6221 | `hpp-N42/current/PyKeithley2` | AC excitation current |
| PyRelais | `hpp-N42/current/PyRelais` | optical relay switching |
| AttoDRY2100 | `hpp-N42/attoDRY/attoDRY` | cryostat: field (±9 T) + temperature |
| DG645 | `intermag/dg645/1` | TR-MOKE delay generator |
| RTV40 | `hpp-N42/pulser/RTV40` | Kentech HV pulse generator |
| Lights | `hpp-N42/light/lights` | setup LEDs (calibration tab) |
| SetupLock | `hpp-N42/samba/lock` | multi-computer scan mutex |

Device paths and attribute names are all editable in the **Setup Defaults** tab; new
sensors are added through the **Device Registry** tab (name, path, trigger command,
integration/settling attributes, channels) and immediately appear in every sensor
dropdown.

---

## 15. Simulation mode

If `pytango` is not installed, SAMBA runs entirely against `SimProxy` (dummy values for
every read, no-op writes). All panels, scans, plots, and the data browser work — useful
for UI development away from the lab. When pytango **is** installed, unreachable devices
degrade per-device instead (and scans refuse to start if the stage/magnet itself is
unreachable, to avoid recording fake data).

---

## 16. Troubleshooting

**"Setup 'X' is already in use" at scan start** — another computer (or a crashed
session) holds the setup lock. Locks older than 12 h are taken over automatically;
younger ones can be cleared manually in Jive (`hpp-N42/samba/lock`, set `*busy` False).

**Measurement paused with a warning popup** — a sensor device failed to trigger, poll,
or read 5 times in a row. No data was recorded for that point. Fix the device (check the
TANGO server, use its `Reconnect` command if it has one) and press Resume — the same
point is retried from scratch.

**LED buttons stay grey** — hover them: the tooltip shows the read failure. Usually the
Lights TANGO server predates the `led1`/`led2` attributes (redeploy from TANGO_Devices)
or the device is unreachable.

**Startup warning about setup files** — a `<Setup>.json` was unreadable (backed up to
`.json.bad`, original untouched) or missing while others loaded. Restore the file before
saving anything, otherwise defaults will be written over it.

**`ModuleNotFoundError: No module named '<core module>'`** — a new `core/` module is
missing its one-line re-export shim in `Samba_main/` and/or `Cryo/` (see §2).

**ZI lock-in returns zeros / crashes** — make sure the v5 thread-safe ZI servers from
TANGO_Devices are deployed; the old v4 servers corrupt their connection under concurrent
access. The v5 servers have a `Reconnect` command for recovery without a restart.

**Stage axis wedged after using the hand controller (IR SmarAct)** — Calibration tab →
"⟲ Reinitialise" (requires the TANGO_Devices server with the `Initialise` command; falls
back to plain `Init` on older servers).

**Weird x-axis values on field/temperature sweeps** — fixed since June 2026 (labels and
readback attributes are config-driven); if you see it, your configs predate the
migration — simply loading them in a current SAMBA upgrades them.

---

## 17. Tests

```bash
python test_runner.py -v
```

61 hardware-free unit tests covering the scan engine (acquisition, retries, auto-pause,
zigzag/fast-axis traversal, FIELD ramp wait), HDF5 writing (DC-hyst cycles, calibration,
dedup), the lab notebook, hysteresis cycle math, and config load hardening. Only numpy
and h5py are required — Qt and TANGO are stubbed. A GitHub Actions workflow runs the
suite on every push and pull request.
