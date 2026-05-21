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
# Root of the Scanning/Data folder on the NAS (adjust if your mount differs).
NAS_DATA = r'Z:\projects\MOKE_lab\Scanning\Data'

# Scanlist lives in ScanLists_Cryo on the NAS.
SCANLIST = os.path.join(NAS_DATA, 'ScanLists_Cryo',
    '20260521_Pt(5)Co(5)-wu89_12.5mA_8725Hz_scan-x_PMOKE_12p50mm_TestSamba_lam2_20260521_002301.txt')

# Folder containing the .h5 files.
# This test was taken before the new Data_Samba_Cryo structure, so they
# are in a plain date subfolder.  Switch to the commented line for new data.
DATA_BASE_DIR = os.path.join(NAS_DATA, 'Data_Samba_Cryo', '20260521')

# ── Run full analysis ─────────────────────────────────────────────────────────
res = SambaSOTAnalysis.import_analyze(
    scanlist_path  = SCANLIST,
    x1_ch          = 'ZI_x1',     # 1st harmonic X  → Kerr rotation
    y1_ch          = 'ZI_y1',     # 1st harmonic Y  → ellipticity
    reflec_ch      = 'DC',        # DC intensity for edge detection (None to skip)
    see_channels   = ['ZI_x1', 'ZI_y1'],
    current_mA     = 12.5,
    phase          = 0.0,
    calibration    = 1.0,         # set to µrad/µV once calibrated
    data_base_dir  = DATA_BASE_DIR,
)
