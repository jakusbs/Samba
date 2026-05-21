"""
analyze_samba.py  —  SOT/MOKE analysis for SAMBA Cryo HDF5 data.
Based on analysis_samba.py by Tobias Goldenberg (ETH Zürich, 2026).
Adapted for the Cryo setup: /data/ HDF5 group, absolute scanlist paths,
trace/retrace direction support, Linux-compatible saving.

Channel mapping (auto):
    ZI_x1  → zix1      ZI_y1  → ziy1
    ZI_x2  → zix2      ZI__y2 → ziy2   (handles double-underscore typo)
    DC/Mon → FL         (reflection / focus-laser equivalent)
    actuator_x → 'x'   (already in µm in Cryo HDF5)
"""

import os
import re
import datetime
import warnings

import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from scipy.optimize import curve_fit, minimize
from scipy import interpolate, signal
import h5py


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


# ---------------------------------------------------------------------------
# HDF5 I/O
# ---------------------------------------------------------------------------

def data_load(filename, data_channel):
    """Load one channel from a SAMBA HDF5 file.

    Supports three formats:
    - Cryo / new SAMBA : /data/<channel>
    - Green/IR SAMBA   : /measurement/<channel>  (no scan_X groups)
    - Old scan-server  : /scan_X/measurement/<channel>
    """
    with h5py.File(filename, 'r') as f:

        # ── Cryo format ─────────────────────────────────────────────────
        if 'data' in f and isinstance(f['data'], h5py.Group):
            if data_channel in f['data']:
                data = np.array(f['data'][data_channel], dtype=float)
            else:
                warnings.warn(f'data_load: "{data_channel}" not found in {filename}')
                return np.zeros(1)

        # ── Green/IR new SAMBA format ────────────────────────────────────
        elif ('measurement' in f
              and isinstance(f['measurement'], h5py.Group)
              and not any(k.startswith('scan_') for k in f.keys())):
            if data_channel in f['measurement']:
                data = np.array(f['measurement'][data_channel], dtype=float)
            else:
                warnings.warn(f'data_load: "{data_channel}" not found in {filename}')
                return np.zeros(1)

        # ── Old scan_X format ────────────────────────────────────────────
        else:
            scans = list(f.keys())
            if not scans:
                return np.zeros(1)
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
                return np.zeros(1)
            data /= len(scans)

    if data.ndim == 2 and data.shape[0] == 1:
        data = data.reshape(-1)

    # Spike removal (1-D only)
    if data.ndim == 1 and len(data) > 3:
        g = np.gradient(data)
        lim = np.mean(np.abs(g))
        for i in range(1, len(data) - 1):
            if (np.abs(g[i - 1]) >= 10 * lim
                    and np.abs(g[i + 1]) >= 10 * lim
                    and np.sign(g[i - 1]) == -np.sign(g[i + 1])):
                data[i] = np.nan
    return data


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
                if os.path.exists(localfile):
                    filename = localfile
                elif data_base_dir:
                    alt = os.path.join(data_base_dir, os.path.basename(localfile))
                    if os.path.exists(alt):
                        filename = alt
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

        # Cryo / new SAMBA
        if 'data' in f and isinstance(f['data'], h5py.Group):
            grp = f['data']
        elif 'measurement' in f and isinstance(f['measurement'], h5py.Group):
            grp = f['measurement']
        else:
            grp = {}

        for name in grp:
            if isinstance(grp[name], h5py.Dataset):
                print(f'    {name}')
                res[name] = name

    return res, meta


def nan_helper(y):
    return np.isnan(y), lambda z: z.nonzero()[0]


# ---------------------------------------------------------------------------
# Edge detection & phase optimisation
# ---------------------------------------------------------------------------

def find_edges_width(position, reflex):
    """Find device edges from reflection profile using spline + derivative peaks."""
    def derivatives(x, y):
        h = x[1] - x[0]
        dy  = [(y[i + 1] - y[i - 1]) / (2 * h)          for i in range(1, len(x) - 1)]
        ddy = [(y[i + 1] - 2 * y[i] + y[i - 1]) / (h * h) for i in range(1, len(x) - 1)]
        return list(x[1:-1]), dy, ddy

    tck = interpolate.splrep(position, reflex, s=0)
    step = (position[1] - position[0]) / 10.0
    pos_i = np.arange(position[0], position[-1], step)
    ref_i = interpolate.splev(pos_i, tck, der=0)

    newpos, dy, _ = derivatives(pos_i, ref_i)
    newpos = np.array(newpos)
    dy = np.array(dy)

    threshold = 0.3 * np.max(np.abs(dy))
    min_dist  = max(10, len(dy) // 50)

    neg_idx, _ = signal.find_peaks(-dy, height=threshold, distance=min_dist)
    pos_idx, _ = signal.find_peaks( dy, height=threshold, distance=min_dist)

    if len(neg_idx) > 0 and len(pos_idx) > 0:
        left_idx  = neg_idx[np.argmax(neg_idx)]
        right_idx = pos_idx[np.argmin(pos_idx)]
        if left_idx >= right_idx:           # reversed scan / flipped polarity
            left_idx  = pos_idx[np.argmax(pos_idx)]
            right_idx = neg_idx[np.argmin(neg_idx)]
    elif len(neg_idx) > 0:
        s = np.sort(neg_idx)
        left_idx, right_idx = s[0], s[-1]
    elif len(pos_idx) > 0:
        s = np.sort(pos_idx)
        left_idx, right_idx = s[0], s[-1]
    else:
        left_idx  = int(np.argmin(dy))
        right_idx = int(np.argmax(dy))
        if left_idx > right_idx:
            left_idx, right_idx = right_idx, left_idx

    x1 = round(float(newpos[left_idx]),  2)
    x2 = round(float(newpos[right_idx]), 2)
    return [x1, x2], round(x2 - x1, 2)


def find_phase(x, x1_data, y1_data, edges, ch, do_plot=False):
    """Find lock-in phase offset that minimises imaginary component inside device."""
    mask = (x >= edges[0]) & (x <= edges[1])

    def min_imag(theta, x, x1, y1, mask):
        theta_rad = theta * np.pi / 180.0
        return np.std((-x1 * np.sin(theta_rad) + y1 * np.cos(theta_rad))[mask])

    result = minimize(min_imag, 0, args=(x, x1_data, y1_data, mask),
                      method='Nelder-Mead')
    theta = float(result.x[0])
    if do_plot:
        theta_rad = theta * np.pi / 180.0
        imag = -x1_data * np.sin(theta_rad) + y1_data * np.cos(theta_rad)
        plt.scatter(x[mask], imag[mask], label=f'min imag: {ch}')
    return theta


# ---------------------------------------------------------------------------
# Channel name mapping
# ---------------------------------------------------------------------------

_SKIP_CH = {'actuator_x_setpoint', 'time', 'Field', 'Temperature'}


def _map_channel_name(ch_name):
    """Map a SAMBA Cryo channel name to an analysis-dict key.

    ``ZI_x1`` → ``zix1``,  ``ZI__y2`` → ``ziy2``  (handles double-underscore)
    ``DC`` / ``Mon`` → ``FL``
    """
    if ch_name.upper().startswith('ZI'):
        suffix = ch_name[2:].lstrip('_')   # strip all leading underscores
        return 'zi' + suffix.lower()        # 'zix1', 'ziy1', 'zix2', 'ziy2', …

    lower = ch_name.lower()
    if lower in ('dc', 'fl', 'mon'):
        return 'FL'
    if lower in ('field', 'temperature', 'time'):
        return lower
    return lower.replace(' ', '_')


# ---------------------------------------------------------------------------
# Per-channel data loading: scan file → pos/neg average
# ---------------------------------------------------------------------------

def _resolve_path(localfile, data_base_dir=None):
    if os.path.exists(localfile):
        return localfile
    if data_base_dir:
        alt = os.path.join(data_base_dir, os.path.basename(localfile))
        if os.path.exists(alt):
            return alt
    return None


def data_calculation_cryo(scanlist_path, ch_x='actuator_x', ch_var='ZI_x1',
                           direction=None, ignorLines=(),
                           data_base_dir=None, median=False):
    """Load one data channel from all scans in a SAMBA Cryo scanlist.

    Groups scans by  effective sign = relay_sign × sign(field_T).

    Returns ``[x, diff, sum, std, res_pos, res_neg, n_pos]`` — the same
    7-element format as ``data_calculation_SOT`` / ``data_calculation_new``.
    """
    first_scan = first_pos = first_neg = True
    var_pos = var_neg = x = None
    n_pos = n_neg = 0
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
            if len(parts) < 3:
                continue

            localfile = parts[0].strip()
            bname = os.path.basename(localfile)

            if direction == 'trace'   and '_trace'   not in bname:
                continue
            if direction == 'retrace' and '_retrace' not in bname:
                continue

            filepath = _resolve_path(localfile, data_base_dir)
            if filepath is None:
                warnings.warn(f'data_calculation_cryo: not found: {localfile}')
                continue

            try:
                relay_sign = int(parts[1].strip().replace('+', ''))
                field_T    = float(parts[2].strip())
            except ValueError:
                relay_sign, field_T = 1, 0.0

            pol = relay_sign * (1 if field_T >= 0.0 else -1)

            if first_scan:
                first_scan = False
                x = data_load(filepath, ch_x)
                if x is None or len(x) < 2:
                    x = None
                    first_scan = True
                    continue

            var = data_load(filepath, ch_var)
            if len(var) != len(x):
                var = np.interp(np.linspace(0, 1, len(x)),
                                np.linspace(0, 1, len(var)), var)

            nans, z = nan_helper(var)
            if np.any(nans):
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
        warnings.warn(f'data_calculation_cryo: no valid data for "{ch_var}"')
        return [np.zeros(1)] * 7

    if var_pos.ndim == 1:
        var_pos = var_pos[np.newaxis, :]
    if var_neg.ndim == 1:
        var_neg = var_neg[np.newaxis, :]

    fn = np.median if median else np.mean
    res_pos = fn(var_pos, axis=0)
    res_neg = fn(var_neg, axis=0)

    diff     = (res_pos - res_neg) / 2.0
    summation = (res_pos + res_neg) / 2.0
    std      = np.sqrt(np.std(var_pos, axis=0)**2 +
                       np.std(var_neg, axis=0)**2) / 2.0

    return [x, diff, summation, std, res_pos, res_neg, n_pos]


def linescan_calc_cryo(scanlist_path, direction=None, ignorLines=(),
                        data_base_dir=None, x_ch='actuator_x'):
    """Load all sensor channels from a SAMBA Cryo scanlist.

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
        warnings.warn('linescan_calc_cryo: get_channels returned no channels; '
                      'check data_base_dir')
        return {}

    my_dict   = {}
    x_loaded  = False
    skip      = _SKIP_CH | {x_ch, 'actuator_x_setpoint'}

    for ch_name in res_ch:
        if ch_name in skip or 'actuator' in ch_name.lower():
            continue

        data = data_calculation_cryo(
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


def intensity_mean_cryo(scanlist_path, ch_var='DC', direction=None,
                         ignorLines=(), data_base_dir=None):
    """Collect per-scan profiles of *ch_var* (for ``see_intensity`` plot).

    Returns ``(I, var_all)`` where *I* is the per-scan mean and *var_all*
    has shape ``(n_scans, n_points)``.
    """
    var_all    = None
    line_counter = 0

    with open(scanlist_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            line_counter += 1
            if line_counter in ignorLines:
                continue

            parts    = line.split('\t')
            localfile = parts[0].strip()
            bname    = os.path.basename(localfile)

            if direction == 'trace'   and '_trace'   not in bname:
                continue
            if direction == 'retrace' and '_retrace' not in bname:
                continue

            filepath = _resolve_path(localfile, data_base_dir)
            if filepath is None:
                continue

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

class analyze_cryo:
    """SOT / MOKE scan analysis for SAMBA Cryo HDF5 data.

    Typical usage::

        # Single direction
        res = analyze_cryo.import_analyze_SOT(
            'path/to/scanlist.txt',
            current_mA=12.5,
            see_channels=('DC', 'ZI_x1'),
        )

        # Trace + retrace separately (piezo hysteresis)
        tr, rt = analyze_cryo.import_analyze_both(
            'path/to/scanlist.txt',
            current_mA=12.5,
        )
    """

    def __init__(self, scanlist_path, current_mA=None, calibration=1.0,
                 sln=None, theta=0.0, theta2=0.0, R=(1.0, 1.0),
                 direction=None, data_base_dir=None,
                 x_ch='actuator_x', li_type='zi',
                 reflec_key='FL', x_unit='µm', signal_unit='V',
                 save_dir=None, save_subdir=True):
        self.scanlist_path = str(scanlist_path)
        self.direction     = direction
        self.data_base_dir = data_base_dir
        self.x_ch          = x_ch
        self.x_unit        = x_unit
        self.signal_unit   = signal_unit
        self._reflec_key   = reflec_key

        # ── current: explicit > scanlist filename > default 10 mA ─────────
        if current_mA is None:
            current_mA = parse_current_from_name(scanlist_path)
            if current_mA is not None:
                print(f'  Current auto-detected from filename: {current_mA} mA')
            else:
                warnings.warn('current_mA not given and could not be parsed '
                              'from filename — defaulting to 10.0 mA')
                current_mA = 10.0

        # ── calibration: ``calibration`` is canonical; ``sln`` kept as alias
        cal = float(sln if sln is not None else calibration)

        # ── calc_info ─────────────────────────────────────────────────────
        class CalcInfo:
            pass
        ci          = CalcInfo()
        ci.current  = float(current_mA)
        ci.sln      = cal              # multiplies raw signal → Kerr angle
        ci.calibration = cal
        ci.theta    = float(theta)
        ci.theta2   = float(theta2)
        ci.R        = list(R)
        ci.LI_type  = li_type

        name   = os.path.splitext(os.path.basename(scanlist_path))[0]
        parts  = name.split('_')
        ci.system   = parts[1] if len(parts) > 1 else name
        ci.LightPol = 'PMOKE'
        for p in parts:
            if any(x in p.lower() for x in ('moke', 'pol')):
                ci.LightPol = p
        ci.logfilenameShort = os.path.basename(scanlist_path)
        ci.specific         = name[-20:] if len(name) > 20 else name
        self.calc_name  = [ci.logfilenameShort]
        self.calc_info  = ci

        # ── state ─────────────────────────────────────────────────────────
        self.data          = None
        self.analyzed_data = None
        self.edges         = None
        self.dev_center    = None
        self.width         = None
        self.fit_DL_mT         = None
        self.fit_DL_error_mT   = None

        # ── output directory ──────────────────────────────────────────────
        # Resolution order:
        #   save_dir + save_subdir=True  → save_dir/<ts>_<direction>/   (default)
        #   save_dir + save_subdir=False → save_dir/                     (write directly)
        #   no save_dir                   → <scanlist_dir>/<ts>_<direction>/
        ts     = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        suffix = f'_{direction}' if direction else ''
        if save_dir is None:
            base = os.path.dirname(os.path.abspath(scanlist_path))
            self.path3 = os.path.join(base, ts + suffix)
        elif save_subdir:
            self.path3 = os.path.join(os.path.abspath(save_dir), ts + suffix)
        else:
            self.path3 = os.path.abspath(save_dir)
        os.makedirs(self.path3, exist_ok=True)
        print(f'  Saving plots to: {self.path3}')

    # ── data loading ──────────────────────────────────────────────────────

    def import_data(self, ignorLines=(), data_base_dir=None):
        """Load all sensor channels from the scanlist."""
        if data_base_dir:
            self.data_base_dir = data_base_dir

        self.data = linescan_calc_cryo(
            self.scanlist_path,
            direction=self.direction,
            ignorLines=ignorLines,
            data_base_dir=self.data_base_dir,
            x_ch=self.x_ch,
        )
        print(f'  Data keys loaded: {list(self.data.keys())}')
        return self

    # ── per-scan intensity plot ───────────────────────────────────────────

    def see_intensity(self, ch_var='DC', ignorelines=(), ylim=()):
        """Plot per-scan mean intensity and individual profiles (copper colourmap)."""
        I, var_all = intensity_mean_cryo(
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
        plt.tight_layout()
        fname = os.path.join(self.path3, f'intensity_{ch_var}.png')
        plt.savefig(fname, dpi=150)
        print(f'  Plot saved: {fname}')
        plt.show()
        return self

    # ── edge detection ────────────────────────────────────────────────────

    def get_edges(self, I_ch=None):
        """Detect device edges from the reflection channel."""
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
            edges, width = find_edges_width(xsort[5:-5], rsort[5:-5])
        except Exception as e:
            warnings.warn(f'get_edges: find_edges_width failed: {e}')
            return self

        print(f'  Edges: {edges[0]:.2f} – {edges[1]:.2f} {self.x_unit}  '
              f'width = {width:.2f} {self.x_unit}')
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
        plt.tight_layout()
        fname = os.path.join(self.path3, 'edges.png')
        plt.savefig(fname, dpi=150)
        print(f'  Plot saved: {fname}')
        plt.show()
        return self

    # ── phase auto-detection ──────────────────────────────────────────────

    def get_theta(self, LI_str=None, do_plot=False):
        """Auto-detect lock-in phase by minimising imaginary component."""
        if self.edges is None:
            self.get_edges()
        D  = self.data
        li = LI_str or self.calc_info.LI_type
        print(f'  Using LIA: {li}')

        t_pos = find_phase(D['x'], D[li+'x1'][4], D[li+'y1'][4],
                           self.edges, 'pos', do_plot=do_plot)
        t_neg = find_phase(D['x'], D[li+'x1'][5], D[li+'y1'][5],
                           self.edges, 'neg', do_plot=do_plot)
        theta = float(np.mean([t_pos, t_neg]))
        print(f'  theta = {theta:.2f}°  (from {t_pos:.2f}° & {t_neg:.2f}°)')
        if do_plot:
            plt.grid(); plt.legend(); plt.show()
        self.calc_info.theta = theta

        if li + 'x2' in D and li + 'y2' in D:
            t2_pos = find_phase(D['x'], D[li+'x2'][4], D[li+'y2'][4],
                                self.edges, 'pos', do_plot=do_plot)
            t2_neg = find_phase(D['x'], D[li+'x2'][5], D[li+'y2'][5],
                                self.edges, 'neg', do_plot=do_plot)
            theta2 = float(np.mean([t2_pos, t2_neg]))
            print(f'  theta2 = {theta2:.2f}°  (from {t2_pos:.2f}° & {t2_neg:.2f}°)')
            if do_plot:
                plt.grid(); plt.legend(); plt.show()
            self.calc_info.theta2 = theta2

        return self

    # ── evaluate_data ─────────────────────────────────────────────────────

    def evaluate_data(self, phase=None, phase2=None, plot_2axs=False,
                      do_plot='sumdiff', fs=16, reflection=None):
        """Compute Kerr angles and produce standard SOT plots.

        *do_plot*: ``'sumdiff'`` | ``'negpos'`` | ``'realimag'``
        """
        if reflection is None:
            reflection = self._reflec_key

        plotname = (self.calc_info.system + '_'
                    + str(self.calc_info.current) + 'mA_'
                    + self.calc_info.LightPol + '_' + do_plot + '_'
                    + self.calc_info.specific)

        theta  = phase  if phase  is not None else self.calc_info.theta
        theta2 = phase2 if phase2 is not None else self.calc_info.theta2
        li  = self.calc_info.LI_type
        sln = self.calc_info.sln
        t1  = theta  * np.pi / 180.0
        t2  = theta2 * np.pi / 180.0

        D   = self.data
        fac = 1000 if 'sr' in li else 1

        theta_Oe  = (D[li+'x1'][2] * np.cos(t1) + D[li+'y1'][2] * np.sin(t1)) * sln
        theta_DL  = (D[li+'x1'][1] * np.cos(t1) + D[li+'y1'][1] * np.sin(t1)) * sln
        error_bar = (np.sqrt((D[li+'x1'][3] * np.cos(t1))**2 +
                             (D[li+'y1'][3]  * np.sin(t1))**2) * np.abs(sln))

        pos       = D['x']
        theta_neg = (D[li+'x1'][5] * np.cos(t1) + D[li+'y1'][5] * np.sin(t1)) * sln
        theta_pos = (D[li+'x1'][4] * np.cos(t1) + D[li+'y1'][4] * np.sin(t1)) * sln

        if do_plot in ('realimag', 'realimag2nd'):
            plot_2axs = True
        else:
            if plot_2axs:
                fig, (ax1, ax3) = plt.subplots(2, 1, figsize=(8, 8))
            else:
                fig, ax1 = plt.subplots(figsize=(6, 4))

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

        elif do_plot == 'realimag':
            fig, (ax1, ax3) = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
            ax3.plot(pos, D[li+'x1'][5] * sln, '-.v', color='b')
            ax3.plot(pos, D[li+'y1'][5] * sln, '-.v', color='r')
            ax1.plot(pos, D[li+'x1'][4] * sln, '-.o', color='b', label='real')
            ax1.plot(pos, D[li+'y1'][4] * sln, '-.o', color='r', label='imag')
            ax1.set_title('R$^+$')
            ax3.set_title('R$^-$')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]', fontsize=fs)
            ax1.set_xlabel(r'$x$ $[\mu m]$')

        # ── common axes decoration ─────────────────────────────────────────
        ax1.legend(fontsize=fs, loc=1)
        ax1.grid(True)
        ax1.axhline(y=0, color='k')
        ax2 = ax1.twinx()
        ax2.plot(D['x'], D[reflection][2], color='firebrick', label=r'I$_{FL}$')

        if plot_2axs and do_plot not in ('realimag', 'realimag2nd'):
            ax3.grid(True)
            ax4 = ax3.twinx()
            ax4.plot(D['x'], D[reflection][2], color='firebrick', label=r'I$_{FL}$')
            if 'real' not in do_plot:
                ax3.legend(fontsize=fs, loc=1)
            else:
                ax1.set_xlabel(r'$x$ $[\mu m]$', fontsize=fs)
                ax1.tick_params(axis='both', which='major', labelsize=fs - 2)
            ax3.axhline(y=0, color='k')
            ax3.set_xlabel(r'$x$ $[\mu m]$', fontsize=fs)
            ax4.legend(fontsize=fs, loc=4)
            ax3.tick_params(axis='both', which='major', labelsize=fs - 2)
        else:
            ax1.set_xlabel(r'$x$ $[\mu m]$', fontsize=fs)
            ax1.tick_params(axis='both', which='major', labelsize=fs - 2)

        ax2.legend(fontsize=fs, loc=4)
        plt.tight_layout()
        fname = os.path.join(self.path3, plotname + '.png')
        plt.savefig(fname, pad_inches=0.1)
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
        return self

    # ── eval_width_and_fit ────────────────────────────────────────────────

    def eval_width_and_fit(self, current_coefficient2=0.99, fit_edge_offset=5,
                           nice_plot=False, use_Oe_as_edges=True):
        """Fit Oersted (log) and DL (constant) contributions; compute SOT fields."""
        mpl.rcParams['font.size'] = 16

        def parallel_channel(R1, R2):
            return 1 - R1 / R2

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

        Ic  = self.calc_info.current * current_coefficient2
        Ic1 = Ic * parallel_channel(*self.calc_info.R)

        # ── build mask ────────────────────────────────────────────────────
        mask = (position > 0) & (position < width)
        off  = ~mask
        true_idx = np.where(mask)[0]
        if len(true_idx) > 2 * fit_edge_offset:
            mask[true_idx[:fit_edge_offset]]  = False
            mask[true_idx[-fit_edge_offset:]] = False
        print(f'  {mask.sum()} points in fit window')

        if mask.sum() < 3:
            warnings.warn('eval_width_and_fit: not enough interior points for fit')
            return self

        pos_fit  = position[mask]
        err_fit  = np.maximum(error_bar[mask], 1e-12)
        pos_mask = position[(~mask) & (position > 0) & (position < width)]

        # Constant offset of Oe outside device
        try:
            const_offset, _ = curve_fit(Const_fit, position[off], theta_Oe[off],
                                         sigma=np.maximum(error_bar[off], 1e-12),
                                         absolute_sigma=True)
        except Exception:
            const_offset = [0.0]

        # DL constant fit
        try:
            pConst, covConst = curve_fit(Const_fit, pos_fit, theta_DL[mask],
                                          sigma=err_fit, absolute_sigma=True)
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
                sigma=err_fit, absolute_sigma=True)
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
        plt.savefig(fname)
        print(f'  Plot saved: {fname}')
        plt.show()

        # Update analyzed_data with shifted x
        self.analyzed_data['x']        = position
        self.analyzed_data['sum']      = theta_Oe
        self.analyzed_data['diff']     = theta_DL
        self.analyzed_data['errorbar'] = error_bar

        if nice_plot:
            self._nice_plot(position, theta_Oe, theta_DL, error_bar,
                            reflex, pos_fit, pLog, pConst, width, plotname)
        return self

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
        plt.tight_layout(pad=0.5)
        fname = os.path.join(self.path3, f'nice_plot_{plotname}.png')
        plt.savefig(fname, bbox_inches='tight')
        print(f'  Plot saved: {fname}')
        plt.show()

    # ── pipeline entry points ─────────────────────────────────────────────

    @staticmethod
    def import_analyze_SOT(scanlist_path, see_channels=('DC', 'ZI_x1'),
                            direction=None, ignorLines=(), fit_edge_offset=5,
                            force_theta_0=False, **kwargs):
        """Full SOT analysis pipeline for a single scan direction.

        Equivalent to ``analyze_SHE_OHE.import_analyze_SOT`` from Jakub_methods.py.
        """
        res = analyze_cryo(scanlist_path, direction=direction, **kwargs)
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
    def import_analyze_both(scanlist_path, see_channels=('DC', 'ZI_x1'),
                             ignorLines=(), fit_edge_offset=5, **kwargs):
        """Run the full pipeline for each direction available in the scanlist.

        Returns ``(res_trace, res_retrace)``.  Either element is ``None``
        when that direction isn't in the scanlist.  For Green/IR scanlists
        with no trace/retrace markers, runs once with ``direction=None``
        and returns ``(res, None)``.
        """
        data_base_dir = kwargs.get('data_base_dir')
        dirs = detect_directions(scanlist_path, data_base_dir=data_base_dir)
        print(f'  Directions detected in scanlist: '
              f'{sorted(dirs) if dirs else "none (legacy single-direction)"}')

        if not dirs:
            print('=' * 60 + '\n  SINGLE DIRECTION\n' + '=' * 60)
            res = analyze_cryo.import_analyze_SOT(
                scanlist_path, see_channels=see_channels, direction=None,
                ignorLines=ignorLines, fit_edge_offset=fit_edge_offset, **kwargs)
            return res, None

        res_trace = res_retrace = None
        if 'trace' in dirs:
            print('=' * 60 + '\n  TRACE\n' + '=' * 60)
            res_trace = analyze_cryo.import_analyze_SOT(
                scanlist_path, see_channels=see_channels, direction='trace',
                ignorLines=ignorLines, fit_edge_offset=fit_edge_offset, **kwargs)
        if 'retrace' in dirs:
            print('=' * 60 + '\n  RETRACE\n' + '=' * 60)
            res_retrace = analyze_cryo.import_analyze_SOT(
                scanlist_path, see_channels=see_channels, direction='retrace',
                ignorLines=ignorLines, fit_edge_offset=fit_edge_offset, **kwargs)
        return res_trace, res_retrace


# backwards-compatibility alias used by existing measurement scripts
SambaSOTAnalysis = analyze_cryo
