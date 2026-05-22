# -*- coding: utf-8 -*-
"""
Created on Mon Dec 15 09:49:21 2025

@author: tgoldenbe
"""

import sys
from Jakub_methods import *
from analysis_field_emir import *
import os
import numpy as np
import pandas as pd
from matplotlib.widgets import Button, Slider


cwd= '\\\\d.ethz.ch\\groups\\matl\\intermag\\projects\\moke_lab\\scanning\\Analysis' ## Current working directory (Should be ../Scanning/Analysis)
print(cwd)
os.chdir(cwd)
path = cwd.split('Analysis')[0]+'Data\\Scanlists_S1\\' ## for the 2nd setup use Scanlist_S2' --- The proper filenames are in the Scanlists folders
os.listdir('\\\\d.ethz.ch\\groups\\matl\\intermag\\projects\\moke_lab\\Scanning\\Data\\Scanlists_S1\\')


search_string = 'ETH2'
plots_folder = 'Z:projects\MOKE_lab\People\Jakub'

listnames = search_print_measurements(search_string, path,do_print = True)
for string in listnames:
    if '90deg' in string:
        print(string)
#%%
pols = ['ver','45deg','90deg']
cal_spec = {pol: {} for pol in pols}
cal_spec['90deg'] = ['calibration_Ppol_L2','calibration_Ppol_L4','calibration_Ppol_L4L2']
cal_spec['45deg'] = ['calibration_45deg_L2','calibration_45deg_L4','calibration_45deg_L4L2']
cal_spec['ver'] = ['calibration_Spol_L2', 'calibration_Spol_L4','calibration_Spol_L4L2']
currents = ['6mA','8mA','10mA','12mA','14mA','16mA']
#%%

scanlist ='20260402_ETH2-PT(5)Gd(3)CoFe(3)_20mA_8.75kHz_SOT_PMOKE_ZI_45deg_12.50mm_noDC_lambda2_and_lambda4.txt'

spec = cal_spec['ver'][1][11::]
#res = analyze_SOT.import_analyze(scanlist,path,see_channels = ['data_03'],cal_spec = cal_spec['45deg'][0][11::],ignorLines=list(range(20,50)),theta = 2)
#res.calc_info.theta
analysis_field.get_channels(scanlist)

res = analyze_SHE_OHE.import_analyze_SOT(scanlist,path,see_channels = ['data_02','data_03','data_04'],spec_cal = None,ignorLines= list(range(0,20)))

#%%
calc_names = {pol: {} for pol in pols}
calc_names['ver'] = ['20250626_Pt(5)Co(5)SiN(8)Wu37_6mA_8.75kHz_SOT_PMOKE_ZISR_ver_12.50mm_SHE_autofocus.txt',
                     '20250626_Pt(5)Co(5)SiN(8)Wu37_8mA_8.75kHz_SOT_PMOKE_ZISR_ver_12.50mm_SHE_autofocus.txt',
                     '20250626_Pt(5)Co(5)SiN(8)Wu37_10mA_8.75kHz_SOT_PMOKE_ZISR_ver_12.50mm_SHE_autofocus.txt',
                     '20250626_Pt(5)Co(5)SiN(8)Wu37_12mA_8.75kHz_SOT_PMOKE_ZISR_ver_12.50mm_SHE_autofocus.txt',
                     '20250625_Pt(5)Co(5)SiN(8)Wu37_14mA_8.75kHz_SOT_PMOKE_ZISR_ver_12.50mm_SHE_autofocus.txt',
                     '20250625_Pt(5)Co(5)SiN(8)Wu37_16mA_8.75kHz_SOT_PMOKE_ZISR_ver_12.50mm_SHE_autofocus.txt']
      

calc_names['60deg'] = ['20250627_Pt(5)Co(5)SiN(8)Wu37_6mA_8.75kHz_SOT_PMOKE_ZISR_60deg_12.50mm_SHE_autofocus.txt',
                       '20250627_Pt(5)Co(5)SiN(8)Wu37_10mA_8.75kHz_SOT_PMOKE_ZISR_60deg_12.50mm_SHE_autofocus.txt',
                       '20250627_Pt(5)Co(5)SiN(8)Wu37_16mA_8.75kHz_SOT_PMOKE_ZISR_60deg_12.50mm_SHE_autofocus.txt']

calc_names['45degEllip'] = ['20250902_Pt(5)Co(5)Wu37_6mA_8.751kHz_SOT_PMOKE_ZI_45degEllip_12.50mm.txt',
                             '20250902_Pt(5)Co(5)Wu37_10mA_8.751kHz_SOT_PMOKE_ZI_45degEllip_12.50mm_2scans.txt',
                             '20250902_Pt(5)Co(5)Wu37_12mA_8.751kHz_SOT_PMOKE_ZI_45degEllip_12.50mm.txt',
                             '20250902_Pt(5)Co(5)Wu37_14mA_8.751kHz_SOT_PMOKE_ZI_45degEllip_12.50mm.txt',
                             '20250902_Pt(5)Co(5)Wu37_16mA_8.751kHz_SOT_PMOKE_ZI_45degEllip_12.50mm.txt' ]

calc_names['45deg'] = ['20250628_Pt(5)Co(5)SiN(8)Wu37_6mA_8.75kHz_SOT_PMOKE_ZISR_45deg_12.50mm_SHE_autofocus.txt',
                       '20250627_Pt(5)Co(5)SiN(8)Wu37_8mA_8.75kHz_SOT_PMOKE_ZISR_45deg_12.50mm_SHE_autofocus.txt',
                       '20250627_Pt(5)Co(5)SiN(8)Wu37_10mA_8.75kHz_SOT_PMOKE_ZISR_45deg_12.50mm_SHE_autofocus.txt',
                       '20250627_Pt(5)Co(5)SiN(8)Wu37_12mA_8.75kHz_SOT_PMOKE_ZISR_45deg_12.50mm_SHE_autofocus.txt',
                       '20250627_Pt(5)Co(5)SiN(8)Wu37_14mA_8.75kHz_SOT_PMOKE_ZISR_45deg_12.50mm_SHE_autofocus.txt',
                       '20250627_Pt(5)Co(5)SiN(8)Wu37_16mA_8.75kHz_SOT_PMOKE_ZISR_45deg_12.50mm_SHE_autofocus.txt']

calc_names['90deg'] = ['20250626_Pt(5)Co(5)SiN(8)Wu37_6mA_8.75kHz_SOT_PMOKE_ZISR_90deg_12.50mm_SHE_autofocus.txt',
                       '20250626_Pt(5)Co(5)SiN(8)Wu37_8mA_8.75kHz_SOT_PMOKE_ZISR_90deg_12.50mm_SHE_autofocus.txt',
                       '20250626_Pt(5)Co(5)SiN(8)Wu37_10mA_8.75kHz_SOT_PMOKE_ZISR_90deg_12.50mm_SHE_autofocus.txt',
                       '20250626_Pt(5)Co(5)SiN(8)Wu37_12mA_8.75kHz_SOT_PMOKE_ZISR_90deg_12.50mm_SHE_autofocus.txt',
                       '20250626_Pt(5)Co(5)SiN(8)Wu37_14mA_8.75kHz_SOT_PMOKE_ZISR_90deg_12.50mm_SHE_autofocus.txt',
                       '20250626_Pt(5)Co(5)SiN(8)Wu37_16mA_8.75kHz_SOT_PMOKE_ZISR_90deg_12.50mm_SHE_autofocus.txt']

calc_names['30deg'] = ['20250628_Pt(5)Co(5)SiN(8)Wu37_6mA_8.75kHz_SOT_PMOKE_ZISR_30deg_12.50mm_SHE_autofocus.txt',
                       '20250628_Pt(5)Co(5)SiN(8)Wu37_10mA_8.75kHz_SOT_PMOKE_ZISR_30deg_12.50mm_SHE_autofocus.txt',
                       '20250628_Pt(5)Co(5)SiN(8)Wu37_16mA_8.75kHz_SOT_PMOKE_ZISR_30deg_12.50mm_SHE_autofocus.txt']


#%% plot AC fieldsweep Pt(10)Fe(5)

plots_folder = 'Z:\projects\MOKE_lab\People\Jakub\Jakub'
angles = ['0°','30°','45°','45°($\epsilon$)','60°','90°']
phi = [0,30,45,45,60,90,45]
pols = ['ver','30deg','45deg','45degEllip','60deg','90deg']
files45deg_L2 = {}
files45deg_L4L2 = {}
files45deg_L4 = {}
files0deg_L4L2 = {}
files0deg_L4 = {}
files0deg_L2 = {}
files90deg_L4L2 = {}
files90deg_L4 = {}
files90deg_L2 = {}

path = 'Z:/projects/MOKE_lab/Scanning/Data/Data_S1/'
calib_path ='Z:\projects\MOKE_lab\Scanning\Analysis\Pt(3)Fe(5)\LMokeCalibrations'

#45deg_L2

files45deg_L2['L+'] = path+'20251211/moke_17h01m04.243.nxs'
files45deg_L2['L-'] = path+'20251211/moke_17h37m44.817.nxs'


#45deg_L4L2
files45deg_L4L2['L+'] = path+'20251211/moke_10h42m17.315.nxs'
files45deg_L4L2['L-'] = path+'20251211/moke_11h29m53.639.nxs'

#45deg_L4
files45deg_L4['L+'] = path+'20251211/moke_10h11m27.539.nxs'
files45deg_L4['L-'] = path+'20251211/moke_09h52m25.335.nxs'

#0deg_L4L2
files0deg_L4L2['L+'] = path+'20251209/moke_12h54m48.539.nxs'
files0deg_L4L2['L-'] = path+'20251209/moke_14h31m40.342.nxs'

#0deg_L4
#files0deg_L4['L+'] = path+'20251126/moke_11h27m51.881.nxs'
#files0deg_L4['L-'] = path+'20251210/moke_12h08m49.718.nxs'

#0deg_L2
files0deg_L2['L+'] = path+'20251209/moke_12h39m33.580.nxs'
files0deg_L2['L-'] = path+'20251209/moke_12h09m50.557.nxs'


#90deg_L4L2
files90deg_L4L2['L+'] = path+'20251212/moke_12h08m04.287.nxs'
files90deg_L4L2['L-'] = path+'20251212/moke_11h46m55.578.nxs'

#90deg_L4
#files90deg_L4['L+'] = path+'20251204/moke_11h01m05.957.nxs'
#files90deg_L4['L-'] = path+'20251204/moke_11h41m20.716.nxs'

#90deg_L2
files90deg_L2['L+'] = path+'20251212/moke_10h51m56.816.nxs'
files90deg_L2['L-'] = path+'20251212/moke_11h10m00.503.nxs'

files = {'45deg_L2'  :files45deg_L2,
         '45deg_L4L2' : files45deg_L4L2,
         '45deg_L4' : files45deg_L4,
         'Spol_L2'  :files0deg_L2,
         'Spol_L4L2' : files0deg_L4L2,
         'Spol_L4' : files0deg_L4,
         'Ppol_L2'  :files90deg_L2,
         'Ppol_L4L2' : files90deg_L4L2,
         'Ppol_L4' : files90deg_L4,}

plot_jc = False
#colors = plt.get_cmap('magma')(np.linspace(0,0.8,len(files.keys())))
markers = ['o','v','s','*','D','X']
h = 10
shift = 0



i = 0
calibration_type='LM'
#calibration_polarisation='_45deg_L4L2'


for x,obj in files.items():
    calibration_polarisation='_' + x
    
    fig, ax = plt.subplots(dpi=200, figsize=(7, 4.5))
    
    for y in obj:
        if y == 'L+':
            calibration_type='_LP'
            color='red'
        elif y == 'L-':
            calibration_type='_LM'
            color='blue'
        ax=plot_DC_kerr(ax,obj[y],calibration_type,calibration_polarisation,y,color,calib_path)
    
    ax.set_ylabel(r'$\theta_{DC}$ [$\mu$rad]   (offset)', fontsize=18)    
    ax.set_xlabel(r'$H_{ext}$ [mT]', fontsize=18)  
    ax.grid()
    lgd = ax.legend(bbox_to_anchor=(1, 0.8), markerscale=3, fontsize=16)

    fig.savefig(plots_folder + calibration_polarisation+'.svg', format='svg', bbox_inches='tight')
    
    plt.show()
    plt.close(fig)
    i+=1

  
# Format the plot  
