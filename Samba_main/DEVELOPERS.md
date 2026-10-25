# Developer Reference

Internal architecture notes, patterns, and conventions for anyone working on
Samba.  The authoritative reference is `../CLAUDE.md` (§1–22) and the dated
design notes in `../docs/CHANGELOG.md`; this file is the short tour.

---

## Module Map

Paths are relative to the repository root.  `Samba_main/` and `Cryo/` are the
two applications; everything they share lives in `core/`, and each app
directory carries one-line re-export shims (`Samba_main/hardware.py` →
`core/hardware.py`, …) so app code can keep bare-name imports.

| File / Package | Role |
|---|---|
| `Samba_main/samba.py` | `MainWindow(ScanHostMixin, QMainWindow)` — entry point, action bar, signal routing, scan orchestration (Green + IR) |
| `Cryo/samba_cryo.py` | `CryoMainWindow(ScanHostMixin, QMainWindow)` — the Cryo application |
| `core/scan_host.py` | `ScanHostMixin` — the run lifecycle shared by both windows: setup lock, status bar, pause/abort, engine errors, refocus, current-sweep sequencer, close |
| `core/scan/runner.py` | `ScanRunner` — pure Python scan logic, no Qt dependencies |
| `core/scan/workers.py` | `ScanWorker`, `ScanlistWorker` — thin QThread wrappers over ScanRunner |
| `Samba_main/scan/__init__.py`, `Cryo/scan/__init__.py` | Re-exports for `from scan import ScanWorker` |
| `Samba_main/panels/trajectory.py` | `TrajectoryPanel`, `ActuatorGroup`, `FieldSegmentList` |
| `Samba_main/panels/scanlist.py` | `ScanlistPanel` — polarity control, current sweep, refocus |
| `Samba_main/panels/right_panel.py` | `RightPanel` — sensor list, plot controls |
| `Samba_main/panels/config_list.py` | `ConfigListPanel` — named scan configs per setup |
| `Samba_main/panels/hardware_panel.py` | `HardwarePanel` — magnet, relay, Keithley interactive controls |
| `Samba_main/panels/setup_defaults.py` | `SetupDefaultsPanel` — per-setup device paths and attribute names |
| `Samba_main/panels/sensor_picker.py` | `SensorPickerRow` — device/channel/axis dropdown row |
| `Samba_main/panels/_widgets.py` | `NoScroll*`, `MokeMetadataGroup`, `_NoUnderscoreValidator` |
| `Cryo/panels.py`, `Cryo/panels_cryo.py`, `Cryo/defaults_panel.py` | The Cryo copies of the panels (own `ActuatorGroup`, `ScanlistPanel`, `CryoHardwarePanel`, setup defaults) |
| `Samba_main/config.py`, `Cryo/config.py` | JSON load/save, defaults, schema migration; `APP_VERSION` from `core/version.py` |
| `core/version.py` | `__version__` — the one place the application version is written |
| `core/launch.py` | `samba` / `samba-cryo` console entry points (`pyproject.toml`) |
| `core/hardware.py` | `SimProxy`, `get_proxy()`, `fresh_proxy()`, `probe_alive()`, `safe_read/write`, demag |
| `core/bgcall.py` | `BackgroundCall` — hardware-panel writes off the GUI thread |
| `core/validation.py` | `validate_scan_config`, point and travel limits |
| `core/setup_lock.py` | Advisory TANGO mutex for multi-workstation setups |
| `core/plot_widgets.py` | `Live2DWidget`, `Live1DWidget` — throttled live rendering |
| `core/plot_interact.py`, `core/theme.py`, `core/kerr.py` | Plot interaction helpers, Catppuccin tokens/palettes, Kerr-rotation display |
| `core/data_browser.py`, `core/scan_index.py` | `DataBrowserPanel`, `ScanFile` — HDF5 file browser and its metadata index |
| `core/calibration.py` | `CalibrationPanel` — digit jog, autofocus, time scan, stage reinit/zero, LEDs |
| `core/bd_calibration.py`, `core/bd_fit.py` | λ/2 (BD) calibration panel and the step fit behind "Fit & Import" |
| `core/current_sweep.py`, `core/current_sweep_ui.py` | Current sweep and refocus: Qt-free logic + the widgets and settle worker |
| `core/nstep.py`, `core/polarity.py` | N ↔ Δ coupling; AB / ABBA flip schedule |
| `core/lab_notebook.py`, `core/server_sync.py` | CSV lab notebook; NAS sync (subprocess worker in `sync_worker.py`) |
| `core/applog.py` | Rotating file log, noisy-logger pinning |
| `core/device_registry.py` | `DeviceRegistryPanel` — TANGO device database editor |
| `core/script_console.py` | `ScriptConsolePanel` — embedded Python interpreter |
| `core/play_intro.py` | Splash screen |

The TANGO device servers (ZI lock-ins, DG645, RTV40, SmarAct stage, Beckhoff
bridge, …) live in the separate **TANGO_Devices** repository.

---

## Architecture Principles

**ScanRunner is Qt-free.** It communicates via plain Python callbacks (`point`, `progress`, `status`, `log`, `dc_loop`). This makes it unit-testable without a display. `ScanWorker` is a thin QThread wrapper that bridges callbacks to Qt signals.

**The run lifecycle exists once.** Everything a scan run needs around the engine — taking the setup lock, the status bar, pause/resume/abort, the engine-error dialog, the boundary refocus, the current-sweep sequencer, the close handler — is `ScanHostMixin` in `core/scan_host.py`. The two windows differ in layout and config handling and override only the hooks declared at the top of the mixin. A unit test (`TestScanHost`) refuses any method that is defined near-identically in both windows again.

**SimProxy is a drop-in for development only.** `hardware.get_proxy()` returns a `SimProxy` when pytango is not installed. With pytango present, an unreachable device makes a scan **refuse to start** rather than record simulated data; `SimProxy` has an attribute table so the simulated application behaves like the real one (settling time 0 so it is fast).

**Config is version-migrated.** Every scan config carries `_schema_version`. On load, `_migrate_config()` runs the chain of migration functions needed to bring it up to `SCHEMA_VERSION`. Adding a new field only requires a new migration function and bumping the version constant.

**HDF5 is crash-safe.** Files are created at scan start with datasets pre-filled with `NaN`. Each point is written immediately after acquisition. `scan_status` starts as `"running"` and is updated to `"completed"` or `"aborted"` at the end. A crash leaves a valid file with all data collected up to that point. The layout carries NeXus `NXdata` attributes additively (the file type is plain `.h5`, and nothing that reads the files today changed).

**One version.** `core/version.py` is read by both apps (`APP_VERSION`: window titles, `--version`, the `samba_version` provenance attribute in every scan file) and by `pyproject.toml`. Bump it on every regular commit and tag the commit `v<version>`.

---

## Key Patterns

### Sensor trigger pattern
Sensors requiring explicit triggering (e.g. `DoubleInBeckhoffAverage`, the ZI lock-ins) use:
1. `command_inout_asynch(trigger_cmd)` on all devices in a tight loop (~100 µs total jitter)
2. Phase A: poll `state()` until each device is `RUNNING`; Phase B: until none is
3. **collect every asynchronous reply** (`command_inout_reply`) — a server-side failure of `Start` only exists in the reply, and uncollected replies accumulate for the life of the process
4. 10 ms guard delay (output registers settle)
5. `read_attributes()` per device (batch, eliminates inter-channel skew)

A device that rejected its trigger, never reached RUNNING, is in FAULT, or returns a non-finite value fails the point; the point is retried with a fresh proxy and the scan auto-pauses on it, naming the device.

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
`hardware._pcache` is accessed from both the main thread and scan QThreads. All reads and writes are guarded by `_pcache_lock`. Use `get_proxy()` for cached access during scans, `fresh_proxy()` for interactive operations where a stale SimProxy must not intercept writes, and `probe_alive()` to tell a live device from one whose database entry merely exists.

### Panel load guard pattern
Every panel that restores values in `load()` sets a `_loading` flag and funnels all its change signals through one `_emit_changed()` that consults it. A `changed` signal fired mid-load makes the main window save a half-restored panel over the setup file (this happened: Cryo's Keithley device became a lock-in).

### Background hardware write pattern
Hardware-panel writes go through `BackgroundCall.run(key, fn, on_done)` (`core/bgcall.py`): `fn` runs on a daemon thread, `on_done` on the GUI thread via a queued signal, one call per key in flight. Never touch widgets from the worker thread; never `QTimer.singleShot` from a plain thread (delivery is Qt-version dependent) — emit a queued signal.

---

## DC Hysteresis Data Flow

```
Config → write MagneticField/NumberOfPoints/Cycles/IntegrationTime (+ source1..6) to PyHysteresis
       → Start()
       → poll state() at max(0.2, int_time/4) s interval
       → per completed cycle: _read_and_emit_hyst_loop() reads spectrum attrs
         (result1–6, field arrays) → dc_loop callback → DC monitor canvas
       → on completion: final read, write HDF5 (+ /data/cycles per-cycle group), emit scalars (Hc, Hshift, Mr, Ms)
```

---

## Colour Palette (Catppuccin Mocha)

Tokens live in `core/theme.py`; the curve palettes there are validated for
colour-vision deficiency and must not be reordered.

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
| `smaract2/control/IR-controller` | IR stage positioner (MCS2) | `x`, `y`, `z` (µm) |
| lock-in device as registered (class `ZI` = Green dev4855, `ZI2` = IR dev30933) | ZI MFLI lock-ins | `x1`–`x4`, `y1`–`y4`, `settlingtime`, `integrationtime`, `Start` |
| `hpp-N42/beckhoff/analogIn2` | Focus diode (DC) | `Value` |
| `hpp-N42/beckhoff/averageIn1` | Averaged balanced diode | `Value` (needs `Start`) |
| `hpp-N42/beckhoff/magnet` | Field readback | `field_polar_corr`, `field_longitudinal_corr` (mT) |
| `hpp-N42/beckhoff/pyhystlongi` | DC hyst (longitudinal) | `result1`–`result6`, `field` |
| `hpp-N42/beckhoff/pyhystpolar` | DC hyst (polar) | `result1`–`result6`, `field` |
| `hpp-N42/current/PyKeithley` | Keithley 6221 (Green, sine) | `current`, `amplitude`, `frequency`, `range` |
| `hpp-N42/current/PyKeithley2` | Keithley 6221 (IR/Cryo) | same |
| `hpp-N42/current/PyKeithleyPulse` | Keithley 6221 unit 1, square wave | `pulseAmplitude`, `frequency`, `compliance`, `range` (no `current`) |
| `hpp-N42/current/PyRelais` | Optical relay | `switchvar` |
| `hpp-N42/samba/lock` | Setup mutex | `greenbusy`, `irbusy`, `cryobusy` + `*info` |
| `intermag/dg645/1` | DG645 delay generator | `DelayA`–`H`, `AmplitudeAB/CD/EF/GH` |
| `hpp-N42/pulser/RTV40` | Kentech pulse generator (TR-MOKE sync) | `PulseWidth`, `TriggerSource` |
| `hpp-N42/attoDRY/attoDRY` | Cryostat (Cryo only) | `MagneticField`, `Temperature`, `MOVING` while ramping |

### Beckhoff notes
- `DoubleInBeckhoff` — thin wrapper over `AdsBridge.ReadReal(variableName)`. Passive: just read `Value`.
- `DoubleInBeckhoffAverage` — requires explicit `Start()` trigger before each read. Uses `trigger_cmd = "Start"` in the sensor config. A failed `Start` leaves it ON and raises — which the client only sees in the asynchronous reply.
- `AdsBridge2` provides transparent TANGO access to Beckhoff PLC variables. From Samba's perspective all Beckhoff variables are normal TANGO attributes.

### ZI lock-in notes
The ZI MFLI TANGO device uses a `subscribe/poll/np.mean` software-averaging pattern. The instrument's native hardware filter (time constant) is not used for averaging. This works correctly for integration times ≥ 50 ms. A future improvement would be to use `getSample()` after waiting for the filter to settle (native hardware average). Both instruments are served from one source (`TANGO_Devices/tango_servers_new/ZurichInstruments_lockin/`), with `ZI` and `ZI2` differing only in property defaults.

---

## Tests

```bash
python test_runner.py            # unit suite — Qt and TANGO stubbed, no hardware
python -m pytest tests/ -q       # real in-process TANGO devices (tests/fakes.py), NAS sync, logging
ruff check --select F821,F822,F823,F811,E722 core Samba_main Cryo
```

Offscreen Qt harnesses (`QT_QPA_PLATFORM=offscreen`, a throwaway
`SAMBA_CONFIG_DIR`) drive the real windows — scans, panel writes, the error
dialog, whole current sweeps — and are written per batch; they are not
committed because CI has no PyQt6.  **Never construct a main window against
the live `~/.config/moke_scan`** (§67).

---

## Planned / Future Work

- **ZI hardware filtering** — Replace `subscribe/poll/np.mean` with `getSample()` + hardware time-constant settling.
- **Scan history overlay in data browser** — Drag-and-drop overlay of multiple HDF5 datasets.
- **Smaller screens** — at the declared 1180×640 minimum the bottom tab area collapses (§86 layout check); dock widgets would be the structural fix, left as is because the lab screens are ≥ 1920 px.
- **Plot setup at scan start** — ~250 ms of matplotlib layout between Start and the first point (§87).
