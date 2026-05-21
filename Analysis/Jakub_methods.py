# -*- coding: utf-8 -*-
"""
Created on Wed Dec 17 14:57:21 2025

@author: jstrnad
"""

import os
import pandas as pd
import sys;sys.path.append('../Scripts/')
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
import importlib
import h5py
import analysis_field
import analysis_2D
#import nexus
import analysis_field_emir
importlib.reload(analysis_field)
importlib.reload(analysis_2D)
import scipy

from  scipy import ndimage
from scipy.optimize import minimize
from scipy.optimize import curve_fit
from scipy import interpolate
from scipy.interpolate import interp1d
from matplotlib.colors import Normalize
from matplotlib.patches import Rectangle
import math
from scipy.optimize import curve_fit
from scipy.optimize import dual_annealing
#from __future__ import print_function
from ipywidgets import interact, interactive, fixed
import ipywidgets as widgets
from IPython.display import display
import re
import csv
from scipy.optimize import differential_evolution
import datetime


def iterate_find_impurity(theta_DL,path_findimp,calc_info,do_plot = False):
    DL_ind = np.arange(0,len(theta_DL),1)
    theta_DL = np.abs(theta_DL)# Code works only for positive DL torque, but returns mask anway --> does not matter
    dy = np.gradient(theta_DL)
    Redge = (np.where(dy[1:-2] == min(dy[1:-2]))[0] + 1)[0]
    Ledge = (np.where(dy[1:-2] == max(dy[1:-2]))[0] + 1)[0]
    print('R-Ledge',Redge,Ledge)
    def min_mean_DL(inputs):
        mask = analysis_field.find_impurities(theta_DL,Ledge,Redge,standard_noise = inputs[0],dIfac = inputs[1])
        #
        #print(type(mask))
        #mask_ind = DL_ind[mask]
        device_mask = (DL_ind > Ledge) & (DL_ind < Redge)
        use_mask = device_mask & np.invert(mask)
        #print(np.sum(use_mask))
        return np.std(theta_DL[use_mask]) #/(np.sum(use_mask))
    #res = minimize(min_mean_DL,x0 = [0.001,10])
    """
    bounds = [(0.0001,0.01),(1,100)]
    nint = 1
    tries = 0
    while nint == 1:
        res = differential_evolution(min_mean_DL,bounds)
        nint = res.nit
        print(res.fun)
        tries += 1
        if tries > 100:
            #raise Exception('Did not converge to find impurity?')
            print('Did not converge to find impurity?')
            break
    
    print(res.x,res.nit)
    mask = analysis_field.find_impurities(theta_DL,Ledge,Redge,standard_noise = res.x[0],dIfac=res.x[1])
    """
    mask = analysis_field.find_impurities_peaks(theta_DL,peakheight = 1,do_plot = False)
    mask_ind = DL_ind[mask]
    #print(mask)
    device_mask = (DL_ind > Ledge) & (DL_ind < Redge)
    use_mask = device_mask & np.invert(mask)
    
    if do_plot:
        plot_name = calc_info.date + '_ ' + str(calc_info.current) + 'mA ' + calc_info.LightPol + 'Pol'
        plt.title(plot_name)
        plt.plot(theta_DL)
        plt.plot(mask_ind,np.ones(len(mask_ind))*0.9*max(theta_DL),label = 'removed (find_impurity)')
        plt.scatter( DL_ind[use_mask],theta_DL[use_mask],color = 'r',label = 'Used for fit')
        plt.legend(loc = 'lower left')
        #plt.savefig(path_findimp+'\\'+ 'find_vacancy_minimize_'+plot_name +'.png' ,bbox_inches='tight')
        plt.show()
    return use_mask

def search_print_measurements(search_string,file_path,do_print = True):
    
    datalist = os.listdir(file_path)
    datalist1=[i for i in datalist if search_string in i]
    
    datadict = {}             ## For choosing the file by typing the corresponding number
    for i,j in zip(range(len(datalist1)),datalist1):
        datadict[i] = j
    
    #sad ih mogu easy ispisati   
    if do_print:
        for i in datadict:
            print(i,datadict[i])
    return datalist1
        
class analyze_SOT:
    def __init__(self,calc_name,path):
        # Initialize instance variables (attributes)
        self.data = None
        self.calc_name = calc_name
        self.paths = []
        self.calc_info  = {}
        """
                              
                              
                              
}"""
        
   

    def prepare_SOT_data(self,spec_calibration = None):
        
        class CalcInfo:
            
            def __init__(self, theta,theta2, sln, date, calc_info,R1,R2,logfilenameShort):
                    # Assign each value directly as an attribute
                    self.theta = theta
                    self.theta2 = theta2
                    self.sln = sln
                    self.date = date
                    self.system = calc_info[0]
                    self.current = float(calc_info[1].split('mA')[0])  # Assuming 'mA' is present
                    self.LI_ref = calc_info[2]
                    self.measurement = calc_info[3]
                    self.MOKE_type = calc_info[4]
                    self.LI_type = calc_info[5]  
                    self.LightPol = calc_info[6]
                    self.logfilenameShort = logfilenameShort
                    self.R = [R1,R2]
                    if self.LI_type == 'ZISR':
                        dchannel_type = '2LI+avgSingle'
                        print(dchannel_type)
                    else:
                        dchannel_type = None
                    if 'SR' in calc_info[5]:
                        self.LI_type = 'srlockin'
                    elif 'ZI' in calc_info[5]:
                        self.LI_type = 'zi' 
                        
                    self.dchanneltype = dchannel_type
                    
        cwd = os.getcwd()
        datalist1 = self.calc_name
        print(datalist1)
        
        if len(str(datalist1).replace("'", "?").split("?")) == 1:
            logfilenameShort=(str(datalist1).replace("'", "?").split("?"))[0]
        else:
            logfilenameShort=(str(datalist1).replace("'", "?").split("?"))[1]
        print(logfilenameShort)
        path1=cwd+'\\'+(logfilenameShort.split('_'))[1]
        
        self.path1 = path1
        time=str(datetime.datetime.now()).split('.')[0]
        dtime=''.join((time.split(' ')[0]).split('-'))
        ttime=''.join((time.split(' ')[1]).split(':'))
        
        
        try:
            # Create target Directory
            os.makedirs(path1)
            print("Directory " , path1 ,  " Created ") 
        except FileExistsError:
            print("Directory " , path1 ,  " already exists")
            
        
        for name in logfilenameShort.split('_'):
            if 'mA' in name:
                current=name
        current_date=current+ ' ' + (logfilenameShort.split('_'))[0]
        path2=path1+'\\'+ current_date
        self.path2 = path2
        try:
            # Create target Directory
            os.makedirs(path2)
            print("Directory " , path2 ,  " Created ")
        except FileExistsError:
            print("Directory " , path2 ,  " already exists")
        try:
            # Create target Directory
            path_findimp = path1 + '//' + 'find_impurity_minimize'
            self.path_findimp = path_findimp
            #os.makedirs(path_findimp)
            #print("Directory " , path_findimp ,  " Created ") 
        except FileExistsError:
            print("Directory " , path_findimp ,  " already exists")
           
        #Where to save the plots
        path3=path2+'\\'+dtime+' '+ttime
        self.path3 = path3
        try:
            # Create target Directory
            os.makedirs(path3)
            print("Directory " , path3 ,  " Created ") 
        except FileExistsError:
            print("Directory " , path3 ,  " already exists")
            
        
        plotname=logfilenameShort.split('.txt')[0]+'.png'    
        
        print('There are the following Calibration files:')
        for file in os.listdir(path1):
            if file.startswith("calibration"):
                print(file)
        """
        print('\nMOKE @',Lmoke[0:-4])
        for file in os.listdir(path1):
            if file.startswith("calibration"):
                if Lmoke in file:
                    xtra_cal_name = file[11:-4]
                    found = True
                    print('AUTO: using this file! ',xtra_cal_name)
        if not found:
            xtra_cal_name = input('Use xtra calibration file? Leave empty if standard one is preferred')
        """
        
        ##Calibration data+ Resistivities+ Phase
        
        try: 
            
            if spec_calibration:
                print(path1+'\\calibration' + spec_calibration + '.txt')
                file = open(path1+'\\calibration' + spec_calibration + '.txt', 'r')
                
                print('Using the calibration file: calibration' + spec_calibration + '.txt')
            else:
                file = open(path1+'\\calibration' + '.txt', 'r')
                print(" Using Calibration file" , path1+'\\calibration' + '.txt') 
            calibration_data=np.fromstring(file.readline().split('\n')[0], dtype=float, sep=' ')
            R1=float(file.readline().split('\n')[0])
            R2=float(file.readline().split('\n')[0])
            theta=float(file.readline().split('\n')[0])
            #theta2 = float(file.readline().split('\n')[0])
            file.close()
        
        except FileNotFoundError:
            file= open(path1+'\\calibration.txt', 'w')
            print("Calibration file" , path1+'\\calibration.txt' ,  " created ")
            print('\n Input the calibration data manually')
            calibration_data=input('\n')
            
            print('\n Input the resistance of the NM/M system')  
            R1=input('\n')
            print('\n Input the resistance of the M (reference without NM)')
            R2=input('\n')
            
            print('\n Input the first harmonic phase offset')
            theta=input('\n')
            print('\n Input the 2nd harmonic phase offset')
            #theta2=input('\n')
            
         
            file.write(calibration_data+ '\n')
            file.write(R1+ '\n' )
            file.write(R2+ '\n')
            file.write(theta)
            #file.write(theta2)
        
            calibration_data=np.fromstring(calibration_data, dtype=float, sep=' ')
            R1=float(R1)
            R2=float(R2)
            theta=float(theta)
            #theta2=float(theta2)
            
            file.close()
         
        sln = analysis_field.calibrate(np.linspace(0,25,6),calibration_data,plotting=False)
        sln = 1/(sln)*np.pi/180.0*1e6 ##(µrad/mV)
        plt.show()
        #sln=-sln #remove this later
        print(R1,R2)
        
        theta_rad=theta*np.pi/180.0
        print(theta_rad)
                
        #CTR = 2.213e-05*1e6
        #CTR= -0.58*1e-4*1e6  
        print(cwd)
        pattern =r'_(.*?)(?=_|$)'
        date_pattern = r'(\d{8})'
        # Find all matches
        print('datalist1',datalist1)
        calc_info = re.findall(pattern, datalist1[0])
      
        date = re.findall(date_pattern,datalist1[0])[0]
        print(logfilenameShort)
       
        theta2 = 0
        self.calc_info = CalcInfo(theta,theta2,sln,date,calc_info,R1,R2,logfilenameShort)
        print(logfilenameShort)
        
        
        
        
        return self
    
    def import_SOT_data(self,ignorLines = [],new = True):
        
        self.data =  analysis_field.linescan_SNE_SOT(self.calc_info.logfilenameShort,ch_pol='None',ignorLines=ignorLines,ch_x='actuator_1_1',setup=1,
                                                     field = True,setup_spec =  self.calc_info.dchanneltype, new = new)
        print('len data: ',len(self.data))
        return self
    def import_SOT_data_new(self,ignorLines = [],remove_channels = []):
        
        self.data =  analysis_field.linescan_calc_Tobi(self.calc_info.logfilenameShort,ch_pol='None',ignorLines=ignorLines,ch_x='actuator_1_1',rm_channels = remove_channels,convert_dict_to_list=False)
        return self
    """
    def get_edges(self):
        
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
            
        elif len(self.data) == 10 :
            x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        elif len(self.data) == 8 :
            x,I,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        else:
            x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
            
        reflec = I[2]
        mask = np.argsort(x)
        xsort = x[mask]
        reflecsort = I[2][mask]
        fig = plt.figure()   
        edges, width = analysis_field.find_edges_width(xsort,reflecsort)
        print('Edges',edges)
        self.edges = edges
        self.dev_center = sum(edges)/2
        return self
     """   
    def get_edges(self, I_ch = 'averagein2value'):
        
        """
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
            
        elif len(self.data) == 10 :
            x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        else:
            x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        """ 
        
        D = self.data
        
        reflec = D[I_ch][2]
        mask = np.argsort(D['x'])
        xsort = D['x'][mask]
        reflecsort = reflec[mask]
        fig = plt.figure()   
        edges, width = analysis_field.find_edges_width(xsort[5:-5],reflecsort[5:-5])
        print('Edges',edges)
        self.edges = edges
        self.dev_center = sum(edges)/2
        return self
    """
    def get_theta(self):
        
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
            
        elif len(self.data) == 10 :
            x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        elif len(self.data) == 8 :
            x,I,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        else:
            x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
            
        self.get_edges()
        channel = 'pos'
        theta_pos = analysis_field.find_phase(x,l1x1[4],l1y1[4],edges = self.edges,ch = channel)
        
        channel = 'neg'
        theta_neg = analysis_field.find_phase(x,l1x1[5],l1y1[5],edges = self.edges,ch = channel)
        
        theta = np.mean([theta_pos,theta_neg])
        print('Mean theta = %.2f \nfrom' %theta, [theta_pos,theta_neg])
        plt.grid()
        plt.legend()
        plt.show()
        self.calc_info.theta = theta
        return self
    """
    def get_theta(self,LI_str = None):
        """
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
            
        elif len(self.data) == 10 :
            x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        else:
            x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
           """ 
        self.get_edges()
        D = self.data
        if LI_str:
            li = LI_str
        else:
            li = self.calc_info.LI_type
        fig = plt.subplots(figsize=(4,3))
        channel = 'pos'
        theta_pos = analysis_field.find_phase(D['x'],D[li+'x1'][4],D[li+'y1'][4],edges = self.edges,ch = channel)
        
        channel = 'neg'
        theta_neg = analysis_field.find_phase(D['x'],D[li+'x1'][5],D[li+'y1'][5],edges = self.edges,ch = channel)
        
        theta = np.mean([theta_pos,theta_neg])
        print('Mean theta = %.2f \nfrom' %theta, [theta_pos,theta_neg])

        plt.grid()
        plt.legend()
        #plt.text(0.01, 0.25, 'theta = %.2f' %theta)
        plt.show()
        
        
        self.calc_info.theta = theta
        fig = plt.subplots(figsize=(4,3))
        channel = 'pos'
        theta2_pos = analysis_field.find_phase(D['x'],D[li+'x2'][4],D[li+'y2'][4],edges = self.edges,ch = channel)
        
        channel = 'neg'
        theta2_neg = analysis_field.find_phase(D['x'],D[li+'x2'][5],D[li+'y2'][5],edges = self.edges,ch = channel)
        
        theta2 = np.mean([theta2_pos,theta2_neg])
        print('Mean theta2 = %.2f \nfrom' %theta2, [theta2_pos,theta2_neg])

        plt.grid()
        plt.legend()
        #plt.text(0.01, 0.25, 'theta2 = %.2f' %theta2)
        plt.show()
        
        
        self.calc_info.theta2 = theta2
        return self
    
    
    
    def see_intensity(self,ch_var = 'data_02',ignorelines = list(range(0,0))+list(range(0,0)),setup = 1):
        logfilenameShort = self.calc_name[0]
        I, var_all = analysis_field.intensity_mean_SOT(logfilenameShort,ch_var=ch_var, ignorLines=ignorelines, setup=setup)
    
        print(len(var_all))
        plt.figure(figsize=(28,6))
        plt.plot(I*1e3)
        #plt.ylim(0, 200)
        plt.xlabel('number of scans')
        plt.ylabel('mean intensity [mV]')
        plt.show()
        
    
        colors = plt.cm.copper(np.linspace(0, 1, len(var_all)))
        plt.figure(figsize=(15,12))
        for i in range(len(var_all)):
            plt.plot(self.data[0], var_all[i],"x-",color=colors[i])
            #plt.plot(DATA[0], r2w_normalized[i],"x-",color=colors[i])
        #plt.xlim(-8.2,-7)
        #plt.ylim(0, 0.86)
        plt.axhline(y = 0.0, color = 'r', linestyle = '-')
        plt.show()
    
    def see_intensity_new(self,Icutoff,ch_var = 'data_02', ignorelines = [],only_focused = True, shift = True,setup = 1,plot_raw_1stharmonic = True):
        #First remove data which is not in focus
        logfilenameShort = self.calc_name[0]
        remove_lines = analysis_field.plot_scans_tobi(logfilenameShort,ignorelines,data_ch = 'data_02', only_focused = only_focused, shift = shift, Icutoff = Icutoff,
                                              setup = 1,setup_spec = self.calc_info.dchanneltype)
        if plot_raw_1stharmonic:
            print('PLOTTING raw first harmonic x-data for both relay positions!\n')
            analysis_field.plot_scans_tobi(logfilenameShort,remove_lines,data_ch = 'data_04', only_focused = only_focused, shift = shift, Icutoff = Icutoff,
                                              setup = 1,setup_spec = self.calc_info.dchanneltype)
        return remove_lines
        
    def evaluate_data(self,phase = None,phase2 = None,plot_2axs = False,do_plot = 'sumdiff',ylim = None):
        plotname = self.calc_info.system + '_' + str(self.calc_info.current) + 'mA_' + self.calc_info.LightPol +'_' + do_plot
        if phase:
            theta = phase
            print('Ignoring calc_info.theta = ', self.calc_info.theta, '. Using ', theta)
        else:
            theta = self.calc_info.theta
        if phase2:
            theta2 = phase2
            print('Ignoring calc_info.theta2 = ', self.calc_info.theta2, '. Using ', theta2)
        else:
            theta2 = self.calc_info.theta2
        lock_in = self.calc_info.LI_type
        
        if lock_in == 'SR':
            fac = 1000
        elif 'ZI' in lock_in:
            fac = 1
        sln = self.calc_info.sln
        theta_rad=theta*np.pi/180.0
        theta_rad2=theta2*np.pi/180.0
        fac = 1
        print('Len Data',len(self.data))
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
            theta_2w_avg6 = (l2x2[2]*np.cos(theta_rad2) + l2y2[2]*np.sin(theta_rad2))/I_BD_avg6[2]
            theta_2w_avg6_err=np.sqrt((l2x2[3]*np.cos(theta_rad2))**2+(l2y2[3]*np.sin(theta_rad2))**2) ## Gaussian Error Propagation
        elif len(self.data) == 10 :
            x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        elif len(self.data) == 8 :
            x,I,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
           
        else:
            x,I,I1,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
            
        if np.mean(H[1]) == 1 or np.mean(H[1]) == 0: #Check if H is actually relaypos
            self.calc_info.H_ext = relaypos[1]
        else:
            self.calc_info.H_ext = H[1]
            
            
            
        if do_plot == 'realimag':
            plot_2axs = True
            print('Plotting 2 axes')
            
        theta_Oe=(l1x1[2]*np.cos(theta_rad)+l1y1[2]*np.sin(theta_rad))*sln  
        theta_DL=(l1x1[1]*np.cos(theta_rad)+l1y1[1]*np.sin(theta_rad))*sln
        theta_2w = (l1x2[2]*np.cos(theta_rad2) + l1y2[2]*np.sin(theta_rad2))
        theta_2w_err=np.sqrt((l1x2[3]*np.cos(theta_rad2))**2+(l1y2[3]*np.sin(theta_rad2))**2) ## Gaussian Error Propagation
        
        error_bar=np.sqrt((l1x1[3]*np.cos(theta_rad))**2+(l1y1[3]*np.sin(theta_rad))**2)*np.abs(sln) ## Gaussian Error Propagation
        position=x
        theta_neg=(l1x1[5]*np.cos(theta_rad)+l1y1[5]*np.sin(theta_rad))*sln  
        theta_pos=(l1x1[4]*np.cos(theta_rad)+l1y1[4]*np.sin(theta_rad))*sln
        if plot_2axs:
            fig,[ax1,ax3] = plt.subplots(figsize=(8,8),nrows = 2)
        else:
            fig,ax1 = plt.subplots(figsize=(8,6))
        if do_plot == 'negpos':
            ax1.plot(x,theta_pos,'-.v',color='red',label='+H$_{ext}$')
            #print(theta_pos*fac)
            ax1.plot(x,theta_neg,'-.v',color='blue',label='-H$_{ext}$')
            y = theta_pos
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=24)
            """
            if 'ZI' in lock_in:
                ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=24)
            else:
                ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [µrad]',fontsize=24)
                """
                
        elif do_plot == 'sumdiff':
            if plot_2axs:
                ax3.plot(x,theta_Oe*fac,'-.v',color='black',label='sum')
                ax3.errorbar(x,theta_Oe*fac, yerr=error_bar ,color='black')
                ax3.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=24)
    
            else:
                ax1.plot(x,theta_Oe*fac,'-.v',color='black',label='sum')
                ax1.errorbar(x,theta_Oe*fac, yerr=error_bar ,color='black')
            ax1.plot(x,theta_DL*fac,'-.v',color='green',label='diff')
            y = theta_Oe
            ax1.errorbar(x,theta_DL*fac, yerr=error_bar ,color='green')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=24)
            """
            if lock_in == 'ZI':
                ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
            else:
                ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [µrad]')
                """
    
        elif do_plot == 'custom_sumdiff':
            theta_Oe = (theta_pos + theta_neg)/2*sln
            theta_DL = (theta_pos - theta_neg)/2*sln
            if plot_2axs:
                ax3.plot(x,theta_Oe*fac,'-.v',color='black',label='custom sum')
                ax3.errorbar(x,theta_Oe*fac, yerr=error_bar ,color='black')
                if lock_in == 'ZI':
                    ax3.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
                else:
                    ax3.set_ylabel(r'$\theta_{K}^{1\omega}$ [µrad]')
            ax1.plot(x,theta_DL*fac,'-.v',color='green',label='custom diff')
            y = theta_Oe
            ax1.errorbar(x,theta_DL*fac, yerr=error_bar ,color='green')
            if lock_in == 'ZI':
                ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
            else:
                ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [µrad]')
        elif do_plot == 'realimag':
            fig,[ax1,ax3] = plt.subplots(figsize=(8,8),nrows = 2)
            ax1.plot(x,l1x1[5]*sln,'-.v',color='k',label='- real')
            ax3.plot(x,l1y1[5]*sln,'-.v',color='cyan',label='- imag')
            ax1.plot(x,l1x1[4]*sln,'-.o',color='r',label='+ real')
            ax3.plot(x,l1y1[4]*sln,'-.o',color='green',label='+ imag')
            #y = l1x1[5]
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
            ax3.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
    
    
                
        elif do_plot == 'findphase':
            #ax1.plot(x,l1x1[4]*np.cos(theta_rad) + l1y1[4]*np.sin(theta_rad),'-.v',color='k',label='real +')
            ax1.plot(x,-l1x1[4]*np.sin(theta_rad) + l1y1[4]*np.cos(theta_rad),'-.v',color='cyan',label='imag + (-> $0$)')
            ax1.plot(x,-l1x1[5]*np.sin(theta_rad2) + l1y1[5]*np.cos(theta_rad2),'-.v',color='k',label='imag - (-> $0$)')
            
            ax1.set_ylim([-1, 1])
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=24)
            
        elif do_plot == '2harmonic':
            #ax1.plot(x,l1x1[4]*np.cos(theta_rad) + l1y1[4]*np.sin(theta_rad),'-.v',color='k',label='real +')
            ax1.plot(x,theta_Oe*fac,'-.v',color='black',label='sum')
            ax1.errorbar(x,theta_Oe*fac, yerr=error_bar ,color='black')
            ax1.plot(x,theta_DL*fac,'-.v',color='green',label='diff')
            ax1.errorbar(x,theta_DL*fac, yerr=error_bar ,color='green')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
            
            """
            if LI == 1:
                ax3.plot(x,theta_2w,'-.v',color='k',label='LI2 sum')
                ax3.errorbar(x,theta_2w, yerr=theta_2w_err ,color='k')       
                ax3.set_ylabel(r'$R^{2\omega}$ ')     """  
        
            ax3.plot(x,theta_2w_avg6,'-.v',color='k',label='LI2 sum')
            ax3.errorbar(x,theta_2w_avg6, yerr=theta_2w_avg6_err ,color='k')       
            ax3.set_ylabel(r'$R^{2\omega}/R$ ')
            
        if ylim:
            ax1.set_ylim(ylim)
        
        
        #ax1.set_xlabel(r'$x$ $[\mu m]$')
        ax1.legend(loc='upper center', bbox_to_anchor=(0.5, 1.2), ncol=2, fancybox=True, shadow=True, fontsize=24)
        #ax1.legend(fontsize=24,loc='center')
        ax1.grid(1)
        ax1.axhline(y = 0, color = 'k')
        ax2 = ax1.twinx()
        ax2.plot(x,I[2],color='firebrick',label='reflection')
        if plot_2axs:
            ax3.grid(1)
            ax4 = ax3.twinx()
            ax4.plot(x,I[2],color='firebrick',label='refl. BD')
            #ax4.set_ylabel(r'$R$ [a.u.]')
            ax3.legend(fontsize=24)
            ax3.axhline(y = 0, color = 'k')
            ax3.set_xlabel(r'y [$\mu$ m]',fontsize=24)
            #ax3.set_ylim(ylim[0],ylim[1])
            ax4.legend(fontsize=24)
        else:
            ax1.set_xlabel(r'y [$\mu$m]',fontsize=24)
        
        #ax2.set_ylabel(r'$R$ [a.u.]')
        
        ax2.legend(fontsize=24,loc = 'lower right')
        ax2.tick_params(axis='both', which='major', labelsize=22)
        ax1.tick_params(axis='both', which='major', labelsize=22)
        plt.tight_layout(pad=0.4, w_pad=0.5, h_pad=1.0)
        #plt.suptitle(r'$\varphi = %.0f$°: %s' %(theta,self.calc_info.logfilenameShort[9:-4]))
        plt.savefig(self.path3+ '\\'+plotname+'.png',bbox_inches='tight')
        plt.savefig(self.path3+ '\\'+plotname+'.eps',bbox_inches='tight')
        plt.show()
        mask = np.argsort(x)
        analyzed_data = {
            "x": x[mask],
            "intFL": I[2][mask],
            "sum": theta_Oe[mask],
            "diff": theta_DL[mask],
            "pos": theta_pos[mask],
            "neg": theta_neg[mask],
            "errorbar": error_bar[mask],
        }
        
        # Create DataFrame
        data_df = pd.DataFrame(analyzed_data)
        self.analyzed_data = data_df
        
        return self
        
        
        #ww = interactive(placeholder,i=(0,len(p)-1,1),theta=(0,360,1))
        #display(ww)
        
    def eval_width_and_fit(self,reflec_for_mask = False,current_coefficient2 = 0.99,do_plot = False,use_find_impurity =False,co = 50,shift = 0,nice_plot = False):
        
        theta_rad=self.calc_info.theta*np.pi/180.0
        theta_rad2=self.calc_info.theta2*np.pi/180.0
        mpl.rcParams['font.size'] = 16
        def derivatives(x,y): ##Calculation of the first and second derivatives of the intensity diode's DC signal
            h=x[1]-x[0]
            dy,ddy,newpos=[0]*(len(x)-2),[0]*(len(x)-2),[0]*(len(x)-2)
            
            for i in range(1,(len(x)-1)):
                dy[i-1]=(y[i+1]-y[i-1])/(2*h)
                ddy[i-1]=(y[i+1]-2-y[i]+y[i-1])/(h*h)
                newpos[i-1]=x[i]
            return newpos,dy,ddy
        
        def parallel_channel(R1,R2):   ## Calculation the ratio of the current flowing through the NM with respect to the applied current (parallel channel model)
            coeff=1-R1/R2
            #coeff=round(coeff,2)
            return coeff
        plotname = self.calc_info.system + '_' + str(self.calc_info.current) + 'mA_' + self.calc_info.LightPol
        
        #x,I,I2,l1x1,l1y1,l1x2,l1y2,l2x2,l2y2,l2x1,l2y1,relaypos,H=DATA
        #x,I,I2,l1x1,l1y1,l1x2,l1y2,H,relaypos= DATA
        #x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H= self.data
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data

            theta_2w_avg6 = -(l2x2[2]*np.cos(theta_rad2) + l2y2[2]*np.sin(theta_rad2))/I_BD_avg6[2]*1e5
            theta_2w_avg6_err=np.sqrt((l2x2[3]*np.cos(theta_rad2))**2+(l2y2[3]*np.sin(theta_rad2))**2)*1e5 ## Gaussian Error Propagation
        elif len(self.data) == 10:
             x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
             
        elif len(self.data) == 8 :
            x,I,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        else:
            x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
            
        position=np.array(x)
        #print('Position',position)
        #position=np.flip(position)
        #print(position)
        #position=position[::-1] ##MINUS SIGN BECAUSE I ALWAYS MEASURE AS:  -I || B (for B>0)
        reflex=np.array(I[2])
        
        #theta_rad=90*np.pi/180.0 
        #print(theta_rad)    
        theta_Oe=(l1x1[2]*np.cos(theta_rad)+l1y1[2]*np.sin(theta_rad))*self.calc_info.sln   ##MINUS SIGN BECAUSE I ALWAYS MEASURE AS:  -I || B (for B>0)
        theta_DL=(l1x1[1]*np.cos(theta_rad)+l1y1[1]*np.sin(theta_rad))*self.calc_info.sln  ##MINUS SIGN BECAUSE I ALWAYS MEASURE AS:  -I || B (for B>0)
        #theta_DL=(l1x1[2]*np.cos(theta_rad)+l1y1[2]*np.sin(theta_rad))*sln   ##MINUS SIGN BECAUSE I ALWAYS MEASURE AS:  -I || B (for B>0)
        #theta_Oe=(l1x1[1]*np.cos(theta_rad)+l1y1[1]*np.sin(theta_rad))*sln  ##MINUS SIGN BECAUSE I ALWAYS MEASURE AS:  -I || B (for B>0)
       
        
        DL_label = 'DL'
        Oe_label = 'Oe'
        #error_bar=(l1x1[3]*np.cos(theta_rad)+l1y1[3]*np.sin(theta_rad))*sln
        #error_bar=(np.abs(l1x1[3])*np.cos(theta_rad)+np.abs(l1y1[3])*np.sin(theta_rad))*sln  ## Error Propagation..
        error_bar=np.abs(np.sqrt((l1x1[3]*np.cos(theta_rad))**2+(l1y1[3]*np.sin(theta_rad))**2)*self.calc_info.sln) ##Gaussian Error Propagation..
        #print(l1x1[6],np.sqrt(l1x1[6]))
        if 'ZI' in self.calc_info.LI_type:
            theta_Oe = theta_Oe/1000
            theta_DL = theta_DL/1000
            error_bar = error_bar/1000
        #print('error_bar',error_bar)
        
         ##### Saving data
        Columns=['x [µm]','R [a.u.]','theta_Oe [µrad]','error_bar[µrad]','theta_DL [µrad]','error_bar[µrad]']
        Data1=np.column_stack((position, reflex, theta_Oe, error_bar, theta_DL, error_bar))
        #Data1=np.transpose(Data1)
        df=pd.DataFrame(Data1,columns=Columns)
        logfilenameShort = self.calc_name[0]
        df.to_excel(self.path3+ '\\'+ logfilenameShort.split('.txt')[0]+'.xlsx')
        df.to_csv(self.path3+ '\\' + logfilenameShort.split('.txt')[0]+'.txt')
        
        
        
        
        
        
        position, reflex,theta_Oe,theta_DL,error_bars = zip(*sorted(zip(position, reflex,theta_Oe,theta_DL,error_bar))) # Sort the data for interpolation
        #print(position)
        #position, reflex = zip(*sorted(zip(position, reflex))) #
        
        ## Spline interpolation
        tck = interpolate.splrep(position, reflex, s=0) ## determination of interpolation parameters (nodes,..)
        position_interp= np.arange(position[0], position[-1], (position[1]-position[0])/10.) ## Increasing number of points for the new (interpolated) data
        reflex_interp = interpolate.splev(position_interp, tck, der=0) ## Creating new data points by applying the interp parameters to the new grid
        
        
        dy, d2y= [], []
        newpos, dy, d2y= derivatives(position_interp,reflex_interp)
        min_index = dy.index(min(dy[co:-co]))
        max_index = dy.index(max(dy[co:-co]))
        print('index',min_index,newpos[max_index])
        i = 1
        
        while np.abs(max_index - min_index) < 150:
            if min_index < int(len(dy)/2):
                min_index = dy.index(np.sort(dy)[i])
            elif max_index > int(len(dy)/2):
                max_index =  dy.index(np.sort(dy)[-i])
            print(np.abs(max_index - min_index))
            i += 1
            if i > 100:
                break

        x1=newpos[min_index]
        #print(dy.index(min(dy)))
        #x1=-11.0
        x2=newpos[max_index]
        print(x1,x2)
        width=(x2-x1)
        if (width<0):
            a=x1
            x1=x2
            x2=a
            width=-width
        print(x1,x2)
        """
        use_get_edges = True
        if use_get_edges:
            self.get_edges()
            [x1,x2] = self.edges()
        """
        
        #x1=np.float64(-10.25)
        #x2=np.float64(11.21)
        #width=x2-x1
            
        x1=round(x1, 2)
        x2=round(x2, 2)
        print(x1,x2)
        width=round(width, 2)
        x_width=[x1,x2]
        y_width=[reflex_interp[dy.index(min(dy))+1],reflex_interp[dy.index(max(dy))+1]]
        width1=round(width,0)
        
    
        
        fig,ax = plt.subplots(figsize=(10,10))
        
        ax1 = ax.twinx()
        ax.plot(position_interp, reflex_interp, '-.v',color='navy',label='reflection')
        ax1.plot(newpos, dy, '-.v',color='orange',label='1st derivative')
        ax.plot(x_width, y_width, color="green", marker="o", ms=10)
        annkw = dict(xytext=(0,15), textcoords="offset pixels", color="green", ha="center", fontsize=20)
        ax.annotate(str(width)+'µm', xy=(x1+width/2, (y_width[0]+y_width[1])/2.), **annkw)
        ax.plot(x_width, y_width, color="green", marker="o", ms=10)
        annkw=dict(xytext=(0,15), textcoords="offset pixels", color="green", ha="left", fontsize=30)
        ax.annotate(str(x1)+'µm', xy=(x1,y_width[0]), **annkw)
        annkw=dict(xytext=(0,15), textcoords="offset pixels", color="green", ha="right", fontsize=30)
        ax.annotate(str(x2)+'µm', xy=(x2,y_width[1]), **annkw)
        
        
        ax.grid()
        ax.legend(loc=3)
        ax1.legend(loc=4)
        
        
        plt.savefig(self.path3+ '\\'+ 'width_'+plotname +'.png',bbox_inches='tight')
        plt.show()
        
        #position=np.flip(position)
        
        position=position-x1 ## Shifts the x-values to start from the left edge (zero point is left edge of device)
        
        theta_Oe=np.ravel(theta_Oe)
        theta_DL=np.ravel(theta_DL)
        
        position=np.ravel(position)
        error_bar=np.ravel(error_bar)
        
        width=round(width, 1)
        Current_coefficient=parallel_channel(self.calc_info.R[0],self.calc_info.R[1]) ## Percentage of current flowing through the NM, R1 corresponds to NM/M, R2 to system without NM
        print('curr coeff2',current_coefficient2)
        #Ic=float(current.split('mA')[0])
        Ic=self.calc_info.current*current_coefficient2   #in mA
        #Ic=1.98
        
        
        ###### WE NEED TO ACCOUNT FOR THE PROPER CURRENT 
        ###### JUNE 2022: we figured out that the nominally applied current does not match the actual current => current_coefficient2
        
        Ic1=Ic*Current_coefficient
        mask = np.zeros(len(theta_DL), dtype = bool)

        if use_find_impurity:
            if reflec_for_mask:
                data_find_imp = I[2]
            else:
                data_find_imp = theta_DL
            mask[1:-2] = iterate_find_impurity(data_find_imp[1:-2]/self.calc_info.current,self.path_findimp,self.calc_info,do_plot = do_plot)
        else:
            mask = (position > 0)* (position < x2-x1)
        offmask = np.invert(mask)
        True_ind = np.where(mask == True)[0]
        mask[True_ind[0:2]] = False
        mask[True_ind[-2::]] = False
        
        def Lin_fit(x,A,n):
            return A*x+n #np.log((10-(x))/(x))
        
            #The first function also takes into account an offset in x: x0
        
        #def Log_fit(x,A,x0,A0,width):
        #    #width=
        #    return A0+A*np.log(((width-(x+x0))/(x+x0)))
    
            
        def Log_fit(x,A,A0,width):
            return A0+A*np.log((width-(x))/(x))
        
        def Const_fit(x,y0):
            return y0
        
        position_DL_fit = position[mask]
        position_mask = position[np.invert(mask)]
        position_Oe_fit = position[mask]
        theta_DL_fit = theta_DL[mask]
        theta_Oe_fit = theta_Oe[mask]
        error_DL_fit = error_bar[mask]
        error_Oe_fit = error_bar[mask]
        

        const_offset, offset_err = curve_fit(Const_fit, position[offmask], theta_Oe[offmask], sigma=error_bar[offmask], absolute_sigma=True)
        #theta_Oe_fit -= const_offset[0]/np.sqrt(Ic)/4
        #theta_DL -= const_offset[0]/2
        #theta_DL_fit -= const_offset[0]/2
        #theta_Oe -= const_offset[0]/np.sqrt(Ic)/4
        print(const_offset)
        fig,ax = plt.subplots(figsize=(12,8))
        plt.title('Laser Polarization_' + self.calc_info.LightPol +'_'+ str(self.calc_info.current) + 'mA',pad=100)
        fitParamsConst_fit, fitCovariancesConst_fit= curve_fit(Const_fit, position_DL_fit, theta_DL_fit, sigma=error_DL_fit, absolute_sigma=True)
       #fitParamsLog_fit, fitCovariancesLog_fit= curve_fit(lambda x, A, x0, A0: Log_fit(x,A,x0,A0,width), position_Oe_fit, theta_Oe_fit, sigma=error_Oe_fit, absolute_sigma=True)
        fitParamsLog_fit, fitCovariancesLog_fit= curve_fit(lambda x, A, A0: Log_fit(x,A,A0,width), position_Oe_fit, theta_Oe_fit, sigma=error_Oe_fit, absolute_sigma=True)#,bounds = [(-1, 1), (0, 2)])
        #print('LOGFIT params',fitParamsLog_fit)
        fit_err_Log = np.sqrt(np.diag(fitCovariancesLog_fit)) #1sigma error
        fit_err_Const = np.sqrt(np.diag(fitCovariancesConst_fit)) #1sigma error
        const_array=[fitParamsConst_fit[0]]*len(position_DL_fit)
    
        
        
        
        #Conversion and calculation of DL-field
        
        conconst=fitParamsLog_fit[0]*width/(2*Ic)*10  #µrad/mT  Corresponds to the conversion constant (obtained form the Oersted field)
        conDL=(fitParamsConst_fit[0])/conconst #mT  Obtained by using the conversion constant (where we now convert the signal corresponding to the DL field)
        conDL_error=conDL*(fit_err_Const[0]/fitParamsConst_fit[0]+fit_err_Log[0]/fitParamsLog_fit[0])
        print('conDL',conDL)
        
        ax.plot(position, theta_DL, '-.v',color='navy',label=DL_label)
        ax.errorbar(position, theta_DL, yerr=error_bar, color='navy')
        #ax.plot(position_DL_fit, theta_DL_fit, color='orange', linewidth=4)
        ax.plot(position_DL_fit, const_array, color='deepskyblue',linewidth=4, label='fit const,'+ '  const='+str("{0:.4g}".format(fitParamsConst_fit[0]))+'±'+str("{0:.4g}".format(fit_err_Const[0])))
    
        #ax.plot(position_DL_fit,Lin_fit(position_DL_fit,fitParamsLin_fit[0],fitParamsLin_fit[1]), label='y=kx+n')
    
        
        ax.plot(position, theta_Oe, '-.v',color='firebrick',label=Oe_label)
        ax.errorbar(position, theta_Oe, yerr=error_bar, color='firebrick')
        ax.scatter(position_mask,np.ones(len(position_mask))*max(theta_DL)*0.8, color = 'r', label = 'Not used for fit')
        #The first line also takes into account an offset in x: x0
        
        #ax.plot(position_Oe_fit,Log_fit(position_Oe_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],fitParamsLog_fit[2],width), label='fit A*ln((w-x)/x),'+ '  A='+str("{0:.4g}".format(fitParamsLog_fit[0]))+'±'+str("{0:.3g}".format(fit_err_Log[0])),color='orange', linewidth=4)
        ax.plot(position_Oe_fit,Log_fit(position_Oe_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],width), label='fit A0+ A*ln((w-x)/x),'+  'A0='+str("{0:.2g}".format(fitParamsLog_fit[1]))+'  A='+str("{0:.2g}".format(fitParamsLog_fit[0]))+'±'+str("{0:.3g}".format(fit_err_Log[0])),color='orange', linewidth=4)
        
        
        
        plt.plot([], [], ' ', label='The obtained conversion coefficient is '+str("{0:.4g}".format(conconst))+'µrad/mT'+ "\n"+ 'and the corresponding DL-field ' + '(' + str("{0:.4g}".format(conDL)) + '±' + str("{0:.4g}".format(conDL_error))+ ')' + 'mT')
        #ax.legend(extra, label='The obtained conversion coefficient is'+str("{0:.4g}".format(conconst))+'µrad/mT and the corresponding DL-field '+str("{0:.4g}".format(conconst))+'mT')
        self.fit_DL_mT = conDL
        self.fit_DL_error_mT = conDL_error
        
        ax.yaxis.major.formatter._useMathText= True
        ax.xaxis.major.formatter._useMathText= True
        
        ax.set_ylabel(r'$\theta_{K}^{1\omega}$ [µrad]')
        plt.xlabel('y [µm]')
        
        #box = ax.get_position()
        #ax.set_position([box.x0, box.y0 + box.height * 0.2, box.width, box.height * 0.8])
        #ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.05), fancybox=True, shadow=True, ncol=5)
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.2), ncol=2, fancybox=True, shadow=True, fontsize=14)
        #plt.legend(bbox_to_anchor=(1.05,1),loc=2,borderaxespad=0., fontsize=15)
        ax.grid(1)
        ax2 = ax.twinx()
        # uncomment if you want to plot the temperature
        #ax2.plot(x,T,color='firebrick')
        # comment if you want to show the temperature and umcomment line above, if you want to plot the reflected intensity DC
        #########################################################################################################################################################
        #ax2.plot(position,I2[1],color='firebrick',label='reflection')      
        #########################################################################################################################################################
        ax2.set_ylabel(r'$R$ [a.u.]')
        ax2.plot(position,reflex,color='firebrick',label='reflection')
        #ax2.legend(fontsize=16,loc=4)
        #plt.savefig('graphs/Ni60Cu40_1nm_6mA.png')
        #np.savetxt('saved_data/Ni60Cu40_1nm_6mA.txt', np.transpose((x,first_harmonicR_Oe,first_harmonicR_DL)),delimiter=',')
        
        #ax.set_ylim(ymin=-575)
        #ax.set_ylim(ymax=700)
        plt.savefig(self.path3+ '\\'+ 'fit_'+plotname+'.png')
        plt.show()
        self.analyzed_data['x'] = position
        self.analyzed_data['sum'] = theta_Oe
        self.analyzed_data['diff'] = theta_DL
        self.analyzed_data['errorbar'] = error_bar
        if nice_plot:
            fig,ax = plt.subplots(figsize=(11,8))
            fs = 30
            #print(const_array)
            ax.plot(position, theta_DL, '-.v',color='green',label=r'$\theta_{DL}$')
            ax.errorbar(position, theta_DL, yerr=error_bar, color='green')
            #ax.plot(position_DL_fit, theta_DL_fit, color='orange', linewidth=4)
            ax.plot(position_DL_fit, np.array(const_array), color='lightgreen',linewidth=4, label='fit const. ')#+ str("{0:.2g}".format(fitParamsConst_fit[0]))+'±'+str("{0:.1f}".format(fit_err_Const[0])) +' $\mu$rad')
        
            #ax.plot(position_DL_fit,Lin_fit(position_DL_fit,fitParamsLin_fit[0],fitParamsLin_fit[1]), label='y=kx+n')
        
            
            ax.plot(position, theta_Oe, '-.v',color='k',label=r'$\theta_{Oe}$')
            ax.errorbar(position, theta_Oe, yerr=error_bar, color='k')
            #ax.scatter(position_mask,np.ones(len(position_mask))*max(theta_DL)*0.8, color = 'r', label = 'Not used for fit')
            #The first line also takes into account an offset in x: x0
            
            #ax.plot(position_Oe_fit,Log_fit(position_Oe_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],fitParamsLog_fit[2],width), label='fit A*ln((w-x)/x),'+ '  A='+str("{0:.4g}".format(fitParamsLog_fit[0]))+'±'+str("{0:.3g}".format(fit_err_Log[0])),color='orange', linewidth=4)
            ax.plot(position_Oe_fit,Log_fit(position_Oe_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],width), label='fit A*ln((w-x)/x)', color = 'grey', linewidth=4)
            
            
            
          #  plt.plot([], [], ' ', label='The obtained conversion coefficient is '+str("{0:.4g}".format(conconst))+'µrad/mT'+ "\n"+ 'and the corresponding DL-field ' + '(' + str("{0:.4g}".format(conDL)) + '±' + str("{0:.4g}".format(conDL_error))+ ')' + 'mT')
            
            ax.set_ylabel(r'$\theta_{K}^{1\omega}$ [µrad]',fontsize=fs)
            ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.3), ncol=2, fancybox=True, shadow=True, fontsize=fs)
            #ax.set_ylabel(r'$B_{loc}$ [mT]')
            plt.xlabel('y [µm]',fontsize=fs)
            
            #box = ax.get_position()
            #ax.set_position([box.x0, box.y0 + box.height * 0.2, box.width, box.height * 0.8])
            #ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.05), fancybox=True, shadow=True, ncol=5)
            
            #plt.legend(bbox_to_anchor=(1.05,1),loc=2,borderaxespad=0., fontsize=15)
            ax.grid(1)
            ax2 = ax.twinx()
            # uncomment if you want to plot the temperature
            #ax2.plot(x,T,color='firebrick')
            # comment if you want to show the temperature and umcomment line above, if you wanst to plot the reflected intensity DC
            #########################################################################################################################################################
            #ax2.plot(position,I2[1],color='firebrick',label='reflection')      
            #########################################################################################################################################################
            ax2.set_ylabel(r'$R$ [a.u.]',fontsize=fs)
            ax2.plot(position,reflex,color='firebrick',label='reflection')
            #ax2.legend(loc = 'upper right',fontsize=24)
            ax.tick_params(axis='both', which='major', labelsize=fs)
            ax2.tick_params(axis='both'
                            , which='major', labelsize=fs)
            plt.tight_layout(pad=0.5, w_pad=0.8, h_pad=1.0)
            #ax.set_ylim(ymin=-575)
            #ax.set_ylim(ymax=700)
            plt.savefig(self.path3+ '\\'+ 'nice_plotfit_KerrRot'+plotname+'.eps',bbox_inches='tight')
            plt.savefig(self.path3+ '\\'+ 'nice_plotfit_KerrRot'+plotname+'.png',bbox_inches='tight')
        
    
    
        
    def import_analyze(scanlist,path,see_channels = ['data_02','data_04'],cal_spec = None,ignorLines = [],theta = 0):

        
        res = analyze_SOT([scanlist],path)
        print('calibration specification:', cal_spec)
        res.prepare_SOT_data(spec_calibration=cal_spec)
        res.calc_info.theta = theta
        res.import_SOT_data(ignorLines =ignorLines)
        for ch in see_channels:
            res.see_intensity(ch_var = ch,ignorelines = ignorLines)
            

        res.evaluate_data(do_plot= 'realimag')
        res.evaluate_data(do_plot= 'negpos')
        res.evaluate_data(do_plot= 'sumdiff')
        return res
    def import_analyze_theta(scanlist,path,see_channels = ['data_02','data_04'],cal_spec = None,ignorLines = [],theta = 0):

        
        res = analyze_SOT([scanlist],path)
        print('calibration specification:', cal_spec)
        res.prepare_SOT_data(spec_calibration=cal_spec)
        res.calc_info.theta = theta
        res.import_SOT_data_new(ignorLines =ignorLines)
        plt.show()
        #ONLY get_theta works!!
        return res
    
class analyze_SHE_OHE:
    
    
    def __init__(self,calc_name,path):
        # Initialize instance variables (attributes)
        self.data = None #Containing raw and analyzed data
        self.calc_name = calc_name
        self.paths = []
        self.calc_info  = {}

        
   

    def prepare_data(self,spec_calibration = None):
        
        class CalcInfo:
            
            def __init__(self, theta,theta2, sln, date, calc_info,R1,R2,logfilenameShort):
                    # Assign each value directly as an attribute
                    j = 0
                    self.theta = theta
                    self.theta2 = theta2
                    self.sln = sln
                    self.date = date
                    self.system = calc_info[0]
                    self.current = float(calc_info[1+j].split('mA')[0])  # Assuming 'mA' is present
                    self.LI_ref = calc_info[2+j]
                    self.measurement = calc_info[3+j]
                    self.MOKE_type = calc_info[4+j]
                    self.LI_type = calc_info[5+j]  # Repeated key: You may want to change it if it's different
                    self.LightPol = calc_info[6+j]
                    self.logfilenameShort = logfilenameShort
                    self.R = [R1,R2]
                    
                    
        cwd = os.getcwd()
        datalist1 = self.calc_name
        print(datalist1)
        
        if len(str(datalist1).replace("'", "?").split("?")) == 1:
            logfilenameShort=(str(datalist1).replace("'", "?").split("?"))[0]
        else:
            logfilenameShort=(str(datalist1).replace("'", "?").split("?"))[1]
        print(logfilenameShort)
        path1=cwd+'\\'+(logfilenameShort.split('_'))[1]
        
        self.path1 = path1
        time=str(datetime.datetime.now()).split('.')[0]
        dtime=''.join((time.split(' ')[0]).split('-'))
        ttime=''.join((time.split(' ')[1]).split(':'))
        
        
        try:
            # Create target Directory
            os.makedirs(path1)
            print("Directory " , path1 ,  " Created ") 
        except FileExistsError:
            print("Directory " , path1 ,  " already exists")
            
        
        for name in logfilenameShort.split('_'):
            if 'mA' in name:
                current=name
        current_date=current+ ' ' + (logfilenameShort.split('_'))[0]
        path2=path1+'\\'+ current_date
        self.path2 = path2
        try:
            # Create target Directory
            os.makedirs(path2)
            print("Directory " , path2 ,  " Created ")
        except FileExistsError:
            print("Directory " , path2 ,  " already exists")
        try:
            # Create target Directory
            path_findimp = path1 + '//' + 'find_impurity_minimize'
            self.path_findimp = path_findimp
            #os.makedirs(path_findimp)
            #print("Directory " , path_findimp ,  " Created ") 
        except FileExistsError:
            print("Directory " , path_findimp ,  " already exists")
           
        #Where to save the plots
        path3=path2+'\\'+dtime+' '+ttime
        self.path3 = path3
        try:
            # Create target Directory
            os.makedirs(path3)
            print("Directory " , path3 ,  " Created ") 
        except FileExistsError:
            print("Directory " , path3 ,  " already exists")
            
        
        plotname=logfilenameShort.split('.txt')[0]+'.png'    
        
        print('There are the following Calibration files:')
        for file in os.listdir(path1):
            if file.startswith("calibration"):
                print(file)
        
        
        
        ##Calibration data+ Resistivities+ Phase
        print(spec_calibration)
        try: 
            
            if spec_calibration:
                print(path1+'\\calibration' + spec_calibration + '.txt')
                file = open(path1+'\\calibration' + spec_calibration + '.txt', 'r')
                
                print('Using the calibration file: calibration' + spec_calibration + '.txt')
            else:
                file = open(path1+'\\calibration' + '.txt', 'r')
                print(" Using Calibration file" , path1+'\\calibration' + '.txt') 
            calibration_data=np.fromstring(file.readline().split('\n')[0], dtype=float, sep=' ')
            R1=float(file.readline().split('\n')[0])
            R2=float(file.readline().split('\n')[0])
            theta=float(file.readline().split('\n')[0])
            #theta2 = float(file.readline().split('\n')[0])
            file.close()
        
        except FileNotFoundError:
            spec_input = input('Chose file by submitting the spec (everything after ..tion and before .txt):')
            if spec_input:
                spec_calibration = spec_input
                print('You inputted %s' %spec_calibration)
            file= open(path1+'\\calibration.txt', 'w')
            print("Calibration file" , path1+'\\calibration.txt' ,  " created ")
            print('\n Input the calibration data manually')
            calibration_data=input('\n')
            
            print('\n Input the resistance of the NM/M system')  
            R1=input('\n')
            print('\n Input the resistance of the M (reference without NM)')
            R2=input('\n')
            
            print('\n Input the first harmonic phase offset')
            theta=input('\n')
            print('\n Input the 2nd harmonic phase offset')
            #theta2=input('\n')
            
         
            file.write(calibration_data+ '\n')
            file.write(R1+ '\n' )
            file.write(R2+ '\n')
            file.write(theta)
            #file.write(theta2)
        
            calibration_data=np.fromstring(calibration_data, dtype=float, sep=' ')
            R1=float(R1)
            R2=float(R2)
            theta=float(theta)
            #theta2=float(theta2)
            
            file.close()
         
        sln = analysis_field.calibrate(np.linspace(0,25,6),calibration_data,plotting=False)
        sln = 1/(sln)*np.pi/180.0*1e6 ##(µrad/mV)
        plt.show()
        #sln=-sln #remove this later
        print(R1,R2)
        
        theta_rad=theta*np.pi/180.0
        print(theta_rad)
                
        #CTR = 2.213e-05*1e6
        #CTR= -0.58*1e-4*1e6  
        print(cwd)
        pattern =r'_(.*?)(?=_|$)'
        date_pattern = r'(\d{8})'
        # Find all matches
        print('datalist1',datalist1)
        calc_info = re.findall(pattern, datalist1[0])
      
        date = re.findall(date_pattern,datalist1[0])[0]
        print(logfilenameShort)
       
        theta2 = 0
        self.calc_info = CalcInfo(theta,theta2,sln,date,calc_info,R1,R2,logfilenameShort)
        print(logfilenameShort)
        

        return self
    
    
    def import_data(self,ignorLines = [],remove_channels = []):
        
        self.data =  analysis_field.linescan_calc_Tobi(self.calc_info.logfilenameShort,ch_pol='None',
                                                       convert_dict_to_list=False, ignorLines=ignorLines,ch_x='actuator_1_1',rm_channels = remove_channels)
        for key in self.data.keys(): #overwrite lock-in type from scanlist string with the one found: 28.08 only working for 1 lockin
            if self.calc_info.LI_type.lower() in key:
                self.calc_info.LI_type = key[0:-2]
                
        for key in self.data.keys():
            if 'field' in key:
                self.calc_info.field = key
        return self
    
    def get_edges(self, I_ch = 'averagein2value'):
        
        """
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
            
        elif len(self.data) == 10 :
            x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        else:
            x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        """ 
        
        D = self.data
        
        reflec = D[I_ch][2]
        mask = np.argsort(D['x'])
        xsort = D['x'][mask]
        reflecsort = reflec[mask]
        fig = plt.figure()   
        edges, width = analysis_field.find_edges_width(xsort[5:-5],reflecsort[5:-5])
        print('Edges',edges)
        self.edges = edges
        self.dev_center = sum(edges)/2
        return self
        
    def get_theta_old(self):
        """
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
            
        elif len(self.data) == 10 :
            x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        else:
            x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
           """ 
        self.get_edges()
        D = self.data
        li = self.calc_info.LI_type
        channel = 'pos'
        theta_pos = analysis_field.find_phase(D['x'],D[li+'x1'][4],D[li+'y1'][4],edges = self.edges,ch = channel)
        
        channel = 'neg'
        theta_neg = analysis_field.find_phase(D['x'],D[li+'x1'][5],D[li+'y1'][5],edges = self.edges,ch = channel)
        
        theta = np.mean([theta_pos,theta_neg])
        print('Mean theta = %.2f \nfrom' %theta, [theta_pos,theta_neg])
        plt.grid()
        plt.legend()
        plt.text(0.01, 0.25, 'theta = %.2f' %theta)
        plt.show()
        self.calc_info.theta = theta
        return self
    
    def get_theta(self,LI_str = None):
        """
        if self.calc_info.dchanneltype == '2LI+avgSingle':
            x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
            
        elif len(self.data) == 10 :
            x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
        else:
            x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
           """ 
        self.get_edges()
        D = self.data
        if LI_str:
            li = LI_str
        else:
            li = self.calc_info.LI_type
        print('Using LIA', li)
        fig = plt.subplots(figsize=(4,3))
        channel = 'pos'
        theta_pos = analysis_field.find_phase(D['x'],D[li+'x1'][4],D[li+'y1'][4],edges = self.edges,ch = channel)
        
        channel = 'neg'
        theta_neg = analysis_field.find_phase(D['x'],D[li+'x1'][5],D[li+'y1'][5],edges = self.edges,ch = channel)
        
        theta = np.mean([theta_pos,theta_neg])
        print('Mean theta = %.2f \nfrom' %theta, [theta_pos,theta_neg])

        plt.grid()
        plt.legend()
        #plt.text(0.01, 0.25, 'theta = %.2f' %theta)
        plt.show()
        
        
        self.calc_info.theta = theta
        fig = plt.subplots(figsize=(4,3))
        channel = 'pos'
        theta2_pos = analysis_field.find_phase(D['x'],D[li+'x2'][4],D[li+'y2'][4],edges = self.edges,ch = channel)
        
        channel = 'neg'
        theta2_neg = analysis_field.find_phase(D['x'],D[li+'x2'][5],D[li+'y2'][5],edges = self.edges,ch = channel)
        
        theta2 = np.mean([theta2_pos,theta2_neg])
        print('Mean theta2 = %.2f \nfrom' %theta2, [theta2_pos,theta2_neg])

        plt.grid()
        plt.legend()
        #plt.text(0.01, 0.25, 'theta2 = %.2f' %theta2)
        plt.show()
        
        
        self.calc_info.theta2 = theta2
        return self
    
    
    def see_intensity(self,ch_var = 'data_02',ignorelines = list(range(0,0))+list(range(0,0)),setup = 1,ylim = []):
        logfilenameShort = self.calc_name[0]
        I, var_all = analysis_field.intensity_mean_SOT(logfilenameShort,ch_var=ch_var, ignorLines=ignorelines, setup=setup)
        fig,[ax1,ax2] = plt.subplots(figsize=(15,12),nrows = 2,gridspec_kw={'height_ratios': [1,3]})
        print(len(var_all))
        #plt.figure(figsize=(28,6))
        ax1.plot(I*1e3)
        #plt.ylim(0, 200)
        relay_offset = False
        if ch_var == 'data_04' or ch_var == 'data_05' or ch_var == 'data_03':
            print('Offseting data of diff relay pos')
            relay_offset = True
        if relay_offset:
            relay_pos = analysis_field.get_relaypos_scanlist(logfilenameShort,ignorLines = ignorelines) - np.ones(len(var_all))
        else:
            relay_pos = np.zeros(len(var_all))
        
        plt.suptitle(logfilenameShort + ch_var)
        #ax1.scatter(remove_lines,np.ones(len(remove_lines))*Imax*1e3,color = 'r', marker = 'x',label = 'remove')
        ax1.set_xticks(np.arange(len(I)))
        ax1.legend()
        ax1.grid()
        #plt.ylim(0, 200)
        ax1.set_xlabel('number of scans')
        ax1.set_ylabel('mean intensity [mV]')
        
    
        colors = plt.cm.copper(np.linspace(0, 1, len(var_all)))
        max_sig = max(var_all[0])
        for i in range(len(var_all)):
            ax2.plot(self.data['x'],var_all[i] + relay_pos[i]*2*max_sig,"x-",color=colors[i], label = i)
            #ax2.plot(self.data['x'],var_all[i] ,"x-",color=colors[i], label = i)
        if relay_offset:
            ax2.axhline(y = 2*max_sig, color = 'r', linestyle = '-')
        ax2.axhline(y = 0.0, color = 'r', linestyle = '-')
        if ylim:
            ax2.set_ylim(ylim)
        else:
            ax2.legend()
        ax2.grid()
        plt.show()
        

    def evaluate_data(self,phase = None,phase2 = None,plot_2axs = False,do_plot = 'sumdiff',fs = 16,reflection = 'averagein2value'):
        plotname = self.calc_info.system + '_' + str(self.calc_info.current) + 'mA_' + self.calc_info.LightPol +'_' + do_plot
        if phase:
            theta = phase
            print('Ignoring calc_info.theta = ', self.calc_info.theta, '. Using ', theta)
        else:
            theta = self.calc_info.theta
        if phase2:
            theta2 = phase2
            print('Ignoring calc_info.theta2 = ', self.calc_info.theta2, '. Using ', theta2)
        else:
            theta2 = self.calc_info.theta2
        li = self.calc_info.LI_type
        
        
        sln = self.calc_info.sln
        theta_rad=theta*np.pi/180.0
        theta_rad2=theta2*np.pi/180.0
       

                      
        D = self.data
        li = self.calc_info.LI_type
        if li == 'ZISR':
            li = input('Select one of the two LIA!')
            for key in D.keys(): #overwrite lock-in type from scanlist string with the one found: 28.08 only working for 1 lockin
                if li.lower() in key:
                    print(key)
                    li = key[0:-2]
            
        if 'sr' in li:
            fac = 1000
        elif 'zi' in li:
            fac = 1    
            
        theta_Oe=(D[li+'x1'][2]*np.cos(theta_rad)+D[li+'y1'][2]*np.sin(theta_rad))*sln  
        theta_DL=(D[li+'x1'][1]*np.cos(theta_rad)+D[li+'y1'][1]*np.sin(theta_rad))*sln
        theta2_Oe=(D[li+'x2'][2]*np.cos(theta_rad2)+D[li+'y2'][2]*np.sin(theta_rad2))*sln  
        theta2_DL=(D[li+'x2'][1]*np.cos(theta_rad2)+D[li+'y2'][1]*np.sin(theta_rad2))*sln
        """ #Not used for these measurements, usually on one lock in plus Focus LIne
        theta_2w = (l1x2[2]*np.cos(theta_rad2) + l1y2[2]*np.sin(theta_rad2))
        theta_2w_err=np.sqrt((l1x2[3]*np.cos(theta_rad2))**2+(l1y2[3]*np.sin(theta_rad2))**2) ## Gaussian Error Propagation
        """
        error_bar=np.sqrt((D[li+'x1'][3]*np.cos(theta_rad))**2+(D[li+'y1'][3]*np.sin(theta_rad))**2)*np.abs(sln) ## Gaussian Error Propagation
        error_bar2=np.sqrt((D[li+'x2'][3]*np.cos(theta_rad2))**2+(D[li+'y2'][3]*np.sin(theta_rad2))**2)*np.abs(sln) ## Gaussian Error Propagation
        pos=D['x']
        theta_neg=(D[li+'x1'][5]*np.cos(theta_rad)+D[li+'y1'][5]*np.sin(theta_rad))*sln  
        theta_pos=(D[li+'x1'][4]*np.cos(theta_rad)+D[li+'y1'][4]*np.sin(theta_rad))*sln
        if do_plot == 'realimag' or do_plot == 'realimag2nd':
            print('Plotting 2 axes')
            plot_2axs = True
        else:
            if plot_2axs:
                fig,[ax1,ax3] = plt.subplots(figsize=(8,8),nrows = 2)
            else:
                fig,ax1 = plt.subplots(figsize=(6,4))
        if do_plot == 'negpos':
            ax1.plot(pos,theta_pos*fac,'-.v',color='red',label='pos')
            #print(theta_pos*fac)
            ax1.plot(pos,theta_neg*fac,'-.v',color='blue',label='neg')
            y = theta_pos
            if 'zi' in li:
                ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=fs)
            else:
                ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [µrad]',fontsize=fs)
                
        elif do_plot == 'sumdiff':
            if plot_2axs:
                ax3.plot(pos,theta_Oe*fac,'-.v',color='black',label='sum')
                ax3.errorbar(pos,theta_Oe*fac, yerr=error_bar ,color='black')
                ax3.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=fs)
    
            else:
                ax1.plot(pos,theta_Oe*fac,'-.v',color='black',label='sum')
                ax1.errorbar(pos,theta_Oe*fac, yerr=error_bar ,color='black')
            ax1.plot(pos,theta_DL*fac,'-.v',color='green',label='diff')
            ax1.errorbar(pos,theta_DL*fac, yerr=error_bar ,color='green')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=fs)

        elif do_plot == 'sumdiff2nd':
            
            if plot_2axs:
                ax3.plot(pos,theta2_Oe*fac,'-.v',color='black',label='sum')
                ax3.errorbar(pos,theta2_Oe*fac, yerr=error_bar ,color='black')
                ax3.set_ylabel(r'$\theta_{K}^{2\omega}$ [nrad]',fontsize=fs)
    
            else:
                ax1.plot(pos,theta2_Oe*fac,'-.v',color='black',label='sum')
                ax1.errorbar(pos,theta2_Oe*fac, yerr=error_bar ,color='black')
            ax1.plot(pos,theta2_DL*fac,'-.v',color='green',label='diff')
            ax1.errorbar(pos,theta2_DL*fac, yerr=error_bar ,color='green')
            ax1.set_ylabel(r'$\theta_{K}^{2\omega}$ [nrad]',fontsize=fs)
        
        elif do_plot == 'thermoreflectance':
            therm_ref = -theta2_Oe/D[reflection][2]
            #ax1.plot(pos,therm_ref,'-.v',color='r')
            ax1.errorbar(pos,therm_ref, marker = 'v',yerr=error_bar2,color='r')
            ax1.set_ylabel(r'$-R^{2\omega}/R$ ',fontsize=fs)
            
        elif do_plot == 'comp_1st_2nd':

            ax3.plot(pos,theta2_Oe*fac,'-.v',color='black',label='sum')
            ax3.errorbar(pos,theta2_Oe*fac, yerr=error_bar ,color='black')
            ax3.plot(pos,theta2_DL*fac,'-.v',color='green',label='diff')
            ax3.errorbar(pos,theta2_DL*fac, yerr=error_bar ,color='green')
            ax3.set_ylabel(r'$\theta_{K}^{2\omega}$ [nrad]',fontsize=fs)
    
            ax1.plot(pos,theta_Oe*fac,'-.v',color='black',label='sum')
            ax1.errorbar(pos,theta_Oe*fac, yerr=error_bar ,color='black')
            ax1.plot(pos,theta_DL*fac,'-.v',color='green',label='diff')
            ax1.errorbar(pos,theta_DL*fac, yerr=error_bar ,color='green')
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=fs)
            
            
        elif do_plot == 'realimag':
            fig,[ax1,ax3] = plt.subplots(figsize=(12,4),ncols = 2,sharey=True)
            ax3.plot(pos,D[li+'x1'][5]*sln,'-.v',color='b')
            ax3.plot(pos,D[li+'y1'][5]*sln,'-.v',color='r')
            ax1.plot(pos,D[li+'x1'][4]*sln,'-.o',color='b',label='real')
            ax1.plot(pos,D[li+'y1'][4]*sln,'-.o',color='r',label='imag')
            #y = l1x1[5]
            ax1.set_title('R$^+$')
            ax3.set_title('R$^-$')
            #ax3.set_ylabel(r'$\theta_{K}^{1\omega}(R^-)$ [nrad]',fontsize=fs)
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=fs)
            ax1.set_xlabel(r'$x$ $[\mu m]$')
            
        elif do_plot == 'realimag2nd':
            fig,[ax1,ax3] = plt.subplots(figsize=(12,4),ncols = 2,sharey=True)
            ax3.plot(pos,D[li+'x2'][5]*sln,'-.v',color='b')
            ax3.plot(pos,D[li+'y2'][5]*sln,'-.v',color='r')
            ax1.plot(pos,D[li+'x2'][4]*sln,'-.o',color='b',label='real')
            ax1.plot(pos,D[li+'y2'][4]*sln,'-.o',color='r',label='imag')
            #y = l1x1[5]
            ax1.set_title('R$^+$',fontsize=fs)
            ax3.set_title('R$^-$',fontsize=fs)
            
            #ax3.set_ylabel(r'$\theta_{K}^{2\omega}$ [nrad]',fontsize=fs)
            ax1.set_ylabel(r'$\theta_{K}^{2\omega}$ [nrad]',fontsize=fs)
            ax1.set_xlabel(r'$x$ $[\mu m]$')
    
                
        elif do_plot == 'findphase':
            #ax1.plot(x,l1x1[4]*np.cos(theta_rad) + l1y1[4]*np.sin(theta_rad),'-.v',color='k',label='real +')
            ax1.plot(pos,-D[li+'x1'][4]*np.sin(theta_rad) + D[li+'y1'][4]*np.cos(theta_rad),'-.v',color='cyan',label='imag + (-> $0$)')
            ax1.plot(pos,-D[li+'x1'][5]*np.sin(theta_rad) + D[li+'y1'][5]*np.cos(theta_rad),'-.v',color='k',label='imag - (-> $0$)')
            
            ax1.set_ylim([-1, 1])
            ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=fs)
            
            
        
        #ax1.set_ylim(-50,50)
        
        
        #ax1.set_xlabel(r'$x$ $[\mu m]$')
        #ax1.set_xlim([-15,16])
        ax1.legend(fontsize=fs,loc=1)
        ax1.grid(1)
        ax1.axhline(y = 0, color = 'k')
        ax2 = ax1.twinx()
    
        ax2.plot(D['x'],D[reflection][2],color='firebrick',label='I$_{FL}$')
        if plot_2axs:
            ax3.grid(1)
            ax4 = ax3.twinx()
            ax4.plot(D['x'],D[reflection][2],color='firebrick',label='I$_{FL}$')
            #ax4.set_ylabel(r'$R$ [a.u.]')
            if 'real' not in do_plot:
                ax3.legend(fontsize=fs,loc=1)
            else:
                ax1.set_xlabel(r'$x$ $[\mu m]$',fontsize=fs)
                ax1.tick_params(axis='both', which='major', labelsize=fs-2)
            ax3.axhline(y = 0, color = 'k')
            ax3.set_xlabel(r'$x$ $[\mu m]$',fontsize=fs)
            #ax3.set_ylim(ylim[0],ylim[1])
            ax4.legend(fontsize=fs,loc=4)
            ax3.tick_params(axis='both', which='major', labelsize=fs-2)
        else:
            ax1.set_xlabel(r'$x$ $[\mu m]$',fontsize=fs)
            ax1.tick_params(axis='both', which='major', labelsize=fs-2)
        #ax2.set_ylabel(r'$R$ [a.u.]')
        ax2.legend(fontsize=fs,loc=4)
        """
        if '2nd' in do_plot:
            plt.suptitle(r'$\varphi_2 = %.0f$°: %s' %(theta2,self.calc_info.logfilenameShort[9:-4]),y = 0.98)
        else:
            plt.suptitle(r'$\varphi = %.0f$°: %s' %(theta,self.calc_info.logfilenameShort[9:-4]),y = 0.98)
            """
        plt.tight_layout()
        plt.savefig(self.path3+ '\\'+plotname+'.png',pad_inches = 0.1)
        plt.savefig(self.path3+ '\\'+plotname+'.eps',pad_inches = 0.1)
        plt.show()
        mask = np.argsort(pos)
        analyzed_data = {
            "x": pos[mask],
            "intR":D[reflection][2][mask],
            "sum": theta_Oe[mask],
            "diff": theta_DL[mask],
            "pos": theta_pos[mask],
            "neg": theta_neg[mask],
            "errorbar": error_bar[mask],
        }
        if do_plot == 'thermoreflectance':
            analyzed_data['thermoreflectance'] = therm_ref
            analyzed_data['errorbar_2omega'] = error_bar2
        
        # Create DataFrame
        #data_df = pd.DataFrame(analyzed_data)
        self.analyzed_data = analyzed_data
        return self
    
    def eval_width_and_fit(self,current_coefficient2 = 0.99,do_plot = False,fit_edge_offset = 5,shift = 0,nice_plot = False):
        
        theta_rad=self.calc_info.theta*np.pi/180.0
        theta_rad2=self.calc_info.theta2*np.pi/180.0
        mpl.rcParams['font.size'] = 16
        def derivatives(x,y): ##Calculation of the first and second derivatives of the intensity diode's DC signal
            h=x[1]-x[0]
            dy,ddy,newpos=[0]*(len(x)-2),[0]*(len(x)-2),[0]*(len(x)-2)
            
            for i in range(1,(len(x)-1)):
                dy[i-1]=(y[i+1]-y[i-1])/(2*h)
                ddy[i-1]=(y[i+1]-2-y[i]+y[i-1])/(h*h)
                newpos[i-1]=x[i]
            return newpos,dy,ddy
        
        def parallel_channel(R1,R2):   ## Calculation the ratio of the current flowing through the NM with respect to the applied current (parallel channel model)
            coeff=1-R1/R2
            #coeff=round(coeff,2)
            return coeff
        plotname = self.calc_info.system + '_' + str(self.calc_info.current) + 'mA_' + self.calc_info.LightPol
        
        #x,I,I2,l1x1,l1y1,l1x2,l1y2,l2x2,l2y2,l2x1,l2y1,relaypos,H=DATA
        #x,I,I2,l1x1,l1y1,l1x2,l1y2,H,relaypos= DATA
        #x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H= self.data
        D = self.analyzed_data
            
        position=D['x']

        reflex=D['intR']
        
        #theta_rad=90*np.pi/180.0 
        #print(theta_rad)    
        theta_Oe= D['sum']
        theta_DL=  D['diff']
        #theta_DL=(l1x1[2]*np.cos(theta_rad)+l1y1[2]*np.sin(theta_rad))*sln   ##MINUS SIGN BECAUSE I ALWAYS MEASURE AS:  -I || B (for B>0)
        #theta_Oe=(l1x1[1]*np.cos(theta_rad)+l1y1[1]*np.sin(theta_rad))*sln  ##MINUS SIGN BECAUSE I ALWAYS MEASURE AS:  -I || B (for B>0)
       
        
        DL_label = 'DL'
        Oe_label = 'Oe'
        #error_bar=(l1x1[3]*np.cos(theta_rad)+l1y1[3]*np.sin(theta_rad))*sln
        #error_bar=(np.abs(l1x1[3])*np.cos(theta_rad)+np.abs(l1y1[3])*np.sin(theta_rad))*sln  ## Error Propagation..
        error_bar=D['errorbar'] ##Gaussian Error Propagation..
        #print(l1x1[6],np.sqrt(l1x1[6]))
        
        #print('error_bar',error_bar)
        
         ##### Saving data
        Columns=['x [µm]','R [a.u.]','theta_Oe [µrad]','error_bar[µrad]','theta_DL [µrad]','error_bar[µrad]']
        Data1=np.column_stack((position, reflex, theta_Oe, error_bar, theta_DL, error_bar))
        #Data1=np.transpose(Data1)
        df=pd.DataFrame(Data1,columns=Columns)
        logfilenameShort = self.calc_name[0]
        df.to_excel(self.path3+ '\\'+ logfilenameShort.split('.txt')[0]+'.xlsx')
        df.to_csv(self.path3+ '\\' + logfilenameShort.split('.txt')[0]+'.txt')
        
        
        
        
        
        
        position, reflex,theta_Oe,theta_DL,error_bars = zip(*sorted(zip(position, reflex,theta_Oe,theta_DL,error_bar))) # Sort the data for interpolation
        #print(position)
        #position, reflex = zip(*sorted(zip(position, reflex))) #
        
        ## Spline interpolation
        tck = interpolate.splrep(position, reflex, s=0) ## determination of interpolation parameters (nodes,..)
        position_interp= np.arange(position[0], position[-1], (position[1]-position[0])/10.) ## Increasing number of points for the new (interpolated) data
        reflex_interp = interpolate.splev(position_interp, tck, der=0) ## Creating new data points by applying the interp parameters to the new grid
        
        
        dy, d2y= [], []
        newpos, dy, d2y= derivatives(position_interp,reflex_interp)
        co = 50
        min_index = dy.index(min(dy[co:-co]))
        max_index = dy.index(max(dy[co:-co]))
        print('index',min_index,newpos[max_index])
        i = 1
        
        while np.abs(max_index - min_index) < 150:
            if min_index < int(len(dy)/2):
                min_index = dy.index(np.sort(dy)[i])
            elif max_index > int(len(dy)/2):
                max_index =  dy.index(np.sort(dy)[-i])
            print(np.abs(max_index - min_index))
            i += 1
            if i > 100:
                break

        x1=newpos[min_index]
        #print(dy.index(min(dy)))
        #x1=-11.0
        x2=newpos[max_index]
        print(x1,x2)
        width=(x2-x1)
        if (width<0):
            a=x1
            x1=x2
            x2=a
            width=-width
        print(x1,x2)
        """
        use_get_edges = True
        if use_get_edges:
            self.get_edges()
            [x1,x2] = self.edges()
        """
        
        #x1=np.float64(-10.25)
        #x2=np.float64(11.21)
        #width=x2-x1
            
        x1=round(x1, 2)
        x2=round(x2, 2)
        print(x1,x2)
        width=round(width, 2)
        x_width=[x1,x2]
        y_width=[reflex_interp[dy.index(min(dy))+1],reflex_interp[dy.index(max(dy))+1]]
        width1=round(width,0)
        
    
        
        fig,ax = plt.subplots(figsize=(10,10))
        
        ax1 = ax.twinx()
        ax.plot(position_interp, reflex_interp, '-.v',color='navy',label='reflection')
        ax1.plot(newpos, dy, '-.v',color='orange',label='1st derivative')
        ax.plot(x_width, y_width, color="green", marker="o", ms=10)
        annkw = dict(xytext=(0,15), textcoords="offset pixels", color="green", ha="center", fontsize=20)
        ax.annotate(str(width)+'µm', xy=(x1+width/2, (y_width[0]+y_width[1])/2.), **annkw)
        ax.plot(x_width, y_width, color="green", marker="o", ms=10)
        annkw=dict(xytext=(0,15), textcoords="offset pixels", color="green", ha="left", fontsize=30)
        ax.annotate(str(x1)+'µm', xy=(x1,y_width[0]), **annkw)
        annkw=dict(xytext=(0,15), textcoords="offset pixels", color="green", ha="right", fontsize=30)
        ax.annotate(str(x2)+'µm', xy=(x2,y_width[1]), **annkw)
        
        
        ax.grid()
        ax.legend(loc=3)
        ax1.legend(loc=4)
        
        
        plt.savefig(self.path3+ '\\'+ 'width_'+plotname +'.png',bbox_inches='tight')
        plt.show()
        
        #position=np.flip(position)
        
        position=position-x1 ## Shifts the x-values to start from the left edge (zero point is left edge of device)
        
        theta_Oe=np.ravel(theta_Oe)
        theta_DL=np.ravel(theta_DL)
        
        position=np.ravel(position)
        error_bar=np.ravel(error_bar)
        
        width=round(width, 1)
        Current_coefficient=parallel_channel(self.calc_info.R[0],self.calc_info.R[1]) ## Percentage of current flowing through the NM, R1 corresponds to NM/M, R2 to system without NM
        print('curr coeff2',current_coefficient2)
        #Ic=float(current.split('mA')[0])
        Ic=self.calc_info.current*current_coefficient2   #in mA
        #Ic=1.98
        
        
        ###### WE NEED TO ACCOUNT FOR THE PROPER CURRENT 
        ###### JUNE 2022: we figured out that the nominally applied current does not match the actual current => current_coefficient2
        
        Ic1=Ic*Current_coefficient
        mask = np.zeros(len(theta_DL), dtype = bool)

        
        mask = (position > 0)* (position < x2-x1)
        offmask = np.invert(mask)
        True_ind = np.where(mask == True)[0]
        mask[True_ind[0:fit_edge_offset]] = False
        mask[True_ind[-fit_edge_offset::]] = False
        
        def Lin_fit(x,A,n):
            return A*x+n #np.log((10-(x))/(x))
        
            #The first function also takes into account an offset in x: x0
        
        #def Log_fit(x,A,x0,A0,width):
        #    #width=
        #    return A0+A*np.log(((width-(x+x0))/(x+x0)))
    
            
        def Log_fit(x,A,A0,width):
            return A0+A*np.log((width-(x))/(x))
        
        def Const_fit(x,y0):
            return y0
        
        position_DL_fit = position[mask]
        position_mask = position[np.invert(mask)]
        position_Oe_fit = position[mask]
        theta_DL_fit = theta_DL[mask]
        theta_Oe_fit = theta_Oe[mask]
        error_DL_fit = error_bar[mask]
        error_Oe_fit = error_bar[mask]
        

        const_offset, offset_err = curve_fit(Const_fit, position[offmask], theta_Oe[offmask], sigma=error_bar[offmask], absolute_sigma=True)
        #theta_Oe_fit -= const_offset[0]/np.sqrt(Ic)/4
        #theta_DL -= const_offset[0]/2
        #theta_DL_fit -= const_offset[0]/2
        #theta_Oe -= const_offset[0]/np.sqrt(Ic)/4
        print(const_offset)
        fig,ax = plt.subplots(figsize=(12,8))
        plt.title('Laser Polarization_' + self.calc_info.LightPol +'_'+ str(self.calc_info.current) + 'mA',pad=100)
        fitParamsConst_fit, fitCovariancesConst_fit= curve_fit(Const_fit, position_DL_fit, theta_DL_fit, sigma=error_DL_fit, absolute_sigma=True)
       #fitParamsLog_fit, fitCovariancesLog_fit= curve_fit(lambda x, A, x0, A0: Log_fit(x,A,x0,A0,width), position_Oe_fit, theta_Oe_fit, sigma=error_Oe_fit, absolute_sigma=True)
        fitParamsLog_fit, fitCovariancesLog_fit= curve_fit(lambda x, A, A0: Log_fit(x,A,A0,width), position_Oe_fit, theta_Oe_fit, sigma=error_Oe_fit, absolute_sigma=True)#,bounds = [(-1, 1), (0, 2)])
        #print('LOGFIT params',fitParamsLog_fit)
        fit_err_Log = np.sqrt(np.diag(fitCovariancesLog_fit)) #1sigma error
        fit_err_Const = np.sqrt(np.diag(fitCovariancesConst_fit)) #1sigma error
        const_array=[fitParamsConst_fit[0]]*len(position_DL_fit)
    
        
        
        
        #Conversion and calculation of DL-field
        print('Width',width,'logfit',fitParamsLog_fit[0])
        conconst=fitParamsLog_fit[0]*width/(2*Ic)*10  #µrad/mT  Corresponds to the conversion constant (obtained form the Oersted field)
        conDL=(fitParamsConst_fit[0])/conconst #mT  Obtained by using the conversion constant (where we now convert the signal corresponding to the DL field)
        conDL_error=conDL*(fit_err_Const[0]/fitParamsConst_fit[0]+fit_err_Log[0]/fitParamsLog_fit[0])
        print('conDL',conDL)
        
        ax.plot(position, theta_DL, '-.v',color='navy',label=DL_label)
        ax.errorbar(position, theta_DL, yerr=error_bar, color='navy')
        #ax.plot(position_DL_fit, theta_DL_fit, color='orange', linewidth=4)
        ax.plot(position_DL_fit, const_array, color='deepskyblue',linewidth=4, label='fit const,'+ '  const='+str("{0:.4g}".format(fitParamsConst_fit[0]))+'±'+str("{0:.4g}".format(fit_err_Const[0])))
    
        #ax.plot(position_DL_fit,Lin_fit(position_DL_fit,fitParamsLin_fit[0],fitParamsLin_fit[1]), label='y=kx+n')
    
        
        ax.plot(position, theta_Oe, '-.v',color='firebrick',label=Oe_label)
        ax.errorbar(position, theta_Oe, yerr=error_bar, color='firebrick')
        ax.scatter(position_mask,np.ones(len(position_mask))*max(theta_DL)*0.8, color = 'r', label = 'Not used for fit')
        #The first line also takes into account an offset in x: x0
        
        #ax.plot(position_Oe_fit,Log_fit(position_Oe_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],fitParamsLog_fit[2],width), label='fit A*ln((w-x)/x),'+ '  A='+str("{0:.4g}".format(fitParamsLog_fit[0]))+'±'+str("{0:.3g}".format(fit_err_Log[0])),color='orange', linewidth=4)
        ax.plot(position_Oe_fit,Log_fit(position_Oe_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],width), label='fit A0+ A*ln((w-x)/x),'+  'A0='+str("{0:.2g}".format(fitParamsLog_fit[1]))+'  A='+str("{0:.2g}".format(fitParamsLog_fit[0]))+'±'+str("{0:.3g}".format(fit_err_Log[0])),color='orange', linewidth=4)
        
        
        
        plt.plot([], [], ' ', label='The obtained conversion coefficient is '+str("{0:.4g}".format(conconst))+'µrad/mT'+ "\n"+ 'and the corresponding DL-field ' + '(' + str("{0:.4g}".format(conDL)) + '±' + str("{0:.4g}".format(conDL_error))+ ')' + 'mT')
        #ax.legend(extra, label='The obtained conversion coefficient is'+str("{0:.4g}".format(conconst))+'µrad/mT and the corresponding DL-field '+str("{0:.4g}".format(conconst))+'mT')
        self.fit_DL_mT = conDL
        self.fit_DL_error_mT = conDL_error
        
        ax.yaxis.major.formatter._useMathText= True
        ax.xaxis.major.formatter._useMathText= True
        
        ax.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
        plt.xlabel('y [µm]')
        
        #box = ax.get_position()
        #ax.set_position([box.x0, box.y0 + box.height * 0.2, box.width, box.height * 0.8])
        #ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.05), fancybox=True, shadow=True, ncol=5)
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.2), ncol=2, fancybox=True, shadow=True, fontsize=14)
        #plt.legend(bbox_to_anchor=(1.05,1),loc=2,borderaxespad=0., fontsize=15)
        ax.grid(1)
        ax2 = ax.twinx()
        # uncomment if you want to plot the temperature
        #ax2.plot(x,T,color='firebrick')
        # comment if you want to show the temperature and umcomment line above, if you want to plot the reflected intensity DC
        #########################################################################################################################################################
        #ax2.plot(position,I2[1],color='firebrick',label='reflection')      
        #########################################################################################################################################################
        ax2.set_ylabel(r'$R$ [a.u.]')
        ax2.plot(position,reflex,color='firebrick',label='reflection')
        #ax2.legend(fontsize=16,loc=4)
        #plt.savefig('graphs/Ni60Cu40_1nm_6mA.png')
        #np.savetxt('saved_data/Ni60Cu40_1nm_6mA.txt', np.transpose((x,first_harmonicR_Oe,first_harmonicR_DL)),delimiter=',')
        
        #ax.set_ylim(ymin=-575)
        #ax.set_ylim(ymax=700)
        plt.savefig(self.path3+ '\\'+ 'fit_'+plotname+'.png')
        plt.show()
        self.analyzed_data['x'] = position
        self.analyzed_data['sum'] = theta_Oe
        self.analyzed_data['diff'] = theta_DL
        self.analyzed_data['errorbar'] = error_bar
        if nice_plot:
            fig,ax = plt.subplots(figsize=(11,8))
            fs = 30
            #print(const_array)
            ax.plot(position, theta_DL, '-.v',color='green',label=r'$\theta_{DL}$')
            ax.errorbar(position, theta_DL, yerr=error_bar, color='green')
            #ax.plot(position_DL_fit, theta_DL_fit, color='orange', linewidth=4)
            ax.plot(position_DL_fit, np.array(const_array), color='lightgreen',linewidth=4, label='fit const. ')#+ str("{0:.2g}".format(fitParamsConst_fit[0]))+'±'+str("{0:.1f}".format(fit_err_Const[0])) +' $\mu$rad')
        
            #ax.plot(position_DL_fit,Lin_fit(position_DL_fit,fitParamsLin_fit[0],fitParamsLin_fit[1]), label='y=kx+n')
        
            
            ax.plot(position, theta_Oe, '-.v',color='k',label=r'$\theta_{Oe}$')
            ax.errorbar(position, theta_Oe, yerr=error_bar, color='k')
            #ax.scatter(position_mask,np.ones(len(position_mask))*max(theta_DL)*0.8, color = 'r', label = 'Not used for fit')
            #The first line also takes into account an offset in x: x0
            
            #ax.plot(position_Oe_fit,Log_fit(position_Oe_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],fitParamsLog_fit[2],width), label='fit A*ln((w-x)/x),'+ '  A='+str("{0:.4g}".format(fitParamsLog_fit[0]))+'±'+str("{0:.3g}".format(fit_err_Log[0])),color='orange', linewidth=4)
            ax.plot(position_Oe_fit,Log_fit(position_Oe_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],width), label='fit A*ln((w-x)/x)', color = 'grey', linewidth=4)
            
            
            
          #  plt.plot([], [], ' ', label='The obtained conversion coefficient is '+str("{0:.4g}".format(conconst))+'µrad/mT'+ "\n"+ 'and the corresponding DL-field ' + '(' + str("{0:.4g}".format(conDL)) + '±' + str("{0:.4g}".format(conDL_error))+ ')' + 'mT')
            
            ax.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize=fs)
            ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.3), ncol=2, fancybox=True, shadow=True, fontsize=fs)
            #ax.set_ylabel(r'$B_{loc}$ [mT]')
            plt.xlabel('y [µm]',fontsize=fs)
            
            #box = ax.get_position()
            #ax.set_position([box.x0, box.y0 + box.height * 0.2, box.width, box.height * 0.8])
            #ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.05), fancybox=True, shadow=True, ncol=5)
            
            #plt.legend(bbox_to_anchor=(1.05,1),loc=2,borderaxespad=0., fontsize=15)
            ax.grid(1)
            ax2 = ax.twinx()
            # uncomment if you want to plot the temperature
            #ax2.plot(x,T,color='firebrick')
            # comment if you want to show the temperature and umcomment line above, if you wanst to plot the reflected intensity DC
            #########################################################################################################################################################
            #ax2.plot(position,I2[1],color='firebrick',label='reflection')      
            #########################################################################################################################################################
            ax2.set_ylabel(r'$R$ [a.u.]',fontsize=fs)
            ax2.plot(position,reflex,color='firebrick',label='reflection')
            #ax2.legend(loc = 'upper right',fontsize=24)
            ax.tick_params(axis='both', which='major', labelsize=fs)
            ax2.tick_params(axis='both'
                            , which='major', labelsize=fs)
            plt.tight_layout(pad=0.5, w_pad=0.8, h_pad=1.0)
            #ax.set_ylim(ymin=-575)
            #ax.set_ylim(ymax=700)
            plt.savefig(self.path3+ '\\'+ 'nice_plotfit_KerrRot'+plotname+'.eps',bbox_inches='tight')
            plt.savefig(self.path3+ '\\'+ 'nice_plotfit_KerrRot'+plotname+'.png',bbox_inches='tight')
    
    def import_analyze(scanlist,path,see_channels = ['data_02','data_04'],auto_spec = True,ignorLines = []):
        Lmoke = None
        
        res = analyze_SHE_OHE([scanlist],path)
        if auto_spec:
            Lmoke = (scanlist.split('_'))[8]
            if '.txt' in Lmoke:
                Lmoke = Lmoke[0:-4]
        else: 
            Lmoke = input('Give calibration file specification if known (leave empty if not known): \n')

        print(Lmoke)
        res.prepare_data(spec_calibration=Lmoke)
        res.import_data(ignorLines= ignorLines)
        for ch in see_channels:
            res.see_intensity(ch_var = ch,ignorelines = ignorLines)
            
    
        res.evaluate_data(do_plot= 'sumdiff')
        res.evaluate_data(do_plot= 'negpos')
        res.evaluate_data(do_plot= 'realimag')
        return res
    
    def import_analyze_SOT(scanlist,path,see_channels = ['data_02','data_04'],spec_cal = None,ignorLines = [],fit_edge_offset = 5):
        
        
        res = analyze_SHE_OHE([scanlist],path)
        
        res.prepare_data(spec_calibration=spec_cal)
        res.import_data(ignorLines= ignorLines)
        for ch in see_channels:
            res.see_intensity(ch_var = ch,ignorelines = ignorLines)
            
    
        res.evaluate_data(do_plot= 'sumdiff')
        res.evaluate_data(do_plot= 'negpos')
        res.evaluate_data(do_plot= 'realimag')
        res.eval_width_and_fit(current_coefficient2 = 0.99,do_plot = False,fit_edge_offset = fit_edge_offset,shift = 0,nice_plot = True)
        return res
    
    def analyzed_to_csv(self):
        D = self.analyzed_data
        csv_filename = self.calc_name[0][0:-4] + '.csv'
        df = pd.DataFrame(D) 

        
        df.to_csv(self.path1 +'//'+ csv_filename, index=False,sep=';')
        print('Data saved at %s' %self.path1)
    
    
    
def analyze_incidences(Data,compare_keys = ['L+','L-'],manual_shift = None):

    if type(Data) == dict:
        if isinstance(Data.keys(),type({}.keys())):
            print('Okay input')
        else:
            raise Exception('Not proper input')
    print(Data.keys())
    ck = compare_keys
    for key1 in Data.keys():
        print(key1)
        Dp= Data[key1][ck[0]].analyzed_data
        Dm= Data[key1][ck[1]].analyzed_data
        Dp['x'] = np.sort(Dp['x'])# Sort by increasing value, incase of scan from + to -
        Dm['x'] = np.sort(Dm['x'])
        plt.plot(Dp['x'],Dp['intFL'],label = ck[0])
        plt.plot(Dm['x'],Dm['intFL'],label = ck[1])
        plt.title('Average drift between two scanlists')
        plt.legend()
        plt.grid()
        plt.show()
        if hasattr(Data[key1][ck[0]], 'edges'):
            edges_Lp = Data[key1][ck[0]].edges
            edges_Lm = Data[key1][ck[1]].edges
        else: 
            Data[key1][ck[0]].get_edges()
            Data[key1][ck[1]].get_edges()
            edges_Lp = Data[key1][ck[0]].edges
            edges_Lm = Data[key1][ck[1]].edges
            
        print(edges_Lp, edges_Lm)
        dx_scan = round((Dm['x'][-1]-Dm['x'][0])/len(Dm['intFL']),2)
        diff_dx1 = int(round((edges_Lm[0]-edges_Lp[0])/dx_scan))
        diff_dx2 = int(round((edges_Lm[1]-edges_Lp[1])/dx_scan))
        diff_dx = int((diff_dx1 + diff_dx2)/2)
        print('Avg shift:', (edges_Lm[0]-edges_Lp[0]), 'corresponding to', diff_dx, 'steps')
        delta = (edges_Lm[0]-edges_Lp[0])
        
        if manual_shift:
            diff_dx = manual_shift
            print('Using manual shift, not edges! diff_dx = ', diff_dx)
            delta = diff_dx*0.25
        if diff_dx>0:
            Dm['intFL'] = Dm['intFL'][diff_dx::]
            Dm['x']=Dm['x'][diff_dx::]
            Dm['x'] -= delta
            Dm['sum'] = Dm['sum'][diff_dx::]
            Dm['diff'] = Dm['diff'][diff_dx::]
            Dm['errorbar'] = Dm['errorbar'][diff_dx::]
        
            Dp['intFL'] = Dp['intFL'][0:-diff_dx]
            Dp['x'] = Dp['x'][0:-diff_dx]
            Dp['sum'] = Dp['sum'][0:-diff_dx]
            Dp['diff'] = Dp['diff'][0:-diff_dx]
            Dp['errorbar'] = Dp['errorbar'][0:-diff_dx]
        
        elif diff_dx == 0:
            print('Nothing to shift!')
        elif diff_dx<0:
            Dm['intFL'] = Dm['intFL'][0:diff_dx]
            Dm['x']= Dm['x'][0:diff_dx]
            Dm['x'] -= delta
            Dm['sum'] = Dm['sum'][0:diff_dx]
            Dm['diff'] = Dm['diff'][0:diff_dx]
            Dm['errorbar'] = Dm['errorbar'][0:diff_dx]
        
            Dp['diff'] = Dp['diff'][int(abs(diff_dx))::]
            Dp['x'] = Dp['x'][int(abs(diff_dx))::]
            Dp['intFL'] = Dp['intFL'][int(abs(diff_dx))::]
            Dp['errorbar'] = Dp['errorbar'][int(abs(diff_dx))::]
            print(Dm['x'],Dm['intFL'])
            
        else:
            raise Exception('Error occured')
            
            
        plt.plot(Dp['x'],Dp['intFL'],label = ck[0])
        plt.plot(Dm['x'],Dm['intFL'],label = ck[1])
        plt.title('Shifted data')
        plt.legend(bbox_to_anchor=(1, 0.5),loc = 'center left')
        plt.ylabel(r'$\theta_{K}^{1\omega}$ [nrad]')
        plt.xlabel('position [$\mu$m]')
        plt.grid()
        plt.show()
        Data[key1][ck[0]].analyzed_data = Dp
        Data[key1][ck[1]].analyzed_data = Dm
        
    return Data

def plot_IP_OOP_LMOKE(Data,add_PMOKE = False,ylim = 70,keys_input = None,invert_x = False,plot_errbar = True, fs = 15,figfactor = 1,Prot = False):
    
    figs = figfactor
    if type(Data) == dict:
        if isinstance(Data.keys(),type({}.keys())):
            print('Okay input')
        else:
            raise Exception('Not proper input')
    ylim = [-ylim,ylim]
    if keys_input:
        keys = keys_input
    else:
        keys = Data.keys()
    if invert_x:
        fac = -1
    else:
        fac = 1 
    fsN = fs
    for key1 in keys:   
        fig,ax1 = plt.subplots(figsize=(6*figs,4.5*figs))
        ax2 = ax1.twinx()
        Ifac = 1e2
        Dp= Data[key1]['L+'].analyzed_data
        Dm= Data[key1]['L-'].analyzed_data
        print(len(Dm['diff']),len(Dp['diff']))
        error_bar = np.sqrt(Dm['errorbar']**2 + Dp['errorbar']**2)
        title = Data[key1]['L+'].calc_name[0].split('_')[1] + ' @ ' + Data[key1]['L+'].calc_name[0].split('_')[2]
        

        if add_PMOKE:
            #title += '_PMOKE'
            Pedges = Data[key1]['P'].get_edges()
            Pedges = np.array(Data[key1]['P'].edges)
            edges_Lp = np.array(Data[key1]['L+'].edges)
            shift = np.mean(Pedges-edges_Lp)
            
            if Prot:
                Protedges = Data[key1]['P-rot'].get_edges()
                Protedges = np.array(Data[key1]['P-rot'].edges)
                edges_Lprot = np.array(Data[key1]['L+'].edges)
                shiftrot = np.mean(Protedges-edges_Lprot)
 
            
        ax2.plot(Dp['x']*fac,Dp['intFL']*Ifac,linewidth = 0.5,color = 'b',linestyle = '--', label = r'R$_{FL}$($\varphi^+$)')
        #ax2.plot(Dm['x']*fac,(Dm['intFL']-0.002)*Ifac*2.5,linewidth = 0.5,color = 'r',linestyle = '--',label = r'R$_{FL}$($\varphi^-$)')
        ax2.plot(Dm['x']*fac,(Dm['intFL'])*Ifac,linewidth = 0.5,color = 'r',linestyle = '--',label = r'R$_{FL}$($\varphi^-$)')
        #plt.plot(Dp['x'],Dp['diff'],color = 'b',marker = '.', label = 'L+ diff')
        #plt.plot(Dm['x'],Dm['diff'],color = 'r',marker = '.',label = 'L- diff')
        if add_PMOKE:
            
            DPMOKE= Data[key1]['P'].analyzed_data
            
            
            if plot_errbar:
                ax1.errorbar((DPMOKE['x']-shift)*fac,DPMOKE['diff'],color = 'k',yerr=DPMOKE['errorbar'],marker = '^', label = r'$\theta_K ^{P}$',elinewidth = 0.5)
            else:
                ax1.plot((DPMOKE['x']-shift)*fac,DPMOKE['diff'],color = 'k',ms = 6,marker = '^', label = r'$\theta_K ^{P}$')
            ax2.plot((DPMOKE['x']-shift)*fac,DPMOKE['intFL']*Ifac,linewidth = 0.5,color = 'k',linestyle = '--', label = 'R$_{FL}$(P)')
            
        if plot_errbar:    
            ax1.errorbar(Dp['x']*fac,Dp['diff'],color = 'b',yerr=error_bar,ms = 6,marker = '.', label = r'$\theta_K ^{LP}(\varphi^+)$',elinewidth = 0.5)
            ax1.errorbar(Dm['x']*fac,Dm['diff'],color = 'r',yerr=error_bar,ms = 6,marker = '.',label = r'$\theta_K ^{LP}(\varphi^-)$',elinewidth = 0.5)
        else:
            ax1.plot(Dp['x']*fac,Dp['diff'],color = 'b',ms = 10,marker = '.', label = r'$\theta_K ^{LP}(\varphi^+)$')
            ax1.plot(Dm['x']*fac,Dm['diff'],color = 'r',ms = 10,marker = '.',label = r'$\theta_K ^{LP}(\varphi^-)$')
        
        ax1.set_ylim(ylim[0],ylim[1])
        #plt.title(r'$\pm$ LMOKE of ' + Data[key1]['L+'].calc_name[0].split('_')[1],pad=20)
        
        
        ax1.legend(loc='upper center', bbox_to_anchor=(0.5, 1.45), ncol=2, fancybox=True, shadow=True, fontsize=fs)
        ax2.legend(loc = 'lower right', fontsize=fs)
        #ax2.set_ylabel(r'R [a. u.]',fontsize = fs)
        ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize = fs)
        ax1.set_xlabel('position [$\mu$m]',fontsize = fs)
        ax1.tick_params(axis='both', which='major', labelsize=fs)
        ax2.tick_params(axis='both', which='major', labelsize=fs)
        ax1.grid()
        fig.tight_layout()
        
        plt.savefig(Data[key1]['L+'].path2+ '\\'+ 'preliminary_%s_LpLm.png' %title,bbox_inches='tight')
        plt.savefig(Data[key1]['L+'].path2+ '\\'+ 'preliminary_%s_LpLm.eps' %title,bbox_inches='tight')
        print('Saved at:',Data[key1]['L+'].path2[32::])
        #plt.show()
        
        

        fig,ax1 = plt.subplots(figsize=(6*figs,5*figs))
        ax2 = ax1.twinx()
        def Const_fit(x,y0):
                    return y0
        
            

        theta_IP = (Dp['diff'] - Dm['diff'])/2
        theta_OOP= (Dp['diff'] + Dm['diff'])/2
        edgesP, width = analysis_field.find_edges_width(Dp['x'],Dp['intFL'])
        edgesM, width = analysis_field.find_edges_width(Dm['x'],Dm['intFL'])
        print('Edges',edgesP,edgesM)
        ledge = max([edgesP[0],edgesM[0]])
        redge =min([edgesP[1],edgesM[1]])
        print('Using Edges',ledge,redge)
        width = np.ceil(np.abs(redge - ledge))
        mask = (Dp['x'] < redge -0.5)*(Dp['x'] > ledge+ 0.5)
        
        def Log_fit(x,A,A0,width):
                return A0-A*np.log((width-(x))/(x))
        """
        if np.mean(Dp['x']-Dm['x']) < 0.5: #shifted less than one step
            mask = (Dp['x'] < redge -0.75)*(Dp['x'] > ledge+ 0.75)
        else:
            raise Exception('Two curves are still shifted!!')
        """
        pos_OOP_fit = Dp['x'][mask]
        theta_OOP_fit = theta_OOP[mask]
        
        pos_OOP_fit = pos_OOP_fit - pos_OOP_fit[0]+0.5
        A_guess = 1.0
        A0_guess = np.mean(theta_OOP_fit)

        fitParamsConst_fit, fitCovariancesConst_fit= curve_fit(lambda x,y0: Const_fit(x,y0),Dp['x'][mask] , theta_IP[mask], sigma=error_bar[mask], absolute_sigma=True)
        fit_err = np.sqrt(np.diag(fitCovariancesConst_fit)) #1sigma error
        fitParamsLog_fit, fitCovariancesLog_fit= curve_fit(lambda x, A, A0: Log_fit(x,A,A0,width),  pos_OOP_fit, theta_OOP_fit,p0=[A_guess, A0_guess], method =  'trf', sigma=error_bar[mask],absolute_sigma=True)
        logfit_err = np.sqrt(np.diag(fitCovariancesLog_fit)) #1sigma erro
        ax1.plot((pos_OOP_fit+ Dp['x'][mask][0]-0.5)*fac,Log_fit(pos_OOP_fit,fitParamsLog_fit[0],fitParamsLog_fit[1],width),label = r'fit $\theta ^{P*}_K$',color = 'orange')
        const_array=[fitParamsConst_fit[0]]*len(Dp['x'][mask])
        print('Fit of constant',const_array[0],fit_err)
        
        if add_PMOKE:
            
            ax2.plot((DPMOKE['x']-shift)*fac,DPMOKE['intFL']*Ifac,linewidth = 0.5,color = 'k',linestyle = '--', label = 'R$_{FL}$(P)')
        ax2.plot(Dp['x']*fac,Dp['intFL']*Ifac,linewidth = 0.5,linestyle = '--',label = 'R$_{FL}(L,P*)$')
        #plt.plot(Dm['x'],Dm['intFL']*100,linewidth = 0.5,linestyle = '--',label = 'I(L-)')
        if Prot:
           DProtMOKE= Data[key1]['P-rot'].analyzed_data
           ax2.plot((DProtMOKE['x']-shiftrot)*fac,DProtMOKE['intFL']*Ifac,linewidth = 0.5,color = 'violet',linestyle = '--', label = 'R$_{FL}$(P-90°)')

           if plot_errbar:
               ax1.errorbar((DProtMOKE['x']-shiftrot)*fac,DProtMOKE['diff'],color = 'darkred',yerr=DProtMOKE['errorbar'],marker = 's', label = r'$\theta^P_K (90°)$',elinewidth = 0.5)
           else:
               ax1.plot((DProtMOKE['x']-shiftrot)*fac,DProtMOKE['diff'],ms = 4,color = 'darkred',marker = 's', label = r'$\theta^P_K (90°)$')
        
        
            
            
           
       
        ax1.plot(Dp['x'][mask]*fac,const_array,color = 'r',label = r'fit $\theta ^{L}_K$')
        #ax1.plot((Dp['x']+0.05)*fac,theta_OOP, marker = 's',ms= 4,color = 'grey', label = r'$\theta ^{P*}_K$')
        if plot_errbar:
            ax1.errorbar(Dp['x']*fac,theta_IP, yerr=error_bar ,linewidth = 1,color='g', elinewidth = 0.5,label = r'$\theta ^{L}_K$')
            ax1.errorbar((Dp['x']+0.05)*fac,theta_OOP, yerr=error_bar ,linewidth = 1,color='grey',elinewidth = 0.5, label = r'$\theta ^{P*}_K$')
        else:
            ax1.plot(Dp['x']*fac,theta_IP, marker = '.',ms = 8,color = 'green', label = r'$\theta ^{L}_K$')
            ax1.plot((Dp['x']+0.05)*fac,theta_OOP, marker = 's',ms= 4,color = 'grey', label = r'$\theta ^{P*}_K$')
        if add_PMOKE:
            if plot_errbar:
                ax1.errorbar((DPMOKE['x']-shift)*fac,DPMOKE['diff'],color = 'k',yerr=DPMOKE['errorbar'],marker = '^', label = r'$\theta^P_K$',elinewidth = 0.5)
            else:
                ax1.plot((DPMOKE['x']-shift)*fac,DPMOKE['diff'],color = 'k',ms = 6,marker = '^', label = r'$\theta^P_K$')
            
        #plt.plot(Dp['x'][mask],const_array,color = 'r',label = '$\theta_K^{fit}$ = %.1f$\pm$%.1f nrad' %(fitParamsConst_fit[0],fit_err))
        
        
       #plt.plot(Dp['x'][mask],Log_fit(Dp['x'][mask],fitParamsLog_fit[0],fitParamsLog_fit[1],width), label='fit')
        
        #plt.title(r'IP and OOP $\theta_K^{1\omega}$ of %s' %Data[key1]['L+'].calc_name[0].split('_')[1],pad=20)
        
        #ax1.legend(bbox_to_anchor=(1, 0.5),loc = 'center left',fontsize = fs)
        ax1.legend(loc='upper center', bbox_to_anchor=(0.5, 1.45), ncol=3, fancybox=True, shadow=True, fontsize=fsN)
       #ax1.set_ylabel(r'$ \theta_{K}^{1\omega}$ [nrad]',fontsize = fsN)
        ax2.set_ylabel(r'R [a. u.]',fontsize = fsN)
        ax1.set_xlabel('position [$\mu$m]',fontsize = fsN)
        ax1.set_ylim(0.7*ylim[0],0.7*ylim[1])
        ax2.legend(loc = 'lower right', fontsize=fsN-2)
        ax1.tick_params(axis='both', which='major', labelsize=fsN)
        ax2.tick_params(axis='both'
                        , which='major', labelsize=fsN)
        ax1.grid()
        plt.tight_layout()
        plt.savefig(Data[key1]['L+'].path2+ '\\'+ 'preliminary_%s_IP_OOP_accumulation.png' %title)
        plt.savefig(Data[key1]['L+'].path2+ '\\'+ 'preliminary_%s_IP_OOP_accumulation.eps' %title)
        print('Saved at:',Data[key1]['L+'].path2[32::])
        plt.show()
        dataN = {}
        dataN['IP'] = theta_IP
        dataN['OOP'] = theta_OOP
        dataN['errorbar'] = error_bar
        dataN['fit'] = fitParamsConst_fit[0]
        dataN['fit_err'] = fit_err[0]
        dataN['logfit'] = fitParamsLog_fit[0]
        dataN['logfit_err'] = logfit_err[0]
        
        Data[key1]['diff'] = dataN
    return Data
    
def plot_data_2nested_analyzed_data(Data,currs,keys,plotkey,fs  = 12,invertx = False,shift = None,mark = 'o'):
    if len(currs) > 1:
        colors = mpl.cm.tab20b(np.linspace(0, 0.3, len(currs)))
    else:
        colors = mpl.cm.tab20b(np.linspace(0,0.3, len(keys)))
    if type(Data) == dict:
        if isinstance(Data.keys(),type({}.keys())):
            print('Okay input')
        else:
            raise Exception('Not proper input')
    if not shift:
        shift = np.zeros(100)
    if invertx:
        fac = -1
    else:
        fac= 1
    i = 0
    fig,ax1 = plt.subplots(figsize=(6,4))
    ax2 = ax1.twinx()
    if plotkey == 'thermoreflectance':
        errfac = 1e5
        ylabel = r'$-R^{2\omega}/R$'
    else:
        errfac = 1
        ylabel = r'$\theta_K^{P}$'
    
    for key1 in currs:
        for key in keys:
            D = Data[key1][key].analyzed_data
            ax2.plot(D['x'][1:-2]*fac+ shift[i],D['intFL'][1:-2],linewidth = 1,color = colors[i],linestyle = '--',label = 'R$_{Mon}$(%s)'%key)
            #*(i/2+1)
            
            ax1.plot(D['x'][1:-2]*fac + shift[i],D[plotkey][1:-2], marker = mark, ms = 5,color = colors[i], label = ylabel + ' (%s)'%key)
            ax1.errorbar(D['x'][1:-2]*fac + shift[i],D[plotkey][1:-2], yerr=D['errorbar'][1:-2]/errfac ,linewidth = 1,color=colors[i], elinewidth = 0.5)
            if len(currs) == 1:
                i+= 1
        i += 1
        
    ax1.set_xlim([-9,9])
    ax2.set_ylabel(r'$R$ [a.u.]',fontsize=fs)
    title = Data[currs[0]][keys[0]].calc_name[0].split('_')[1] 
    ax1.legend(loc='upper center', bbox_to_anchor=(0.5, 1.3), ncol=2, fancybox=True, shadow=True, fontsize=fs)
    #plt.title('%s' %(title))
    if plotkey == 'thermoreflectance':
        ax1.set_ylabel(r'$-R^{2\omega}/R \cdot 10^5$ ',fontsize = fs+2)
    else:
        ax1.set_ylabel(r'$\theta_{K}^{1\omega}$ [nrad]',fontsize = fs+2)
    #ax1.legend(bbox_to_anchor=(1.3, 0.5),loc = 'center left',fontsize = fs)
    ax1.tick_params(axis='both', which='major', labelsize=fs)
    ax2.tick_params(axis='both'
                    , which='major', labelsize=fs)
    
    ax1.set_xlabel('position [$\mu$m]',fontsize = fs+2)
    ax1.grid()
    ax2.legend(fontsize = fs,loc = 'upper right')
    plt.tight_layout()
    plt.savefig(Data[key1][key].path1+ '\\'+ 'preliminary_%s_%s_%s_accumulation.png' %(title,currs,keys))
    plt.savefig(Data[key1][key].path1+ '\\'+ 'preliminary_%s_%s_%s_accumulation.eps' %(title,currs,keys))
    print('Saved in ',Data[key1][key].path1)
    plt.show()
    
def plot_data_single(Data,plotkey,info,input_keys = [],save_path = 'C:/Users/tgoldenbe/polybox/Thesis/Images/Data',ylim = [-1,1],fs = 12):
    if input_keys:
        keys = input_keys
    else:
        keys = list(Data.keys())
    
    title = info['title']
    colors = mpl.cm.tab10(np.linspace(0, 0.5, len(keys)))
    if type(Data) == dict:
        if isinstance(Data.keys(),type({}.keys())):
            print('Okay input')
        else:
            raise Exception('Not proper input')
    i = 0
    fig,ax1 = plt.subplots(figsize=(10,4))
    ax2 = ax1.twinx()
    for key in keys:
        D = Data[key]
        ax2.plot(D['x'],D['averagein2value'],linewidth = 0.5,color = colors[i],linestyle = '--')
        
        ax1.plot(D['x'],D[plotkey], marker = '.',color = colors[i], label = key)
        
        i += 1
            
    ax2.set_ylabel(r'$R$ [a.u.]',fontsize = fs) 
    plt.title('%s' %(title),fontsize = fs+2)
    ax1.set_ylabel(info['ylabel'],fontsize = fs)
    ax1.legend(bbox_to_anchor=(1.1+(fs-10)/30, 0.5),loc = 'center left',fontsize = fs)
    ax1.set_xlabel(info['xlabel'],fontsize = fs)
    ax1.tick_params(axis='both', which='major', labelsize=fs)
    ax2.tick_params(axis='both', which='major', labelsize=fs)
    ax1.grid()
    ax1.set_ylim(ylim)
    plt.tight_layout()
    if len(keys) > 2:
        plt.savefig(save_path+ '\\'+ '%s.eps' %(title))
    else:
        plt.savefig(save_path+ '\\'+ '%s_%s.eps' %(title,keys))
    print('saved at %s'%save_path)
    plt.show()
    
def plot_data_averaged(Data,plotkey,info,input_keys = [],averaging = 4,save_path = 'C:/Users/tgoldenbe/polybox/Thesis/Images/Data',ylim = [-1,1]):
    if input_keys:
        keys = input_keys
    else:
        keys = list(Data.keys())
    
    title = info['title']
    colors = mpl.cm.magma(np.linspace(0, 0.8, len(keys)))
    if type(Data) == dict:
        if isinstance(Data.keys(),type({}.keys())):
            print('Okay input')
        else:
            raise Exception('Not proper input')
    i = 0
    fig,ax1 = plt.subplots(figsize=(8,4))
    ax2 = ax1.twinx()
    for key in keys:
        #D = Data[key]
        D = Data
        ax2.plot(D['x'],D['averagein2value'][averaging]*5,linewidth = 0.5,color = colors[i],linestyle = '--')
        
        ax1.plot(D['x'],D[plotkey][averaging], marker = '.',color = colors[i], label = key)
        
        i += 1
            
    ax2.set_ylabel(r'$R$ [a.u.]') 
    plt.title('%s' %(title))
    ax1.set_ylabel(info['ylabel'])
    ax1.legend(bbox_to_anchor=(1.1, 0.5),loc = 'center left')
    ax1.set_xlabel(info['xlabel'])
    ax1.grid()
    ax1.set_ylim(ylim)
    plt.tight_layout()
    plt.savefig(save_path+ '\\'+ '%s_%s.eps' %(title,keys))
    plt.savefig(save_path+ '\\'+ '%s_%s.png' %(title,keys))
    plt.show()

def load_meas_data(meas_list,meas_names,datapathprefix = '../Data/Data_S1/'):
    data = {name: {} for name in meas_names}
    if len(meas_names) != len(meas_list):
        raise Exception('Names and measurement list not same length! Abort.')
    for i in range(len(meas_names)):
        file = meas_list[i]
        name = meas_names[i]
        data[name] = analysis_field.linescan_calc_Tobi(file,convert_dict_to_list = False)
        
    return data



def plot_AC_kerr(fig,file,label,phi_p,color,marker = '.',theta = 15,slope = 1, shift = 0):
    
    theta_rad = theta*np.pi/180
    name = file
    res = analysis_field_emir.direct_fieldscan_AC(name,data_order = '1LI')
    phi_p_rad = phi_p*np.pi/180.0
    H_POLAR = res[-1]
    minH = np.where(H_POLAR == min(H_POLAR))[0][0]
    maxH = np.where(H_POLAR == max(H_POLAR))[0][0]
    indixpH = [minH, maxH]
    indixpH = np.sort(indixpH)  # indices where we sweep from -H to +H
    
    #indixmH = [minH[1], maxH]
    #indixmH = np.sort(indixmH)  # indices where we sweep from -H to +H
    
    theta_K_POLAR = (res[4] * np.cos(theta_rad) + res[5] * np.sin(theta_rad))*slope
    
    
    
    xp = H_POLAR[indixpH[0]:indixpH[1]]
    #xm = H_POLAR[indixmH[0]:indixmH[1]]
    yp = theta_K_POLAR[indixpH[0]:indixpH[1]]
    #ym = theta_K_POLAR[indixmH[0]:indixmH[1]]
    
    ind = np.argsort(xp)    
    #print(len(ind),len(y),len(x))
    x_sort = xp[ind]
    y_sort = yp[ind]
    
    plt.scatter(x_sort,y_sort + shift,label = label,marker = marker,s = 8,color = color)
    mask = (np.abs(x_sort) > 5)
    inv_mask = np.invert(mask)
    
    def func_qMOKE(x,A0,A,B,Hk,phi_p_rad):
        #return A/(x-Hk) + B*np.cos(2*phi_p_rad)/x
        return A0 + np.sign(x)*A/(np.abs(x)-Hk) + np.sign(x)*B*np.cos(2*phi_p_rad)/np.abs(x)
    fit_func = lambda x,A0, A, B, Hk: func_qMOKE(x,A0, A, B, Hk, phi_p_rad)
    
    res,cov = curve_fit(fit_func, x_sort[mask],y_sort[mask])#,bounds=([0,0,-1], [3., 1., 0]))
    A0_fit,A_fit, B_fit, Hk_fit = res
    y_fit = func_qMOKE(x_sort,A0_fit,A_fit,B_fit,Hk_fit,phi_p_rad)
    #plt.plot(x_sort,y_fit,label = label)
    return fig
        
def plot_DC_kerr(ax,file,calibration_type,calibration_polarisation,key,color,calib_path):
     
     cwd = os.getcwd()
     spec_calibration=calibration_type+calibration_polarisation
     
     logfilenameShort = file
     res = analysis_field_emir.fieldscan_DC(logfilenameShort)
    
     plotname=calibration_type+calibration_polarisation+'.png'    
     path1=calib_path
     
     
     print('There are the following Calibration files:')
     for file in os.listdir(path1):
         if file.startswith("calibration"):
             print(file)
     """
     print('\nMOKE @',Lmoke[0:-4])
     for file in os.listdir(path1):
         if file.startswith("calibration"):
             if Lmoke in file:
                 xtra_cal_name = file[11:-4]
                 found = True
                 print('AUTO: using this file! ',xtra_cal_name)
     if not found:
         xtra_cal_name = input('Use xtra calibration file? Leave empty if standard one is preferred')
     """
     
     ##Calibration data+ Resistivities+ Phase
     
     try: 
         
         if spec_calibration:
             print(path1+'\\calibration' + spec_calibration + '.txt')
             file = open(path1+'\\calibration' + spec_calibration + '.txt', 'r')
             
             print('Using the calibration file: calibration' + spec_calibration + '.txt')
         else:
             file = open(path1+'\\calibration' + '.txt', 'r')
             print(" Using Calibration file" , path1+'\\calibration' + '.txt') 
         calibration_data=np.fromstring(file.readline().split('\n')[0], dtype=float, sep=' ')
         R1=float(file.readline().split('\n')[0])
         R2=float(file.readline().split('\n')[0])
         theta=float(file.readline().split('\n')[0])
         #theta2 = float(file.readline().split('\n')[0])
         file.close()
     
     except FileNotFoundError:
         file= open(path1+'\\calibration.txt', 'w')
         print("Calibration file" , path1+'\\calibration.txt' ,  " created ")
         print('\n Input the calibration data manually')
         calibration_data=input('\n')
         
         print('\n Input the resistance of the NM/M system')  
         R1=input('\n')
         print('\n Input the resistance of the M (reference without NM)')
         R2=input('\n')
         
         print('\n Input the first harmonic phase offset')
         theta=input('\n')
         print('\n Input the 2nd harmonic phase offset')
         #theta2=input('\n')
         
      
         file.write(calibration_data+ '\n')
         file.write(R1+ '\n' )
         file.write(R2+ '\n')
         file.write(theta)
         #file.write(theta2)
     
         calibration_data=np.fromstring(calibration_data, dtype=float, sep=' ')
         R1=float(R1)
         R2=float(R2)
         theta=float(theta)
         #theta2=float(theta2)
         
         file.close()
      
     sln = analysis_field.calibrate(np.linspace(0,25,6),calibration_data,plotting=True)
     sln = 1/(sln)*np.pi/180.0*1e6 ##(µrad/mV)
     
     #sln=-sln #remove this later
     print(R1,R2)
     
     theta_rad=theta*np.pi/180.0
     print(theta_rad)
             
     #CTR = 2.213e-05*1e6
     #CTR= -0.58*1e-4*1e6  
     print(cwd)
     pattern =r'_(.*?)(?=_|$)'
     date_pattern = r'(\d{8})'
     # Find all matches
     
     print(logfilenameShort)
     offset = np.min(res[1])
     ax.scatter(res[0], res[1]-offset, label=key, s=8, color=color)
     
     return ax
     
     
 
def import_polar_AC(filename,slope,theta,theta2):
    theta_rad = theta*np.pi/180
    theta2_rad = theta2*np.pi/180
    sln_POLAR = slope
    DATA_POLAR,ScanNumber_POLAR,titles_POLAR=analysis_field_emir.data_calculation_AC(filename)
    #print(np.shape(DATA[0]))
    x_POLAR,I_POLAR,I2_POLAR,x1_POLAR,y1_POLAR,x2_POLAR,y2_POLAR,l2x2_POLAR,l2y2_POLAR,l2x1_POLAR,l2y1_POLAR,H_POLAR=DATA_POLAR
    
    x_POLAR=x_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))  #format the data so that each measurement is represented by a specific color
    I_POLAR=I_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    I2_POLAR=I2_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    x1_POLAR=x1_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    y1_POLAR=y1_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    x2_POLAR=x2_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    y2_POLAR=y2_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    l2x2_POLAR=l2x2_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    l2y2_POLAR=l2y2_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    l2x1_POLAR=l2x1_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    l2y1_POLAR=l2y1_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    H_POLAR=H_POLAR.reshape((ScanNumber_POLAR,len(H_POLAR)//ScanNumber_POLAR))
    
    ###############################
    ######Saving the raw data######
    ###############################
    
    Data1_POLAR=np.row_stack((x_POLAR,I_POLAR,I2_POLAR,x1_POLAR,y1_POLAR,x2_POLAR,y2_POLAR,l2x2_POLAR,l2y2_POLAR,l2x1_POLAR,l2y1_POLAR,H_POLAR))
    Data1_POLAR=np.transpose(Data1_POLAR)
                                                                            #It is working like this, but it is not really nice looking :( Let's see whether we can take care
    
    
    header_POLAR = pd.MultiIndex.from_product([['Imag (A)','BD DC (V)', 'ID DC (V)','l1x1 (uV)','l1y1 (uV)', 'l1x2 (uV)', 'l1y2 (uV)', 'l2x2 (uV)','l2y2 (uV)', 'l2x1 (uV)', 'l2y1 (uV)', 'Hext (mT)'],
                                         titles_POLAR])
    df_POLAR = pd.DataFrame(Data1_POLAR, columns=header_POLAR)
    
    #df_POLAR.to_excel(path3+ '\\'+ filename.split('.txt')[0]+'_RAW.xlsx')
    
    
    
    
    
    ###########################################
    #Taking phase and calibration into account
    ###########################################
    
    ###############################################################################################DC BD signal
    thetaDC_K_POLAR=I_POLAR*1000*sln_POLAR #### factor 1000 because DC signal from BD is recorded in V, slope is however given in murad/mV 
    #print(np.shape(theta_K_POLAR))
    
    fig,ax = plt.subplots(figsize=(12,8))
    colors = mpl.cm.tab10(np.linspace(0, 1, ScanNumber_POLAR))
    #colors = mpl.cm.rainbow(np.linspace(0, 1, ScanNumber))
    for x,y, c, t in zip(H_POLAR,thetaDC_K_POLAR, colors, titles_POLAR):
        ax.plot(x, y, color=c, label=t)
        #print(x,y)
        
    #autoscaling
    ax.autoscale_view(True,True,True)
    ax.relim()
    #labeling
    plt.title(r'$\theta_{K}$ vs $B_{ext}$')
    plt.ylabel(r'$\theta_{K}$ (µrad)', fontsize = 16) 
    plt.xlabel('$B_{ext}$ (mT)', fontsize = 16)
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    ax.yaxis.set_ticks_position('both')
    ax.xaxis.set_ticks_position('both')
    #ax.yaxis.set_major_locator(plt.MaxNLocator(5))     #OVo cudo ogranici broj labela na y osi
    ax.minorticks_on() 
    ax.tick_params(axis='both', which='major', direction='in')
    ax.tick_params(axis='both', which='minor', direction='in')
    #plt.savefig(path3+ '\\'+ 'POLAR_DC' + plotname)
    plt.show()  #Okay, it is working :) Now you have to do the standard stuff (take the calibration and the phase into account and so on..)
    
    
    ###############################################################################################1st harmonic
    theta_K_POLAR=(x1_POLAR*np.cos(theta_rad)+y1_POLAR*np.sin(theta_rad))*sln_POLAR 
    #print(np.shape(theta_K_POLAR))
    
    fig,ax = plt.subplots(figsize=(12,8))
    colors = mpl.cm.tab10(np.linspace(0, 1, ScanNumber_POLAR))
    #colors = mpl.cm.rainbow(np.linspace(0, 1, ScanNumber))
    for x,y, c, t in zip(H_POLAR,theta_K_POLAR, colors, titles_POLAR):
        ax.plot(x, y, color=c, label=t)
        #print(x,y)
        
    #autoscaling
    ax.autoscale_view(True,True,True)
    ax.relim()
    #labeling
    plt.title(r'$\theta_{K}$ vs $B_{ext}$')
    plt.ylabel(r'$\theta_{K}$ (µrad)', fontsize = 16) 
    plt.xlabel('$B_{ext}$ (mT)', fontsize = 16)
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    ax.yaxis.set_ticks_position('both')
    ax.xaxis.set_ticks_position('both')
    #ax.yaxis.set_major_locator(plt.MaxNLocator(5))     #OVo cudo ogranici broj labela na y osi
    ax.minorticks_on() 
    ax.tick_params(axis='both', which='major', direction='in')
    ax.tick_params(axis='both', which='minor', direction='in')
    #plt.savefig(path3+ '\\'+ 'POLAR_1w' + plotname)
    plt.show()  #Okay, it is working :) Now you have to do the standard stuff (take the calibration and the phase into account and so on..)
    
    ###############################################################################################2nd harmonic
    theta2_K_POLAR=(x2_POLAR*np.cos(theta2_rad)+y2_POLAR*np.sin(theta2_rad))*sln_POLAR 
    #print(np.shape(theta2_K_POLAR))
    
    fig,ax = plt.subplots(figsize=(12,8))
    colors = mpl.cm.tab10(np.linspace(0, 1, ScanNumber_POLAR))
    #colors = mpl.cm.rainbow(np.linspace(0, 1, ScanNumber))
    for x,y, c, t in zip(H_POLAR,theta2_K_POLAR, colors, titles_POLAR):
        ax.plot(x, y, color=c, label=t)
        #print(x,y)
        
    #autoscaling
    ax.autoscale_view(True,True,True)
    ax.relim()
    #labeling
    plt.title(r'$\theta_{K}$ vs $B_{ext}$')
    plt.ylabel(r'$\theta_{K}$ (µrad)', fontsize = 16) 
    plt.xlabel('$B_{ext}$ (mT)', fontsize = 16)
    plt.legend(bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0.)
    ax.yaxis.set_ticks_position('both')
    ax.xaxis.set_ticks_position('both')
    #ax.yaxis.set_major_locator(plt.MaxNLocator(5))     #OVo cudo ogranici broj labela na y osi
    ax.minorticks_on() 
    ax.tick_params(axis='both', which='major', direction='in')
    ax.tick_params(axis='both', which='minor', direction='in')
    #plt.savefig(path3+ '\\'+ 'POLAR_2w' + plotname)
    plt.show()  #Okay, it is working :) Now you have to do the standard stuff (take the calibration and the phase into account and so on..)
    
    
    #####################################
    ######Saving the evaluated data######
    #####################################
    
    
    Data1_POLAR_evaluated=np.row_stack((x_POLAR, thetaDC_K_POLAR, theta_K_POLAR, theta2_K_POLAR ,H_POLAR))
    Data1_POLAR_evaluated=np.transpose(Data1_POLAR_evaluated)
                                                                            #It is working like this, but it is not really nice looking :( Let's see whether we can take care
    
    
    header_POLAR_evaluated = pd.MultiIndex.from_product([['Imag (A)','theta_K_DC (murad)','theta_K_1st (murad)', 'theta_K_2nd (murad)', 'Hext (mT)'],
                                         titles_POLAR])
    df_POLAR_evaluated = pd.DataFrame(Data1_POLAR_evaluated, columns=header_POLAR_evaluated)
    
    #df_POLAR_evaluated.to_excel(path3+ '\\'+ logfilenameShort_POLAR.split('.txt')[0]+'_KERR.xlsx')
    


def toymodel_dichroism(frac_E_ex_split,dE_CB_VB = 10, SOC = True, CB = 'p', VB = 'd'):
    """
    Careful: Selection rules demand dl = +- 1 AND dml = +- 1 (hence also only p <-> d transitions etc)
    
    """
    if np.abs(frac_E_ex_split) > 1:
        raise Exception('E_ex_split must be in [0,1], since its a fraction of dE_CB_VB')
    if CB == 'p':
        num_CBlvl = 3
    elif CB == 'd':
        num_CBlvl = 5
    
    if VB == 'p':
        num_VBlvl = 3
    elif VB == 'd':
        num_VBlvl = 5    

    CB_lvl = np.linspace(-(num_CBlvl-1)/2,(num_CBlvl-1)/2,num_CBlvl) 
    VB_lvl = np.linspace(-(num_VBlvl-1)/2,(num_VBlvl-1)/2,num_VBlvl) 
    
    CB_up = CB_lvl + dE_CB_VB/2
    CB_down = CB_lvl +  dE_CB_VB/2 #Sure not to multiply CB_lvl by *(-1) for opposite spins???
    VB_up = VB_lvl + frac_E_ex_split*dE_CB_VB - dE_CB_VB/2
    VB_down = VB_lvl*(-1) - dE_CB_VB/2
    
    Eup = [CB_up,VB_up]
    Edown = [CB_down,VB_down]
    up_ms = [CB_lvl,VB_lvl]
    down_ms = [CB_lvl,VB_lvl*(-1)] #Sure not to multiply CB_lvl by *(-1) for opposite spins???
    
    up_trans = {'l':[],'r':[]}
    down_trans = {'l':[],'r':[]} #define m_final - m_initial < 0 to be left circ polarized
   
    for m in down_ms[1]:
        for sgn in [-1,1]: #transition due to left or right circ polarized light
            f_index = np.where(down_ms[0] == (m+sgn*1))
            i_index = np.where(down_ms[1] == (m))
            #print(Edown[1][i_index],Edown[0][f_index],Edown)
            dE = Edown[0][f_index] - Edown[1][i_index]
            if dE.size > 0:
                if (down_ms[0][f_index][0]-m) < 0:
                    down_trans['l'].append(dE[0])
                elif (down_ms[0][f_index][0]-m) > 0:
                    down_trans['r'].append(dE[0])
                else: 
                    raise Exception('Transition not allowed!')
    for m in up_ms[1]:
        for sgn in [-1,1]: #transition due to left or right circ polarized light
            f_index = np.where(up_ms[0] == (m+sgn*1))
            i_index = np.where(up_ms[1] == (m))
            #print(Eup[1][i_index],Eup[0][f_index],Eup)
            dE = Eup[0][f_index] - Eup[1][i_index]
            if dE.size > 0:
                if (up_ms[0][f_index][0]-m) < 0:
                    up_trans['l'].append(dE[0])
                elif (up_ms[0][f_index][0]-m) > 0:
                    up_trans['r'].append(dE[0])
                else: 
                    raise Exception('Transition not allowed!')  
                    
    trans = {'up':up_trans,'down':down_trans}
    dichroism = {'l':np.zeros(2),'r':np.zeros(2) }
    for channel in trans:
        #print(channel)
        for photon_pol in trans[channel]:
            #print(photon_pol)
            dichroism[photon_pol][0] += len(trans[channel][photon_pol]) #number of transitions
            dichroism[photon_pol][1] += np.sum(trans[channel][photon_pol]) #number of transitions
    dichroism['l'][1] = dichroism['l'][1]/dichroism['l'][0]
    dichroism['r'][1] = dichroism['r'][1]/dichroism['r'][0]
    return {'up':Eup,'down':Edown},{'up':up_ms,'down':down_ms},dichroism

def qmoke_fit_func(Hext,Qv = -1,Bquad = 1,Hk =-0.5,phi_p = 90,B_fl = 1,B_dl = 2,H_oe = 0.1,phi_incident = 0,theta_K_rem = 0.01,Hk_plane = 0.01,Ms = 5, source = 'Montazeri'):
    
    """
    Hext being array from [-Ha,Ha]
    other parameters are floats
    phi_incident = 0 == PMOKE (in degrees)
    """
    hyst_resp = np.ones(len(Hext))
    hyst_resp[0:int(len(Hext)/2)] = -1
    hyst_resp = hyst_resp*theta_K_rem
    
    phi_inc_rad = np.deg2rad(phi_incident)
    phi_p_rad = np.deg2rad(phi_p)
    print(phi_inc_rad,phi_p_rad)
    #Hext = np.linspace(-Ha,Ha,num = 1000)
    if source == 'Montazeri':  
        h_parallel = H_oe + B_fl
        del_theta_k = Qv*B_dl/((Hext)-Hk) + Bquad*h_parallel*np.cos(2*phi_p_rad)/(Hext)
    if source == 'Fan':
        h_parallel = H_oe + B_fl
        del_theta_k = Qv*B_dl/((Hext)+Hk_plane+Ms-Hk) + Bquad*h_parallel*np.cos(2*phi_p_rad)/(Hext+Hk_plane)
    if source == 'custom':
        h_parallel = H_oe + B_fl
        sig = np.sign(Hext)
        del_theta_k = sig*(np.cos(phi_inc_rad)*Qv*B_dl/(np.abs(Hext)+Hk_plane+Ms-Hk) + (np.cos(phi_inc_rad)*Bquad*h_parallel*np.cos(2*phi_p_rad))/(np.abs(Hext)+Hk_plane) + np.sin(phi_inc_rad)*Qv*h_parallel/(np.abs(Hext)+Hk_plane))
        del_theta_k =del_theta_k + hyst_resp
    return del_theta_k 

def qmoke_fit_emir(Hext, A0, A1, B2,Hk,phi_p):
    phi_p_rad = np.deg2rad(phi_p)
    #A0 + A1/(Hext-Hk) + B2*np.cos(2*phi_p_rad)/Hext
    #A0 + (-1)*np.sign(Hext)*A1/(np.abs(Hext)-Hk) + (-1)*np.sign(Hext)*B2*np.cos(2*phi_p_rad)/np.abs(Hext)
    return A0 + (-1)*np.sign(Hext)*A1/(np.abs(Hext)-Hk) + (-1)*np.sign(Hext)*B2*np.cos(2*phi_p_rad)/np.abs(Hext)

def find_min_SOT_Hext(filedate, filename,phi_p,data_order = '1LI',flip = False,use_fit = True, crit_ratio = None):
    theta = int(input('Input the estimated phase in degrees:'))
    from scipy.optimize import curve_fit
    """
    Determine the minimal applied external magnetic field to minimize quadratic contributions to the PMOKE first harmonic Kerr signal.
    The condition: The normalized signal (signal / (lim H -> infty)) must deviate less than 1/10 of the std deviation due to the noise from the obtained fit.

    The supplied measurement: PMOKE, record first harmonic signal of the balanced diode, H external field sweep, preferrably 0 -> -H_ext -> +H_ext -> 0

    This version is compatible with the TR moke database.
    
    Parameters
    ----------
    filedate : str
        Date of the measurement: e.g. '20250625'
    filename : str
        name of measurement: e.g. 'moke_15h01m34.963.nxs'
    phi_p : flaot
        Give the angle between polarization plane and applied external field in degrees. e.g. 0
    data_order : str, optional
        direct_fieldscan_AC, defines the order of the data when importing . The default is '1LI'.
    flip : bool, optional
        to flip the raw data, to take slope of lambda/2 (i.e. BD) into account

    Returns
    -------
        Plot of the fitted first harmonic signal and minimal external field which fulfills the condition.

    """
    theta_rad = theta*np.pi/180
    name = filedate + '/' + filename
    res = analysis_field_emir.direct_fieldscan_AC(name,data_order = '1LI')
    phi_p_rad = phi_p*np.pi/180.0
    H_POLAR = res[-1]
    minH = np.where(H_POLAR == min(H_POLAR))[0][0]
    maxH = np.where(H_POLAR == max(H_POLAR))[0][0]
    indixpH = [minH, maxH]
    indixpH = np.sort(indixpH)  # indices where we sweep from -H to +H
    
    #indixmH = [minH[1], maxH]
    #indixmH = np.sort(indixmH)  # indices where we sweep from -H to +H
    
    theta_K_POLAR = (res[4] * np.cos(theta_rad) + res[5] * np.sin(theta_rad))
    
    
    
    xp = H_POLAR[indixpH[0]:indixpH[1]]
    #xm = H_POLAR[indixmH[0]:indixmH[1]]
    yp = theta_K_POLAR[indixpH[0]:indixpH[1]]
    #ym = theta_K_POLAR[indixmH[0]:indixmH[1]]
    fig = plt.figure()
    
    ind = np.argsort(xp)    
    #print(len(ind),len(y),len(x))
    x_sort = xp[ind]
    y_sort = yp[ind]
    
    mask = (np.abs(x_sort) > 5)
    inv_mask = np.invert(mask)
    
    def func_qMOKE(x,A0,A,B,Hk,phi_p_rad):
        #return A/(x-Hk) + B*np.cos(2*phi_p_rad)/x
        return A0 + np.sign(x)*A/(np.abs(x)-Hk) + np.sign(x)*B*np.cos(2*phi_p_rad)/np.abs(x)
    fit_func = lambda x,A0, A, B, Hk: func_qMOKE(x,A0, A, B, Hk, phi_p_rad)
    
    res,cov = curve_fit(fit_func, x_sort[mask],y_sort[mask])#,bounds=([-1,-100,-1000,-1000], [1,1000, 1000, 1000]))
    A0_fit,A_fit, B_fit, Hk_fit = res
    y_fit = func_qMOKE(x_sort,A0_fit,A_fit,B_fit,Hk_fit,phi_p_rad)
    
    plt.grid()
    plt.title(name[0:-4])
    plt.xlabel('$H_{ext}^{corr}$')
    plt.ylabel('$\theta_K^{1\omega}$')
    h = (max(y_sort)-min(y_sort))/2
    plt.ylim([-h,h])
    if use_fit:
        def find_Hcrit(x,A_fit,B_fit,Hk_fit):
            theta_Bdl = np.sign(x)*A_fit/(np.abs(x)-Hk_fit)
            theta_Q = np.sign(x)*B_fit*np.cos(2*phi_p_rad)/np.abs(x)
            return np.abs(theta_Q/theta_Bdl)
        x0 = 50  # initial guess
        std_err = np.abs(np.std((y_sort[mask]-y_fit[mask])))
        print('std error',std_err)
        if crit_ratio == None:
            crit_ratio =std_err
            print('crit_ratio =', crit_ratio)
        
        min_func = lambda x, A_fit, B_fit, Hk_fit: np.abs(find_Hcrit(x, A_fit, B_fit, Hk_fit) - crit_ratio)

        res = minimize(min_func, x0, args=(A_fit, B_fit, Hk_fit))
        Hcrit = res.x
        #print('B_DL = ',theta_Bdl, 'Q = ', ratio, 'in micro rad')
        print('Hcrit',Hcrit )
    else:
        theta_DL = np.abs(y_fit[0])
        print(theta_DL)
        std_err = np.std((y_sort[mask]-y_fit[mask]))
        print('std error',std_err)
        critl = np.abs((y_fit- y_fit[0])/theta_DL) > std_err/10
        critl_index = min(np.where(critl)[0])
        neg_Hcrit = x_sort[critl_index]
        critr = np.abs((y_fit- y_fit[-1])/theta_DL) > std_err/10
        
        critr_index = max(np.where(critr)[0])
        pos_Hcrit = x_sort[critr_index]
        Hcrit = np.mean([pos_Hcrit,-neg_Hcrit])
    plt.vlines([-Hcrit,Hcrit], -h, h, color = 'blue', label = 'min $H_{ext}$ = %.1f mT' %Hcrit)
    plt.legend()
    ymin, ymax = plt.ylim()

    plt.fill_between(x_sort, ymin, ymax, where=((x_sort >= -Hcrit-0.1) & (x_sort <= Hcrit+0.1)), color='red', alpha=0.3)
    if flip:
        y_fit = (-1)*y_fit
        y_sort = (-1)*y_sort
    plt.plot(x_sort,y_fit)
    plt.scatter(x_sort,y_sort,color = 'k')
    plt.show()
    print('Returning A0_fit,A_fit, B_fit, Hk_fit')

    return np.array([A0_fit,A_fit, B_fit, Hk_fit])

def find_min_Hext_linpart(filedate, filename,phi_p,data_order = '1LI',flip = False):
    theta = int(input('Input the estimated phase in degrees:'))
    
    """
    Determine the minimal applied external magnetic field to minimize quadratic contributions to the PMOKE first harmonic Kerr signal.
    The condition: The normalized signal (signal / (lim H -> infty)) must deviate less than 1/10 of the std deviation due to the noise from the obtained fit.

    The supplied measurement: PMOKE, record first harmonic signal of the balanced diode, H external field sweep, preferrably 0 -> -H_ext -> +H_ext -> 0

    This version is compatible with the TR moke database.
    
    Parameters
    ----------
    filedate : str
        Date of the measurement: e.g. '20250625'
    filename : str
        name of measurement: e.g. 'moke_15h01m34.963.nxs'
    phi_p : flaot
        Give the angle between polarization plane and applied external field in degrees. e.g. 0
    data_order : str, optional
        direct_fieldscan_AC, defines the order of the data when importing . The default is '1LI'.
    flip : bool, optional
        to flip the raw data, to take slope of lambda/2 (i.e. BD) into account

    Returns
    -------
        Plot of the fitted first harmonic signal and minimal external field which fulfills the condition.

    """
    theta_rad = theta*np.pi/180
    name = filedate + '/' + filename
    res = analysis_field_emir.direct_fieldscan_AC(name,data_order = '1LI')
    phi_p_rad = phi_p*np.pi/180.0
    H_POLAR = res[-1]
    minH = np.where(H_POLAR == min(H_POLAR))[0][0]
    maxH = np.where(H_POLAR == max(H_POLAR))[0][0]
    indixpH = [minH, maxH]
    indixpH = np.sort(indixpH)  # indices where we sweep from -H to +H
    
    #indixmH = [minH[1], maxH]
    #indixmH = np.sort(indixmH)  # indices where we sweep from -H to +H
    
    theta_K_POLAR = (res[4] * np.cos(theta_rad) + res[5] * np.sin(theta_rad))
    
    
    
    xp = H_POLAR[indixpH[0]:indixpH[1]]
    #xm = H_POLAR[indixmH[0]:indixmH[1]]
    yp = theta_K_POLAR[indixpH[0]:indixpH[1]]
    #ym = theta_K_POLAR[indixmH[0]:indixmH[1]]
    fig = plt.figure()
    
    ind = np.argsort(xp)    
    #print(len(ind),len(y),len(x))
    x_sort = xp[ind]
    y_sort = yp[ind]
    
    mask = (np.abs(x_sort) > 5)

    
    def func_qMOKE(x,A0,A,B,Hk,phi_p_rad):
        #return A/(x-Hk) + B*np.cos(2*phi_p_rad)/x
        return A0 + np.sign(x)*A/(np.abs(x)-Hk) + np.sign(x)*B*np.cos(2*phi_p_rad)/np.abs(x)
    fit_func = lambda x,A0, A, B, Hk: func_qMOKE(x,A0, A, B, Hk, phi_p_rad)
    
    res,cov = curve_fit(fit_func, x_sort[mask],y_sort[mask])#,bounds=([-1,-100,-1000,-1000], [1,1000, 1000, 1000]))
    A0_fit,A_fit_full, B_fit, Hk_fit = res
    y_fit = func_qMOKE(x_sort,A0_fit,A_fit_full,B_fit,Hk_fit,phi_p_rad)
    std_err = np.std((y_sort[mask]-y_fit[mask]))
    print('std error',std_err)
    
    def shift_mask(Hmin):
        mask = (np.abs(x_sort) > Hmin)
    
        def func_qMOKE_lin(x, A0, A, Hk):
            return A0 + np.sign(x) * A / (np.abs(x) - Hk)
    
        fit_func_lin = lambda x, A0, A, Hk: func_qMOKE_lin(x, A0, A, Hk)
    
        try:
            res, _ = curve_fit(fit_func_lin, x_sort[mask], y_sort[mask])
            A0_fit, A_fit, Hk_fit_lin = res
            return A_fit,Hk_fit_lin
        except Exception:
            return np.nan,0  # Fail gracefully
    Hmin = np.arange(20,max(x_sort),step = 10)
    As = np.zeros(len(Hmin))
    Hks = np.copy(As)
    for i in range(len(As)):
        As[i] = shift_mask(Hmin[i])[0]
        Hks[i] = shift_mask(Hmin[i])[1]
    print(As,Hks)  
    lin_part_fit = np.abs(As/(Hmin-Hks))
    plt.plot(Hmin,lin_part_fit)
    plt.show()
    Hcrit = min(np.where(lin_part_fit < std_err))

    #bounds = [(5, 400)]
    #res = dual_annealing(min_func-std_err, bounds,maxiter = 3000,visit = 2,maxfun = 1e5)
    #print(res)

    
    plt.grid()
    plt.title(name[0:-4])
    plt.xlabel('$H_{ext}^{corr}$')
    plt.ylabel('$\theta_K^{1\omega}$')
    h = (max(y_sort)-min(y_sort))/2
    plt.ylim([-h,h])
    
    plt.vlines([-Hcrit,Hcrit], -h, h, color = 'blue', label = 'min $H_{ext}$ = %.1f mT' %Hcrit)
    plt.legend()
    ymin, ymax = plt.ylim()

    plt.fill_between(x_sort, ymin, ymax, where=((x_sort >= -Hcrit-0.1) & (x_sort <= Hcrit+0.1)), color='red', alpha=0.3)
    if flip:
        y_fit = (-1)*y_fit
        y_sort = (-1)*y_sort
    plt.plot(x_sort,y_fit)
    plt.scatter(x_sort,y_sort,color = 'k')
    plt.show()
    print('Returning A0_fit,A_fit, B_fit, Hk_fit')

     
