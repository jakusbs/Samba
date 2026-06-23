# SAMBA — Continuation / Handoff Notes

State as of the June 2026 review session (branch
`claude/app-review-suggestions-jozwry` in both the **Samba** and
**TANGO_Devices** repos). This is the "what's done / what's next" list so a
fresh chat can pick up without re-deriving context.

---

## Status

- **Done & merged-ready** — see the "Recent Changes" sections at the end of
  `Samba/CLAUDE.md` (§30) and `TANGO_Devices/CLAUDE.md`.
- **Verified on hardware** (by Jakub): the PLC `HystSource1..6` change,
  PyHysteresis source selection + per-cycle commands, the new TANGO device
  servers, and the Konami easter egg.
- **Tests**: `python test_runner.py` (32, hardware-free) + GitHub Actions.

---

## Open follow-ups (roughly priority order)

### A. PyHysteresis per-cycle UI in Samba (the natural next step)
The TANGO device already retains every cycle and can re-average excluding bad
ones (`GetNumberOfCycles`, `GetCycle(n)`, `SetExcludedCycles`, `RecomputeAverage`).
Nothing in Samba uses these yet. To finish the "see individual scans, kick one
out of the average" feature the colleague asked for:
1. **Save per-cycle data to HDF5** — in `core/scan/runner.py` `_run_dc_hyst`, after
   completion loop `GetCycle(1..N)` and write a `/data/cycles` dataset
   (`[n_cycles, 7, 2*NumberOfPoints]` or similar). ~20 lines, low risk; preserves
   the raw scans in every file so exclusion can also be done offline.
2. **Live overlay + checkboxes** in the DC-Hyst panel: faint per-cycle traces over
   the average; unchecking a cycle calls `SetExcludedCycles`/`RecomputeAverage`
   and repaints. The real interactive UX.
3. **Analysis pipeline** (`Analysis/analyze_samba.py`): read `/data/cycles`, show
   them, drop outliers when re-fitting.
4. **Source-selection dropdown** in the Samba DC-Hyst panel writing the new
   `source1..6` attributes (currently set from Jive). ~30 lines.

### B. Samba structural refactors (deferred from the original review — larger)
- **Cryo ↔ Samba_main UI dedup**: `Cryo/panels.py` (~2400 lines) reimplements
  `SensorPickerRow`, `MokeMetadataGroup`, `NoScroll*`, `ActuatorGroup`,
  `TrajectoryPanel`, `HardwarePanel`, `ScanlistPanel`, `RightPanel`,
  `ConfigListPanel`; `samba.py`/`samba_cryo.py` duplicate the status bar / ETA /
  estimate / probe / sync-bar. Move the lowest-divergence widgets into `core/`
  one at a time (parameterize the accent color). Needs GUI testing → own session.
- **Merge the scan loops in `runner.run()`**: the interleaved-X, interleaved-Y,
  Y-fast and standard loops duplicate the per-point body. Extract one
  `_scan_point()` to stop drift (adaptive settle already lives in only some).

### C. TANGO device-server items still open
- **Deploy + bench-verify the remaining server fixes** beyond PyHysteresis:
  Socket (reconnect + orderly-close), ANC300 (reply draining), AttoDRY
  (Connect/daemon + setpoint wedge), AdsBridge2 (init order + array raise),
  PyKeithley frequency re-arm, PyRelais grounded-fault, Magnet ±10 V guard,
  ZI Start race / FAULT-on-fail. Re-run the relevant `install.sh` and restart;
  watch the first scan after each.
- **From `TANGO_Devices/CLAUDE.md` "What Still Needs Attention"**: D02→D04
  Keithley firmware; network switch idle-TCP timeout (keepalives?); AdsBridge2
  auto-reconnect watchdog; ANC300 absolute-position drift; Magnet zero-guards on
  `HallSensitivity_*`/`AmperePerVolt_*`; DG645 `LERR?` polling cost; update ZI2
  LabOne firmware 24.10.6 → 25.x (then `AllowVersionMismatch=False`).

### D. Hardware-verification checklist (couldn't be tested from the dev box)
- **Cryo field + temperature sweep**: confirm the x-axis now reads sensible
  T / K values (not the old `setpoint × 0.15`) end-to-end on the AttoDRY.
- **Magnet `AmperePerVolt`**: confirm the per-device DB property matches the
  supply's true A/V (the field readback factor depends on it; see the §X-axis
  units discussion — a 2 A/V property halved the apparent field once).
- **Samba field scans**: confirm they now label/store **mT** (Beckhoff) — and
  that this matches DC-Hyst, which already used mT.

### E. Known issues carried over (`Samba/CLAUDE.md §17`)
- Zigzag 2D piezo-hysteresis asymmetry; ZI poll-and-average vs `getSample()`
  hardware filtering; sequential (skewed) multi-device sensor reads; TR-MOKE
  HDF5 stores raw seconds not display units; stale snapshot files.

---

## Repo / workflow notes
- Two repos, both branch `claude/app-review-suggestions-jozwry`, base `main`.
- The launcher runs the repo **in place** (`exec python samba.py`), so deploying
  = `git pull` on the lab machine + restart the app (no copy step).
- Device-server source lives **only** in TANGO_Devices now (the in-repo copies
  were removed). Both apps add the repo root to `sys.path` and import `core.*`.
- `SAMBA_EGG_DEBUG=1` logs the easter-egg key watcher to stderr if it ever needs
  debugging again.
EOF
