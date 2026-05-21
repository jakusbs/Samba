# -*- coding: utf-8 -*-
"""
Pt(5)Co(5)-wu89 PMOKE scan-x analysis — TestSamba 2026-05-21
"""
import sys, os
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from analyze_samba import analyze_cryo

# ── Paths ─────────────────────────────────────────────────────────────────────
NAS_DATA = r'Z:\projects\MOKE_lab\Scanning\Data'

SCANLIST = os.path.join(NAS_DATA, 'ScanLists_Cryo',
    '20260521_Pt(5)Co(5)-wu89_12.5mA_8725Hz_scan-x_PMOKE_12p50mm_TestSamba_lam2_20260521_002301.txt')

DATA_BASE_DIR = os.path.join(NAS_DATA, 'Data_Samba_Cryo', '20260521')

# Where the plots (and timestamped subfolder) should go.  Leave as None to use
# the scanlist's own directory.
SAVE_DIR = os.path.join(NAS_DATA, 'Analysis_Output', 'Pt(5)Co(5)-wu89_PMOKE')

# ── Run full analysis ────────────────────────────────────────────────────────
# Trace and retrace are analysed independently because piezo hysteresis
# shifts the real sample position between the two scan directions.  For a
# legacy Green/IR scanlist without trace/retrace markers, import_analyze_both
# falls back to a single analysis and returns (res, None).
res_trace, res_retrace = analyze_cryo.import_analyze_both(
    SCANLIST,
    see_channels   = ('DC', 'ZI_x1', 'ZI_y1'),
    current_mA     = None,          # None → auto-parsed from filename (12.5 mA here)
    calibration    = 1.0,           # µrad/V (or whatever your sln gives).  1.0 → raw V.
    data_base_dir  = DATA_BASE_DIR,
    save_dir       = SAVE_DIR,
)
