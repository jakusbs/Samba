"""
samba_io.py — Data loaders for SAMBA HDF5 and Salsa NXS formats.
"""

import os
import warnings
import numpy as np
import h5py
# scipy is imported lazily inside average_scans() so the loaders and the
# DC-hysteresis cycle helpers stay usable with only numpy + h5py installed.


# ---------------------------------------------------------------------------
# SAMBA HDF5 loader
# ---------------------------------------------------------------------------

def load_samba_h5(path: str) -> dict:
    """Load a single SAMBA HDF5 scan file into a flat dict."""
    path = str(path)
    result = {
        'path': path,
        'labels': {},
        'units': {},
        'metadata': {},
    }
    try:
        with h5py.File(path, 'r') as f:
            # Root attributes
            root_attrs = dict(f.attrs)
            result['scan_type'] = str(root_attrs.get('scan_type', ''))
            result['timestamp'] = str(root_attrs.get('timestamp', ''))
            result['_x_key'] = str(root_attrs.get('_x_key', 'actuator_x'))

            # Metadata group
            if 'metadata' in f:
                for k, v in f['metadata'].attrs.items():
                    try:
                        result['metadata'][k] = v.item() if hasattr(v, 'item') else v
                    except Exception:
                        result['metadata'][k] = str(v)

            # Data group — read all datasets
            x_key = result['_x_key']
            if 'data' not in f:
                raise KeyError("No 'data' group found in file")

            data_grp = f['data']
            for ds_name in data_grp:
                ds = data_grp[ds_name]
                arr = np.array(ds, dtype=float)
                attrs = dict(ds.attrs)
                result[ds_name] = arr
                result['labels'][ds_name] = str(attrs.get('label', ds_name))
                result['units'][ds_name] = str(attrs.get('unit', ''))

            # Canonical 'x' key
            if x_key in result:
                result['x'] = result[x_key]
                result['x_unit'] = result['units'].get(x_key, '')
            elif 'actuator_x' in result:
                result['x'] = result['actuator_x']
                result['x_unit'] = result['units'].get('actuator_x', '')
            else:
                # Fall back to time or first dataset
                for candidate in ('time', 'actuator_x_setpoint'):
                    if candidate in result:
                        result['x'] = result[candidate]
                        result['x_unit'] = result['units'].get(candidate, '')
                        break

            # Convenience scalars
            if 'Field' in result:
                result['field_T'] = float(np.nanmean(result['Field']))
            else:
                result['field_T'] = 0.0

            if 'Temperature' in result:
                result['temperature_K'] = float(np.nanmean(result['Temperature']))
            else:
                result['temperature_K'] = None

    except Exception as e:
        warnings.warn(f"load_samba_h5: could not open {path}: {e}")
        return {'path': path, 'error': str(e), 'labels': {}, 'units': {},
                'metadata': {}, 'field_T': 0.0, 'temperature_K': None}

    return result


# ---------------------------------------------------------------------------
# DC-hysteresis per-cycle data  (/data/cycles, written by ScanRunner)
# ---------------------------------------------------------------------------

_HYST_BLOCKS = ('field', 'result1', 'result2', 'result3',
                'result4', 'result5', 'result6')


def _as_str(v, default=''):
    return v.decode() if isinstance(v, bytes) else (str(v) if v is not None else default)


def load_hyst_cycles(path: str) -> dict:
    """Read the raw per-cycle DC-hysteresis half-loops written by ScanRunner.

    Current layout — ``/data/cycles`` is a **group** of per-quantity 2-D
    datasets, each ``[n_cycles, n_loop]``::

        /data/cycles/field      (mT)
        /data/cycles/result1 .. result6

    Legacy layout (early builds) — ``/data/cycles`` is a single 3-D dataset
    ``[n_cycles, 7, n_loop]``; still read transparently.

    Returns ``None`` if the file has no per-cycle data (older files, or a
    device server without the GetCycle commands).

    Result dict::

        {
          'field':   np.ndarray [n_cycles, n_loop],   # mT
          'result1': np.ndarray [n_cycles, n_loop], ... 'result6',
          'cube':    np.ndarray [n_cycles, 7, n_loop],
          'n_cycles': int,                # cycles actually present (not all-NaN)
          'valid':    np.ndarray[bool] [n_cycles],
          'labels':   {'result1': 'MOKE (R1)', ...},
          'field_unit': 'mT',
        }
    """
    path = str(path)
    blocks = {}            # name -> [n_cycles, n_loop]
    labels = {}
    field_unit = 'mT'
    try:
        with h5py.File(path, 'r') as f:
            if 'data' not in f or 'cycles' not in f['data']:
                return None
            node = f['data']['cycles']

            if isinstance(node, h5py.Group):
                # New layout: one 2-D dataset per quantity.
                for name in _HYST_BLOCKS:
                    if name in node:
                        ds = node[name]
                        blocks[name] = np.array(ds, dtype=float)
                        lbl = ds.attrs.get('label')
                        if lbl is not None:
                            labels[name] = _as_str(lbl, name)
                        if name == 'field':
                            field_unit = _as_str(ds.attrs.get('unit'), 'mT')
                if 'field' not in blocks:
                    warnings.warn(f"load_hyst_cycles: /data/cycles group has no "
                                  f"'field' dataset in {path}")
                    return None
            else:
                # Legacy 3-D cube [n_cycles, 7, n_loop].
                cube = np.array(node, dtype=float)
                if cube.ndim != 3 or cube.shape[1] < 7:
                    warnings.warn(f"load_hyst_cycles: unexpected /data/cycles "
                                  f"shape {cube.shape} in {path}")
                    return None
                for i, name in enumerate(_HYST_BLOCKS):
                    blocks[name] = cube[:, i, :]
                attrs = dict(node.attrs)
                field_unit = _as_str(attrs.get('field_unit'), 'mT')
                for k, v in attrs.items():
                    if k.startswith('channel_') and k.endswith('_label'):
                        labels[k[len('channel_'):-len('_label')]] = _as_str(v)
    except Exception as e:
        warnings.warn(f"load_hyst_cycles: could not open {path}: {e}")
        return None

    # Stack into a cube for the `cube`/`valid` conveniences; align lengths.
    n_cyc  = blocks['field'].shape[0]
    n_loop = blocks['field'].shape[1]
    cube = np.full((n_cyc, 7, n_loop), np.nan)
    for i, name in enumerate(_HYST_BLOCKS):
        arr = blocks.get(name)
        if arr is not None and arr.shape == (n_cyc, n_loop):
            cube[:, i, :] = arr

    # A cycle that failed to read at acquisition time stays all-NaN.
    valid = ~np.all(np.isnan(cube.reshape(n_cyc, -1)), axis=1)

    out = {
        'cube':       cube,
        'n_cycles':   int(valid.sum()),
        'valid':      valid,
        'labels':     labels,
        'field_unit': field_unit,
    }
    for i, name in enumerate(_HYST_BLOCKS):
        out[name] = cube[:, i, :]
    return out


def _included_mask(cyc: dict, exclude=()):
    """Boolean [n_cycles] mask of valid, non-excluded cycles (1-based exclude)."""
    valid = cyc['valid'].copy()
    for n in (exclude or ()):
        idx = int(n) - 1
        if 0 <= idx < len(valid):
            valid[idx] = False
    return valid


def hyst_cycle_average(cyc: dict, exclude=()) -> dict:
    """Average the retained cycles, dropping 1-based cycle numbers in ``exclude``.

    Mirrors the device's ``RecomputeAverage`` offline so a bad scan can be
    kicked out of the average without re-measuring.  Returns a dict with
    ``field`` and ``result1``..``result6`` each a 1-D ``[n_loop]`` array, plus
    ``included`` (the 1-based cycle numbers used).
    """
    mask = _included_mask(cyc, exclude)
    if not mask.any():
        raise ValueError("hyst_cycle_average: every cycle is excluded/invalid")
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', category=RuntimeWarning)
        avg = {name: np.nanmean(cyc[name][mask], axis=0)
               for name in _HYST_BLOCKS}
    avg['included'] = [i + 1 for i in range(len(mask)) if mask[i]]
    return avg


def hyst_detect_outliers(cyc: dict, channel: str = 'result1',
                         n_sigma: float = 3.0) -> list:
    """Flag outlier cycles by robust deviation of one channel's loop.

    For each valid cycle, the RMS distance of its ``channel`` loop from the
    per-point median across all valid cycles is computed.  Cycles whose RMS
    distance exceeds ``median + n_sigma * 1.4826 * MAD`` are returned as a
    sorted list of 1-based cycle numbers.  With < 3 valid cycles there is no
    robust baseline, so an empty list is returned.
    """
    if channel not in cyc:
        raise KeyError(f"hyst_detect_outliers: unknown channel {channel!r}")
    valid = cyc['valid']
    idxs  = np.where(valid)[0]
    if len(idxs) < 3:
        return []
    loops = cyc[channel][idxs]                       # [n_valid, n_loop]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', category=RuntimeWarning)
        median_loop = np.nanmedian(loops, axis=0)
        rms = np.sqrt(np.nanmean((loops - median_loop) ** 2, axis=1))
        med = np.nanmedian(rms)
        mad = np.nanmedian(np.abs(rms - med))
    thresh = med + n_sigma * 1.4826 * mad
    if not np.isfinite(thresh) or mad == 0:
        return []
    return sorted(int(idxs[i]) + 1 for i in range(len(idxs)) if rms[i] > thresh)


def plot_hyst_cycles(cyc: dict, channel: str = 'result1', exclude=(),
                     ax=None, show_average: bool = True):
    """Overlay each retained cycle's loop (faint) + the average (bold).

    Excluded/invalid cycles are drawn dashed.  ``matplotlib`` is imported
    lazily so the rest of this module stays usable without it.  Returns the
    matplotlib Axes.
    """
    import matplotlib.pyplot as plt
    if ax is None:
        _, ax = plt.subplots()
    mask = _included_mask(cyc, exclude)
    field, sig = cyc['field'], cyc[channel]
    label = cyc['labels'].get(channel, channel)
    for i in range(cyc['cube'].shape[0]):
        if not cyc['valid'][i]:
            continue
        included = mask[i]
        ax.plot(field[i], sig[i], lw=0.7,
                alpha=0.6 if included else 0.25,
                ls='-' if included else '--',
                color='#89b4fa' if included else '#f38ba8',
                label=None)
    if show_average and mask.any():
        avg = hyst_cycle_average(cyc, exclude)
        ax.plot(avg['field'], avg[channel], lw=2.0, color='#cdd6f4',
                label=f'average ({len(avg["included"])} cyc)')
        ax.legend(fontsize=8)
    ax.set_xlabel(f"Field [{cyc['field_unit']}]")
    ax.set_ylabel(label)
    return ax


# ---------------------------------------------------------------------------
# SAMBA scanlist parser
# ---------------------------------------------------------------------------

def load_samba_scanlist(txt_path: str, direction: str = None,
                        data_base_dir: str = None) -> list:
    """Parse a SAMBA scanlist .txt file.

    Returns list of dicts: {'path', 'relay_sign', 'field_T'}.
    direction: 'trace', 'retrace', or None (all).
    data_base_dir: alternate directory to resolve missing paths.
    """
    entries = []
    txt_path = str(txt_path)
    try:
        with open(txt_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) < 3:
                    continue
                fpath = parts[0].strip()
                try:
                    relay_sign = int(parts[1].strip().replace('+', ''))
                except ValueError:
                    relay_sign = 1
                try:
                    field_T = float(parts[2].strip())
                except ValueError:
                    field_T = 0.0

                # Resolve path
                if not os.path.exists(fpath) and data_base_dir is not None:
                    alt = os.path.join(data_base_dir, os.path.basename(fpath))
                    if os.path.exists(alt):
                        fpath = alt

                entries.append({
                    'path': fpath,
                    'relay_sign': relay_sign,
                    'field_T': field_T,
                })
    except Exception as e:
        warnings.warn(f"load_samba_scanlist: could not read {txt_path}: {e}")
        return []

    if direction == 'trace':
        entries = [e for e in entries if '_trace' in os.path.basename(e['path'])]
    elif direction == 'retrace':
        entries = [e for e in entries if '_retrace' in os.path.basename(e['path'])]

    return entries


# ---------------------------------------------------------------------------
# Salsa NXS loader
# ---------------------------------------------------------------------------

def load_salsa_nxs(path: str, channels: list = None) -> dict:
    """Load a Salsa NXS file and return trace/retrace split.

    Returns {'trace': data_dict, 'retrace': data_dict}.
    Each data_dict: {'x', 'data_01'...'data_06', 'timestamps', 'field_T', 'path'}.
    Multi-scan files are averaged before splitting.
    """
    path = str(path)
    _default_data_channels = [f'data_{i:02d}' for i in range(1, 7)]

    def _load_one_scan(scan_grp):
        """Extract arrays from a single scan group."""
        sd = scan_grp['scan_data']
        act = np.array(sd['actuator_1_1'], dtype=float)
        N2 = len(act)
        N = N2 // 2

        out = {}
        out['_act'] = act
        out['_N'] = N

        chs_to_load = channels if channels is not None else _default_data_channels
        for ch in chs_to_load:
            if ch in sd:
                out[ch] = np.array(sd[ch], dtype=float)

        if 'sensors_timestamps' in sd:
            out['_timestamps'] = np.array(sd['sensors_timestamps'], dtype=float)
        else:
            out['_timestamps'] = np.zeros(N2)
        return out

    try:
        with h5py.File(path, 'r') as f:
            scan_keys = list(f.keys())
            if not scan_keys:
                raise ValueError("No scans found in file")

            # Load all scans and average
            all_scans = [_load_one_scan(f[k]) for k in scan_keys]
            N = all_scans[0]['_N']

            # Average the raw arrays across scans
            avg = {}
            for field in all_scans[0]:
                arrs = [s[field] for s in all_scans if field in s]
                if arrs:
                    try:
                        avg[field] = np.mean(np.stack(arrs, axis=0), axis=0)
                    except Exception:
                        avg[field] = arrs[0]

    except Exception as e:
        warnings.warn(f"load_salsa_nxs: could not open {path}: {e}")
        empty = {'x': np.array([]), 'timestamps': np.array([]),
                 'field_T': 0.0, 'path': path}
        return {'trace': empty, 'retrace': empty}

    act = avg['_act']
    N = int(avg['_N'])

    # Trace: indices 0..N-1 (x goes 0 to max)
    trace_x = act[:N]
    # Retrace: indices N..2N-1 (x goes max to 0) — flip to ascending
    retrace_x = act[N:][::-1]

    def _split(arr):
        tr = arr[:N]
        rt = arr[N:][::-1]
        return tr, rt

    trace = {'x': trace_x, 'field_T': 0.0, 'path': path}
    retrace = {'x': retrace_x, 'field_T': 0.0, 'path': path}

    chs_to_load = channels if channels is not None else _default_data_channels
    for ch in chs_to_load:
        if ch in avg:
            tr, rt = _split(avg[ch])
            trace[ch] = tr
            retrace[ch] = rt

    ts_tr, ts_rt = _split(avg.get('_timestamps', np.zeros(2 * N)))
    trace['timestamps'] = ts_tr
    retrace['timestamps'] = ts_rt

    return {'trace': trace, 'retrace': retrace}


# ---------------------------------------------------------------------------
# Salsa scanlist parser
# ---------------------------------------------------------------------------

def load_salsa_scanlist(txt_path: str, base_dir: str = None) -> list:
    """Parse a Salsa scanlist file.

    Returns list of dicts: {'path', 'field_T', 'relay_sign'}.
    relay_sign is always +1 (no relay in Salsa).
    """
    entries = []
    txt_path = str(txt_path)
    try:
        with open(txt_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) < 2:
                    continue
                fpath = parts[0].strip()
                field_str = parts[1].strip()
                # Parse field like '+0.0500T' or '-0.0500T'
                try:
                    field_T = float(field_str.replace('T', '').replace(' ', ''))
                except ValueError:
                    field_T = 0.0

                # Resolve path
                if not os.path.exists(fpath) and base_dir is not None:
                    alt = os.path.join(base_dir, os.path.basename(fpath))
                    if os.path.exists(alt):
                        fpath = alt
                    else:
                        # Try parent dir of txt_path
                        alt2 = os.path.join(os.path.dirname(txt_path),
                                            os.path.basename(fpath))
                        if os.path.exists(alt2):
                            fpath = alt2

                entries.append({
                    'path': fpath,
                    'field_T': field_T,
                    'relay_sign': 1,
                })
    except Exception as e:
        warnings.warn(f"load_salsa_scanlist: could not read {txt_path}: {e}")
        return []

    return entries


# ---------------------------------------------------------------------------
# Utilities: sign grouping and averaging
# ---------------------------------------------------------------------------

def group_by_sign(entries: list, scans: list):
    """Split entries/scans into positive and negative effective-field groups.

    Effective sign = relay_sign * sign(field_T).
    Returns (pos_scans, neg_scans).
    """
    pos_scans = []
    neg_scans = []
    for entry, scan in zip(entries, scans):
        field_sign = 1 if entry['field_T'] >= 0 else -1
        eff = entry['relay_sign'] * field_sign
        if eff >= 0:
            pos_scans.append(scan)
        else:
            neg_scans.append(scan)
    return pos_scans, neg_scans


def average_scans(scans: list, channels: list, x_ref: np.ndarray = None) -> dict:
    """Interpolate scans onto common x grid and return pointwise average.

    Returns {'x': x_ref, channel: avg_array, ...}.
    If x_ref is None, uses the x array of the first scan.
    """
    if not scans:
        return {}

    if x_ref is None:
        x_ref = scans[0]['x']

    result = {'x': x_ref}

    for ch in channels:
        arrays = []
        for scan in scans:
            if ch not in scan:
                continue
            x_s = scan['x']
            y_s = scan[ch]
            if len(x_s) < 2:
                continue
            # Sort by x before interpolating
            order = np.argsort(x_s)
            x_sorted = x_s[order]
            y_sorted = y_s[order]
            try:
                from scipy.interpolate import interp1d
                f_interp = interp1d(x_sorted, y_sorted,
                                    bounds_error=False,
                                    fill_value='extrapolate',
                                    kind='linear')
                arrays.append(f_interp(x_ref))
            except Exception as e:
                warnings.warn(f"average_scans: interpolation failed for ch '{ch}': {e}")

        if arrays:
            stacked = np.stack(arrays, axis=0)
            result[ch] = np.mean(stacked, axis=0)
            if stacked.shape[0] > 1:
                result[ch + '_std'] = np.std(stacked, axis=0, ddof=1)
            else:
                result[ch + '_std'] = np.zeros_like(result[ch])

    return result
