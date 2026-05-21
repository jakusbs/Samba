# -*- coding: utf-8 -*-
"""
Pt(5)Co(5) PMOKE scan-x — SAMBA HDF5 analysis
Reproduces the same plots as analyze_SHE_OHE.import_analyze_SOT:
  1. see_intensity    — raw per-scan profiles + mean per scan number
  2. sumdiff          — theta_Oe (sum) and theta_DL (diff) vs position
  3. negpos           — positive and negative field profiles separately
  4. realimag         — X1 and Y1 for R+ and R- side-by-side
  5. eval_width_and_fit — edge detection, log/const fits → DL and Oe

Run with PRINT_CHANNELS = True first to confirm channel names.
"""

import os, sys, datetime
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from scipy import interpolate
from scipy.optimize import curve_fit

_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from samba_io import load_samba_scanlist, load_samba_h5, group_by_sign, average_scans

mpl.rcParams['font.size'] = 14

# ── Configuration ─────────────────────────────────────────────────────────────

SCANLIST = os.path.join(_this_dir, 'Cryo',
    '20260521_Pt(5)Co(5)-wu89_12.5mA_8725Hz_scan-x_PMOKE_12p50mm_TestSamba_lam2_20260521_002301.txt')

# If paths inside the scanlist don't exist on this machine, set this to the
# folder containing the .h5 files.  Works on both Linux and Windows, e.g.:
#   DATA_BASE_DIR = r'\\d.ethz.ch\groups\matl\intermag\projects\moke_lab\Scanning\Data\Data_Samba_Cryo\20260521'
DATA_BASE_DIR = None

PRINT_CHANNELS = True   # set False after confirming channel names below

# Channel names — run with PRINT_CHANNELS=True to confirm
CH_X1   = 'ZI2 x1'    # 1st harmonic X  (Kerr rotation component)
CH_Y1   = 'ZI2 y1'    # 1st harmonic Y  (ellipticity component)
CH_X2   = 'ZI2 x2'    # 2nd harmonic X  (set None if not recorded)
CH_Y2   = 'ZI2 y2'    # 2nd harmonic Y  (set None if not recorded)
CH_REFL = 'avgIn1'     # reflectivity / DC intensity (set None to skip edge fit)

# Phase correction (degrees). Set after running 'findphase' analysis.
PHASE   = 0.0
PHASE2  = 0.0

# Calibration (µrad/µV). Set 1.0 until you have a calibration file.
# With 1.0 the y-axes are in µV (raw lock-in units).
CALIB   = 1.0
CALIB_UNIT = 'µV' if CALIB == 1.0 else 'nrad'

# Current in mA (from filename), used only in eval_width_and_fit.
CURRENT_mA = 12.5

# nm → µm conversion for the position axis.
X_SCALE = 1e-3
X_LABEL = r'$x$ [µm]'

# Lines to skip (0-based index into the scanlist).  Empty = use all.
IGNORE_LINES: list = []

# Font size for all plots.
FS = 16

# ── Load scanlist ─────────────────────────────────────────────────────────────

_all_entries = load_samba_scanlist(SCANLIST, data_base_dir=DATA_BASE_DIR)
_all_entries = [e for i, e in enumerate(_all_entries) if i not in IGNORE_LINES]

print(f"Total scans after filtering: {len(_all_entries)}")

# ── Inspect channels on first run ─────────────────────────────────────────────

if PRINT_CHANNELS and _all_entries:
    _s = load_samba_h5(_all_entries[0]['path'])
    if 'error' in _s:
        print(f"\nCould not open sample file: {_s['error']}")
        print("Set DATA_BASE_DIR to the folder containing the .h5 files.")
        sys.exit(1)
    print("\nChannels in HDF5:")
    for k, v in _s.items():
        if isinstance(v, np.ndarray) and k != 'x':
            print(f"  '{k}'  shape={v.shape}  unit='{_s['units'].get(k,'')}'")
    print(f"  x-axis: '{_s.get('_x_key','?')}'  unit='{_s.get('x_unit','')}'")
    print()

# ── Load all files ────────────────────────────────────────────────────────────

_scans_all = []
for e in _all_entries:
    d = load_samba_h5(e['path'])
    if 'error' in d:
        print(f"  Warning: skipping {os.path.basename(e['path'])}: {d['error']}")
    else:
        _scans_all.append(d)

_entries_used = _all_entries[:len(_scans_all)]

# ── Group by field sign and average ──────────────────────────────────────────

_channels = [ch for ch in [CH_X1, CH_Y1, CH_X2, CH_Y2, CH_REFL] if ch]

pos_scans, neg_scans = group_by_sign(_entries_used, _scans_all)
print(f"+B scans: {len(pos_scans)},  -B scans: {len(neg_scans)}")

_x_ref = _scans_all[0]['x'] if _scans_all else None
avg_pos = average_scans(pos_scans, _channels, x_ref=_x_ref)
avg_neg = average_scans(neg_scans, _channels, x_ref=_x_ref)

x_pos = avg_pos.get('x', np.array([])) * X_SCALE
x_neg = avg_neg.get('x', np.array([])) * X_SCALE
x = x_pos  # common x grid

# Derived quantities
ph  = np.deg2rad(PHASE)
ph2 = np.deg2rad(PHASE2)

def _rot(d, xch, ych, angle):
    x1 = d.get(xch, np.zeros_like(x))
    y1 = d.get(ych, np.zeros_like(x))
    return x1 * np.cos(angle) + y1 * np.sin(angle)

x1_pos = _rot(avg_pos, CH_X1, CH_Y1, ph)   if CH_X1 and CH_Y1 else None
x1_neg = _rot(avg_neg, CH_X1, CH_Y1, ph)   if CH_X1 and CH_Y1 else None
x2_pos = _rot(avg_pos, CH_X2, CH_Y2, ph2)  if CH_X2 and CH_Y2 else None
x2_neg = _rot(avg_neg, CH_X2, CH_Y2, ph2)  if CH_X2 and CH_Y2 else None

theta_DL = (x1_pos - x1_neg) / 2.0 * CALIB if x1_pos is not None else None
theta_Oe = (x1_pos + x1_neg) / 2.0 * CALIB if x1_pos is not None else None
theta2_DL = (x2_pos - x2_neg) / 2.0 * CALIB if x2_pos is not None else None
theta2_Oe = (x2_pos + x2_neg) / 2.0 * CALIB if x2_pos is not None else None
theta_pos = x1_pos * CALIB if x1_pos is not None else None
theta_neg = x1_neg * CALIB if x1_neg is not None else None

# Error: Gaussian propagation of std / sqrt(N)
def _err(d, xch, ych, angle):
    sx = d.get(xch + '_std', np.zeros_like(x))
    sy = d.get(ych + '_std', np.zeros_like(x)) if ych else np.zeros_like(x)
    return np.sqrt((sx * np.cos(angle))**2 + (sy * np.sin(angle))**2) * CALIB

error_bar = (_err(avg_pos, CH_X1, CH_Y1, ph) + _err(avg_neg, CH_X1, CH_Y1, ph)) / 2.0 \
            if CH_X1 and CH_Y1 else np.zeros_like(x)
error_bar2 = (_err(avg_pos, CH_X2, CH_Y2, ph2) + _err(avg_neg, CH_X2, CH_Y2, ph2)) / 2.0 \
             if CH_X2 and CH_Y2 else np.zeros_like(x)

refl = ((avg_pos.get(CH_REFL, 0) + avg_neg.get(CH_REFL, 0)) / 2.0) \
       if CH_REFL else np.zeros_like(x)
refl_pos = avg_pos.get(CH_REFL, np.zeros_like(x)) if CH_REFL else np.zeros_like(x)
refl_neg = avg_neg.get(CH_REFL, np.zeros_like(x)) if CH_REFL else np.zeros_like(x)

# ── Output directory ──────────────────────────────────────────────────────────

_scanlist_stem = os.path.splitext(os.path.basename(SCANLIST))[0]
_now = datetime.datetime.now()
_out_dir = os.path.join(os.path.dirname(SCANLIST),
                        _scanlist_stem,
                        _now.strftime('%Y%m%d %H%M%S'))
os.makedirs(_out_dir, exist_ok=True)
print(f"Saving plots to: {_out_dir}")


def _save(fig, name):
    fig.savefig(os.path.join(_out_dir, name + '.png'), bbox_inches='tight', dpi=150)
    fig.savefig(os.path.join(_out_dir, name + '.eps'), bbox_inches='tight')


# ═══════════════════════════════════════════════════════════════════════════════
# 1. see_intensity — raw per-scan profiles + mean per scan number
# ═══════════════════════════════════════════════════════════════════════════════

for ch in [ch for ch in [CH_X1, CH_Y1, CH_X2, CH_Y2] if ch]:
    intensities = [np.mean(s[ch]) for s in _scans_all if ch in s]
    profiles    = [s[ch] for s in _scans_all if ch in s]
    xs          = [s['x'] * X_SCALE for s in _scans_all if ch in s]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 12),
                                   gridspec_kw={'height_ratios': [1, 3]})
    fig.suptitle(f"{_scanlist_stem}\n{ch}")

    ax1.plot(intensities, '.-')
    ax1.set_xticks(range(len(intensities)))
    ax1.set_xlabel('scan number')
    ax1.set_ylabel('mean signal [µV]')
    ax1.grid(True)

    colors = plt.cm.copper(np.linspace(0, 1, len(profiles)))
    for i, (xi, yi) in enumerate(zip(xs, profiles)):
        ax2.plot(xi, yi, 'x-', color=colors[i], label=str(i))
    ax2.axhline(0, color='r', linestyle='-')
    ax2.set_xlabel(X_LABEL)
    ax2.set_ylabel(f'{ch} [µV]')
    ax2.grid(True)
    if len(profiles) <= 12:
        ax2.legend(fontsize=8)

    plt.tight_layout()
    _save(fig, f'intensity_{ch.replace(" ", "_")}')
    plt.show()

# ═══════════════════════════════════════════════════════════════════════════════
# 2. sumdiff — theta_Oe (sum) and theta_DL (diff) vs position
# ═══════════════════════════════════════════════════════════════════════════════

if theta_DL is not None:
    fig, ax1 = plt.subplots(figsize=(6, 4))
    ax1.plot(x, theta_Oe, '-.v', color='black', label='sum (Oe)')
    ax1.errorbar(x, theta_Oe, yerr=error_bar, color='black', fmt='none')
    ax1.plot(x, theta_DL, '-.v', color='green', label='diff (DL)')
    ax1.errorbar(x, theta_DL, yerr=error_bar, color='green', fmt='none')
    ax1.axhline(0, color='k')
    ax1.set_ylabel(rf'$\theta_K^{{1\omega}}$ [{CALIB_UNIT}]', fontsize=FS)
    ax1.set_xlabel(X_LABEL, fontsize=FS)
    ax1.legend(fontsize=FS, loc=1)
    ax1.grid(True)
    ax2 = ax1.twinx()
    ax2.plot(x, refl, color='firebrick', label=r'$I_{FL}$')
    ax2.set_ylabel(r'$R$ [a.u.]')
    ax2.legend(fontsize=FS, loc=4)
    plt.tight_layout()
    _save(fig, 'sumdiff')
    plt.show()

# ═══════════════════════════════════════════════════════════════════════════════
# 3. negpos — positive and negative field profiles separately
# ═══════════════════════════════════════════════════════════════════════════════

if theta_pos is not None:
    fig, ax1 = plt.subplots(figsize=(6, 4))
    ax1.plot(x_pos, theta_pos, '-.v', color='red',  label='pos (+B)')
    ax1.plot(x_neg, theta_neg, '-.v', color='blue', label='neg (−B)')
    ax1.axhline(0, color='k')
    ax1.set_ylabel(rf'$\theta_K^{{1\omega}}$ [{CALIB_UNIT}]', fontsize=FS)
    ax1.set_xlabel(X_LABEL, fontsize=FS)
    ax1.legend(fontsize=FS, loc=1)
    ax1.grid(True)
    ax2 = ax1.twinx()
    ax2.plot(x, refl, color='firebrick', label=r'$I_{FL}$')
    ax2.set_ylabel(r'$R$ [a.u.]')
    ax2.legend(fontsize=FS, loc=4)
    plt.tight_layout()
    _save(fig, 'negpos')
    plt.show()

# ═══════════════════════════════════════════════════════════════════════════════
# 4. realimag — X1 and Y1 for R+ and R- side-by-side
# ═══════════════════════════════════════════════════════════════════════════════

if CH_X1 and CH_Y1 and CH_X1 in avg_pos and CH_Y1 in avg_pos:
    fig, (ax1, ax3) = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
    # R+ (positive field)
    ax1.plot(x_pos, avg_pos[CH_X1] * CALIB, '-.o', color='b', label='real (X1)')
    ax1.plot(x_pos, avg_pos[CH_Y1] * CALIB, '-.o', color='r', label='imag (Y1)')
    ax1.set_title(r'$R^+$')
    ax1.set_ylabel(rf'$\theta_K^{{1\omega}}$ [{CALIB_UNIT}]', fontsize=FS)
    ax1.set_xlabel(X_LABEL, fontsize=FS)
    ax1.legend(fontsize=FS)
    ax1.axhline(0, color='k')
    ax1.grid(True)
    ax2 = ax1.twinx()
    ax2.plot(x, refl_pos, color='firebrick', label=r'$I_{FL}$', linewidth=0.8)
    ax2.legend(fontsize=FS, loc=4)
    # R- (negative field)
    ax3.plot(x_neg, avg_neg[CH_X1] * CALIB, '-.v', color='b')
    ax3.plot(x_neg, avg_neg[CH_Y1] * CALIB, '-.v', color='r')
    ax3.set_title(r'$R^-$')
    ax3.set_xlabel(X_LABEL, fontsize=FS)
    ax3.axhline(0, color='k')
    ax3.grid(True)
    ax4 = ax3.twinx()
    ax4.plot(x, refl_neg, color='firebrick', label=r'$I_{FL}$', linewidth=0.8)
    ax4.set_ylabel(r'$R$ [a.u.]')
    ax4.legend(fontsize=FS, loc=4)
    plt.tight_layout()
    _save(fig, 'realimag')
    plt.show()

# ═══════════════════════════════════════════════════════════════════════════════
# 5. eval_width_and_fit — edge detection + log/const fits
#    Skipped automatically if CH_REFL is None or reflectivity is all zeros.
# ═══════════════════════════════════════════════════════════════════════════════

if CH_REFL and np.any(refl != 0) and theta_DL is not None:

    FIT_EDGE_OFFSET = 5   # points to trim at each edge before fitting

    # Sort everything by ascending x
    _sort = np.argsort(x)
    position  = x[_sort]
    reflex    = refl[_sort]
    tDL       = theta_DL[_sort]
    tOe       = theta_Oe[_sort]
    ebar      = error_bar[_sort]

    # ── Spline interpolation of reflectivity → 1st derivative → edges ────────
    tck = interpolate.splrep(position, reflex, s=0)
    pos_i = np.arange(position[0], position[-1],
                      (position[1] - position[0]) / 10.0)
    ref_i = interpolate.splev(pos_i, tck, der=0)

    h = pos_i[1] - pos_i[0]
    dy = np.gradient(ref_i, h)

    co = 50
    min_i = np.argmin(dy[co:-co]) + co
    max_i = np.argmax(dy[co:-co]) + co
    x1_edge = pos_i[min_i]
    x2_edge = pos_i[max_i]
    if x1_edge > x2_edge:
        x1_edge, x2_edge = x2_edge, x1_edge
    width = round(x2_edge - x1_edge, 2)
    print(f"Edges: {x1_edge:.2f} µm  {x2_edge:.2f} µm   width = {width} µm")

    # ── Plot: reflectivity + derivative + width annotation ────────────────────
    fig, ax = plt.subplots(figsize=(10, 6))
    ax1b = ax.twinx()
    ax.plot(pos_i, ref_i, '-.', color='navy', label='reflection')
    ax1b.plot(pos_i, dy, '-.', color='orange', label='1st derivative')
    ax.plot([x1_edge, x2_edge],
            [interpolate.splev(x1_edge, tck), interpolate.splev(x2_edge, tck)],
            'go', ms=10)
    ax.annotate(f'{width} µm',
                xy=((x1_edge + x2_edge) / 2,
                    (interpolate.splev(x1_edge, tck) + interpolate.splev(x2_edge, tck)) / 2),
                xytext=(0, 20), textcoords='offset pixels',
                color='green', ha='center', fontsize=20)
    ax.set_xlabel(X_LABEL, fontsize=FS)
    ax.set_ylabel('R [a.u.]')
    ax.legend(loc=3, fontsize=FS)
    ax1b.legend(loc=4, fontsize=FS)
    ax.grid(True)
    plt.tight_layout()
    _save(fig, 'width')
    plt.show()

    # ── Fit DL (const) and Oe (log) inside the device ─────────────────────────
    position = position - x1_edge   # shift origin to left edge
    x2_shifted = x2_edge - x1_edge  # = width

    mask = (position > 0) & (position < x2_shifted)
    true_idx = np.where(mask)[0]
    if len(true_idx) > 2 * FIT_EDGE_OFFSET:
        mask[true_idx[:FIT_EDGE_OFFSET]]  = False
        mask[true_idx[-FIT_EDGE_OFFSET:]] = False
    offmask = ~mask

    def _log_fit(pos, A, A0):
        return A0 + A * np.log((width - pos) / pos)

    def _const_fit(pos, y0):
        return np.full_like(pos, y0)

    try:
        popt_const, pcov_const = curve_fit(
            _const_fit, position[mask], tDL[mask],
            sigma=ebar[mask] if np.any(ebar[mask] > 0) else None,
            absolute_sigma=True)
        popt_log, pcov_log = curve_fit(
            _log_fit, position[mask], tOe[mask],
            sigma=ebar[mask] if np.any(ebar[mask] > 0) else None,
            absolute_sigma=True)
        err_const = np.sqrt(np.diag(pcov_const))
        err_log   = np.sqrt(np.diag(pcov_log))

        # Conversion factor and DL field (only meaningful with real calibration)
        conconst = popt_log[0] * width / (2.0 * CURRENT_mA) * 10  # µrad/mT
        conDL    = popt_const[0] / conconst if conconst != 0 else float('nan')
        conDL_err = abs(conDL) * (err_const[0] / abs(popt_const[0])
                                  + err_log[0]  / abs(popt_log[0]))
        print(f"conconst = {conconst:.4g} µrad/mT")
        print(f"H_DL     = ({conDL:.4g} ± {conDL_err:.4g}) mT")
        fit_ok = True
    except Exception as fe:
        print(f"Fit failed: {fe}")
        fit_ok = False

    fig, ax = plt.subplots(figsize=(12, 8))
    ax.plot(position, tDL, '-.v', color='navy',     label='DL')
    ax.errorbar(position, tDL, yerr=ebar, color='navy', fmt='none')
    ax.plot(position, tOe, '-.v', color='firebrick', label='Oe')
    ax.errorbar(position, tOe, yerr=ebar, color='firebrick', fmt='none')
    ax.scatter(position[offmask], np.full(offmask.sum(), np.max(tDL) * 0.8),
               color='r', label='excluded from fit', zorder=5)

    if fit_ok:
        const_arr = np.full(mask.sum(), popt_const[0])
        ax.plot(position[mask], const_arr, color='deepskyblue', linewidth=4,
                label=f'DL const fit: {popt_const[0]:.4g} ± {err_const[0]:.4g} {CALIB_UNIT}')
        ax.plot(position[mask], _log_fit(position[mask], *popt_log),
                color='orange', linewidth=4,
                label=(f'Oe log fit: A={popt_log[0]:.3g} ± {err_log[0]:.3g},  '
                       f'A0={popt_log[1]:.3g}\n'
                       f'conconst={conconst:.4g} µrad/mT,  '
                       f'H_DL=({conDL:.4g} ± {conDL_err:.4g}) mT'))

    ax.axhline(0, color='k')
    ax.set_ylabel(rf'$\theta_K^{{1\omega}}$ [{CALIB_UNIT}]', fontsize=FS)
    ax.set_xlabel(X_LABEL, fontsize=FS)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.25),
              ncol=2, fancybox=True, shadow=True, fontsize=12)
    ax.grid(True)
    ax2 = ax.twinx()
    ax2.plot(position, reflex[_sort], color='firebrick', label=r'$R$')
    ax2.set_ylabel(r'$R$ [a.u.]')
    ax2.legend(fontsize=FS, loc=4)
    plt.tight_layout()
    _save(fig, 'fit')
    plt.show()

    # ── Save data to CSV/Excel ────────────────────────────────────────────────
    try:
        import pandas as pd
        df = pd.DataFrame({
            'x [µm]':          position,
            'R [a.u.]':         reflex[_sort],
            f'theta_Oe [{CALIB_UNIT}]': tOe,
            f'error_bar [{CALIB_UNIT}]': ebar,
            f'theta_DL [{CALIB_UNIT}]': tDL,
        })
        _csv_path = os.path.join(_out_dir, _scanlist_stem + '.txt')
        _xlsx_path = os.path.join(_out_dir, _scanlist_stem + '.xlsx')
        df.to_csv(_csv_path, index=False, sep='\t')
        df.to_excel(_xlsx_path, index=False)
        print(f"Saved data: {_csv_path}")
    except ImportError:
        print("pandas not installed — skipping CSV/Excel save")

else:
    print("Skipping eval_width_and_fit (no reflectivity channel or data).")

print("Done.")
