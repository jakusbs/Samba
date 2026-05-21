# -*- coding: utf-8 -*-
"""
PMOKE scan-x analysis for SAMBA HDF5 data.

Loads a SAMBA scanlist, splits trace/retrace and +B/-B field groups,
averages each group, and plots:
  - Raw ±B profiles for X1 (Kerr rotation) and Y1 (ellipticity)
  - MOKE contrast (pos - neg) / 2 for trace and retrace

First run with PRINT_CHANNELS = True to confirm channel names in your HDF5 files.
"""

import os, sys
import numpy as np
import matplotlib.pyplot as plt

_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from samba_io import load_samba_scanlist, load_samba_h5, group_by_sign, average_scans

# ── Configuration ─────────────────────────────────────────────────────────────

SCANLIST = os.path.join(_this_dir, 'Cryo',
    '20260521_Pt(5)Co(5)-wu89_12.5mA_8725Hz_scan-x_PMOKE_12p50mm_TestSamba_lam2_20260521_002301.txt')

# If the absolute paths in the scanlist don't exist on this machine,
# point DATA_BASE_DIR to the folder that contains the .h5 files.
DATA_BASE_DIR = None   # e.g. '/mnt/nas/projects/MOKE_lab/Scanning/Data/...'

# Set True on the first run to print available channel names, then set False.
PRINT_CHANNELS = True

# Channel names — adjust after inspecting PRINT_CHANNELS output.
CH_X1   = 'ZI2 x1'   # 1st harmonic X  →  Kerr rotation
CH_Y1   = 'ZI2 y1'   # 1st harmonic Y  →  ellipticity

# Optional: calibration factor µrad/µV (set 1.0 until calibrated).
CALIB   = 1.0
# Optional: lock-in phase correction in degrees.
PHASE   = 0.0

# Position unit conversion: SAMBA stores in nm, divide by 1e3 for µm.
X_SCALE = 1e-3
X_LABEL = 'Position (µm)'

# ── Load scanlist ─────────────────────────────────────────────────────────────

entries_tr = load_samba_scanlist(SCANLIST, direction='trace',
                                 data_base_dir=DATA_BASE_DIR)
entries_rt = load_samba_scanlist(SCANLIST, direction='retrace',
                                 data_base_dir=DATA_BASE_DIR)

print(f"Trace scans:   {len(entries_tr)}")
print(f"Retrace scans: {len(entries_rt)}")

# ── Inspect channels on first run ─────────────────────────────────────────────

if PRINT_CHANNELS and entries_tr:
    sample = load_samba_h5(entries_tr[0]['path'])
    if 'error' in sample:
        print(f"\nCould not open sample file: {sample['error']}")
        print("Check that the paths in the scanlist are accessible, or set DATA_BASE_DIR.")
        sys.exit(1)
    print(f"\nAvailable channels (first trace file):")
    for k, arr in sample.items():
        if isinstance(arr, np.ndarray) and k not in ('x',):
            print(f"  '{k}'  shape={arr.shape}  unit='{sample['units'].get(k, '')}'")
    print(f"  x-axis key: '{sample.get('_x_key', '?')}'  unit='{sample.get('x_unit', '')}'")
    print()

# ── Load all files ────────────────────────────────────────────────────────────

def _load_group(entries):
    scans = []
    for e in entries:
        d = load_samba_h5(e['path'])
        if 'error' in d:
            print(f"  Warning: skipping {os.path.basename(e['path'])}: {d['error']}")
        else:
            scans.append(d)
    return scans

scans_tr = _load_group(entries_tr)
scans_rt = _load_group(entries_rt)

# ── Group by field sign ───────────────────────────────────────────────────────
# effective sign = relay_sign * sign(field_T)
# Here relay_sign is always +1, so sign is determined by field_T alone.

channels = [CH_X1, CH_Y1]

def _split_and_average(entries, scans):
    pos_scans, neg_scans = group_by_sign(entries[:len(scans)], scans)
    x_ref = scans[0]['x'] if scans else None
    avg_pos = average_scans(pos_scans, channels, x_ref=x_ref)
    avg_neg = average_scans(neg_scans, channels, x_ref=x_ref)
    print(f"  +B: {len(pos_scans)} scans,  -B: {len(neg_scans)} scans")
    return avg_pos, avg_neg

print("Trace:")
avg_pos_tr, avg_neg_tr = _split_and_average(entries_tr, scans_tr)
print("Retrace:")
avg_pos_rt, avg_neg_rt = _split_and_average(entries_rt, scans_rt)

# ── Phase rotation (if needed) ────────────────────────────────────────────────

def _rotated(avg, ch):
    """Apply lock-in phase correction to X1/Y1 and return the requested channel."""
    if PHASE == 0.0 or ch == CH_Y1:
        return avg.get(ch)
    ph = np.deg2rad(PHASE)
    x1 = avg.get(CH_X1, np.zeros(1))
    y1 = avg.get(CH_Y1, np.zeros_like(x1))
    if ch == CH_X1:
        return x1 * np.cos(ph) + y1 * np.sin(ph)
    return None

# ── MOKE contrast = (pos − neg) / 2 ─────────────────────────────────────────

def _contrast(avg_pos, avg_neg, ch):
    s_pos = _rotated(avg_pos, ch)
    s_neg = _rotated(avg_neg, ch)
    if s_pos is None or s_neg is None:
        return None, None
    contrast = (s_pos - s_neg) / 2.0 * CALIB
    background = (s_pos + s_neg) / 2.0 * CALIB
    return contrast, background

# ── Plot ──────────────────────────────────────────────────────────────────────

fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True)
fig.suptitle(os.path.basename(SCANLIST).replace('.txt', ''), fontsize=9)

ch_labels = {CH_X1: 'X1  (Kerr rotation)', CH_Y1: 'Y1  (ellipticity)'}
y_unit = 'µrad' if CALIB != 1.0 else 'µV'

for col, ch in enumerate([CH_X1, CH_Y1]):
    x_tr = avg_pos_tr.get('x', np.array([])) * X_SCALE
    x_rt = avg_pos_rt.get('x', np.array([])) * X_SCALE

    # ── row 0: raw ±B profiles ───────────────────────────────────────────────
    ax = axes[0, col]
    for avg, label, ls, alpha in [
        (avg_pos_tr, '+B trace',   '-',  1.0),
        (avg_neg_tr, '−B trace',   '-',  1.0),
        (avg_pos_rt, '+B retrace', '--', 0.6),
        (avg_neg_rt, '−B retrace', '--', 0.6),
    ]:
        x = avg.get('x', np.array([])) * X_SCALE
        y = avg.get(ch)
        if y is not None and len(x):
            ax.plot(x, y, ls=ls, alpha=alpha, label=label)
    ax.set_title(f'Raw ±B — {ch_labels[ch]}')
    ax.set_ylabel(f'{ch} (µV)')
    ax.legend(fontsize=7, ncol=2)
    ax.grid(True, alpha=0.3)

    # ── row 1: MOKE contrast ─────────────────────────────────────────────────
    ax = axes[1, col]
    for x, avg_pos, avg_neg, label, ls in [
        (x_tr, avg_pos_tr, avg_neg_tr, 'trace',   '-'),
        (x_rt, avg_pos_rt, avg_neg_rt, 'retrace', '--'),
    ]:
        contrast, _ = _contrast(avg_pos, avg_neg, ch)
        if contrast is not None and len(x):
            ax.plot(x, contrast, ls=ls, label=label)
    ax.set_title(f'MOKE contrast — {ch_labels[ch]}')
    ax.set_ylabel(f'(+B − −B) / 2  ({y_unit})')
    ax.set_xlabel(X_LABEL)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

plt.tight_layout()
out_png = SCANLIST.replace('.txt', '_analysis.png')
plt.savefig(out_png, dpi=150, bbox_inches='tight')
print(f"\nSaved: {out_png}")
plt.show()
