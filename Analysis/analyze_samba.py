"""
analyze_samba.py  —  SOT/MOKE scan analysis for SAMBA HDF5 data
(Green, IR and Cryo setups).
Based on analysis_samba.py by Tobias Goldenberg (ETH Zürich, 2026).
Handles /data/ (Cryo) and /measurement/ (Green/IR) HDF5 groups, absolute
scanlist paths, trace/retrace direction support, Linux-compatible saving.

Primary entry class: ``analyze_SOT``.  The old names ``analyze_SOT`` and
``SambaSOTAnalysis`` are kept as aliases so existing scripts keep working.

Channel mapping (auto):
    ZI_x1  → zix1      ZI_y1  → ziy1
    ZI_x2  → zix2      ZI__y2 → ziy2   (handles double-underscore typo)
    DC/Mon → FL         (reflection / focus-laser equivalent)
    actuator_x / actuator_y → 'x'   (already in µm in Cryo HDF5; scan axis
                                     is auto-detected, so SOT-y scans work)
"""

import os
import re
import csv
import json
import datetime
import warnings

import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from scipy.optimize import curve_fit, minimize_scalar
from scipy import interpolate, signal
import h5py

# Physical constants (SI) for the SOT / spin-Hall efficiency
_E_CHARGE = 1.602176634e-19      # C
_H_BAR    = 1.054571817e-34      # J·s
_MU0      = 1.25663706212e-6     # T·m/A


# ---------------------------------------------------------------------------
# Filename / scanlist parsing helpers
# ---------------------------------------------------------------------------

_CURRENT_RE = re.compile(r'(\d+(?:[.p]\d+)?)\s*mA', re.IGNORECASE)


def parse_current_from_name(name):
    """Extract current (mA) from a scanlist or HDF5 filename.

    Looks for the SAMBA convention ``..._<num>mA_...`` (e.g. ``12.5mA``,
    ``12p5mA``). Returns ``None`` if no match.
    """
    m = _CURRENT_RE.search(os.path.basename(name))
    if not m:
        return None
    try:
        return float(m.group(1).replace('p', '.'))
    except ValueError:
        return None


def detect_directions(scanlist_path, data_base_dir=None):
    """Return the set of scan directions present in a scanlist.

    Returns a subset of ``{'trace', 'retrace'}``. Empty set means the
    scanlist has no trace/retrace markers (e.g. legacy Green/IR data) —
    callers should run a single-direction analysis with ``direction=None``.
    """
    found = set()
    try:
        with open(scanlist_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                bname = os.path.basename(line.split('\t')[0].strip()).lower()
                if '_trace'   in bname: found.add('trace')
                if '_retrace' in bname: found.add('retrace')
    except Exception as e:
        warnings.warn(f'detect_directions: {e}')
    return found


def search_print_measurements(search_string, file_path, do_print=True):
    """List files in *file_path* whose name contains *search_string*.

    Returns the list of matching basenames (sorted by mtime, newest last).
    If *do_print* is True, prints them numbered so the user can pick.
    """
    try:
        entries = [n for n in os.listdir(file_path) if search_string in n]
    except OSError as e:
        warnings.warn(f'search_print_measurements: cannot list {file_path}: {e}')
        return []

    try:
        entries.sort(key=lambda n: os.path.getmtime(os.path.join(file_path, n)))
    except OSError:
        entries.sort()

    if do_print:
        for i, n in enumerate(entries):
            print(f'  [{i:3d}] {n}')
    return entries


# ---------------------------------------------------------------------------
# Impurity detection (ported from analysis_field.find_impurities_peaks /
# Jakub_methods.iterate_find_impurity).  Used to flag points inside the
# device that look like reflection artefacts so they can be excluded from
# the SOT fit.
# ---------------------------------------------------------------------------

def find_impurities_peaks(theta_DL, peakheight=1.0, do_plot=False, ax=None):
    """Mask points that look like impurity spikes in the DL signal.

    Smoothing-spline + peak detection on the derivative locates pairs of
    consecutive min/max derivatives — these bracket a localised feature
    (impurity).  Returns a boolean mask of indices inside any such bracket.
    """
    try:
        from scipy.interpolate import make_smoothing_spline
    except ImportError:
        warnings.warn('find_impurities_peaks: scipy>=1.10 needed; returning '
                      'empty mask.')
        return np.zeros(len(theta_DL), dtype=bool)

    y    = np.asarray(theta_DL, dtype=float)
    x    = np.arange(len(y))
    mask = np.zeros(len(y), dtype=bool)
    if len(y) < 6:
        return mask

    try:
        spl = make_smoothing_spline(x, y, lam=1.0)
    except Exception as e:
        warnings.warn(f'find_impurities_peaks: spline failed: {e}')
        return mask

    dspl   = np.gradient(spl(x))
    height = peakheight * 0.02 * 5 * float(np.max(np.abs(dspl)) or 1.0)
    peaks, _ = signal.find_peaks(np.abs(dspl), height=height)
    if len(peaks) < 2:
        return mask

    # Pair each minimum with each maximum derivative; smallest separation wins
    mins = peaks[dspl[peaks] < 0]
    maxs = peaks[dspl[peaks] >= 0]
    if len(mins) == 0 or len(maxs) == 0:
        return mask
    if len(mins) == 1 and len(maxs) == 1:
        # Single min/max → these are the device edges, not impurities.
        return mask

    combos = [(mn, mx, abs(mx - mn)) for mn in mins for mx in maxs]
    combos.sort(key=lambda c: c[2])
    for mn, mx, _d in combos[:min(len(mins), len(maxs))]:
        lo, hi = sorted((int(mn), int(mx)))
        mask[max(0, lo - 2):min(len(y), hi + 3)] = True

    if do_plot:
        a = ax if ax is not None else plt.gca()
        keep = ~mask
        a.plot(x, y, color='lightgray', label='DL (raw)')
        a.scatter(x[keep], y[keep], color='C2', s=14, label='used for fit')
        a.scatter(x[mask], y[mask], color='C3', s=14, label='flagged impurity')
        a.plot(x, dspl, color='C1', alpha=0.5, label='spline d/dx')
        a.legend(fontsize=9)
    return mask


def iterate_find_impurity(theta_DL, calc_info=None, do_plot=False, ax=None):
    """Return a boolean *use_mask* of points to keep for the DL fit.

    Wraps :func:`find_impurities_peaks` and combines it with the device-edge
    mask derived from the DL gradient (the two strongest derivatives are
    assumed to mark the left/right device edges).
    """
    y    = np.abs(np.asarray(theta_DL, dtype=float))
    idx  = np.arange(len(y))
    dy   = np.gradient(y)
    if len(dy) < 4:
        return np.ones(len(y), dtype=bool)

    Redge = int(np.argmin(dy[1:-2]) + 1)
    Ledge = int(np.argmax(dy[1:-2]) + 1)
    if Redge < Ledge:
        Ledge, Redge = Redge, Ledge

    mask     = find_impurities_peaks(y, peakheight=1.0, do_plot=False)
    device   = (idx > Ledge) & (idx < Redge)
    use_mask = device & ~mask
    print(f'  iterate_find_impurity: edges=[{Ledge},{Redge}], '
          f'{int(mask.sum())} impurity pt(s), {int(use_mask.sum())} kept')

    if do_plot:
        a = ax if ax is not None else plt.gca()
        title = ''
        if calc_info is not None:
            title = f'{getattr(calc_info, "system", "")} ' \
                    f'{getattr(calc_info, "current", "")}mA ' \
                    f'{getattr(calc_info, "LightPol", "")}'
        a.set_title(title)
        a.plot(y, color='gray', label='|DL|')
        a.scatter(idx[mask], y[mask], color='C3', label='flagged')
        a.scatter(idx[use_mask], y[use_mask], color='C2', label='used')
        a.legend()
    return use_mask


def first_h5_in_scanlist(scanlist_path, data_base_dir=None):
    """Return the first resolvable HDF5 file referenced by a scanlist, or None."""
    try:
        with open(scanlist_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                localfile = line.split('\t')[0].strip()
                resolved = _resolve_path(localfile, data_base_dir)
                if resolved:
                    return resolved
    except Exception as e:
        warnings.warn(f'first_h5_in_scanlist: {e}')
    return None


def read_h5_meta(h5_path):
    """Return ``/metadata`` attrs as a plain dict, decoding bytes to str."""
    out = {}
    try:
        with h5py.File(h5_path, 'r') as f:
            if 'metadata' in f:
                for k, v in f['metadata'].attrs.items():
                    if isinstance(v, bytes):
                        v = v.decode('utf-8', errors='replace')
                    elif hasattr(v, 'item'):
                        try: v = v.item()
                        except Exception: pass
                    out[k] = v
    except Exception as e:
        warnings.warn(f'read_h5_meta: {e}')
    return out


# ---------------------------------------------------------------------------
# Sample-folder + calibration file (compatible with the old Jakub_methods
# convention: 4-line text file — 6 calibration sweep values, R1, R2, theta)
# ---------------------------------------------------------------------------

DEFAULT_ANALYSIS_BASE = r'Z:\projects\MOKE_lab\Scanning\Analysis_Scripts'

_CALIB_X_TICKS = np.linspace(0, 25, 6)   # micrometer-screw ticks for sweep

# Characters that aren't safe in folder names on Windows/NAS shares
_BAD_DIRNAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_dirname(name):
    """Sanitize a string for use as a folder name (keeps parentheses)."""
    return _BAD_DIRNAME_CHARS.sub('_', str(name)).strip().rstrip('. ')


def _json_safe(v):
    """Convert numpy / bytes / non-finite scalars to JSON-serialisable forms."""
    if isinstance(v, bytes):
        return v.decode('utf-8', errors='replace')
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return f if np.isfinite(f) else None
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, float) and not np.isfinite(v):
        return None
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    return v


def _safe_savefig(path, dpi=150, tight=True, tight_kw=None, **savekw):
    """Save the current figure, tolerating ``tight_layout`` failures.

    ``tight_layout`` can raise ``ValueError: cannot convert float NaN to
    integer`` when a two-panel ``sharey`` figure with twin axes carries very
    large tick labels (e.g. real ``sln`` × raw lock-in signal).  On failure
    we restore the pre-tight axes positions and save the figure flat, so a
    single unlucky plot never aborts the analysis pipeline.
    """
    fig = plt.gcf()
    saved_pos = [ax.get_position(original=True) for ax in fig.axes]
    if tight:
        try:
            fig.tight_layout(**(tight_kw or {}))
        except Exception:
            pass
    try:
        fig.savefig(path, dpi=dpi, **savekw)
        return True
    except Exception as e:
        warnings.warn(f'_safe_savefig: tight layout failed ({type(e).__name__}: '
                      f'{e}); saving "{os.path.basename(path)}" without it')
        try:
            fig.set_layout_engine('none')
            for ax, pos in zip(fig.axes, saved_pos):
                ax.set_position(pos)
            fig.savefig(path, dpi=dpi, **savekw)
            return True
        except Exception as e2:
            warnings.warn(f'_safe_savefig: could not save '
                          f'{os.path.basename(path)}: {e2}')
            return False


def _moke_calibrate(um_ticks, m_volts):
    """Fit MOKE-calibration sweep → slope (mV/deg). HWP doubles the angle."""
    x = np.array(um_ticks, dtype=float) / (100.0 / 4.0) * 2.0   # ticks → deg
    y = np.array(m_volts,  dtype=float)
    slope, _ = np.polyfit(x, y, 1)
    return slope


def get_sample_folder(sample_name, base=DEFAULT_ANALYSIS_BASE):
    """Return ``<base>/<sample_name>/``, creating it on demand."""
    folder = os.path.join(base, str(sample_name))
    os.makedirs(folder, exist_ok=True)
    return folder


def read_h5_calibration(h5_path):
    """BD calibration straight from a SAMBA HDF5 file.

    Reads ``/data/calibration`` (the 6 mV λ/2-plate readings at ticks
    0,5,10,15,20,25 that Samba writes on every scan) and converts the slope
    to ``sln`` in µrad/mV, exactly like :func:`read_calibration`.

    Returns ``(sln, cal_mV_array)``, or ``(None, None)`` when the dataset is
    absent or unusable (old files predating the BD-calibration feature).
    """
    if not h5_path or not os.path.exists(h5_path):
        return None, None
    try:
        with h5py.File(h5_path, 'r') as f:
            if 'data' in f and 'calibration' in f['data']:
                cal = np.asarray(f['data']['calibration'], dtype=float).ravel()
            else:
                return None, None
    except Exception as e:
        warnings.warn(f'read_h5_calibration: {e}')
        return None, None

    if cal.size < 2 or not np.all(np.isfinite(cal)) or np.allclose(cal, 0):
        return None, (cal if cal.size else None)
    slope = _moke_calibrate(_CALIB_X_TICKS[:cal.size], cal)
    if slope == 0 or not np.isfinite(slope):
        return None, cal
    sln = (1.0 / slope) * np.pi / 180.0 * 1e6
    return sln, cal


_CALIB_MARK = 'samba_calib'          # any versioned marker line


def read_calibration(folder, filename='calibration.txt', allow_prompt=True,
                     h5_sln=None, h5_cal_mV=None,
                     Ms=None, t_stack_nm=None, t_fm_nm=None, theta=None):
    """Resolve the per-sample calibration constants and persist them.

    Line-based ``calibration.txt`` (v3) — five data lines:
        line 1 : 6 space-separated mV λ/2 sweep readings (ticks 0,5,…,25)
        line 2 : Ms       — saturation magnetization (A/m); 0 = unset
        line 3 : t_stack  — current-carrying stack thickness (nm); 0 = unset
        line 4 : t_FM     — ferromagnet thickness (nm); 0 = unset
        line 5 : theta    — 1st-harmonic phase offset (deg)

    (v2 files without the t_FM line are still read — 4 data lines →
    mV / Ms / t_stack / theta — and upgraded on the next write.)

    Resolution: ``sln`` comes from the HDF5 ``/data/calibration`` (``h5_sln``)
    when available, else from the file's 6 mV line, else a prompt.  ``Ms``,
    ``t_stack`` and ``t_FM`` come from an explicit arg (for t_FM the caller
    passes the HDF5 ``fm_thickness_nm`` metadata when set), else the file,
    else a prompt.  Whatever is missing is prompted for (when
    ``allow_prompt``) and the file is (re)written so later runs are silent.
    Enter a blank/0 to leave a value unset (ξ_DL is then skipped).

    Returns ``(sln, Ms, t_stack_nm, t_fm_nm, theta, cal_mV)`` — may be None.
    """
    path = os.path.join(folder, filename)

    file_mV = file_Ms = file_tstack = file_tfm = file_theta = None
    old_format = False
    if os.path.exists(path):
        try:
            raw = open(path).read()
        except Exception as e:
            warnings.warn(f'read_calibration: cannot read {path}: {e}')
            raw = ''
        if _CALIB_MARK in raw:
            rows = [l.strip() for l in raw.splitlines()
                    if l.strip() and not l.strip().startswith('#')]

            def _row_float(i):
                try:    return float(rows[i])
                except Exception: return None
            if len(rows) >= 1:
                mv = np.fromstring(rows[0], dtype=float, sep=' ')
                file_mV = mv if mv.size >= 2 else None
            if len(rows) >= 2: file_Ms     = _row_float(1)
            if len(rows) >= 3: file_tstack = _row_float(2)
            if len(rows) >= 5:              # v3: mV/Ms/t_stack/t_FM/theta
                file_tfm   = _row_float(3)
                file_theta = _row_float(4)
            elif len(rows) >= 4:            # v2: mV/Ms/t_stack/theta
                file_theta = _row_float(3)
        elif raw.strip():
            old_format = True
            warnings.warn(f'read_calibration: {path} is an old-format (R1/R2) '
                          f'file — ignoring it and rebuilding.')

    # explicit arg (t_FM: incl. HDF5 metadata via caller) > file
    Ms_v     = Ms         if Ms         is not None else file_Ms
    tstack_v = t_stack_nm if t_stack_nm is not None else file_tstack
    tfm_v    = t_fm_nm    if t_fm_nm    is not None else file_tfm
    theta_v  = theta      if theta      is not None else file_theta
    cal_mV   = h5_cal_mV  if h5_cal_mV  is not None else file_mV
    dirty    = old_format or not os.path.exists(path) or (
        file_tfm is None and tfm_v is not None)   # upgrade v2 → v3

    def _prompt_float(hint):
        # blank → None (skip); EOF (non-interactive) → None, no crash
        while True:
            try:
                s = input(f'  {hint}\n  > ').strip()
            except EOFError:
                return None
            if s == '':
                return None
            try:
                return float(s)
            except ValueError:
                print('  Enter a single number (blank to skip).')

    # Only Ms / t_stack are ever prompted for.  sln comes from the HDF5 or the
    # 6 mV line (prompted only if neither is available); theta is auto-detected
    # by get_theta so it is never prompted (defaults to the file value or 0).
    if allow_prompt:
        if h5_sln is None and cal_mV is None:
            print(f'\nNo calibration in the HDF5 or {path} for this sample.')
            while True:
                try:
                    raw = input('  6 mV λ/2 readings at ticks 0 5 10 15 20 25 '
                                '(space-separated):\n  > ').strip()
                except EOFError:
                    break
                mv = np.fromstring(raw, dtype=float, sep=' ')
                if mv.size == 6:
                    cal_mV = mv; dirty = True; break
                print(f'  Expected 6 values, got {mv.size}. Try again.')
        if Ms_v is None:
            print('\n  Ms — saturation magnetization (A/m) [blank to skip ξ_DL]:')
            Ms_v = _prompt_float('e.g. 1.4e6'); dirty = True
        if tstack_v is None:
            print('\n  t_stack — current-carrying stack thickness (nm) '
                  '[blank to skip ξ_DL]:')
            tstack_v = _prompt_float('e.g. 8'); dirty = True
        if tfm_v is None:
            print('\n  t_FM — ferromagnet thickness (nm) [blank to skip ξ_DL; '
                  'normally set in the Samba metadata panel]:')
            tfm_v = _prompt_float('e.g. 3'); dirty = True

    # sln from the 6 mV sweep when the HDF5 didn't supply it
    sln = None
    if h5_sln is not None:
        sln = float(h5_sln)
    elif cal_mV is not None and np.size(cal_mV) >= 2:
        slope = _moke_calibrate(_CALIB_X_TICKS[:np.size(cal_mV)], np.asarray(cal_mV, float))
        if slope and np.isfinite(slope):
            sln = (1.0 / slope) * np.pi / 180.0 * 1e6

    # Persist v3 so subsequent runs are silent
    if dirty and (cal_mV is not None or Ms_v or tstack_v or tfm_v):
        mv_out = (' '.join(f'{v:g}' for v in np.asarray(cal_mV, float))
                  if cal_mV is not None else '')
        content = (
            '# samba_calib v3  —  6 mV λ/2 sweep / Ms (A/m) / t_stack (nm)'
            ' / t_FM (nm) / theta (deg)\n'
            '# 6 calibration mV readings at micrometer ticks 0 5 10 15 20 25\n'
            f'{mv_out}\n'
            '# Ms — saturation magnetization (A/m); 0 = unset\n'
            f'{float(Ms_v) if Ms_v else 0.0!r}\n'
            '# t_stack — current-carrying stack thickness (nm); 0 = unset\n'
            f'{float(tstack_v) if tstack_v else 0.0!r}\n'
            '# t_FM — ferromagnet thickness (nm); 0 = unset\n'
            f'{float(tfm_v) if tfm_v else 0.0!r}\n'
            '# theta — 1st-harmonic phase offset (deg)\n'
            f'{float(theta_v) if theta_v is not None else 0.0!r}\n'
        )
        try:
            with open(path, 'w') as f:
                f.write(content)
            print(f'  Calibration saved to {path}')
        except Exception as e:
            warnings.warn(f'read_calibration: could not write {path}: {e}')

    # 0 in the file means "unset"
    if Ms_v == 0:     Ms_v = None
    if tstack_v == 0: tstack_v = None
    if tfm_v == 0:    tfm_v = None
    if sln is not None:
        print('  Calibration  : sln = {:.4g} µrad/mV{}{}{}{}'.format(
            sln,
            f', Ms = {Ms_v:.4g} A/m' if Ms_v else '',
            f', t_stack = {tstack_v:g} nm' if tstack_v else '',
            f', t_FM = {tfm_v:g} nm' if tfm_v else '',
            f', θ₀ = {theta_v:.4g}°' if theta_v else ''))
    return (sln, Ms_v, tstack_v, tfm_v,
            (0.0 if theta_v is None else float(theta_v)), cal_mV)


# ---------------------------------------------------------------------------
# HDF5 I/O
# ---------------------------------------------------------------------------

_NON_CHANNEL_DS = {'calibration'}   # datasets in /data that aren't scan channels


def _pick_group(f):
    """Return the HDF5 group holding the scan channels.

    Prefers ``/data`` (new SAMBA) but only when it actually contains scan
    channels — a file whose ``/data`` group holds *only* auxiliary datasets
    (e.g. ``calibration``) with the real channels under ``/measurement``
    falls back to ``/measurement``.  Returns ``None`` for the old
    ``/scan_X`` layout (handled separately).
    """
    d = f['data'] if ('data' in f and isinstance(f['data'], h5py.Group)) else None
    m = (f['measurement']
         if ('measurement' in f and isinstance(f['measurement'], h5py.Group)
             and not any(k.startswith('scan_') for k in f.keys()))
         else None)
    if d is not None:
        real = [k for k in d
                if k not in _NON_CHANNEL_DS and isinstance(d[k], h5py.Dataset)]
        if real:
            return d
    if m is not None:
        return m
    return d   # /data with only aux datasets, or None


def data_load(filename, data_channel, despike=False, return_unit=False):
    """Load one channel from a SAMBA HDF5 file.

    Supports three formats:
    - Cryo / new SAMBA : /data/<channel>
    - Green/IR SAMBA   : /measurement/<channel>  (no scan_X groups)
    - Old scan-server  : /scan_X/measurement/<channel>

    Parameters
    ----------
    despike : bool
        If True, NaN-replace single-point spikes (|grad| >10x mean, opposite
        signs on either side).  Default False — only warns when spikes look
        present, leaving the raw data untouched so downstream interpolation
        doesn't bridge over them.
    return_unit : bool
        If True, return ``(data, unit_str)`` instead of just data.
    """
    unit = ''
    with h5py.File(filename, 'r') as f:

        # ── Cryo / Green / IR new SAMBA format (/data or /measurement) ───
        grp = _pick_group(f)
        if grp is not None:
            if data_channel in grp:
                ds   = grp[data_channel]
                data = np.array(ds, dtype=float)
                unit = str(ds.attrs.get('unit', '')) if hasattr(ds, 'attrs') else ''
            else:
                warnings.warn(f'data_load: "{data_channel}" not found in {filename}')
                return (np.zeros(1), '') if return_unit else np.zeros(1)

        # ── Old scan_X format ────────────────────────────────────────────
        else:
            scans = list(f.keys())
            if not scans:
                return (np.zeros(1), '') if return_unit else np.zeros(1)
            first = True
            for s in scans:
                key = s + '/measurement/' + data_channel
                if key in f:
                    arr = np.array(f[key], dtype=float)
                    if first:
                        data = np.zeros_like(arr)
                        first = False
                    data += arr
            if first:
                warnings.warn(f'data_load: "{data_channel}" not found in {filename}')
                return (np.zeros(1), '') if return_unit else np.zeros(1)
            data /= len(scans)

    if data.ndim == 2 and data.shape[0] == 1:
        data = data.reshape(-1)

    # Spike detection — count first
    if data.ndim == 1 and len(data) > 3:
        g = np.gradient(data)
        lim = np.mean(np.abs(g))
        if lim > 0:
            mid = np.arange(1, len(data) - 1)
            spikes = ((np.abs(g[mid - 1]) >= 10 * lim) &
                      (np.abs(g[mid + 1]) >= 10 * lim) &
                      (np.sign(g[mid - 1]) == -np.sign(g[mid + 1])))
            n_spikes = int(spikes.sum())
            if n_spikes:
                warnings.warn(f'data_load: {n_spikes} likely spike(s) in '
                              f'"{data_channel}" of {os.path.basename(filename)}'
                              + (' — NaN-replaced (despike=True)' if despike
                                 else ' — kept as-is (despike=False)'))
                if despike:
                    data[mid[spikes]] = np.nan

    return (data, unit) if return_unit else data


def get_channels(scanlist_or_h5, logfilepath='', data_base_dir=''):
    """Discover available channels from the first H5 file in a scanlist.

    Returns ``(res, meta)`` where:
    - *res*  : dict  channel_name → channel_name
    - *meta* : dict  file-level attributes
    """
    if scanlist_or_h5.endswith('.h5'):
        filename = scanlist_or_h5
    elif scanlist_or_h5.endswith('.txt'):
        fullpath = (os.path.join(logfilepath, scanlist_or_h5)
                    if (logfilepath and not os.path.isabs(scanlist_or_h5))
                    else scanlist_or_h5)
        filename = None
        with open(fullpath, 'r') as f:
            for line in f:
                if line.startswith('#') or not line.strip():
                    continue
                localfile = line.split('\t')[0].strip()
                filename = _resolve_path(localfile, data_base_dir or None)
                if filename:
                    break
        if not filename:
            warnings.warn(f'get_channels: no accessible H5 file in {scanlist_or_h5}')
            return {}, {}
    else:
        warnings.warn(f'get_channels: unrecognised input: {scanlist_or_h5}')
        return {}, {}

    res, meta = {}, {}
    print(f'  Channels in {os.path.basename(filename)}:')
    with h5py.File(filename, 'r') as f:
        for k, v in f.attrs.items():
            meta[k] = v.decode('UTF-8') if isinstance(v, bytes) else v

        grp = _pick_group(f)
        if grp is None:
            grp = {}

        for name in grp:
            if name in _NON_CHANNEL_DS:
                continue
            if isinstance(grp[name], h5py.Dataset):
                print(f'    {name}')
                res[name] = name

    return res, meta


def nan_helper(y):
    return np.isnan(y), lambda z: z.nonzero()[0]


# ---------------------------------------------------------------------------
# Edge detection & phase optimisation
# ---------------------------------------------------------------------------

def find_edges_width(position, reflex, min_width=4.0):
    """Find device edges from the reflection profile (spline + derivative peaks).

    Two strategies are tried, in order, and the first whose width is
    ``>= min_width`` wins (else the widest is returned — best effort):

    1. **Innermost peak pair** (Tobi): left edge = rightmost negative-derivative
       peak, right edge = leftmost positive-derivative peak.  Robust against
       noise spikes *outside* the device.
    2. **Left/right-half** (the earlier method): strongest negative-derivative
       peak in the left half, strongest positive in the right half.  Robust
       when the innermost pair collapses onto a spurious feature near the
       centre (which is what makes a single scan direction fail).
    """
    def derivatives(x, y):
        h = x[1] - x[0]
        dy = [(y[i + 1] - y[i - 1]) / (2 * h) for i in range(1, len(x) - 1)]
        return list(x[1:-1]), dy

    tck = interpolate.splrep(position, reflex, s=0)
    step = (position[1] - position[0]) / 10.0
    pos_i = np.arange(position[0], position[-1], step)
    ref_i = interpolate.splev(pos_i, tck, der=0)

    newpos, dy = derivatives(pos_i, ref_i)
    newpos = np.array(newpos)
    dy = np.array(dy)

    threshold = 0.3 * np.max(np.abs(dy))
    min_dist  = max(10, len(dy) // 50)
    neg_idx, _ = signal.find_peaks(-dy, height=threshold, distance=min_dist)
    pos_idx, _ = signal.find_peaks( dy, height=threshold, distance=min_dist)

    def _innermost():
        # rightmost negative peak / leftmost positive peak
        if len(neg_idx) and len(pos_idx):
            li, ri = int(neg_idx[np.argmax(neg_idx)]), int(pos_idx[np.argmin(pos_idx)])
            if li >= ri:                     # reversed scan / flipped polarity
                li, ri = int(pos_idx[np.argmax(pos_idx)]), int(neg_idx[np.argmin(neg_idx)])
            return li, ri
        if len(neg_idx):
            s = np.sort(neg_idx); return int(s[0]), int(s[-1])
        if len(pos_idx):
            s = np.sort(pos_idx); return int(s[0]), int(s[-1])
        return None

    def _halves():
        n = len(newpos); off = n // 2
        dyL, dyR = dy[:off], dy[off:]
        negL, _ = signal.find_peaks(-dyL, height=threshold, distance=min_dist)
        posL, _ = signal.find_peaks( dyL, height=threshold, distance=min_dist)
        negR, _ = signal.find_peaks(-dyR, height=threshold, distance=min_dist)
        posR, _ = signal.find_peaks( dyR, height=threshold, distance=min_dist)
        if   len(negL): li = int(negL[np.argmax(np.abs(dyL[negL]))])
        elif len(posL): li = int(posL[np.argmax(np.abs(dyL[posL]))])
        else:           li = int(np.argmin(dyL))
        if   len(posR): ri = int(posR[np.argmax(np.abs(dyR[posR]))]) + off
        elif len(negR): ri = int(negR[np.argmax(np.abs(dyR[negR]))]) + off
        else:           ri = int(np.argmax(dyR)) + off
        return li, ri

    def _steepest():
        li, ri = int(np.argmin(dy)), int(np.argmax(dy))
        return (li, ri) if li <= ri else (ri, li)

    cands = []
    for fn in (_innermost, _halves, _steepest):
        try:
            r = fn()
        except Exception:
            r = None
        if not r:
            continue
        li, ri = min(r), max(r)
        cands.append((li, ri, abs(float(newpos[ri]) - float(newpos[li]))))

    chosen = next((c for c in cands if c[2] >= min_width), None)
    if chosen is None:
        chosen = max(cands, key=lambda c: c[2]) if cands else (0, len(newpos) - 1, 0.0)

    li, ri, _w = chosen
    x1 = round(float(newpos[li]), 2)
    x2 = round(float(newpos[ri]), 2)
    return [x1, x2], round(x2 - x1, 2)


def find_phase(x, x1_data, y1_data, edges, ch, do_plot=False, ax=None):
    """Find lock-in phase offset that minimises the imaginary component.

    Uses bounded scalar minimisation on ``theta ∈ [-90°, 90°]`` so the
    optimiser can't wander into an equivalent 180°-flipped solution.
    Phases beyond that range only flip the sign of the real component,
    which we don't care about here.

    If ``do_plot`` is True, plots the residual imaginary component on
    ``ax`` (or the current axes) so the user can verify it sits near zero.
    """
    mask = (x >= edges[0]) & (x <= edges[1])
    if mask.sum() < 2:
        warnings.warn(f'find_phase({ch}): too few points in edge window')
        return 0.0

    def min_imag(theta_deg):
        t = theta_deg * np.pi / 180.0
        return np.std((-x1_data * np.sin(t) + y1_data * np.cos(t))[mask])

    res = minimize_scalar(min_imag, bounds=(-90.0, 90.0), method='bounded',
                          options={'xatol': 1e-3})
    theta = float(res.x)

    if do_plot:
        a = ax if ax is not None else plt.gca()
        t = theta * np.pi / 180.0
        imag = -x1_data * np.sin(t) + y1_data * np.cos(t)
        a.scatter(x[mask], imag[mask],
                  label=f'imag after θ={theta:.2f}° ({ch})')
    return theta


# ---------------------------------------------------------------------------
# Channel name mapping
# ---------------------------------------------------------------------------

_SKIP_CH = {'actuator_x_setpoint', 'actuator_y_setpoint',
            'x_setpoint', 'y_setpoint', 'time', 'Field', 'Temperature',
            'calibration'}

# Priority-ordered candidates for auto-detecting the scan-axis and intensity
# channels.  Y-axis names cover SOT-y scans (e.g. IR SAMBA), where the file
# has actuator_y instead of actuator_x; actual positions beat setpoints.
_X_CH_CANDIDATES         = ('actuator_x', 'x_actual',
                            'actuator_y', 'y_actual',
                            'x_setpoint', 'y_setpoint')
_INTENSITY_CH_CANDIDATES = ('DC', 'FL', 'Mon')

# Regex that matches any lock-in channel name (with or without ZI/ZI2 prefix)
_LI_CH_RE = re.compile(r'^(?:ZI\d*_*)?([xy][1-4])$', re.IGNORECASE)


def _detect_channels(h5_path):
    """Inspect a SAMBA HDF5 file and return detected channel roles.

    Returns::

        {
            'all'      : [list of dataset names in the file],
            'x_ch'     : best x-axis channel name (or None),
            'intensity': best DC/FL intensity channel name (or None),
            'lockin'   : [lock-in channel names in file order],
        }
    """
    out = {'all': [], 'x_ch': None, 'intensity': None, 'lockin': []}
    if not h5_path or not os.path.exists(h5_path):
        return out
    try:
        with h5py.File(h5_path, 'r') as f:
            grp = _pick_group(f)
            if grp is None:
                return out
            names = [n for n in grp
                     if n not in _NON_CHANNEL_DS and isinstance(grp[n], h5py.Dataset)]
    except Exception:
        return out

    out['all'] = names
    for c in _X_CH_CANDIDATES:
        if c in names:
            out['x_ch'] = c
            break
    for c in _INTENSITY_CH_CANDIDATES:
        if c in names:
            out['intensity'] = c
            break
    out['lockin'] = [n for n in names if _LI_CH_RE.match(n)]
    return out


def _map_channel_name(ch_name):
    """Map a SAMBA channel name to an analysis-dict key.

    Handles three lock-in naming conventions:
      - ``ZI_x1`` / ``ZI__y2``   (Cryo, double-underscore)  → ``zix1`` / ``ziy2``
      - ``ZI2_x1``                (Green/IR with device prefix) → ``zix1``
      - bare ``x1`` / ``y2``     (Green/IR new SAMBA, no prefix) → ``zix1`` / ``ziy2``
    Also: ``DC`` / ``Mon`` / ``FL`` → ``FL``
    """
    m = _LI_CH_RE.match(ch_name.strip())
    if m:
        return 'zi' + m.group(1).lower()   # always zix1, ziy1, zix2, ziy2

    lower = ch_name.strip().lower()
    if lower in ('dc', 'fl', 'mon'):
        return 'FL'
    if lower in ('field', 'temperature', 'time'):
        return lower
    return lower.replace(' ', '_')


# ---------------------------------------------------------------------------
# Per-channel data loading: scan file → pos/neg average
# ---------------------------------------------------------------------------

def _infer_data_base_dir(scanlist_path):
    """Derive the data root from the scanlist location.

    Assumes the scanlist lives in a folder whose name matches
    ``ScanLists[_]<setup>`` (e.g. ``ScanLists_Cryo``).  The data root is
    the sibling folder ``Data_Samba_<setup>`` (e.g. ``Data_Samba_Cryo``).

    Returns the inferred path if the directory exists, otherwise None.
    """
    scanlist_dir    = os.path.dirname(os.path.abspath(scanlist_path))
    parent          = os.path.dirname(scanlist_dir)
    folder_name     = os.path.basename(scanlist_dir)
    data_folder     = re.sub(r'(?i)scanlists_?', 'Data_Samba_', folder_name, count=1)
    candidate       = os.path.join(parent, data_folder)
    if os.path.isdir(candidate):
        return candidate
    return None


def _resolve_path(localfile, data_base_dir=None):
    """Find the actual location of a scan file.

    Search order:
    1. Literal path (already accessible).
    2. ``data_base_dir / basename``  — flat layout (date already in base).
    3. ``data_base_dir / <date> / basename``  — date sub-folder extracted
       from the original path, handles multi-day scans with a single root.
    """
    if os.path.exists(localfile):
        return localfile
    if data_base_dir:
        basename = os.path.basename(localfile)
        # flat lookup
        alt = os.path.join(data_base_dir, basename)
        if os.path.exists(alt):
            return alt
        # date-subfolder lookup: extract YYYYMMDD from original path
        date_dir = os.path.basename(os.path.dirname(localfile))
        if re.match(r'^\d{8}$', date_dir):
            alt2 = os.path.join(data_base_dir, date_dir, basename)
            if os.path.exists(alt2):
                return alt2
    return None


_EXPECTED_X_UNITS = {'µm', 'um', 'micrometer', 'micrometre', 'micrometers',
                     'micrometres'}


def _iter_scanlist(scanlist_path, direction=None, ignorLines=(),
                   data_base_dir=None, min_cols=1, warn_missing=False):
    """Iterate a SAMBA scanlist: yields ``(filepath, parts, bname)`` per scan.

    Shared by :func:`data_calculation` and :func:`intensity_mean` — handles
    comment/blank lines, 1-based ``ignorLines``, the trace/retrace filename
    filter, and file resolution via :func:`_resolve_path`.
    """
    line_counter = 0
    with open(scanlist_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            line_counter += 1
            if line_counter in ignorLines:
                continue

            parts = line.split('\t')
            if len(parts) < min_cols:
                continue

            localfile = parts[0].strip()
            bname = os.path.basename(localfile)

            if direction == 'trace'   and '_trace'   not in bname:
                continue
            if direction == 'retrace' and '_retrace' not in bname:
                continue

            filepath = _resolve_path(localfile, data_base_dir)
            if filepath is None:
                if warn_missing:
                    warnings.warn(f'_iter_scanlist: not found: {localfile}')
                continue

            yield filepath, parts, bname


def data_calculation(scanlist_path, ch_x='actuator_x', ch_var='ZI_x1',
                           direction=None, ignorLines=(),
                           data_base_dir=None, median=False,
                           expected_x_unit='µm'):
    """Load one data channel from all scans in a SAMBA scanlist.

    Polarity grouping follows the *original* ``data_calculation_new``
    convention: the group is set by ``-sign(field_T)`` (the field column,
    with the historical ``#INVERTED!!`` sign flip baked in), so field > 0
    lands in ``res_neg`` and field < 0 in ``res_pos``. The relay column is
    intentionally NOT folded in — matching the reference script — so the
    absolute sign of the half-difference ``(res_pos - res_neg) / 2`` (hence
    the DL signal / xi_DL) agrees with the old analysis.

    Returns ``[x, diff, sum, err, res_pos, res_neg, n_pos]`` — the same
    7-element format as ``data_calculation_SOT`` / ``data_calculation_new``.

    NOTE on ``err``: this module uses the **standard error of the mean**
    (SEM = std / sqrt(N), combined in quadrature over the two polarity
    groups) rather than the plain **standard deviation** that the original
    ``data_calculation_new`` used (``sqrt(std_pos**2 + std_neg**2) / 2``).
    SEM is the correct uncertainty on the *averaged* value that is plotted,
    and is ~sqrt(N) smaller than the original bars — so error bars here look
    much tighter than in the old script. This is deliberate; if you want the
    old (larger) STD-style bars back, drop the two ``/ sqrt(n_*)`` factors
    below.
    """
    first_scan = first_pos = first_neg = True
    var_pos = var_neg = x = None
    n_pos = n_neg = 0
    x_unit_checked = False

    for filepath, parts, bname in _iter_scanlist(
            scanlist_path, direction=direction, ignorLines=ignorLines,
            data_base_dir=data_base_dir, min_cols=3, warn_missing=True):

        try:
            field_T = float(parts[2].strip())
        except (ValueError, IndexError):
            field_T = 0.0

        # Match the original data_calculation_new: group by -sign(field_T)
        # (the "#INVERTED!!" convention). Relay column is NOT used, so the
        # DL-signal sign agrees with the reference analysis.
        pol = -1 if field_T >= 0.0 else 1

        if first_scan:
            first_scan = False
            x, x_unit = data_load(filepath, ch_x, return_unit=True)
            if x is None or len(x) < 2:
                x = None
                first_scan = True
                continue
            # Sanity-check x-axis unit (warn once per call)
            if (not x_unit_checked) and x_unit and expected_x_unit:
                if x_unit.strip().lower() not in _EXPECTED_X_UNITS:
                    warnings.warn(
                        f'data_calculation: x-axis unit '
                        f'"{x_unit}" ≠ expected "{expected_x_unit}" '
                        f'(in {bname}). Distances and fit width may '
                        f'be wrong.')
                x_unit_checked = True

        var = data_load(filepath, ch_var)
        if len(var) != len(x):
            var = np.interp(np.linspace(0, 1, len(x)),
                            np.linspace(0, 1, len(var)), var)

        # Bridge NaNs (e.g. flagged spikes) only for averaging — the
        # raw arrays are unchanged on disk.
        nans, z = nan_helper(var)
        if np.any(nans) and (~nans).sum() >= 2:
            var = var.copy()
            var[nans] = np.interp(z(nans), z(~nans), var[~nans])

        if pol >= 0:
            if first_pos:
                first_pos = False
                var_pos = var
            else:
                var_pos = np.vstack((var_pos, var))
            n_pos += 1
        else:
            if first_neg:
                first_neg = False
                var_neg = var
            else:
                var_neg = np.vstack((var_neg, var))
            n_neg += 1

    if x is None or var_pos is None or var_neg is None:
        warnings.warn(f'data_calculation: no valid data for "{ch_var}" '
                      f'(n_pos={n_pos}, n_neg={n_neg})')
        return [np.zeros(1)] * 7

    if var_pos.ndim == 1:
        var_pos = var_pos[np.newaxis, :]
    if var_neg.ndim == 1:
        var_neg = var_neg[np.newaxis, :]

    fn = np.median if median else np.mean
    res_pos = fn(var_pos, axis=0)
    res_neg = fn(var_neg, axis=0)

    diff      = (res_pos - res_neg) / 2.0
    summation = (res_pos + res_neg) / 2.0

    # Standard error of the mean per group (ddof=1, capped to avoid div by 0).
    # NOTE: SEM (std / sqrt(N)), not the original's plain STD — see docstring.
    sem_pos = (np.std(var_pos, axis=0, ddof=1) / np.sqrt(max(n_pos, 1))
               if n_pos > 1 else np.zeros_like(res_pos))
    sem_neg = (np.std(var_neg, axis=0, ddof=1) / np.sqrt(max(n_neg, 1))
               if n_neg > 1 else np.zeros_like(res_neg))
    # Error of (pos − neg) / 2 by quadrature; same expression also valid for
    # (pos + neg) / 2 since pos and neg are independent groups.
    err = 0.5 * np.sqrt(sem_pos ** 2 + sem_neg ** 2)

    return [x, diff, summation, err, res_pos, res_neg, n_pos]


def linescan_calc(scanlist_path, direction=None, ignorLines=(),
                        data_base_dir=None, x_ch='actuator_x'):
    """Load all sensor channels from a SAMBA scanlist.

    Returns a dict::

        {
            'x'    : position array  (µm, from HDF5),
            'zix1' : [x, diff, sum, std, pos_avg, neg_avg, n],
            'ziy1' : …,
            'FL'   : …,   # DC reflection
            …
        }
    """
    res_ch, meta = get_channels(scanlist_path, data_base_dir=data_base_dir)
    if not res_ch:
        warnings.warn('linescan_calc: get_channels returned no channels; '
                      'check data_base_dir')
        return {}

    my_dict   = {}
    x_loaded  = False
    skip      = _SKIP_CH | set(_X_CH_CANDIDATES) | {x_ch}

    for ch_name in res_ch:
        if ch_name in skip or 'actuator' in ch_name.lower():
            continue

        data = data_calculation(
            scanlist_path, ch_x=x_ch, ch_var=ch_name,
            direction=direction, ignorLines=ignorLines,
            data_base_dir=data_base_dir,
        )
        if data[0] is None or len(np.atleast_1d(data[0])) <= 1:
            continue

        if not x_loaded:
            my_dict['x'] = data[0]
            x_loaded = True

        key = _map_channel_name(ch_name)
        my_dict[key] = data

    return my_dict


def intensity_mean(scanlist_path, ch_var='DC', direction=None,
                         ignorLines=(), data_base_dir=None):
    """Collect per-scan profiles of *ch_var* (for ``see_intensity`` plot).

    Returns ``(I, var_all)`` where *I* is the per-scan mean and *var_all*
    has shape ``(n_scans, n_points)``.
    """
    var_all = None

    for filepath, _parts, _bname in _iter_scanlist(
            scanlist_path, direction=direction, ignorLines=ignorLines,
            data_base_dir=data_base_dir):
        var = data_load(filepath, ch_var)
        if var_all is None:
            var_all = var
        else:
            n = var_all.shape[-1] if var_all.ndim > 1 else len(var_all)
            if len(var) == n:
                try:
                    var_all = np.vstack((var_all, var))
                except ValueError:
                    pass

    if var_all is None:
        return np.array([]), np.zeros((0, 1))
    if var_all.ndim == 1:
        var_all = var_all[np.newaxis, :]
    return np.mean(var_all, axis=1), var_all


# ---------------------------------------------------------------------------
# Main analysis class
# ---------------------------------------------------------------------------

class analyze_SOT:
    """SOT / MOKE scan analysis for SAMBA HDF5 data (Green, IR, Cryo).

    Typical usage::

        # Single direction
        res = analyze_SOT.import_analyze_SOT(
            'path/to/scanlist.txt',
            current_mA=12.5,
            see_channels=None,   # auto-detect from HDF5
        )

        # Trace + retrace separately (piezo hysteresis)
        tr, rt = analyze_SOT.import_analyze_both(
            'path/to/scanlist.txt',
            current_mA=12.5,
        )
    """

    def __init__(self, scanlist_path, current_mA=None, calibration=None,
                 sln=None, theta=None, theta2=0.0, R=None,
                 direction=None, data_base_dir=None,
                 x_ch='actuator_x', li_type='zi',
                 reflec_key='FL', x_unit='µm', signal_unit='V',
                 sample_name=None,
                 analysis_base_dir=None,
                 save_dir=None, save_subdir=True,
                 use_calibration_file=True,
                 Ms=None, t_stack_nm=None, t_fm_nm=None):
        self.scanlist_path = str(scanlist_path)
        self.direction     = direction
        # ── auto-infer data_base_dir from scanlist location ───────────────
        if data_base_dir is None:
            inferred = _infer_data_base_dir(scanlist_path)
            if inferred:
                print(f'  Data base dir auto-inferred: {inferred}')
                data_base_dir = inferred
        self.data_base_dir = data_base_dir
        self.x_unit        = x_unit
        self.signal_unit   = signal_unit
        self._reflec_key   = reflec_key

        name  = os.path.splitext(os.path.basename(scanlist_path))[0]
        parts = name.split('_')

        # ── auto-detect channel names from first HDF5 in scanlist ────────
        first_h5           = first_h5_in_scanlist(scanlist_path, data_base_dir)
        detected           = _detect_channels(first_h5) if first_h5 else \
                             {'all': [], 'x_ch': None, 'intensity': None, 'lockin': []}
        self._detected     = detected

        if x_ch == 'actuator_x' and detected['x_ch'] and detected['x_ch'] != 'actuator_x':
            print(f'  X-axis channel: auto-detected "{detected["x_ch"]}" '
                  f'(default "actuator_x" not present)')
            x_ch = detected['x_ch']
        self.x_ch = x_ch

        # ── sample-id : metadata > explicit > filename token ──────────────
        h5_meta  = read_h5_meta(first_h5) if first_h5 else {}
        if sample_name is None:
            sample_name = (h5_meta.get('sample_id', '').strip()
                           or (parts[1] if len(parts) > 1 else name))
        sample_name = _safe_dirname(sample_name)

        # ── current : explicit > HDF5 metadata > filename > default ───────
        if current_mA is None:
            v = h5_meta.get('hw_keithley_amplitude_mA')
            if v not in (None, '', 0):
                try:
                    current_mA = float(v)
                    print(f'  Current from HDF5 metadata: {current_mA} mA')
                except Exception:
                    current_mA = None
        if current_mA is None:
            current_mA = parse_current_from_name(scanlist_path)
            if current_mA is not None:
                print(f'  Current auto-detected from filename: {current_mA} mA')
        if current_mA is None:
            warnings.warn('current_mA not given and not found in metadata or '
                          'filename — defaulting to 10.0 mA')
            current_mA = 10.0

        # ── sample folder + calibration.txt ───────────────────────────────
        # ── analysis base: "Analysis_Samba" folder ────────────────────────
        # Default: two levels above the scanlist folder — e.g. scanlists in
        # <...>/Scanning/Data/ScanLists_IR/ put the analysis in
        # <...>/Scanning/Analysis_Samba/, sample-name directories inside.
        if analysis_base_dir is None:
            scan_dir          = os.path.dirname(os.path.abspath(scanlist_path))
            analysis_base_dir = os.path.join(
                os.path.dirname(os.path.dirname(scan_dir)), 'Analysis_Samba')
            print(f'  Analysis base auto-set: {analysis_base_dir}')
        sample_folder = get_sample_folder(sample_name, base=analysis_base_dir)
        self.sample_name   = sample_name
        self.sample_folder = sample_folder
        print(f'  Sample folder: {sample_folder}')

        # ── BD calibration straight from the HDF5 file (per-scan) ─────────
        h5_sln, h5_cal_mV = (read_h5_calibration(first_h5)
                             if first_h5 else (None, None))
        if h5_sln is not None:
            print(f'  Calibration from HDF5 /data/calibration: '
                  f'sln = {h5_sln:.4g} µrad/mV')

        # t_FM: explicit arg > HDF5 metadata (fed to the calibration resolver
        # so it can prompt only for what's genuinely missing).
        _mv = h5_meta.get('fm_thickness_nm')
        tfm_meta = float(_mv) if _mv not in (None, '', 0, 0.0) else None
        tfm_in   = float(t_fm_nm) if t_fm_nm is not None else tfm_meta

        # ── calibration.txt (v3): sln / Ms / t_stack / t_FM / theta ───────
        # Reads the file, fills gaps from the HDF5 metadata / explicit args,
        # prompts for whatever is still missing, and writes it back.
        cal_sln = cal_Ms = cal_tstack = cal_tfm = None
        cal_th  = None
        if use_calibration_file:
            try:
                cal_sln, cal_Ms, cal_tstack, cal_tfm, cal_th, _ = \
                    read_calibration(
                        sample_folder, allow_prompt=True,
                        h5_sln=h5_sln, h5_cal_mV=h5_cal_mV,
                        Ms=Ms, t_stack_nm=t_stack_nm, t_fm_nm=tfm_in,
                        theta=theta)
            except Exception as e:
                warnings.warn(f'read_calibration: {e}')

        # Resolve sln : explicit > HDF5 > calibration.txt > default
        if sln is not None:
            sln_val, sln_src = float(sln), 'explicit sln='
        elif calibration is not None:
            sln_val, sln_src = float(calibration), 'explicit calibration='
        elif h5_sln is not None:
            sln_val, sln_src = float(h5_sln), 'HDF5 /data/calibration'
        elif cal_sln is not None:
            sln_val, sln_src = float(cal_sln), 'calibration.txt'
        else:
            sln_val, sln_src = 1.0, 'default (1.0)'

        # Ms / t_stack / t_FM : explicit (or metadata for t_FM) >
        # calibration.txt.  theta : explicit > calibration.txt > 0
        # (get_theta auto-detects it during analysis).
        Ms         = Ms         if Ms         is not None else cal_Ms
        t_stack_nm = t_stack_nm if t_stack_nm is not None else cal_tstack
        tfm_final  = tfm_in     if tfm_in     is not None else cal_tfm
        if theta is not None:
            theta_val = float(theta)
        elif cal_th is not None:
            theta_val = float(cal_th)
        else:
            theta_val = 0.0

        # ── calc_info ─────────────────────────────────────────────────────
        class CalcInfo:
            pass
        ci          = CalcInfo()
        ci.current  = float(current_mA)
        ci.sln      = sln_val
        ci.calibration = sln_val
        ci.theta      = theta_val
        ci.theta_pos  = theta_val      # per-polarity phases (get_theta refines)
        ci.theta_neg  = theta_val
        ci.theta2     = float(theta2)
        ci.theta2_pos = float(theta2)
        ci.theta2_neg = float(theta2)
        ci.sln_source        = sln_src
        ci.bd_calibration_mV = (list(map(float, h5_cal_mV))
                                if h5_cal_mV is not None else None)
        # Device resistances / id from the HDF5 metadata (recorded in ohms;
        # older files may carry the legacy kΩ keys).
        ci.r_4wire_ohm = h5_meta.get('r_4wire_ohm',
                                     (h5_meta.get('r_4wire_kohm', 0.0) or 0.0) * 1000
                                     if 'r_4wire_kohm' in h5_meta else None)
        ci.r_2wire_ohm = h5_meta.get('r_2wire_ohm',
                                     (h5_meta.get('r_2wire_kohm', 0.0) or 0.0) * 1000
                                     if 'r_2wire_kohm' in h5_meta else None)
        ci.device_id   = str(h5_meta.get('device_id', '') or '')
        # ── SOT / spin-Hall efficiency inputs ──────────────────────────────
        # Ms [A/m], stack and FM thickness [nm] as resolved above (explicit
        # arg / HDF5 metadata / calibration.txt / prompt).  Device width
        # comes from the fit.
        ci.Ms         = float(Ms) if Ms is not None else None
        ci.t_stack_nm = float(t_stack_nm) if t_stack_nm is not None else None
        ci.t_fm_nm    = float(tfm_final) if tfm_final is not None else None
        ci.LI_type  = li_type
        ci.system   = sample_name
        ci.sample_id = sample_name
        ci.LightPol = 'PMOKE'
        for p in parts:
            if any(x in p.lower() for x in ('moke', 'pol')):
                ci.LightPol = p
        # Prefer the HDF5 metadata incidence when present
        if h5_meta.get('incidence'):
            ci.LightPol = str(h5_meta['incidence'])
        ci.logfilenameShort = os.path.basename(scanlist_path)
        ci.specific         = name[-20:] if len(name) > 20 else name
        self.calc_name  = [ci.logfilenameShort]
        self.calc_info  = ci
        self.h5_meta    = h5_meta

        # ── state ─────────────────────────────────────────────────────────
        self.data          = None
        self.analyzed_data = None
        self.edges         = None
        self.dev_center    = None
        self.width         = None
        self.fit_DL_mT         = None
        self.fit_DL_error_mT   = None

        # ── output directory ──────────────────────────────────────────────
        # Layout:
        #   <sample>/<current>mA <meas-date>/<run-date> <run-time>[_<dir>]/
        # e.g.  MySample/15mA 20260326/20260702 105936/
        # The mid folder groups every analysis of one measurement (same
        # current + measurement date); each run gets its own date-time folder.
        # save_dir overrides the parent; save_subdir=False writes directly to it.
        cur = float(current_mA)
        cur_str = (f'{int(round(cur))}mA'
                   if abs(cur - round(cur)) < 1e-9 else f'{cur:g}mA')

        # Measurement date: data file's date sub-folder → YYYYMMDD token in the
        # scanlist name → today.
        meas_date = None
        if first_h5:
            dd = os.path.basename(os.path.dirname(first_h5))
            if re.fullmatch(r'\d{8}', dd):
                meas_date = dd
        if meas_date is None:
            m = re.search(r'(20\d{6})', name)
            meas_date = m.group(1) if m else datetime.datetime.now().strftime('%Y%m%d')

        run_ts = datetime.datetime.now().strftime('%Y%m%d %H%M%S')
        suffix = f'_{direction}' if direction else ''
        mid    = _safe_dirname(f'{cur_str} {meas_date}')
        inner  = f'{run_ts}{suffix}'

        if save_dir is None:
            parent = os.path.join(sample_folder, mid)
        else:
            parent = os.path.abspath(save_dir)
        if save_subdir:
            self.path3 = os.path.join(parent, inner)
        else:
            self.path3 = parent
        os.makedirs(self.path3, exist_ok=True)
        print(f'  Saving plots to: {self.path3}')

    # ── data loading ──────────────────────────────────────────────────────

    def import_data(self, ignorLines=(), data_base_dir=None):
        """Load all sensor channels from the scanlist."""
        if data_base_dir:
            self.data_base_dir = data_base_dir

        self.data = linescan_calc(
            self.scanlist_path,
            direction=self.direction,
            ignorLines=ignorLines,
            data_base_dir=self.data_base_dir,
            x_ch=self.x_ch,
        )
        print(f'  Data keys loaded: {list(self.data.keys())}')
        if 'x' not in self.data:
            raise RuntimeError(
                f'import_data: no position data loaded (x_ch="{self.x_ch}", '
                f'channels in file: {self._detected["all"]}). Check that the '
                f'scan-axis channel exists and that the scanlist has scans of '
                f'both field polarities.')
        return self

    # ── per-scan intensity plot ───────────────────────────────────────────

    def see_intensity(self, ch_var='DC', ignorelines=(), ylim=()):
        """Plot per-scan mean intensity and individual profiles (copper colourmap)."""
        I, var_all = intensity_mean(
            self.scanlist_path, ch_var=ch_var,
            direction=self.direction, ignorLines=ignorelines,
            data_base_dir=self.data_base_dir,
        )
        if len(I) == 0:
            warnings.warn(f'see_intensity: no data for channel "{ch_var}"')
            return self

        x = (self.data['x']
             if (self.data and 'x' in self.data)
             else np.arange(var_all.shape[1]))

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 12),
                                        gridspec_kw={'height_ratios': [1, 3]})
        fig.suptitle(os.path.basename(self.scanlist_path) + '  ' + ch_var)

        ax1.plot(I * 1e3)
        ax1.set_xticks(np.arange(len(I)))
        ax1.grid()
        ax1.set_xlabel('scan number')
        ax1.set_ylabel('mean signal [m-unit]')

        colors = plt.cm.copper(np.linspace(0, 1, len(var_all)))
        for i in range(len(var_all)):
            ax2.plot(x, var_all[i], 'x-', color=colors[i], label=str(i))
        ax2.axhline(0.0, color='r', linestyle='-')
        if ylim:
            ax2.set_ylim(ylim)
        elif len(var_all) <= 20:
            ax2.legend(fontsize=8)
        ax2.set_xlabel(f'x [{self.x_unit}]')
        ax2.grid()
        fname = os.path.join(self.path3, f'intensity_{ch_var}.png')
        _safe_savefig(fname)
        print(f'  Plot saved: {fname}')
        plt.show()
        return self

    # ── edge detection ────────────────────────────────────────────────────

    def get_edges(self, I_ch=None, min_width=4.0):
        """Detect device edges from the reflection channel.

        Tries the innermost-pair then the left/right-half strategy
        (:func:`find_edges_width`).  If the width still comes out below
        ``min_width`` (edge detection latched onto noise), it does **not**
        abort — it warns loudly and falls back to a central 15–85 %
        percentile window so the phase search and fit (which use the Oersted
        edges) still run.  Pass ``min_width=0`` to accept any detected width.
        """
        if I_ch is None:
            I_ch = self._reflec_key

        D = self.data
        if I_ch not in D:
            warnings.warn(f'get_edges: channel "{I_ch}" not in data '
                          f'(available: {list(D.keys())})')
            return self

        reflec = D[I_ch][2]          # [2] = summation (field-averaged)
        mask   = np.argsort(D['x'])
        xsort  = D['x'][mask]
        rsort  = reflec[mask]

        try:
            edges, width = find_edges_width(xsort[5:-5], rsort[5:-5],
                                            min_width=min_width)
        except Exception as e:
            warnings.warn(f'get_edges: find_edges_width failed: {e}')
            edges, width = None, 0.0

        if edges is not None and width < 0:
            edges  = [edges[1], edges[0]]
            width  = -width
        if edges is not None:
            print(f'  Edges: {edges[0]:.2f} – {edges[1]:.2f} {self.x_unit}  '
                  f'width = {width:.2f} {self.x_unit}')

        if edges is None or width < min_width:
            lo, hi = np.percentile(xsort, [15, 85])
            fb = [round(float(lo), 2), round(float(hi), 2)]
            warnings.warn(
                f'get_edges: reflection edge detection unreliable '
                f'(width {width:.2f} {self.x_unit} < {min_width}); falling '
                f'back to the central {fb[0]}–{fb[1]} {self.x_unit} window '
                f'for the phase search. The fit uses the Oersted edges, so '
                f'it is unaffected — but check the "{I_ch}" reflection.')
            print(f'  → edge fallback: phase window {fb[0]}–{fb[1]} '
                  f'{self.x_unit}')
            edges, width = fb, round(fb[1] - fb[0], 2)

        self.edges     = edges
        self.dev_center = float(np.mean(edges))
        self.width     = width

        # Edge visualisation plot
        fig, ax = plt.subplots(figsize=(10, 6))
        ax_d = ax.twinx()
        dr = np.gradient(rsort, xsort)
        ax.plot(xsort, rsort, '-.v', color='navy',   label='reflection')
        ax_d.plot(xsort, dr, '-.v', color='orange', label='1st derivative')
        r_at = np.interp(edges, xsort, rsort)
        ax.scatter(edges, r_at, color='green', s=80, zorder=6)
        mid = float(np.mean(edges))
        ax.annotate(f'{width:.2f} {self.x_unit}',
                    xy=(mid, float(np.interp(mid, xsort, rsort))),
                    color='green', fontsize=12, ha='center')
        ax.grid()
        ax.legend(loc=3)
        ax_d.legend(loc=4)
        fname = os.path.join(self.path3, 'edges.png')
        _safe_savefig(fname)
        print(f'  Plot saved: {fname}')
        plt.show()
        return self

    # ── phase auto-detection ──────────────────────────────────────────────

    def get_theta(self, LI_str=None, do_plot=True):
        """Auto-detect lock-in phase by minimising the imaginary component.

        Saves a diagnostic plot ``phase_search.png`` showing the residual
        imaginary component for both polarities (and for the 2nd harmonic
        when available) — should sit near zero across the device window.
        """
        if self.edges is None:
            self.get_edges()
        D  = self.data
        li = LI_str or self.calc_info.LI_type
        print(f'  Using LIA: {li}')

        has_2nd = (li + 'x2' in D) and (li + 'y2' in D)
        n_axes  = 2 if has_2nd else 1
        if do_plot:
            fig, axes = plt.subplots(1, n_axes, figsize=(6 * n_axes, 4),
                                     squeeze=False)
            axes = axes[0]
        else:
            axes = [None] * n_axes

        t_pos = find_phase(D['x'], D[li+'x1'][4], D[li+'y1'][4],
                           self.edges, 'pos', do_plot=do_plot, ax=axes[0])
        t_neg = find_phase(D['x'], D[li+'x1'][5], D[li+'y1'][5],
                           self.edges, 'neg', do_plot=do_plot, ax=axes[0])
        theta = float(np.mean([t_pos, t_neg]))
        print(f'  theta  (1ω) = {theta:.2f}°  (from pos={t_pos:.2f}°, '
              f'neg={t_neg:.2f}°)')
        # The lock-in phase is instrumental — it must be the same for both
        # field polarities.  A large disagreement means drift, a polarity
        # mix-up in the scanlist, or too little signal in one group.
        if abs(t_pos - t_neg) > 5.0:
            warnings.warn(
                f'get_theta: phase from pos ({t_pos:.2f}°) and neg '
                f'({t_neg:.2f}°) scans differ by '
                f'{abs(t_pos - t_neg):.2f}° (> 5°) — check polarity '
                f'grouping / signal quality before trusting the fit.')
        self.calc_info.theta     = theta       # mean, kept for reference
        self.calc_info.theta_pos = float(t_pos)
        self.calc_info.theta_neg = float(t_neg)
        if do_plot:
            axes[0].axhline(0, color='k', lw=0.5)
            axes[0].set_title(rf'1ω : $\theta$={theta:.2f}°')
            axes[0].set_xlabel(f'x [{self.x_unit}]')
            axes[0].set_ylabel('residual imag (a.u.)')
            axes[0].grid(True); axes[0].legend(fontsize=9)

        if has_2nd:
            t2_pos = find_phase(D['x'], D[li+'x2'][4], D[li+'y2'][4],
                                self.edges, 'pos', do_plot=do_plot,
                                ax=axes[1])
            t2_neg = find_phase(D['x'], D[li+'x2'][5], D[li+'y2'][5],
                                self.edges, 'neg', do_plot=do_plot,
                                ax=axes[1])
            theta2 = float(np.mean([t2_pos, t2_neg]))
            print(f'  theta₂ (2ω) = {theta2:.2f}°  (from pos={t2_pos:.2f}°, '
                  f'neg={t2_neg:.2f}°)')
            if abs(t2_pos - t2_neg) > 5.0:
                warnings.warn(
                    f'get_theta: 2ω phase from pos ({t2_pos:.2f}°) and neg '
                    f'({t2_neg:.2f}°) scans differ by '
                    f'{abs(t2_pos - t2_neg):.2f}° (> 5°).')
            self.calc_info.theta2     = theta2
            self.calc_info.theta2_pos = float(t2_pos)
            self.calc_info.theta2_neg = float(t2_neg)
            if do_plot:
                axes[1].axhline(0, color='k', lw=0.5)
                axes[1].set_title(rf'2ω : $\theta_2$={theta2:.2f}°')
                axes[1].set_xlabel(f'x [{self.x_unit}]')
                axes[1].grid(True); axes[1].legend(fontsize=9)

        if do_plot:
            fname = os.path.join(self.path3, 'phase_search.png')
            _safe_savefig(fname)
            print(f'  Plot saved: {fname}')
            plt.show()
        return self

    # ── evaluate_data ─────────────────────────────────────────────────────

    def evaluate_data(self, phase=None, phase2=None, plot_2axs=False,
                      do_plot='sumdiff', fs=16, reflection=None):
        """Compute Kerr angles and produce standard SOT plots.

        ``do_plot`` modes:
          * ``'sumdiff'``        — 1ω sum/diff with error bars (default)
          * ``'sumdiff2nd'``     — same for 2ω
          * ``'comp_1st_2nd'``   — 1ω and 2ω side-by-side
          * ``'negpos'``         — separate +/− field traces (1ω)
          * ``'realimag'``       — X/Y (real/imag) at + and − field, 1ω
          * ``'realimag2nd'``    — same for 2ω
          * ``'thermoreflectance'`` — −θ²_Oe / R    (2nd harmonic thermoreflectance)
          * ``'findphase'``      — residual imaginary component after rotating
                                    by θ — should sit near zero across the device
        """
        if reflection is None:
            reflection = self._reflec_key

        plotname = (self.calc_info.system + '_'
                    + str(self.calc_info.current) + 'mA_'
                    + self.calc_info.LightPol + '_' + do_plot + '_'
                    + self.calc_info.specific)

        # Phases: an explicit ``phase``/``phase2`` argument applies to both
        # polarities; otherwise each polarity is rotated by its own phase
        # from get_theta (falls back to the mean for pre-get_theta calls).
        ci     = self.calc_info
        theta  = phase  if phase  is not None else ci.theta
        theta2 = phase2 if phase2 is not None else ci.theta2
        if phase is not None:
            th1_pos = th1_neg = float(phase)
        else:
            th1_pos = getattr(ci, 'theta_pos', ci.theta)
            th1_neg = getattr(ci, 'theta_neg', ci.theta)
        if phase2 is not None:
            th2_pos = th2_neg = float(phase2)
        else:
            th2_pos = getattr(ci, 'theta2_pos', ci.theta2)
            th2_neg = getattr(ci, 'theta2_neg', ci.theta2)
        li  = ci.LI_type
        sln = ci.sln
        t1  = theta  * np.pi / 180.0     # mean phase (error propagation)
        t2  = theta2 * np.pi / 180.0
        t1p, t1n = np.deg2rad(th1_pos), np.deg2rad(th1_neg)
        t2p, t2n = np.deg2rad(th2_pos), np.deg2rad(th2_neg)

        D   = self.data
        fac = 1000 if 'sr' in li else 1

        # 1ω — rotate each polarity by its own phase, then form sum/diff.
        # (With equal phases this is identical to rotating sum/diff by θ.)
        theta_pos = (D[li+'x1'][4] * np.cos(t1p) + D[li+'y1'][4] * np.sin(t1p)) * sln
        theta_neg = (D[li+'x1'][5] * np.cos(t1n) + D[li+'y1'][5] * np.sin(t1n)) * sln
        theta_Oe  = (theta_pos + theta_neg) / 2.0
        theta_DL  = (theta_pos - theta_neg) / 2.0
        error_bar = (np.sqrt((D[li+'x1'][3] * np.cos(t1))**2 +
                             (D[li+'y1'][3] * np.sin(t1))**2) * np.abs(sln))
        pos       = D['x']

        # 2ω — only if the channels are present
        has_2nd = (li + 'x2' in D) and (li + 'y2' in D)
        theta2_Oe = theta2_DL = error_bar2 = None
        if has_2nd:
            theta2_pos = (D[li+'x2'][4] * np.cos(t2p) + D[li+'y2'][4] * np.sin(t2p)) * sln
            theta2_neg = (D[li+'x2'][5] * np.cos(t2n) + D[li+'y2'][5] * np.sin(t2n)) * sln
            theta2_Oe  = (theta2_pos + theta2_neg) / 2.0
            theta2_DL  = (theta2_pos - theta2_neg) / 2.0
            error_bar2 = (np.sqrt((D[li+'x2'][3] * np.cos(t2))**2 +
                                  (D[li+'y2'][3] * np.sin(t2))**2) * np.abs(sln))
        elif do_plot in ('sumdiff2nd', 'realimag2nd', 'comp_1st_2nd',
                         'thermoreflectance'):
            warnings.warn(f'evaluate_data: 2nd-harmonic plot "{do_plot}" '
                          f'requested but {li}x2/{li}y2 not in data — '
                          f'falling back to "sumdiff".')
            do_plot = 'sumdiff'

        # ── figure layout per mode ─────────────────────────────────────────
        if do_plot in ('realimag', 'realimag2nd'):
            fig, (ax1, ax3) = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
            plot_2axs = True
        elif do_plot == 'comp_1st_2nd':
            fig, (ax1, ax3) = plt.subplots(1, 2, figsize=(12, 4))
            plot_2axs = True
        elif plot_2axs:
            fig, (ax1, ax3) = plt.subplots(2, 1, figsize=(8, 8))
        else:
            fig, ax1 = plt.subplots(figsize=(6, 4))
            ax3 = None

        # ── select plot type ───────────────────────────────────────────────
        if do_plot == 'negpos':
            ax1.plot(pos, theta_pos * fac, '-.v', color='red',  label='pos')
            ax1.plot(pos, theta_neg * fac, '-.v', color='blue', label='neg')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]', fontsize=fs)

        elif do_plot == 'sumdiff':
            if plot_2axs:
                ax3.plot(pos, theta_Oe * fac, '-.v', color='black', label='sum')
                ax3.errorbar(pos, theta_Oe * fac, yerr=error_bar, color='black')
                ax3.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]', fontsize=fs)
            else:
                ax1.plot(pos, theta_Oe * fac, '-.v', color='black', label='sum')
                ax1.errorbar(pos, theta_Oe * fac, yerr=error_bar, color='black')
            ax1.plot(pos, theta_DL * fac, '-.v', color='green', label='diff')
            ax1.errorbar(pos, theta_DL * fac, yerr=error_bar, color='green')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]', fontsize=fs)

        elif do_plot == 'sumdiff2nd':
            ax1.plot(pos, theta2_Oe * fac, '-.v', color='black', label='sum')
            ax1.errorbar(pos, theta2_Oe * fac, yerr=error_bar2, color='black')
            ax1.plot(pos, theta2_DL * fac, '-.v', color='green', label='diff')
            ax1.errorbar(pos, theta2_DL * fac, yerr=error_bar2, color='green')
            ax1.set_ylabel(r'$\theta_{K}^{2\omega}$ [nrad]', fontsize=fs)

        elif do_plot == 'comp_1st_2nd':
            ax1.plot(pos, theta_Oe * fac, '-.v', color='black', label='sum')
            ax1.errorbar(pos, theta_Oe * fac, yerr=error_bar, color='black')
            ax1.plot(pos, theta_DL * fac, '-.v', color='green', label='diff')
            ax1.errorbar(pos, theta_DL * fac, yerr=error_bar, color='green')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]', fontsize=fs)
            ax3.plot(pos, theta2_Oe * fac, '-.v', color='black', label='sum')
            ax3.errorbar(pos, theta2_Oe * fac, yerr=error_bar2, color='black')
            ax3.plot(pos, theta2_DL * fac, '-.v', color='green', label='diff')
            ax3.errorbar(pos, theta2_DL * fac, yerr=error_bar2, color='green')
            ax3.set_ylabel(r'$\theta_{K}^{2\omega}$ [nrad]', fontsize=fs)

        elif do_plot == 'thermoreflectance':
            therm = -theta2_Oe / np.where(D[reflection][2] == 0,
                                           np.nan, D[reflection][2])
            ax1.errorbar(pos, therm, yerr=np.abs(error_bar2 / D[reflection][2]),
                         marker='v', color='r')
            ax1.set_ylabel(r'$-\theta^{2\omega}_{K}\;/\;R$', fontsize=fs)

        elif do_plot == 'realimag':
            ax3.plot(pos, D[li+'x1'][5] * sln, '-.v', color='b')
            ax3.plot(pos, D[li+'y1'][5] * sln, '-.v', color='r')
            ax1.plot(pos, D[li+'x1'][4] * sln, '-.o', color='b', label='real')
            ax1.plot(pos, D[li+'y1'][4] * sln, '-.o', color='r', label='imag')
            ax1.set_title('R$^+$'); ax3.set_title('R$^-$')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]', fontsize=fs)

        elif do_plot == 'realimag2nd':
            ax3.plot(pos, D[li+'x2'][5] * sln, '-.v', color='b')
            ax3.plot(pos, D[li+'y2'][5] * sln, '-.v', color='r')
            ax1.plot(pos, D[li+'x2'][4] * sln, '-.o', color='b', label='real')
            ax1.plot(pos, D[li+'y2'][4] * sln, '-.o', color='r', label='imag')
            ax1.set_title('R$^+$'); ax3.set_title('R$^-$')
            ax1.set_ylabel(r'$\theta_{K}^{2\omega}$ [nrad]', fontsize=fs)

        elif do_plot == 'findphase':
            # Residual imaginary component after rotating by θ (1ω).  Should
            # be near zero across the device if the phase is correct.
            imag_pos = (-D[li+'x1'][4] * np.sin(t1p)
                        + D[li+'y1'][4] * np.cos(t1p))
            imag_neg = (-D[li+'x1'][5] * np.sin(t1n)
                        + D[li+'y1'][5] * np.cos(t1n))
            ax1.plot(pos, imag_pos, '-.v', color='cyan',
                     label='imag pos (→ 0)')
            ax1.plot(pos, imag_neg, '-.v', color='k',
                     label='imag neg (→ 0)')
            ax1.set_ylabel(r'residual imag, $\theta=$' f'{theta:.2f}°',
                           fontsize=fs)

        else:
            warnings.warn(f'evaluate_data: unknown do_plot="{do_plot}"; '
                          f'using sumdiff')
            ax1.plot(pos, theta_Oe * fac, '-.v', color='black', label='sum')
            ax1.plot(pos, theta_DL * fac, '-.v', color='green', label='diff')

        # ── common axes decoration ─────────────────────────────────────────
        ax1.legend(fontsize=fs, loc=1)
        ax1.grid(True)
        ax1.axhline(y=0, color='k')
        ax2 = ax1.twinx()
        ax2.plot(D['x'], D[reflection][2], color='firebrick', label=r'I$_{FL}$')

        if plot_2axs and ax3 is not None:
            ax3.grid(True)
            ax4 = ax3.twinx()
            ax4.plot(D['x'], D[reflection][2], color='firebrick',
                     label=r'I$_{FL}$')
            ax3.axhline(y=0, color='k')
            ax3.set_xlabel(f'x [{self.x_unit}]', fontsize=fs)
            ax3.tick_params(axis='both', which='major', labelsize=fs - 2)
            if do_plot not in ('realimag', 'realimag2nd'):
                ax3.legend(fontsize=fs, loc=1)
            ax4.legend(fontsize=fs, loc=4)

        ax1.set_xlabel(f'x [{self.x_unit}]', fontsize=fs)
        ax1.tick_params(axis='both', which='major', labelsize=fs - 2)
        ax2.legend(fontsize=fs, loc=4)
        fname = os.path.join(self.path3, plotname + '.png')
        _safe_savefig(fname, pad_inches=0.1)
        print(f'  Plot saved: {fname}')
        plt.show()

        idx = np.argsort(pos)
        self.analyzed_data = {
            'x':        pos[idx],
            'intR':     D[reflection][2][idx],
            'sum':      theta_Oe[idx],
            'diff':     theta_DL[idx],
            'pos':      theta_pos[idx],
            'neg':      theta_neg[idx],
            'errorbar': error_bar[idx],
        }
        if has_2nd:
            self.analyzed_data['sum_2w']  = theta2_Oe[idx]
            self.analyzed_data['diff_2w'] = theta2_DL[idx]
            self.analyzed_data['err_2w']  = error_bar2[idx]
        return self

    # ── eval_width_and_fit ────────────────────────────────────────────────

    def eval_width_and_fit(self, current_coefficient2=0.99, fit_edge_offset=5,
                           nice_plot=False, use_Oe_as_edges=True):
        """Fit Oersted (log) and DL (constant) contributions; compute SOT fields.

        Fit-window edges: with ``use_Oe_as_edges=True`` (default) the device
        width is taken from the raw min/max of the Oersted sum, matching the
        original analysis so the DL-field magnitude agrees. The conversion is
        identical to the reference: ``conconst = A·width/(2·Ic)·10`` (nrad/mT)
        and ``conDL = const/conconst`` (mT).
        """
        mpl.rcParams['font.size'] = 16

        def Log_fit(x, A, A0, width):
            return A0 + A * np.log((width - x) / x)

        def Const_fit(x, y0):
            return y0

        D        = self.analyzed_data
        plotname = (self.calc_info.system + '_' + str(self.calc_info.current)
                    + 'mA_' + self.calc_info.LightPol)

        position  = np.array(D['x'])
        reflex    = np.array(D['intR'])
        theta_Oe  = np.array(D['sum'])
        theta_DL  = np.array(D['diff'])
        error_bar = np.array(D['errorbar'])

        # Sort by position
        idx = np.argsort(position)
        position, reflex, theta_Oe, theta_DL, error_bar = (
            position[idx], reflex[idx],
            theta_Oe[idx], theta_DL[idx], error_bar[idx])

        # ── find fitting window edges ──────────────────────────────────────
        if use_Oe_as_edges:
            print('  Using Oersted sum to find fit edges.')
            # Match the original analysis exactly: the fit edges are the raw
            # min/max of the Oersted sum (no smoothing). Smoothing here would
            # shift the picked edge by a grid point, changing the fit width
            # and hence the DL-field magnitude — see the note below.
            x1 = position[np.argmin(theta_Oe)]
            x2 = position[np.argmax(theta_Oe)]
        elif self.edges:
            x1, x2 = self.edges
        else:
            self.get_edges()
            x1, x2 = (self.edges if self.edges else (position[0], position[-1]))

        width = x2 - x1
        if width < 0:
            x1, x2 = x2, x1
            width  = -width
        print(f'  Fit edges: x1={x1:.2f}, x2={x2:.2f}, width={width:.2f} {self.x_unit}')

        position = position - x1    # shift: left edge → 0
        width    = round(width, 1)

        Ic  = self.calc_info.current * current_coefficient2   # actual total current (mA)

        # ── build mask ────────────────────────────────────────────────────
        mask = (position > 0) & (position < width)
        true_idx = np.where(mask)[0]
        if len(true_idx) > 2 * fit_edge_offset:
            mask[true_idx[:fit_edge_offset]]  = False
            mask[true_idx[-fit_edge_offset:]] = False
        print(f'  {mask.sum()} points in fit window')

        if mask.sum() < 3:
            warnings.warn('eval_width_and_fit: not enough interior points for fit')
            return self

        pos_fit  = position[mask]
        pos_mask = position[(~mask) & (position > 0) & (position < width)]

        # Weighted fit only when real error bars exist.  With one scan per
        # polarity the SEM is zero everywhere; clamping zeros to 1e-12 and
        # using absolute_sigma=True would produce meaningless (absurdly
        # small) parameter errors, so fall back to an unweighted fit whose
        # errors are scaled from the fit residuals instead.
        weighted = bool(np.any(error_bar[mask] > 0))
        if weighted:
            fit_kw = dict(sigma=np.maximum(error_bar[mask], 1e-12),
                          absolute_sigma=True)
        else:
            warnings.warn('eval_width_and_fit: all error bars are zero '
                          '(single scan per polarity?) — using an '
                          'unweighted fit with residual-scaled errors.')
            fit_kw = {}

        # DL constant fit
        try:
            pConst, covConst = curve_fit(Const_fit, pos_fit, theta_DL[mask],
                                          **fit_kw)
            errConst = np.sqrt(np.diag(covConst))
        except Exception as e:
            warnings.warn(f'DL const fit failed: {e}')
            pConst   = [float(np.mean(theta_DL[mask]))]
            errConst = [float(np.std(theta_DL[mask]))]

        # Oe log fit
        try:
            pLog, covLog = curve_fit(
                lambda x, A, A0: Log_fit(x, A, A0, width),
                pos_fit, theta_Oe[mask],
                **fit_kw)
            errLog = np.sqrt(np.diag(covLog))
        except Exception as e:
            warnings.warn(f'Oe log fit failed: {e}')
            pLog   = [0.0, float(np.mean(theta_Oe[mask]))]
            errLog = [0.0, 0.0]

        const_array = [pConst[0]] * len(pos_fit)

        # Conversion constant and DL field (in mT)
        conconst = pLog[0] * width / (2 * Ic) * 10   # nrad/mT
        if conconst and not np.isnan(conconst):
            conDL = pConst[0] / conconst
            drel  = (abs(errConst[0] / pConst[0]) if pConst[0] else 0)
            drel += (abs(errLog[0]   / pLog[0])   if pLog[0]  else 0)
            conDL_error = abs(conDL) * drel
        else:
            conDL = conDL_error = np.nan

        print(f'  Width       = {width:.2f} {self.x_unit}')
        print(f'  conconst    = {conconst:.4g} nrad/mT')
        print(f'  DL-field    = ({conDL:.4g} ± {conDL_error:.4g}) mT')
        self.fit_DL_mT       = conDL
        self.fit_DL_error_mT = conDL_error
        self.fit_width_um    = float(width)

        # ── SOT / spin-Hall efficiency ─────────────────────────────────────
        #   ξ_DL = (2e/ℏ) · μ₀ Ms t_FM · (B_DL/μ₀) / J
        #        = (2e/ℏ) · Ms t_FM B_DL / J          (μ₀ cancels)
        # with the charge current density in the stack
        #   J = I / (w · t_stack)         [A/m²]
        # I is the (coefficient-corrected) total current Ic; w is the fitted
        # device width; t_stack / t_FM the stack / ferromagnet thicknesses.
        # Requires Ms [A/m], t_stack [nm] (args) and t_FM [nm] (arg or the
        # HDF5 fm_thickness_nm metadata); otherwise skipped.
        ci = self.calc_info
        xi_DL = xi_DL_err = J = np.nan
        if (ci.Ms and ci.t_stack_nm and ci.t_fm_nm
                and np.isfinite(conDL) and width > 0):
            w_m       = width * 1e-6
            t_stack_m = ci.t_stack_nm * 1e-9
            t_fm_m    = ci.t_fm_nm * 1e-9
            I_A       = Ic * 1e-3                       # Ic = current·coeff2 (mA→A)
            J         = I_A / (w_m * t_stack_m)         # A/m²
            B_DL_T    = conDL * 1e-3                    # mT → T
            pref      = (2.0 * _E_CHARGE / _H_BAR) * ci.Ms * t_fm_m / J
            xi_DL     = pref * B_DL_T
            xi_DL_err = abs(pref * conDL_error * 1e-3)  # from B_DL error only
            print(f'  J           = {J:.4g} A/m²  '
                  f'(Ic={I_A*1e3:.4g} mA, w={width:.2f} µm, '
                  f't_stack={ci.t_stack_nm:g} nm)')
            print(f'  ξ_DL        = {xi_DL:.4g} ± {xi_DL_err:.2g}  '
                  f'(Ms={ci.Ms:.4g} A/m, t_FM={ci.t_fm_nm:g} nm)')
        elif ci.Ms or ci.t_stack_nm or ci.t_fm_nm:
            missing = [n for n, v in (('Ms', ci.Ms),
                                      ('t_stack_nm', ci.t_stack_nm),
                                      ('t_fm_nm', ci.t_fm_nm)) if not v]
            warnings.warn(f'eval_width_and_fit: ξ_DL not computed — missing '
                          f'{", ".join(missing)}.')
        self.xi_DL     = xi_DL
        self.xi_DL_err = xi_DL_err
        self.J_A_per_m2 = J

        # ── main fit plot ─────────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(12, 8))
        plt.title(f'{self.calc_info.LightPol}_{self.calc_info.current} mA', pad=100)

        ax.plot(position, theta_DL, '-.v', color='navy',     label='DL')
        ax.errorbar(position, theta_DL, yerr=error_bar,      color='navy')
        ax.plot(pos_fit, const_array, color='deepskyblue', linewidth=4,
                label=f'fit const,  const={pConst[0]:.4g}±{errConst[0]:.4g}')

        ax.plot(position, theta_Oe, '-.v', color='firebrick', label='Oe')
        ax.errorbar(position, theta_Oe, yerr=error_bar,       color='firebrick')
        ax.scatter(pos_mask,
                   np.ones(len(pos_mask)) * float(np.max(theta_DL)) * 0.8,
                   color='r', label='Not used for fit')

        try:
            ax.plot(pos_fit, Log_fit(pos_fit, pLog[0], pLog[1], width),
                    color='orange', linewidth=4,
                    label=(f'fit A0+A·ln((w−x)/x),  '
                           f'A0={pLog[1]:.2g}  A={pLog[0]:.2g}±{errLog[0]:.3g}'))
        except Exception:
            pass

        plt.plot([], [], ' ',
                 label=(f'Conversion coeff = {conconst:.4g} nrad/mT\n'
                        f'DL-field = ({conDL:.4g} ± {conDL_error:.4g}) mT'))

        ax.yaxis.major.formatter._useMathText = True
        ax.xaxis.major.formatter._useMathText = True
        ax.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
        plt.xlabel(f'y [{self.x_unit}]')
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.2),
                  ncol=2, fancybox=True, shadow=True, fontsize=14)
        ax.grid(True)
        ax.vlines([0, width],
                  ymin=float(np.min(theta_Oe)), ymax=float(np.max(theta_Oe)),
                  color='g')
        ax2 = ax.twinx()
        ax2.set_ylabel(r'$R$ [a.u.]')
        ax2.plot(position, reflex, color='firebrick', label='reflection')

        fname = os.path.join(self.path3, f'fit_{plotname}.png')
        _safe_savefig(fname, tight=False)
        print(f'  Plot saved: {fname}')
        plt.show()

        # Update analyzed_data with shifted x
        self.analyzed_data['x']        = position
        self.analyzed_data['sum']      = theta_Oe
        self.analyzed_data['diff']     = theta_DL
        self.analyzed_data['errorbar'] = error_bar

        # ── Persist results: CSV of analyzed data + JSON of fit summary ───
        self._save_analyzed_csv(position, reflex, theta_Oe, theta_DL,
                                error_bar, plotname)
        self._save_results_json(
            width=width, x1_raw=float(x1), x2_raw=float(x2),
            n_fit_points=int(mask.sum()),
            DL_const=float(pConst[0]), DL_const_err=float(errConst[0]),
            Oe_A=float(pLog[0]), Oe_A_err=float(errLog[0]),
            Oe_A0=float(pLog[1]), conconst_nrad_per_mT=float(conconst),
            DL_field_mT=float(conDL), DL_field_err_mT=float(conDL_error),
            current_mA=float(self.calc_info.current),
            xi_DL=float(xi_DL), xi_DL_err=float(xi_DL_err),
            J_A_per_m2=float(J),
            Ms_A_per_m=(float(ci.Ms) if ci.Ms else None),
            t_stack_nm=(float(ci.t_stack_nm) if ci.t_stack_nm else None),
            t_fm_nm=(float(ci.t_fm_nm) if ci.t_fm_nm else None),
            current_coefficient2=float(current_coefficient2),
            sln=float(self.calc_info.sln),
            theta_deg=float(self.calc_info.theta),
            theta_pos_deg=float(getattr(self.calc_info, 'theta_pos',
                                        self.calc_info.theta)),
            theta_neg_deg=float(getattr(self.calc_info, 'theta_neg',
                                        self.calc_info.theta)),
            theta2_deg=float(self.calc_info.theta2),
            use_Oe_as_edges=bool(use_Oe_as_edges),
            fit_edge_offset=int(fit_edge_offset),
        )

        if nice_plot:
            self._nice_plot(position, theta_Oe, theta_DL, error_bar,
                            reflex, pos_fit, pLog, pConst, width, plotname)
        return self

    # ── Persistence helpers ───────────────────────────────────────────────

    def _save_analyzed_csv(self, position, reflex, theta_Oe, theta_DL,
                            error_bar, plotname):
        """Write analyzed columns to CSV (semicolon-separated, like the old
        ``analyzed_to_csv`` in Jakub_methods.py).  Adds 2ω columns when
        ``analyzed_data`` has them.  No pandas/xlsx dependency."""
        ad   = self.analyzed_data
        have2 = ('sum_2w' in ad and 'diff_2w' in ad and 'err_2w' in ad
                 and ad['sum_2w'].shape == position.shape)
        header = [f'x [{self.x_unit}]', 'R [a.u.]',
                  'theta_Oe [nrad]', 'error [nrad]',
                  'theta_DL [nrad]', 'error [nrad]']
        if have2:
            header += ['theta_Oe_2w [nrad]', 'error_2w [nrad]',
                       'theta_DL_2w [nrad]', 'error_2w [nrad]']
        fname = os.path.join(self.path3, f'analyzed_{plotname}.csv')
        try:
            with open(fname, 'w', newline='') as f:
                w = csv.writer(f, delimiter=';')
                w.writerow(header)
                for i in range(len(position)):
                    row = [f'{position[i]:.6g}', f'{reflex[i]:.6g}',
                           f'{theta_Oe[i]:.6g}', f'{error_bar[i]:.6g}',
                           f'{theta_DL[i]:.6g}', f'{error_bar[i]:.6g}']
                    if have2:
                        row += [f'{ad["sum_2w"][i]:.6g}',
                                f'{ad["err_2w"][i]:.6g}',
                                f'{ad["diff_2w"][i]:.6g}',
                                f'{ad["err_2w"][i]:.6g}']
                    w.writerow(row)
            print(f'  CSV  saved: {fname}')
        except Exception as e:
            warnings.warn(f'_save_analyzed_csv: {e}')

    def _save_results_json(self, **fields):
        """Dump all fit results + analysis parameters to results.json so the
        run is reproducible from the saved folder alone."""
        out = {
            'timestamp':          datetime.datetime.now().isoformat(),
            'scanlist':           self.scanlist_path,
            'direction':          self.direction,
            'sample':             self.sample_name,
            'sample_folder':      self.sample_folder,
            'data_base_dir':      self.data_base_dir,
            'LightPol':           self.calc_info.LightPol,
            'LI_type':            self.calc_info.LI_type,
            'edges_raw':          (list(self.edges) if self.edges else None),
            'dev_center':         self.dev_center,
            'width':              self.width,
            'fit_DL_mT':          self.fit_DL_mT,
            'fit_DL_error_mT':    self.fit_DL_error_mT,
            'sln':                getattr(self.calc_info, 'sln', None),
            'sln_source':         getattr(self.calc_info, 'sln_source', None),
            'bd_calibration_mV':  getattr(self.calc_info, 'bd_calibration_mV', None),
            'device_id':          getattr(self.calc_info, 'device_id', None),
            'r_4wire_ohm':        getattr(self.calc_info, 'r_4wire_ohm', None),
            'r_2wire_ohm':        getattr(self.calc_info, 'r_2wire_ohm', None),
            'h5_metadata':        {k: _json_safe(v)
                                    for k, v in self.h5_meta.items()},
        }
        out.update({k: _json_safe(v) for k, v in fields.items()})
        fname = os.path.join(self.path3, 'results.json')
        try:
            with open(fname, 'w') as f:
                json.dump(out, f, indent=2, default=_json_safe)
            print(f'  JSON saved: {fname}')
        except Exception as e:
            warnings.warn(f'_save_results_json: {e}')

    def _nice_plot(self, position, theta_Oe, theta_DL, error_bar, reflex,
                   pos_fit, pLog, pConst, width, plotname):
        def Log_fit(x, A, A0, width):
            return A0 + A * np.log((width - x) / x)
        fs = 30
        fig, ax = plt.subplots(figsize=(11, 8))
        ax.plot(position, theta_DL, '-.v', color='green', label=r'$\theta_{DL}$')
        ax.errorbar(position, theta_DL, yerr=error_bar, color='green')
        ax.plot(pos_fit, [pConst[0]] * len(pos_fit), color='lightgreen',
                linewidth=4, label='fit const.')
        ax.plot(position, theta_Oe, '-.v', color='k', label=r'$\theta_{Oe}$')
        ax.errorbar(position, theta_Oe, yerr=error_bar, color='k')
        try:
            ax.plot(pos_fit, Log_fit(pos_fit, pLog[0], pLog[1], width),
                    color='grey', linewidth=4, label=r'fit $A\ln\frac{w-x}{x}$')
        except Exception:
            pass
        ax.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]', fontsize=fs)
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.3),
                  ncol=2, fancybox=True, shadow=True, fontsize=fs)
        plt.xlabel(f'y [{self.x_unit}]', fontsize=fs)
        ax.grid(True)
        ax2 = ax.twinx()
        ax2.set_ylabel(r'$R$ [a.u.]', fontsize=fs)
        ax2.plot(position, reflex, color='firebrick', label='reflection')
        ax.tick_params(axis='both', which='major', labelsize=fs)
        ax2.tick_params(axis='both', which='major', labelsize=fs)
        fname = os.path.join(self.path3, f'nice_plot_{plotname}.png')
        _safe_savefig(fname, tight_kw={'pad': 0.5}, bbox_inches='tight')
        print(f'  Plot saved: {fname}')
        plt.show()

    # ── pipeline entry points ─────────────────────────────────────────────

    @staticmethod
    def _resolve_see_channels(see_channels, detected):
        """Return the effective see_channels list, auto-detecting when None."""
        if see_channels is not None:
            return see_channels
        candidates = []
        if detected.get('intensity'):
            candidates.append(detected['intensity'])
        if detected.get('lockin'):
            x1s = [c for c in detected['lockin'] if c.lower().endswith('x1')]
            candidates.append(x1s[0] if x1s else detected['lockin'][0])
        print(f'  see_channels auto-detected: {tuple(candidates)}')
        return tuple(candidates)

    @staticmethod
    def import_analyze_SOT(scanlist_path, see_channels=None,
                            direction=None, ignorLines=(), fit_edge_offset=5,
                            force_theta_0=False, **kwargs):
        """Full SOT analysis pipeline for a single scan direction.

        Equivalent to ``analyze_SHE_OHE.import_analyze_SOT`` from Jakub_methods.py.
        Pass ``see_channels=None`` (default) to auto-detect from the HDF5 file.
        """
        res = analyze_SOT(scanlist_path, direction=direction, **kwargs)
        see_channels = analyze_SOT._resolve_see_channels(see_channels, res._detected)
        res.import_data(ignorLines=ignorLines)

        for ch in see_channels:
            res.see_intensity(ch_var=ch, ignorelines=ignorLines)

        res.get_theta()
        if force_theta_0:
            res.calc_info.theta = 0.0

        res.evaluate_data(do_plot='sumdiff')
        res.evaluate_data(do_plot='negpos')
        res.evaluate_data(do_plot='realimag')
        res.eval_width_and_fit(fit_edge_offset=fit_edge_offset, nice_plot=True)
        return res

    @staticmethod
    def import_analyze_both(scanlist_path, see_channels=None,
                             ignorLines=(), fit_edge_offset=5, **kwargs):
        """Run the full pipeline for each direction available in the scanlist.

        Returns ``(res_trace, res_retrace)``.  Either element is ``None``
        when that direction isn't in the scanlist.  For Green/IR scanlists
        with no trace/retrace markers, runs once with ``direction=None``
        and returns ``(res, None)``.

        Pass ``see_channels=None`` (default) to auto-detect intensity/lock-in
        channels from the HDF5 file.
        """
        data_base_dir = kwargs.get('data_base_dir')
        dirs = detect_directions(scanlist_path, data_base_dir=data_base_dir)
        print(f'  Directions detected in scanlist: '
              f'{sorted(dirs) if dirs else "none (legacy single-direction)"}')

        if not dirs:
            print('=' * 60 + '\n  SINGLE DIRECTION\n' + '=' * 60)
            res = analyze_SOT.import_analyze_SOT(
                scanlist_path, see_channels=see_channels, direction=None,
                ignorLines=ignorLines, fit_edge_offset=fit_edge_offset, **kwargs)
            return res, None

        def _run(direction):
            # A failure in one direction must not lose the other's result —
            # but never fail silently: print the full traceback so the cause
            # is visible in the console.
            try:
                return analyze_SOT.import_analyze_SOT(
                    scanlist_path, see_channels=see_channels,
                    direction=direction, ignorLines=ignorLines,
                    fit_edge_offset=fit_edge_offset, **kwargs)
            except Exception as e:
                import traceback
                print('=' * 60)
                print(f'  !! {direction.upper()} analysis FAILED: '
                      f'{type(e).__name__}: {e}')
                traceback.print_exc()
                print('=' * 60)
                warnings.warn(f'import_analyze_both: {direction} analysis '
                              f'failed: {e}')
                return None

        res_trace = res_retrace = None
        if 'trace' in dirs:
            print('=' * 60 + '\n  TRACE\n' + '=' * 60)
            res_trace = _run('trace')
        if 'retrace' in dirs:
            print('=' * 60 + '\n  RETRACE\n' + '=' * 60)
            res_retrace = _run('retrace')
        return res_trace, res_retrace


# ---------------------------------------------------------------------------
# Backwards-compatibility aliases used by existing measurement scripts.
# The primary names are setup-neutral (the module analyses Green, IR and
# Cryo data alike); the old *_cryo names came from the first port.
# ---------------------------------------------------------------------------
analyze_cryo          = analyze_SOT
SambaSOTAnalysis      = analyze_SOT
data_calculation_cryo = data_calculation
linescan_calc_cryo    = linescan_calc
intensity_mean_cryo   = intensity_mean
