# Samba v3

**S**canning **A**cquisition for **M**ultiple **B**eam-paths and **A**xes — a PyQt6 instrument control application for MOKE (Magneto-Optic Kerr Effect) spectroscopy at the ETH Zürich Intermag Lab.

Samba orchestrates spatial scans, magnetic field sweeps, time-resolved measurements, and DC hysteresis loops across multiple hardware devices in real time, with live plotting and crash-safe HDF5 data persistence.

---

## Requirements

```
Python ≥ 3.10
PyQt6
matplotlib
h5py
numpy
pytango        # optional — simulation mode runs without it
```

Install dependencies:

```bash
pip install PyQt6 matplotlib h5py numpy pytango
```

---

## Launching

```bash
export TANGO_HOST=192.168.1.1:10000   # set to your TANGO server
python samba.py
```

If `pytango` is not installed or `TANGO_HOST` is unreachable, Samba starts in **simulation mode** — all device reads return synthetic data and all writes are silently accepted. The UI is fully functional for testing layout and scan logic without hardware.

---

## Interface Overview

```
┌─────────────────────────────────────────────────────────┐
│  [Green] [IR]  [▶ Start] [⏸ Pause] [■ Abort]  Dir: …  │  ← Action bar
├──────────┬──────────────────────────────┬───────────────┤
│ Config   │  2D Map / 1D Plot /          │  Sensors &    │
│ List     │  Calibration / Log           │  Plot options │
│          │                              │               │
│          │  ──────────────────────────  │               │
│          │  Progress bar   Status msg   │               │
├──────────┴──────────────────────────────┴───────────────┤
│  Trajectory │ Scanlist │ Data Browser │ Script │ Devices │  ← Bottom tabs
└─────────────────────────────────────────────────────────┘
```

### Action bar

| Control | Purpose |
|---|---|
| **Green / IR** | Switch between hardware setups (each has its own device paths and config list) |
| **Start** (F5) | Start a single scan or a scanlist, depending on which bottom tab is active. If the Calibration tab is open, starts a time scan there instead |
| **Pause** | Pause after the current point completes; press again to resume |
| **Abort** | Stop immediately after the current point, saving all data collected so far |
| **Dir** | Root directory for HDF5 output. Files are saved under `<Dir>/YYYYMMDD/` |

---

## Setups: Green, IR, Cryo

Each setup stores its own hardware device paths, active sensor list, and collection of named scan configurations. Switching setups reloads everything — plots clear, device proxies are refreshed, and the config list updates.

Setup data is persisted to `~/.config/moke_scan/{Green,IR,Cryo}.json`.

---

## Scan Types

Configure the scan type in the **Trajectory** tab.

### Spatial — 1D along X
Moves actuator 1 (typically the stage X axis) through a linear range.

### Spatial — 1D along Y
Moves actuator 2 through a linear range.

### Spatial — 2D (X × Y)
Raster scan. Supports **zigzag** (boustrophedon) mode to avoid backlash delays.

### Field sweep
Steps the magnet current through a linear (or multi-segment) range and records field from the calibrated readback attribute. Automatically demagnetizes after completion.

### Time scan
No movement — acquires N points at fixed intervals. Useful for monitoring drift or for calibration.

### DC Hysteresis
Delegates entirely to a PyHysteresis TANGO device (e.g. `hpp-N42/beckhoff/pyhystlongi`). Samba polls state and reads the resulting loop arrays when complete. Live plot refreshes after each averaging cycle.

### TR-MOKE (Time-Resolved)
Drives the DG645 delay generator as the scan actuator. The delay is stepped from start to stop in configurable units (ps / ns / µs).

---

## Configuration Management (Config List)

The left panel lists all saved scan configurations for the active setup.

| Action | How |
|---|---|
| Select | Click the name |
| New | Click **+** |
| Duplicate | Click **⧉** (copies current config) |
| Rename | Double-click the name |
| Delete | Click **✕** (disabled when only one config exists) |
| Save | Ctrl+S or click the floppy icon; also saved automatically on setup switch and scan start |

Configs are stored as versioned JSON inside the setup file. Schema migrations run automatically when opening configs created by older versions.

---

## Sensors & Channels (Right Panel)

The right panel lists the sensors that will be read at each scan point.

- **Enable/disable** each sensor with the checkbox.
- **Device** and **Channel** are populated from the Device Registry.
- **Axis** assigns the sensor to the left (Y1) or right (Y2) y-axis on the 1D plot, or hides it.
- **Display** (2D map) selects which sensor feeds the false-colour image.
- **Colormap** controls the 2D map palette.

For DC Hysteresis scans the panel switches to show the hysteresis result channels (result1…result6) from the PyHysteresis device.

---

## Trajectory Panel

Configures scan geometry for the active scan type:

- **Actuator 1 / 2**: TANGO device path, attribute name, label, unit, start, stop, N points.
- **Integration time**: Written to each sensor device before the first point.
- **Settle time**: Wait after each move before triggering.
- **Move timeout**: Abort a stuck move after this many seconds.
- **Zigzag**: Alternate scan direction each row (2D only).
- **Field segments**: Multi-segment AC sweeps for field scans (e.g. negative → zero → positive → zero → negative in one shot).
- **Monitor**: Optional live readback of any registry device during the scan (displayed in a mini plot).

---

## Scanlist

Run a sequence of N identical scans with optional inter-scan modifications:

| Option | Effect |
|---|---|
| **Relay flip** | Toggles the optical relay polarity between each scan (alternates +1/−1) |
| **Field flip** | Negates the magnet current between each scan; auto-demagnetizes after the list |

A `.txt` index file is written alongside the HDF5 files listing each scan's path, relay sign, and applied field.

---

## Device Registry

The **Device Registry** tab maintains a database of TANGO devices and their available measurement channels. This populates the device/channel dropdowns throughout the UI.

Each entry specifies:
- Device name (human-readable label)
- TANGO device path
- Trigger command (e.g. `Start`)
- Integration time attribute (e.g. `integrationtime`)
- List of channels, each with attribute name, friendly label, and unit

The registry is stored at `~/.config/moke_scan/device_registry.json`.

---

## Data Browser

Browse, inspect, and overlay previously saved HDF5 files from within the app.

- Files are grouped by date folder under the active save directory.
- Click a file to preview metadata and auto-plot.
- **Overlay selected**: plots multiple scans on the same axes for comparison.
- Status icons show whether each scan completed, was aborted, or is still running.

---

## Calibration

The **Calibration** tab provides:

- **Digit jog**: Click individual decimal digits of the Z position to increment/decrement by that digit's place value. Useful for coarse and fine focus adjustments without typing.
- **Read all**: Refreshes the X/Y/Z position readback from hardware.
- **Autofocus**: Scans Z through a configurable range while recording fluorescence intensity, fits a Gaussian, and moves to the peak. Plots the curve live.
- **Time scan shortcut**: Pressing Start while the Calibration tab is in focus runs a time scan and plots it directly on the calibration plot for focus monitoring.

---

## Script Console

An embedded Python interpreter with access to the full hardware and scan API. Useful for automating multi-step procedures or one-off device interactions.

Available in the namespace:

```python
# Hardware
p = get_proxy("hpp-N42/measure/ZI2")
val, err = safe_read(p, "x1")
err = safe_write(p, "integrationtime", 0.5)

# Scans
cfg = get_active_config()   # current GUI config dict
cfg["act1_npts"] = 11       # modify before running
fn = run_scan(cfg)          # returns HDF5 path

# Data
import h5py
f = h5py.File(fn, "r")
data = f["data"]["ZI2_x1"][:]
```

---

## HDF5 File Layout

Every scan produces a single HDF5 file under `<save_dir>/YYYYMMDD/<name>_HHMMSS.h5`.

```
/                           root attrs: scan_status, scan_type, timestamp
/metadata/                  all scan parameters, device paths, sensor config (JSON)
/data/
    actuator_x              actual X positions read back after each move
    actuator_x_setpoint     commanded X positions
    time                    elapsed time at the centre of each integration window
    actuator_y              (2D scans only)
    actuator_y_setpoint     (2D scans only)
    ZI2_x1                  sensor channel — one dataset per enabled sensor
    ZI2_y1
    …
```

`scan_status` is set to `"running"` at file creation and updated to `"completed"` or `"aborted"` at the end. If the application crashes, the file retains all data collected up to that point with `scan_status = "running"`.

---

## Setup Lock

When multiple workstations can access the same TANGO server, Samba uses an optional advisory lock device (`hpp-N42/samba/lock`) to prevent two instances from controlling the same setup simultaneously. If the lock server is unreachable, Samba continues without locking (fail-open).

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| **F5** | Start scan (same as clicking Start) |

---

## Simulation Mode

Samba runs fully without hardware. `SimProxy` replaces all TANGO device proxies and returns synthetic data:

- Sensor reads return a Gaussian function of X/Y position with small random noise.
- Field readback returns `current × 0.15` with noise.
- Stage moves are instantaneous.
- All writes are silently accepted.

Simulation mode activates automatically when `pytango` is not installed, when `TANGO_HOST` is not set, or when a specific device is unreachable.

---

## Troubleshooting

**App starts but no devices respond**
Check `TANGO_HOST` is exported and the TANGO database is reachable (`tango_admin --check-alive $TANGO_HOST`).

**Scan aborted immediately with "No sensors enabled"**
Open the right panel and tick at least one sensor's checkbox.

**Stage never reaches position / move timeout**
Increase `move_timeout` in the Trajectory panel, or check that the TANGO stage controller is in ON state.

**HDF5 file exists but contains only NaN**
The file was created before the scan started but no points were acquired. Check the Log tab for error messages.

**Keithley lost connection mid-scanlist**
Use the reconnect button in the Hardware panel (Trajectory tab). Samba will close and reopen the TCP socket to the instrument.

**"⚠ Auto-paused after N consecutive errors"**
The scan paused itself after repeated sensor read failures. Fix the device issue (check TANGO state), then press **Pause** again to resume.
