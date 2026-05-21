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
# Root of the Scanning/Data folder on the NAS (adjust if your mount differs).
NAS_DATA = r'Z:\projects\MOKE_lab\Scanning\Data'

# Scanlist lives in ScanLists_Cryo on the NAS.
SCANLIST = os.path.join(NAS_DATA, 'ScanLists_Cryo',
    '20260521_Pt(5)Co(5)-wu89_12.5mA_8725Hz_scan-x_PMOKE_12p50mm_TestSamba_lam2_20260521_002301.txt')

# Folder containing the .h5 files.
DATA_BASE_DIR = os.path.join(NAS_DATA, 'Data_Samba_Cryo', '20260521')

# ── Run full analysis — trace and retrace separately ─────────────────────────
# Trace and retrace are analysed independently because piezo hysteresis shifts
# the real sample position between the two scan directions.
res_trace, res_retrace = analyze_cryo.import_analyze_both(
    SCANLIST,
    see_channels   = ('DC', 'ZI_x1', 'ZI_y1'),
    current_mA     = 12.5,
    data_base_dir  = DATA_BASE_DIR,
)
