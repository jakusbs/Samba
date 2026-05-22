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

# ── Run full analysis ────────────────────────────────────────────────────────
# Defaults take care of everything sensible:
#   • Sample name → read from HDF5 metadata (sample_id).
#   • Output folder → Z:\projects\MOKE_lab\Scanning\Analysis_Scripts\<sample>\
#       <timestamp>_<scanlist-stem>_<direction>\
#   • Calibration  → <sample folder>\calibration.txt   (4 lines: 6 mV values,
#       R1, R2, theta).  A template is written on first run if missing —
#       fill it in with real values and re-run.
#   • Current      → HDF5 metadata "hw_keithley_amplitude_mA" if present,
#       otherwise auto-parsed from the scanlist filename (12.5 mA here).
#   • Trace/retrace → auto-detected.  Green/IR scanlists (no _trace/_retrace
#       markers) run a single analysis and return (res, None).
res_trace, res_retrace = analyze_cryo.import_analyze_both(
    SCANLIST,
    see_channels  = ('DC', 'ZI_x1', 'ZI_y1'),
    data_base_dir = DATA_BASE_DIR,
)
