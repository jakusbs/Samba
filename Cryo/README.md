# Samba Cryo

Cryogenic MOKE measurement application for the **ETH Zürich Intermag Lab**.
Controls the AttoDRY cryostat, Keithley 6221 current source, and SmarAct stages
via the [TANGO Controls](https://www.tango-controls.org/) framework.

---

## Requirements

```
Python >= 3.10
PyQt6
numpy
matplotlib
h5py
pytango      # optional — app runs in simulation mode without it
```

Install dependencies:

```bash
pip install PyQt6 numpy matplotlib h5py pytango
```

---

## Quick Start

```bash
# Point to your TANGO server
export TANGO_HOST=192.168.1.1:10000

# Launch the app
python samba_cryo.py
```

Without `pytango`, the app starts in **simulation mode** — all hardware reads/writes
are handled by a built-in SimProxy. Data files are still written normally.

Logs are written to `~/.config/moke_scan/logs/samba_cryo.log` (rotating, 2 MB x 5 files).

---

## Main Window Layout

```
+---------------------------------------------------------+
|  CRYO   [Start F5] [Pause] [Abort Esc]   Dir: ~/...  [.]|  <- Action bar
+----------+------------------------------+----------------+
| Scan     |  2D Map | 1D Plot | Calib | Log |            |
| configs  |                              |  Right panel   |
| (list)   |      Live plot area          |  (sensors,     |
|          |                              |   display)     |
|          +------------------------------+                |
|          | ########....  42/100 pts     |                |
|          |   2m 10s elapsed ~2m left    |                |
+----------+------------------------------+----------------+
| Trajectory | Scanlist | Data Browser | Console | Devices |  <- Bottom tabs
+---------------------------------------------------------+
```

**Top-left:** Scan config list — add, rename, duplicate, or delete named scan configs.

**Top-centre:** Live plot tabs:
- **2D Map** — false-colour image updated point-by-point during spatial scans
- **1D Plot** — dual-Y-axis line plot for field sweeps and time scans
- **Calibration** — autofocus routine + digit-jog stage controls
- **Log** — timestamped messages with severity colouring (filterable)

**Bottom tabs:**
- **Trajectory** — configure scan type, axes, sensors, and integration time
- **Scanlist** — run the same scan N times with automatic field/relay flipping
- **Data Browser** — browse and plot saved HDF5 files
- **Script Console** — embedded Python REPL for ad-hoc automation
- **Device Registry** — manage TANGO device paths used by sensors

---

## Hardware Panel

The hardware panel (inside Trajectory/Scanlist tabs) has two sections:

### Keithley 6221 (left)
| Control | Description |
|---------|-------------|
| Range | Current range: 2 mA / 20 mA / 100 mA |
| Amplitude | AC current amplitude (uA) — output toggled on/off automatically |
| Compliance | Voltage compliance limit (V) |
| Frequency | AC modulation frequency (Hz) |

### AttoDRY Cryostat (right)
| Control | Description |
|---------|-------------|
| Field setpoint | Target magnetic field (+-9 T) |
| Temperature setpoint | Target sample temperature (0-400 K) |
| Mag Ctrl | Toggle active magnetic field control on the superconducting magnet |
| Temp Ctrl | Toggle full temperature PID control |
| Persistent Mode | Switch between persistent (low heat load) and driven magnet modes |
| Monitor | Open the Cryo Monitor rolling-history window |

Readbacks (field, sample T, VTI T, magnet T) update every 500 ms in the background.

---

## Scan Modes

### Spatial Scan
Moves one or two SmarAct stages and reads sensors at each point.

| Parameter | Description |
|-----------|-------------|
| Scan X / Y | Enable 1D (X only) or 2D (X x Y) raster scan |
| Start / Stop / N pts | Axis range and point count |
| Zigzag | Alternate scan direction on each Y row (faster) |
| Integration time | Signal averaging time per point (s) |
| Settle time | Wait after each move before reading (s) |

### Field / Temperature Sweep
Sweeps the AttoDRY magnetic field **or** temperature and reads sensors at each point.

- **Multi-segment sweeps**: define multiple start/stop/N-pts segments (e.g. -2T to 2T to -2T)
- **Temperature sweep**: writes the temperature setpoint and waits `settle_time` (60-300 s recommended) before reading

### Time Scan
No axis movement — reads sensors repeatedly at a fixed position.
Accessed via the **Calibration** tab Start button when no axes are enabled.

---

## Cryo Monitor

Click **Monitor** in the hardware panel to open the rolling-history dialog:

- **Temperatures (K):** Sample, VTI, Magnet, Reservoir
- **Pressures (mbar):** CryostatIn, CryostatOut
- **Heater Powers (W):** Sample, VTI, Reservoir

60-second rolling window, 500 ms poll rate.
The poll pauses automatically while the window is hidden to save resources.

---

## Data Browser

Scans are saved as incremental HDF5 files in `~/moke_data/<YYYYMMDD>/`.
Points are written to disk as they arrive — partial data is preserved on abort.

| Status | Meaning |
|--------|---------|
| `completed` | Scan finished normally |
| `aborted` | Scan was stopped early — partial data saved |
| `running` | File from a crashed session (data may be incomplete) |

**Tips:**
- Select multiple files with Ctrl+click, then **Overlay selected** to compare scans
- Use the X/Y dropdowns to choose which datasets to plot
- **Open...** loads any HDF5 file from outside the default directory (Ctrl+R refreshes the list)

---

## Script Console

Embedded Python REPL for ad-hoc hardware control and data processing.

```python
# Read a sensor manually
p = get_proxy("hpp-N42/measure/ZI2")
val, err = safe_read(p, "x1")
print(f"ZI2 x1 = {val}")

# Move a stage
p = get_proxy("smaract2/control/IR-controller")
safe_write(p, "x", 25000)

# Run a quick scan from the current config
cfg = get_active_config()
cfg["act1_npts"] = 21      # modify as needed
fn = run_scan(cfg)
print(f"Saved to {fn}")
```

**Available names:** `get_proxy`, `safe_read`, `safe_write`, `safe_read_str`,
`get_active_config`, `get_active_setup`, `run_scan`, `ScanRunner`,
`np`, `h5py`, `time`, `sleep`, `os.path`, `os.listdir`, `os.makedirs`

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| F5 | Start scan |
| Escape | Abort scan (with confirmation) |
| Ctrl+L | Clear log |
| Ctrl+R | Refresh data browser |

---

## Configuration

Scan configs are saved per-setup as JSON in `~/.config/moke_scan/`:

```
~/.config/moke_scan/
+-- Cryo.json              # active scan configs and hardware paths
+-- device_registry.json   # device name -> TANGO path mapping
+-- logs/
    +-- samba_cryo.log     # rotating application log (2 MB x 5 files)
```

Delete `Cryo.json` to reset to factory defaults.
Delete `device_registry.json` to reload default device entries.

---

## Hardware & Device Map

| Device | TANGO Path | Key Attributes |
|--------|-----------|----------------|
| AttoDRY | `hpp-N42/attoDRY/attoDRY` | `MagneticField`, `Temperature`, `VtiTemperature`, `MagnetTemperature` |
| Keithley 6221 | `hpp-N42/current/PyKeithley2` | `amplitude`, `frequency`, `compliance`, `range` |
| ZI2 Lock-in | `hpp-N42/measure/ZI2` | `x1`, `y1`, `x2`, `y2` |
| IR Stage | `smaract2/control/IR-controller` | `x`, `y`, `z` |
| Beckhoff DAQ | `hpp-N42/beckhoff/averageIn2` | `Value` |

Device paths can be changed in the **Device Registry** tab or directly in `Cryo.json`.

---

## File Structure

```
samba_cryo.py        Entry point, CryoMainWindow, ReadbackWorker
panels_cryo.py       CryoHardwarePanel (Keithley + AttoDRY controls)
cryo_monitor.py      Rolling-history monitor dialog
keithley_mixin.py    Shared Keithley 6221 UI builder
panels.py            ConfigListPanel, TrajectoryPanel, ScanlistPanel
config.py            Hardware defaults, scan schema, JSON persistence
hardware.py          TANGO proxy cache, SimProxy, safe_read/write
scan.py              ScanRunner, ScanWorker, ScanlistWorker
plot_widgets.py      Live2DWidget, Live1DWidget (matplotlib in Qt)
calibration.py       CalibrationPanel: autofocus, jog controls, time scan
data_browser.py      HDF5 file browser, viewer, overlay
script_console.py    Embedded Python REPL
device_registry.py   Device name -> TANGO path registry
play_intro.py        Splash screen
```

All 14 Python files must be in the same directory.

---

## Development Notes

**Architecture:**

```
config  <-  hardware  <-  scan
config + hardware  <-  panels  <-  keithley_mixin  <-  panels_cryo
                                                       cryo_monitor
All of the above  <-  samba_cryo
```

Key design decisions:
- **ReadbackWorker (QThread):** all TANGO polling runs off the GUI thread
- **Incremental HDF5:** points written immediately; data survives power cuts
- **SimProxy fallback:** every device gracefully degrades to simulation if unreachable
- **hw_panel_class injection:** `TrajectoryPanel`/`ScanlistPanel` accept `hw_panel_class` so `CryoHardwarePanel` is injected at construction, not patched in afterwards
- **Atomic config save:** written to `.tmp` then `os.replace()` — no partial-write corruption
- **Thread-safe proxy cache:** `_pcache` protected by `threading.Lock()`
- **I/O timeouts:** `safe_read`/`safe_write` default to 10 s timeout via `concurrent.futures`

**Differences from standard Samba:**

| Feature | Standard Samba | Samba Cryo |
|---------|---------------|------------|
| Setup selector | Green / IR / Cryo pills | CRYO label only |
| Hardware panel | Keithley + Field & Relay | Keithley + AttoDRY |
| Scan types | Spatial, Field, TR-MOKE | Spatial, Field / Temperature |
| Field readback | Beckhoff (GUI thread) | AttoDRY via ReadbackWorker |
| Demagnetisation | Auto after field scans | Removed (superconducting magnet) |

---

*ETH Zurich -- Intermag Lab -- April 2026*
*Developer: Jakub Adventures -- Collaborator: Tobias Goldenberg*
