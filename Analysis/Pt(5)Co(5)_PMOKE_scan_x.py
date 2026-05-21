# -*- coding: utf-8 -*-
"""
Pt(5)Co(5)-wu89 PMOKE scan-x analysis — TestSamba 2026-05-21
"""
import sys, os
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from analyze_samba import SambaSOTAnalysis

# ── Paths ─────────────────────────────────────────────────────────────────────
# Scanlist is committed in the repo; HDF5 files live on the lab PC or NAS.
# Set DATA_BASE_DIR to the folder containing the .h5 files if the absolute
# paths inside the scanlist don't resolve on this machine, e.g.:
#   DATA_BASE_DIR = r'\\d.ethz.ch\groups\matl\intermag\projects\moke_lab\Scanning\Data\20260521'
DATA_BASE_DIR = None

SCANLIST = os.path.join(_this_dir, 'Cryo',
    '20260521_Pt(5)Co(5)-wu89_12.5mA_8725Hz_scan-x_PMOKE_12p50mm_TestSamba_lam2_20260521_002301.txt')

# ── Run full analysis ─────────────────────────────────────────────────────────
res = SambaSOTAnalysis.import_analyze(
    scanlist_path  = SCANLIST,
    x1_ch          = 'ZI2 x1',    # 1st harmonic X  → Kerr rotation
    y1_ch          = 'ZI2 y1',    # 1st harmonic Y  → ellipticity
    reflec_ch      = 'avgIn1',    # DC intensity for edge detection (None to skip)
    see_channels   = ['ZI2 x1', 'ZI2 y1'],
    current_mA     = 12.5,
    phase          = 0.0,
    calibration    = 1.0,         # set to µrad/µV once calibrated
    data_base_dir  = DATA_BASE_DIR,
)
