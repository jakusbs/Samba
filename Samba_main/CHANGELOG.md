# Changelog

All notable changes to Samba are recorded here chronologically.

---

## v3 (March–April 2026) — Full rewrite in PyQt6

### Chat #1 — Mar 19–20: Recreating ScanServer
- First complete `moke_scan.py` built from scratch in PyQt5
- Established the full TANGO hardware map (SmarAct stages, ZI lock-ins, Beckhoff PLC, DoubleInBeckhoffAverage)
- Discovered the DoubleInBeckhoffAverage handshake requirement: `Start()` → wait for RUNNING → ON → read `Value`. This became the `trigger_cmd` pattern used everywhere.
- HDF5 saving with metadata, JSON config save/load, SimProxy fallback, abort/pause/resume

### Chat #2 — Mar 19: PyQt6 port + multi-setup support
- Full PyQt5 → PyQt6 port (enums, exec_, backend)
- Expanded from single setup to Green / IR / Cryo with independent configs
- Sensors made fully configurable with enable/disable and Y-axis assignment
- Fixed `NavigationToolbar2QT` SIP strict-type crash: pass `None` as parent, not `self`

### Chat #3 — Mar 19: QToolBar TypeError fix
- Quick follow-up: confirmed and documented the NavToolbar `SIP` issue and fix

### Chat #4 — Mar 23: State-of-the-art improvements (largest session)
- Added `NoScrollComboBox/SpinBox/DoubleSpinBox` (prevent accidental scroll changes)
- Added `CalibrationPanel` with autofocus (hill-climbing, Gaussian fit, live plot)
- Added `DataBrowserPanel` (HDF5 file tree, metadata preview, overlay)
- Added `ScriptConsolePanel` (embedded Python with hardware and scan API)
- Added incremental HDF5 writing: pre-fill with NaN at start, write per point, crash-safe
- Added throttled plot rendering (`REDRAW_INTERVAL_MS = 80`)

### Chat #5 — Mar 25: Synchronization & UI refactor
- Replaced threaded trigger dispatch with `command_inout_asynch()` tight loop (~100 µs jitter vs ~5–10 ms)
- Batch `read_attributes()` per device eliminates inter-channel timing skew
- 10 ms guard delay between state→done and readout prevents stale buffer reads
- Timestamp per point = `t_trigger + int_time/2` (centre of integration window)

### Chat #6 — Mar 25: Action bar reorganisation
- Moved scan control buttons to a persistent top action bar
- Setup selector changed from QTabBar to coloured pill buttons (Green/IR/Cryo)
- Save directory moved to action bar for visibility

### Chat #7 — Mar 26: Negative field current & auto-naming
- Fixed field flip in scanlist: negate `current_polar` / `current_longitudinal`
- Added `MokeMetadataGroup` widget (operator, sample, notes, incidence, polarization, λ/2, λ/4, noDC)
- Auto-generated scanlist name from metadata fields

### Chat #8 — Mar 27: DC Hysteresis implementation
- Full DC hysteresis via PyHysteresis TANGO device (`pyhystlongi`, `pyhystpolar`)
- Live plot refreshes after each averaging cycle via `dc_loop` callback
- Separate HDF5 layout for DC_HYST with scalar results (Hc, Hshift, Mr, Ms)
- `DataBrowserPanel` extended to read and display DC_HYST files

### Chat #9 — Mar 30: Latest files snapshot
- Consolidation: confirmed latest file versions across conversations

### Chat #10 — Mar 31: DC Hysteresis review & fixes
- Fixed DC monitor not refreshing mid-scan
- Fixed field readback combo not populated on DC mode switch
- Added per-cycle result overlay in the live 1D plot

### Chat #11 — Apr 8: Lock-in averaging analysis
- Analysed ZI MFLI averaging: device uses `subscribe/poll/np.mean` (software average)
- Documented that native hardware filter (`getSample()` + settle) would be cleaner but current approach works well at typical integration times ≥ 50 ms
- No code change; documented as known limitation

### Chat #12 — Apr 9: Button icons fix
- Replaced Unicode/emoji icons with `QStyle.StandardPixmap` and `QIcon.fromTheme` fallbacks
- Fixed icon rendering on Ubuntu 24 (strict Qt Unicode rendering)

### Chat #13 — Apr 9: Safety lock mechanism & ETA fix
- Added `setup_lock.py` using TANGO advisory lock device (`hpp-N42/samba/lock`)
- Prevents two Samba instances from controlling the same setup simultaneously
- Added ETA estimation to DC hysteresis status bar

### Chat #14 — Apr 9: App icon & splash screen
- Added `play_intro.py` splash screen with progress animation
- Added `.ico`/`.png`/`.svg` app icons at multiple resolutions

### Chat #15 — Apr 10–11: DG645 & TR-MOKE integration
- Added `DG645_DelayGenerator.py` TANGO device server for the Stanford DG645
- Added TR-MOKE scan type: DG645 delay as scan actuator (ps / ns / µs)
- Fixed `_open_hdf5` silently swallowing exceptions (now logs actual error)
- Fixed HDF5 dataset key deduplication (two sensors with same label no longer crash)

### Chat #16 — Apr 11: Cryo setup separation (planned)
- Cryo removed from `SETUP_NAMES` in main `samba.py`
- Planned: `samba_cryo.py` separate entry point with AttoDRY2100, ANC300, ANM200 support
- Architecture defined; implementation deferred

### Chat #17 — Apr 11: Full codebase refactoring
- Split `panels.py` → `panels/` package (8 modules)
- Split `scan.py` → `scan/` package (3 modules)
- Fixed all bare `except:` / `except: pass` throughout
- Extracted `_prepare_scan()` and `_wire_worker()` helpers (deduplicated scan-start)
- Added `threading.Lock` for proxy cache thread safety
- Added versioned config migration chain (`_schema_version`, `_MIGRATIONS`)
- Replaced magic tab-index comparisons with `currentWidget() is panel` pattern
- Made `HardwarePanel` internal methods public (`demagnetize()`, `set_relay_state()`)

### Code review session — Apr 12
- Fixed missing `_pcache_lock` in `reconnect_device` error path (threading race)
- Fixed unhandled `write_attribute` on magnet during FIELD scan → `safe_write`
- Fixed state-poll exception silently dropping device → now logs warning
- Fixed HDF5 flush errors swallowed with `except: pass` → now logged
- Fixed HDF5 file handle leak in `_run_dc_hyst` abort/exception paths → `try/finally`
- Fixed TOCTOU race in `acquire_lock` → verify-after-write; stamp now includes PID
- Renamed `demagnetize_magnet(log=)` → `log_fn=` (parameter shadowed module logger)
- Removed redundant inline `import time` inside field-flip loop
- Polling loop uses `done`-set pattern instead of `list(remaining)` copy
- Parallel `fresh_proxy` setup via `ThreadPoolExecutor`
- Fixed crash: `datetime` not imported in `panels/_widgets.py`
- Replaced one-line `README.md` stub with full user documentation
