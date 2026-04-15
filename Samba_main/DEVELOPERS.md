# Developer Reference

Internal architecture notes, patterns, and conventions for anyone working on Samba.

---

## Module Map

| File / Package | Role |
|---|---|
| `samba.py` | `MainWindow` — entry point, action bar, signal routing, scan orchestration |
| `scan/runner.py` | `ScanRunner` — pure Python scan logic, no Qt dependencies |
| `scan/workers.py` | `ScanWorker`, `ScanlistWorker` — thin QThread wrappers over ScanRunner |
| `scan/__init__.py` | Re-exports for `from scan import ScanWorker` |
| `panels/trajectory.py` | `TrajectoryPanel`, `ActuatorGroup`, `FieldSegmentList` |
| `panels/scanlist.py` | `ScanlistPanel` |
| `panels/right_panel.py` | `RightPanel` — sensor list, plot controls |
| `panels/config_list.py` | `ConfigListPanel` — named scan configs per setup |
| `panels/hardware_panel.py` | `HardwarePanel` — magnet, relay, Keithley interactive controls |
| `panels/sensor_picker.py` | `SensorPickerRow` — device/channel/axis dropdown row |
| `panels/_widgets.py` | `NoScroll*`, `MokeMetadataGroup`, `_NoUnderscoreValidator` |
| `config.py` | JSON load/save, defaults, schema migration |
| `hardware.py` | `SimProxy`, `get_proxy()`, `fresh_proxy()`, `safe_read/write`, demag |
| `plot_widgets.py` | `Live2DWidget`, `Live1DWidget` — throttled live rendering |
| `data_browser.py` | `DataBrowserPanel`, `ScanFile` — HDF5 file browser |
| `calibration.py` | `CalibrationPanel` — digit jog, autofocus |
| `device_registry.py` | `DeviceRegistryPanel` — TANGO device database editor |
| `script_console.py` | `ScriptConsolePanel` — embedded Python interpreter |
| `setup_lock.py` | Advisory TANGO mutex for multi-workstation setups |
| `DG645_DelayGenerator.py` | TANGO device server for Stanford DG645 delay generator |
| `play_intro.py` | Splash screen |

---

## Architecture Principles

**ScanRunner is Qt-free.** It communicates via plain Python callbacks (`point`, `progress`, `status`, `log`, `dc_loop`). This makes it unit-testable without a display. `ScanWorker` is a thin QThread wrapper that bridges callbacks to Qt signals.

**SimProxy is a drop-in.** `hardware.get_proxy()` returns a `SimProxy` when TANGO is unavailable or a device is unreachable. `SimProxy` accepts all reads/writes and returns synthetic Gaussian data. Code above the hardware layer never needs to check for simulation mode explicitly.

**Config is version-migrated.** Every scan config carries `_schema_version`. On load, `_migrate_config()` runs the chain of migration functions needed to bring it up to `SCHEMA_VERSION`. Adding a new field only requires a new migration function and bumping the version constant.

**HDF5 is crash-safe.** Files are created at scan start with datasets pre-filled with `NaN`. Each point is written immediately after acquisition. `scan_status` starts as `"running"` and is updated to `"completed"` or `"aborted"` at the end. A crash leaves a valid file with all data collected up to that point.

---

## Key Patterns

### Sensor trigger pattern
Sensors requiring explicit triggering (e.g. `DoubleInBeckhoffAverage`) use:
1. `command_inout_asynch(trigger_cmd)` on all devices in a tight loop (~100 µs total jitter)
2. Poll `state()` until none are `RUNNING`
3. 10 ms guard delay (output registers settle)
4. `read_attributes()` per device (batch, eliminates inter-channel skew)

Passive sensors (no `trigger_cmd`) are read after the triggered devices finish.

### NoScroll widget pattern
All `QComboBox`, `QSpinBox`, `QDoubleSpinBox` use `NoScroll` subclasses that override `wheelEvent` to `ignore()`. This prevents accidental value changes while scrolling a panel.

### Icon with fallback pattern
```python
icon = QIcon.fromTheme("freedesktop-name")
if not icon.isNull():
    btn.setIcon(icon)
else:
    btn.setText("ascii-fallback")
```
Required for Ubuntu 24 which has strict Qt Unicode rendering in widgets.

### Tab identity pattern
Never use `currentIndex() == N` — tab order can change. Always use:
```python
if self.bottom_tabs.currentWidget() is self.sl_panel:
    ...
```

### Config migration pattern
```python
_MIGRATIONS = [
    (1, _migrate_v0_to_v1),
    (2, _migrate_v1_to_v2),
]

def _migrate_config(cfg: dict):
    v = cfg.get("_schema_version", 0)
    for target_v, fn in _MIGRATIONS:
        if v < target_v:
            fn(cfg)
            v = target_v
    cfg["_schema_version"] = SCHEMA_VERSION
```

### CSS scoping pattern
To prevent Qt stylesheet cascading from a parent widget overriding child button colours, give the parent an `objectName` and scope the rule:
```python
widget.setObjectName("action_bar")
widget.setStyleSheet("#action_bar { background: #12121f; }")
```

### Proxy cache pattern
`hardware._pcache` is accessed from both the main thread (500 ms readback timer) and scan QThreads. All reads and writes are guarded by `_pcache_lock`. Use `get_proxy()` for cached access during scans, `fresh_proxy()` for interactive operations where a stale SimProxy must not intercept writes.

---

## DC Hysteresis Data Flow

```
Config → write MagneticField/NumberOfPoints/Cycles/IntegrationTime to PyHysteresis
       → Start()
       → poll state() at max(0.2, int_time/4) s interval
       → per completed cycle: _read_and_emit_hyst_loop() reads spectrum attrs
         (result1–6, field arrays) → dc_loop callback → DC monitor canvas
       → on completion: final read, write HDF5, emit scalars (Hc, Hshift, Mr, Ms)
```

---

## Colour Palette (Catppuccin Mocha)

| Name | Hex | Used for |
|---|---|---|
| Green | `#a6e3a1` | Start button, positive readbacks |
| Peach | `#fab387` | Pause button, warnings |
| Red | `#f38ba8` | Abort button, errors |
| Blue | `#89b4fa` | Left Y axis, info labels |
| Mauve | `#cba6f7` | Highlights, selected items |
| Base | `#1e1e2e` | Main window background |
| Surface0 | `#313244` | Panel borders, inputs |
| Overlay1 | `#7f849c` | Disabled text |

---

## TANGO Device Map

| TANGO Path | Purpose | Key Attributes |
|---|---|---|
| `smaract2/control/IR-controller` | IR stage positioner | `x`, `y`, `z` (nm) |
| `hpp-N42/measure/ZI2` | Main lock-in (ZI MFLI) | `x1`, `y1`, `x2`, `y2` |
| `hpp-N42/measure/ZI1` | Secondary lock-in | `x1`, `y1`, `x2`, `y2` |
| `hpp-N42/beckhoff/analogIn2` | Focus diode (DC) | `Value` |
| `hpp-N42/beckhoff/averageIn1` | Averaged balanced diode | `Value` (needs `Start`) |
| `hpp-N42/beckhoff/magnet` | Field readback | `field_polar_corr`, `field_longitudinal_corr` |
| `hpp-N42/beckhoff/pyhystlongi` | DC hyst (longitudinal) | `result1`–`result6`, `field` |
| `hpp-N42/beckhoff/pyhystpolar` | DC hyst (polar) | `result1`–`result6`, `field` |
| `hpp-N42/current/PyKeithley` | Keithley 6221 (Green) | `current`, `amplitude`, `frequency`, `range` |
| `hpp-N42/current/PyKeithley2` | Keithley 6221 (IR/Cryo) | same |
| `hpp-N42/current/PyRelais` | Optical relay | `switchvar` |
| `hpp-N42/samba/lock` | Setup mutex | `GreenBusy`, `IrBusy`, `GreenInfo`, `IrInfo` |
| `intermag/dg645/1` | DG645 delay generator | `DelayA`–`H`, `AmplitudeAB/CD/EF/GH` |

### Beckhoff notes
- `DoubleInBeckhoff` — thin wrapper over `AdsBridge.ReadReal(variableName)`. Passive: just read `Value`.
- `DoubleInBeckhoffAverage` — requires explicit `Start()` trigger before each read. Uses `trigger_cmd = "Start"` in the sensor config.
- `AdsBridge` provides transparent TANGO access to Beckhoff PLC variables. From Samba's perspective all Beckhoff variables are normal TANGO attributes.

### ZI lock-in notes
The ZI MFLI TANGO device uses a `subscribe/poll/np.mean` software-averaging pattern. The instrument's native hardware filter (time constant) is not used for averaging. This works correctly for integration times ≥ 50 ms. A future improvement would be to use `getSample()` after waiting for the filter to settle (native hardware average).

---

## Planned / Future Work

- **`samba_cryo.py`** — Separate entry point for Cryo setup with AttoDRY2100 cryostat, ANC300 stepper motors, ANM200 piezo scanners, and live cryostat monitoring window. Cryo hardware is sufficiently different from Green/IR that a partial fork was decided on. Shared modules (`scan/`, `plot_widgets.py`, `data_browser.py`, `hardware.py`, `config.py`) stay common.
- **ZI hardware filtering** — Replace `subscribe/poll/np.mean` with `getSample()` + hardware time-constant settling.
- **Auto-focus before scanlist** — Optional autofocus trigger before each scanlist cycle.
- **Scan history overlay in data browser** — Drag-and-drop overlay of multiple HDF5 datasets.
