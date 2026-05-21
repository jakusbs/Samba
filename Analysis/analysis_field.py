# import all the standard libraries needed for import and calculations, plotting
import numpy as np
import matplotlib.pyplot as plt
#import nexus #commented off 18.08.25 (tobi)
import scipy.optimize as optimization
from scipy.optimize import minimize
from scipy import interpolate
import math
import h5py
import datetime
from datetime import timedelta
# read all data: compared to older scripts, this one imports all the different data channels at once. Should also be independent from other scripts (such as nexus, scan_lockin....)

def calibrate(umTicks, mVolts, plotting=True):
    """ find calibration constant for MOKE in mV/degree
        umTicks: list/array of ticks on micrometer screw (100 ticks are 4 deg)
        mVolts: list/array of signal on balanced detector, in mV
    """
    
    # convert list to array (if needed)
    umTicks = np.array(umTicks,dtype=float)
    mVolts = np.array(mVolts,dtype=float)
    
    # convert micrometer ticks to degrees
    umTicks /= 100.0/4.0
    umTicks *= 2.0  # HWP rotates double amount of the mechanical rotation
    
    # fit with straight line
    slope, offset = np.polyfit(umTicks, mVolts,1)
    #print('Linear fit slope = {:6.3f} mV/deg, offset = {:4.2f} mV'.format(slope,offset))
    
    if plotting:
        # plot result
        plt.figure(figsize=(8,6), linewidth=2)
        plt.tick_params(labelsize=14)
        plt.plot(umTicks, mVolts,'bo-') 
        # plot fit from above
        fitlabel='Fit with slope {:6.3f} mV/deg'.format(slope)          
        plt.plot(umTicks, umTicks*slope+offset,'r-', label=fitlabel)
        plt.title('MOKE calibration',fontsize=16)
        plt.xlabel('Angle (deg)', fontsize=14)
        plt.ylabel('MOKE signal (mV)', fontsize=14)
        plt.legend(loc='best')
        plt.grid(True)
    
    return slope

def data_load(filename, data_channel, scan=0): # similar to nexus.scandata, without plotting option
    """ opens file to read specific data channel
        filename: name of hdf5 or nexus file
        data_channel: string describing the data to read, e.g. 'data_01'
        scan (optional): number of specific scan to read
        scan=0 reads and averages all scans (default)
    """

    with h5py.File(filename,'r') as f: # handles file closing also in case of error
    
        scans = f.keys()   #returns a list of all the available keys in the dictionary.
        
        numscans=len(scans)
        
        if numscans > 0: # yes there is data
            if scan == 0:    
                first_scan = True
                # caution: reusing scan, now a string!!!
                for scan in scans:
                    key=scan+'/scan_data/'+data_channel
                    #print(key) # for debug only
                    datanew = np.array(f[key])
                    if first_scan:
                        data = np.zeros_like(datanew)
                        first_scan = False
                    data = data + datanew
                data = data / numscans
            else: # read only specified scan
                # problem: indexing not supported for scans
                # try with casting into a list
                if (scan <= numscans) and (scan > 0):
                    key=list(scans)[scan-1]+'/scan_data/'+data_channel
                    #print(key) # for debug only
                    data = np.array(f[key])
                else:
                    data = np.zeros(1) # default value
                    print('WARNING: scandata: only {:d} scans in file {:s}'.format(numscans,filename))
            
        else: # no scans found
            data = np.zeros(1) # default value if there are no scans
            print('WARNING: scandata: no scans in file {:s}'.format(filename))
    
    # analyse data structure
    if (data.shape[0] == 1 and data.ndim == 2): # dimension 1 has only one element
        # flatten (reshape) array to 1 dimension
        data = np.reshape(data,-1)
    # script that removes spikes...
    if data.ndim == 1:
        g = np.gradient(data)
        limit = np.mean(np.abs(g))
        for i in range(1,len(data)-1): 
            if np.abs(g[i-1]) >= 10*limit and np.abs(g[i+1])>= 10*limit  and np.sign(g[i-1]) == - np.sign(g[i+1]):
                data[i] = np.nan
    return data



def data_calculation(logfilenameShort,median=False,normalization=False, ch_x='data_12',ch_pol='data_09',ch_var='data_01',ignorLines = [],logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/'):
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    firstscan = True
    firstneg = True
    firstpos = True
    lineTotal = -2
    lineCounter = 0
    
    for line in f:
        # loop over all lines in logfile
        
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        lineElements = line.split("\t")      
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        if firstscan:
            firstscan = False
            x = data_load(datapath + filename, ch_x)
            var_neg = np.zeros_like(x)
            var_pos = np.zeros_like(x)
            n_pos = 0
            n_neg = 0

        # read relay position or magnet...
        if ch_pol == 'None':
            p = pol
            if p % 2 == 0:
                p = -1
            p = -1*p # changed on the 15.11.2020; now it has the same directionality as using the relay position. Before that, using ch_pol lead to a sign change...
        else:
            p = data_load(datapath + filename, ch_pol)[0]
        
        var = data_load(datapath + filename, ch_var)
        # replace all NaN values with the interpolation of the neighbour values. Might cause problems if too many NaNs are given or if data points are far away and not linear.
        nans, z = nan_helper(var)
        var[nans] = np.interp(z(nans), z(~nans), var[~nans])
        
        if p <= 0.0: # p=2 (or p<0) means negative field, p even means relay on; 15.11.2020: Notice how we define the current along the -x direction in this case... This means the magnetic moments in Pt are parallel to the y-direction.
            if firstneg:
                firstneg=False
                var_neg = var
            else:
                var_neg = np.vstack((var_neg,var))   
            n_neg   += 1
        else: # p=1 means relay on or positive field
            if firstpos:
                firstpos=False
                var_pos = var
            else:
                var_pos = np.vstack((var_pos,var))
            n_pos   += 1
        #if boolian_percentile:
			
    f.close()
    #print(n_pos)
    #print(n_neg)
    # calculate median
    if median==True:
        res_pos = np.median(var_pos,axis=0)
        res_neg = np.median(var_neg,axis=0)
    else: # use the mean if the median is not used...
        res_pos = np.mean(var_pos,axis=0)
        res_neg = np.mean(var_neg,axis=0)
    
    # calculate half the difference
    difference = (res_pos-res_neg)/2
    # calculate average, i.e. any effects that do not depend on reversing current (or field)
    summation = (res_pos+res_neg)/2
    res_std = (np.std(var_pos,axis=0)+np.std(var_neg,axis=0))/2   
    # result = [x,difference,summation,res_std] #old one, changed on 2020/07/15
    result = [x,difference,summation,res_std,res_pos,res_neg]
    return result


def intensity_mean(logfilenameShort,ch_var='data_10',ignorLines = [],setup=1):
    #calculates the mean intensity within one scan and writes an array with scans, which have an intensity lower than a given percentage value (used to delete scans that were defocused...)


    logfilepath = '../Data/Scanlists_S1/'
    datapathprefix = '../Data/Data_S1/'
    if setup==2:
        logfilepath = '../Data/Scanlists_S2/'
        datapathprefix = '../Data/Data_S2/'
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')


    
    firstscan =True
    lineTotal = -2
    lineCounter = 0
    
    for line in f:
        # loop over all lines in logfile
        
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        # parse line consisting of file name and polarization value (tab separated)
        lineElements = line.split("\t")
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        if firstscan:
            firstscan = False
            var_all = data_load(datapath + filename, ch_var)
        else:
            var = data_load(datapath + filename, ch_var)
            var_all = np.vstack((var_all,var))
        #if boolian_percentile:
			
    f.close()

    I = np.mean(var_all,axis=1)
    return I


def plotter_multvar(x,y,err=0.0,tit='random',xlab=r'H$_{ext}$ [$mT$]',ylab=r'Kerr rotation [$mV$]',linestyle=['-bo','-ko'],small=14.0,large=16.0,var_leg=['','']):
    # this programm plots all the graphs for the lock-in measurements where x and y are arrays of the kind x = [x1,x2,...] and y = [y1,y2,...]
    plt.figure(figsize=(12,9))
    if err == 0.0:
        for i in range(0,len(x)): 
            plt.plot(x[i],y[i],linestyle[i])
    else:
        for i in range(0,len(x)):
            plt.errorbar(x[i],y[i],err[i],fmt=linestyle[i])
    plt.xlabel(xlab,fontsize=small)
    plt.ylabel(ylab,fontsize=small)
    if tit=='random':
        plt.title(' ',fontsize=large)
    else:
        plt.title(tit,fontsize=large)
    plt.grid('on')
    plt.legend(var_leg,loc='best')
    plt.show()
    return

def load_all_data(logfilenameShort,ignorLines=[],theta=0.0,ch_pol='data_05',ch_x='data_12',two_lockins=False,titletext='$j$',xlab=r'H$_{ext}$',ign_int=True,interval=0.9):
    Imean = intensity_mean(logfilenameShort,ch_var='data_06',ignorLines=ignorLines)
    if two_lockins==True:
        Imean = intensity_mean(logfilenameShort,ch_var='data_10',ignorLines=ignorLines)
    if ign_int:
        index = ignore_based_Intensity(Imean,interval)
        ignore = np.append(ignorLines,index[0])
    else:
        ignore = ignorLines
        index = []
    x1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignore)
    x2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignore)
    y1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignore)
    y2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignore)
    I2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignore)
    I = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignore)
    measured_field = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var=ch_x,ignorLines = ignore)
    if two_lockins==True:
        l2x1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignore)
        l2x2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignore)
        l2y1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignore)
        l2y2 =data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_09',ignorLines = ignore)
        I2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_10',ignorLines = ignore)
        I = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_11',ignorLines = ignore)
    # reload to confirm the data...
    Imean = intensity_mean(logfilenameShort,ch_var='data_06',ignorLines = ignore)
    if two_lockins==True:
        Imean = intensity_mean(logfilenameShort,ch_var='data_10',ignorLines = ignore)
    x = measured_field[2]
    # theta = np.linspace(0,np.pi/2,360)
    #r = x1[1]* np.cos(theta)+y1[1]*np.sin(theta)
    #r_90 = -x1[1]* np.sin(theta)+y1[1]*np.cos(theta)
    plotter_multvar([x,x],[x1[1],y1[1]],tit='first harmonic signal changing sign with '+titletext,xlab=xlab,var_leg=['x','y'])
    plotter_multvar([x,x],[x1[2],y1[2]],tit='first harmonic signal not depending on sign of '+titletext,xlab=xlab,var_leg=['x','y'])
    plotter_multvar([x,x],[x2[1],y2[1]],tit='second harmonic signal changing sign with '+titletext,xlab=xlab,var_leg=['x','y'])
    plotter_multvar([x,x],[x2[2],y2[2]],tit='second harmonic signal not depending on sign of '+titletext,xlab=xlab,var_leg=['x','y'])
    if two_lockins==True:
            plotter_multvar([x,x],[l2x1[1],l2y1[1]],tit='2nd lockin: first harmonic signal changing sign with '+titletext,xlab=xlab,var_leg=['x','y'])
            plotter_multvar([x,x],[l2x1[2],l2y1[2]],tit='2nd lockin: first harmonic signal not depending on sign of '+titletext,xlab=xlab,var_leg=['x','y'])
            plotter_multvar([x,x],[l2x2[1],l2y2[1]],tit='2nd lockin: second harmonic signal changing sign with '+titletext,xlab=xlab,var_leg=['x','y'])
            plotter_multvar([x,x],[l2x2[2],l2y2[2]],tit='2nd lockin: second harmonic signal not depending on sign of '+titletext,xlab=xlab,var_leg=['x','y'])
    plotter_multvar([x],[I2[2]],tit='intensity diode signal [mV]',xlab=xlab,var_leg=['reflectivity'])
    plotter_multvar([x],[I[2]],tit='balanced diode [mV]',xlab=xlab,var_leg=['static Kerr rotation'])
    if two_lockins==True:
            plotter_multvar([x],[I2[1]],tit='difference intensity diode signal [mV]',xlab=xlab,var_leg=['x','y'])
            plotter_multvar([x],[I[1]],tit='difference balanced diode [mV]',xlab=xlab,var_leg=['x','y'])
    plotter_multvar([np.linspace(0,len(Imean),len(Imean))],[Imean],xlab='number of scans',ylab='intensity [mV]')
    all_variables = [x1,x2,y1,y2,I2,I,measured_field,Imean,ignore,index]
    if two_lockins==True:
        all_variables = [x1,x2,y1,y2,l2x1,l2x2,l2y1,l2y2,I2,I,measured_field,Imean,ignore,index]
    return all_variables


def nan_helper(y):
    """Helper to handle indices and logical indices of NaNs.
    Input:
    - y, 1d numpy array with possible NaNs
    Output:
    - nans, logical indices of NaNs
    - index, a function, with signature indices= index(logical_indices),
    to convert logical indices of NaNs to 'equivalent' indices
    Example:
    >>> # linear interpolation of NaNs
    >>> nans, x= nan_helper(y)
    >>> y[nans]= np.interp(x(nans), x(~nans), y[~nans])"""
    return np.isnan(y), lambda z: z.nonzero()[0]

def larmor(B,g = -2.00231930436182):
    # calculates the Larmor frequency for a magnetic field B (in T)
    mub = 9.274009994*1e-24
    hbar = 1.054571800*1e-34
    omega_L = np.zeros_like(B)
    omega_L = g*mub*B/hbar
    return omega_L

def larmor_Kerr_angle(B,tau,g=-2.00231930436182):
    omega_L = larmor(B,g)
    Kerr_angle = omega_L*tau/((omega_L*tau)**2+1)
    return Kerr_angle

def Hanle_Kerr_angle(B,tau,g=-2.00231930436182):
    omega_L = larmor(B,g)
    Kerr_angle = 1/((omega_L*tau)**2+1)
    return Kerr_angle

def ignore_based_Intensity(I,interval=0.9):
    # script takes the intensity I and uses the first entry to decide if the sample is still focused and generates index which includes all nonfocused scans.
    index = []
    ind2 = []
    for i in range(1,len(I)):
        if I[0]*interval>I[i]:
            index = np.append(index,i)
        else:
            ind2 = np.append(ind2,i)
            # print(i)
    index = [index,ind2]
    return index

def linescan_SNE(logfilenameShort,ignorLines=[],ch_pol='data_09',ch_x='actuator_1_1',titletext='$j$',xlab=r'H$_{ext}$',setup=1,std=False,field=False):
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    if setup==2:
        path = '../Data/Scanlists_S2/'
        dpprefix = '../Data/Data_S2/'
    I2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    I = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2x2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2y2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2x1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_10',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2y1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_11',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x = I[0]
    if field:
        H = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_12',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        result = x,I,I2,x1,y1,x2,y2,l2x2,l2y2,l2x1,l2y1,H
    else:
        result = x,I,I2,x1,y1,x2,y2,l2x2,l2y2,l2x1,l2y1
    # plotting
    return result



def linescan_SNE_2(logfilenameShort,ignorLines=[],ch_pol='data_09',ch_x='actuator_1_1',titletext='$j$',xlab=r'H$_{ext}$'):
    path = '../Data/Scanlists_S2/'
    dpprefix = '../Data/Data_S2/'
    I2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    I = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y1 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2x2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2y2 = data_calculation(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x = I[0]
    # plotting
    return x,I,I2,x1,y1,x2,y2,l2x2,l2y2

def timetrace(logfilenameShort,ch_var='data_13',ignorLines = [],setup=1):
    # calculates the laser intensity over time, appending all the measurements from data_13 (photo diode behind the beam splitter) to show if we have fluctuations...
    logfilepath = '../Data/Scanlists_S1/'
    datapathprefix = '../Data/Data_S1/'
    if setup==2:
        logfilepath = '../Data/Scanlists_S2/'
        datapathprefix = '../Data/Data_S2/'
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')

    firstscan =True
    lineTotal = -2
    lineCounter = 0
    
    for line in f:
        # loop over all lines in logfile
        
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        # parse line consisting of file name and polarization value (tab separated)
        lineElements = line.split("\t")
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line

        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        if firstscan:
            firstscan = False
            var_all = data_load(datapath + filename, ch_var)
            t = str(filename[5:7]+':'+filename[8:10]+':'+filename[11:17])
        else:
            var = data_load(datapath + filename, ch_var)
            var_all = np.append(var_all,var)
            t = np.append(t,str(filename[5:7]+':'+filename[8:10]+':'+filename[11:17]))
        #if boolian_percentile
    f.close()
    datetimeFormat = '%H:%M:%S.%f'
    diff = np.zeros(len(t)-1)
    summ=0.0
    for i in range(0,len(t)-1):
        diff[i] = (datetime.datetime.strptime(t[i+1], datetimeFormat)-datetime.datetime.strptime(t[i], datetimeFormat)).seconds
        summ +=diff[i]

    return [var_all,diff,summ]







# 28.01.2022 EK adapted intensity_mean; linescan_SNE and data_calculation for evaluation of the old data (2016 and 2017) for the Stamm et al. PRL (MOKE in Pt and W)


def intensity_mean_old(logfilenameShort,ch_var='data_06',ignorLines = [],setup=1):
    #calculates the mean intensity within one scan and writes an array with scans, which have an intensity lower than a given percentage value (used to delete scans that were defocused...)


    logfilepath = '../Data/Scanlists_S1/old_data_check/'
    datapathprefix = '../Data/Data_S1/old_data_check/'
    if setup==2:
        logfilepath = '../Data/Scanlists_S2/'
        datapathprefix = '../Data/Data_S2/'
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')


    
    firstscan =True
    lineTotal = -2
    lineCounter = 0
    
    for line in f:
        # loop over all lines in logfile
        
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        # parse line consisting of file name and polarization value (tab separated)
        lineElements = line.split("\t")
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        if firstscan:
            firstscan = False
            var_all = data_load(datapath + filename, ch_var)
        else:
            var = data_load(datapath + filename, ch_var)
            var_all = np.vstack((var_all,var))
        #if boolian_percentile:
			
    f.close()

    I = np.mean(var_all,axis=1)
    return I



def linescan_SNE_old(logfilenameShort,ignorLines=[],ch_pol='data_05',ch_x='actuator_1_1',titletext='$j$',xlab=r'H$_{ext}$',setup=1,std=False,field=False):
    path = '../Data/Scanlists_S1/old_data_check/'
    dpprefix = '../Data/Data_S1/old_data_check/'
    if setup==2:
        path = '../Data/Scanlists_S2/'
        dpprefix = '../Data/Data_S2/'

    x1 = data_calculation_old(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y1 = data_calculation_old(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x2 = data_calculation_old(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y2 = data_calculation_old(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    I = data_calculation_old(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x = x1[0]
    result = [x,x1,y1,x2,y2,I]
    return result


def data_calculation_old(logfilenameShort,median=False,normalization=False, ch_x='data_06',ch_pol='data_05',ch_var='data_01',ignorLines = [],logfilepath = '../Data/Scanlists_S1/old_data_check/', datapathprefix = '../Data/Data_S1/old_data_check/'):
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    firstscan = True
    firstneg = True
    firstpos = True
    lineTotal = -2
    lineCounter = 0
    
    for line in f:
        # loop over all lines in logfile
        
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        lineElements = line.split("\t")      
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        if firstscan:
            firstscan = False
            x = data_load(datapath + filename, ch_x)
            var_neg = np.zeros_like(x)
            var_pos = np.zeros_like(x)
            n_pos = 0
            n_neg = 0

        # read relay position or magnet...
        if ch_pol == 'None':
            p = pol
            if p % 2 == 0:
                p = -1
            p = -1*p # changed on the 15.11.2020; now it has the same directionality as using the relay position. Before that, using ch_pol lead to a sign change...
        else:
            p = data_load(datapath + filename, ch_pol)[0]
        
        var = data_load(datapath + filename, ch_var)
        # replace all NaN values with the interpolation of the neighbour values. Might cause problems if too many NaNs are given or if data points are far away and not linear.
        nans, z = nan_helper(var)
        var[nans] = np.interp(z(nans), z(~nans), var[~nans])
        
        if p <= 0.0: # p=2 (or p<0) means negative field, p even means relay on; 15.11.2020: Notice how we define the current along the -x direction in this case... This means the magnetic moments in Pt are parallel to the y-direction.
            if firstneg:
                firstneg=False
                var_neg = var
            else:
                var_neg = np.vstack((var_neg,var))   
            n_neg   += 1
        else: # p=1 means relay on or positive field
            if firstpos:
                firstpos=False
                var_pos = var
            else:
                var_pos = np.vstack((var_pos,var))
            n_pos   += 1
        #if boolian_percentile:
			
    f.close()
    #print(n_pos)
    #print(n_neg)
    # calculate median
    if median==True:
        res_pos = np.median(var_pos,axis=0)
        res_neg = np.median(var_neg,axis=0)
    else: # use the mean if the median is not used...
        res_pos = np.mean(var_pos,axis=0)
        res_neg = np.mean(var_neg,axis=0)
    
    # calculate half the difference
    difference = (res_pos-res_neg)/2
    # calculate average, i.e. any effects that do not depend on reversing current (or field)
    summation = (res_pos+res_neg)/2
    res_std = (np.std(var_pos,axis=0)+np.std(var_neg,axis=0))/2
    # result = [x,difference,summation,res_std] #old one, changed on 2020/07/15
    result = [x,difference,summation,res_std,res_pos,res_neg]
    return result




# 30.01.2022 EK adapted linescan_SNE and data_calculation for SOT measurements (this was working fine anyways)
# 03.05.2023 EK updated error calculation


def intensity_mean_SOT(logfilenameShort,ch_var='data_04',ignorLines = [],setup=1):
    #calculates the mean intensity within one scan and writes an array with scans, which have an intensity lower than a given percentage value (used to delete scans that were defocused...)


    logfilepath = '../Data/Scanlists_S1/'
    datapathprefix = '../Data/Data_S1/'
    if setup==2:
        logfilepath = '../Data/Scanlists_S2/'
        datapathprefix = '../Data/Data_S2/'
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')


    
    firstscan =True
    lineTotal = -2
    lineCounter = 0
    
    for line in f:
        # loop over all lines in logfile
        
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        # parse line consisting of file name and polarization value (tab separated)
        lineElements = line.split("\t")
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        if firstscan:
            firstscan = False
            var_all = data_load(datapath + filename, ch_var)
        else:
            var = data_load(datapath + filename, ch_var)
            var_all = np.vstack((var_all,var))
        #if boolian_percentile:
			
    f.close()

    I = np.mean(var_all,axis=1)
    return I,var_all

    
def plot_scans_tobi(logfilenameShort,ignorLines,data_ch = 'data_02', only_focused = True, shift = True, Icutoff = 0.9,setup = 1,setup_spec = 'ZI'):
    if data_ch != 'data_02':
        print('Overwriting arguments "only_focused" and "shift" to False!')
        shift = False
        only_focused = False
    relay_offset = False
    if data_ch == 'data_04' or data_ch == 'data_05':
        print('Offseting data of diff relay pos')
        relay_offset = True
    
    DATA =  linescan_SNE_SOT(logfilenameShort,ch_pol='None',ignorLines=[],ch_x='actuator_1_1',setup=setup, field = True, setup_spec = setup_spec)
    I, var_all = intensity_mean_SOT(logfilenameShort,ch_var=data_ch, ignorLines=[], setup=1)
    #I, var_all_1 = analysis_field.intensity_mean_SOT(logfilenameShort,ch_var='data_09', ignorLines=list(range(0,0))+list(range(0,0)), setup=1)
    fig,[ax1,ax2] = plt.subplots(figsize=(15,12),nrows = 2,gridspec_kw={'height_ratios': [1,3]})
    scans = range(len(var_all))
    #phase_off=np.arctan(var_all_1/var_all)*180/np.pi
    def normalize_to_zero(x,y,dwidth = 10):
        res1= np.polyfit(x[1:dwidth], y[1:dwidth], 0)
        res2 = np.polyfit(x[-dwidth:-1], y[-dwidth:-1], 0)
        mean_shift = (res1[0] + res2[0]) / 2
        return mean_shift
    #var_all=var_all#*sln
    #print(len(var_all))
    Inew = np.zeros(len(I))
    shifts = np.zeros(len(I))
    if shift:
        for i in scans:
            shifts[i] = normalize_to_zero(DATA[0], var_all[i])
            Inew[i] = np.mean(var_all[i]-shifts[i])
    
    ax1.plot(I*1e3,label = 'raw')
    if shift:
        ax1.plot(Inew*1e3,label = 'shifted')
        Imax = max(Inew)
        focused = np.where(Inew/Imax > Icutoff)[0]
        remove_lines = np.where(Inew/Imax <= Icutoff)[0]
    else:
        Imax = max(I)
        remove_lines = ignorLines
         
   
    
    plt.suptitle(logfilenameShort)
    ax1.scatter(remove_lines,np.ones(len(remove_lines))*Imax*1e3,color = 'r', marker = 'x',label = 'remove')
    ax1.set_xticks(np.arange(len(I)))
    ax1.legend()
    ax1.grid()
    #plt.ylim(0, 200)
    ax1.set_xlabel('number of scans')
    ax1.set_ylabel('mean intensity [mV]')
    
    
    #r2w_normalized=var_all_1/var_all
    
    #plt.plot(DATA[0], var_all.T)
    #plt.show()
    
    #colors = plt.cm.Blues(np.linspace(0, 1, len(var_all)))
    colors = plt.cm.copper(np.linspace(0, 1, len(var_all)))
    #plt.figure(figsize=(15,12))
    if only_focused:
        scans = focused
    else:
        scans = range(len(var_all))

    if relay_offset:
        relay_pos = get_relaypos_scanlist(logfilenameShort,ignorLines = []) - np.ones(len(var_all))
    else:
        relay_pos = np.zeros(len(var_all))
    #print(len(var_all),len(shifts),len(relay_pos))
    max_sig = max(var_all[0])
    
    for i in scans:
        ax2.plot(DATA[0], var_all[i]-shifts[i] + relay_pos[i]*2*max_sig,"x-",color=colors[i], label = i)
    
        #plt.plot(DATA[0], r2w_normalized[i],"x-",color=colors[i])
    #plt.xlim(-8.2,-7)
    #plt.ylim(0.092, 0.094)
    if relay_offset:
        ax2.axhline(y = 2*max_sig, color = 'r', linestyle = '-')
    ax2.axhline(y = 0.0, color = 'r', linestyle = '-')
    ax2.legend()
    ax2.grid()
    plt.show()
    
    return remove_lines

def find_phase(x,x1,y1,edges,ch,do_plot = False):
    
    mask = np.invert((x < edges[1])*(x > edges[0]))
    #print(mask)
    mask = np.invert(mask)
    #mask = np.ones(len(x),dtype = bool)
    """
    def fit(theta, x, x1, y1, mask):
        theta_rad = theta*np.pi/180.0
        imag = -x1*np.sin(theta_rad) + y1*np.cos(theta_rad)
        res = np.abs(np.polyfit(x[mask], imag[mask], 0)[0])

        return res
    """
    def min_imag(theta,x,x1,y1,mask):
        theta_rad = theta*np.pi/180.0
        imag = -x1*np.sin(theta_rad) + y1*np.cos(theta_rad)
        res = np.std(imag[mask])

        return res
    theta0 = 0
    #imag_neg = -x1_neg*np.sin(theta_rad2) + y1_neg*np.cos(theta_rad2)
    result = minimize(min_imag, theta0, args=(x, x1, y1, mask), method='Nelder-Mead')

    # Optimal theta in degrees
    theta = result.x[0]
    theta_rad = theta*np.pi/180.0
    imag = -x1*np.sin(theta_rad) + y1*np.cos(theta_rad)
    if do_plot:
        plt.scatter(x[mask],imag[mask],label = 'min imag: pos %s' %ch)
    
    
    return theta
    
def get_relaypos_scanlist(logfilenameShort,ignorLines = [],logfilepath = '../Data/Scanlists_S1/'):
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    

    firstpos = True
    lineTotal = -2
    lineCounter = 0
    relay = []
    for line in f:
        # loop over all lines in logfile
        #print(line)
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        lineElements = line.split("\t")      
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = int(lineElements[-1]) # last element (may be index 1 or 2)
        relay.append(pol)
    return relay

def data_calculation_SOT(logfilenameShort,median=False,normalization=False, ch_x='actuator_1_1',ch_pol='data_12',ch_var='data_01',ignorLines = [],logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/'):
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    firstscan = True
    firstneg = True
    firstpos = True
    lineTotal = -2
    lineCounter = 0
    relay = []
    for line in f:
        # loop over all lines in logfile
        #print(line)
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        lineElements = line.split("\t")      
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        relay.append(pol)
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        
        if firstscan:
            
            firstscan = False
            x = data_load(datapath + filename, ch_x)
            var_neg = np.zeros_like(x)
            var_pos = np.zeros_like(x)
            n_pos = 0
            n_neg = 0

        # read relay position or magnet...
        if ch_pol == 'None':
            p = pol
            if p % 2 == 0:
                p = -1
            #p = -1*p # changed on the 15.11.2020; now it has the same directionality as using the relay position. Before that, using ch_pol lead to a sign change...
        else:
            p = data_load(datapath + filename, ch_pol)[0]
        #p = data_load(datapath + filename, ch_pol)[0]
        #print('pol',p)
        var = data_load(datapath + filename, ch_var)
        # replace all NaN values with the interpolation of the neighbour values. Might cause problems if too many NaNs are given or if data points are far away and not linear.
        nans, z = nan_helper(var)
        
        var[nans] = np.interp(z(nans), z(~nans), var[~nans])
        
        if p <= 0.0: # p=2 (or p<0) means negative field, p even means relay on; 15.11.2020: Notice how we define the current along the -x direction in this case... This means the magnetic moments in Pt are parallel to the y-direction.
            if firstneg:
                firstneg=False
                var_neg = var
            else:
                var_neg = np.vstack((var_neg,var))   
            n_neg   += 1
        else: # p=1 means relay on or positive field
            if firstpos:
                firstpos=False
                var_pos = var
            else:
                var_pos = np.vstack((var_pos,var))
            n_pos   += 1
        #if boolian_percentile:
			
    f.close()
    #print(var_pos)
    #print(n_neg)
    # calculate median
    if median==True:
        res_pos = np.median(var_pos,axis=0)
        res_neg = np.median(var_neg,axis=0)
    else: # use the mean if the median is not used...
        res_pos = np.mean(var_pos,axis=0)
        res_neg = np.mean(var_neg,axis=0)

    #Normalize using the reflectivity
    #if normalization:
        

    
    # calculate half the difference
    difference = (res_pos-res_neg)/2
    # calculate average, i.e. any effects that do not depend on reversing current (or field)
    summation = (res_pos+res_neg)/2
    #res_std = (np.std(var_pos,axis=0)+np.std(var_neg,axis=0))/2 ############## Old way of error calc (pre 03.05.2023)
    
    res_std=(np.sqrt((np.std(var_pos,axis=0))**2+(np.std(var_neg,axis=0))**2))/2  ########### mean of standard deviation for negative and positive field measurements 
                                                                                  ########### 4*res_std^2=res_pos_std^2 + res_neg_std^2 
    

    result = [x,difference,summation,res_std,res_pos,res_neg,len(var_pos)]
    #print(len(res_pos))
    #print(len(var_pos))
    return result

def data_calculation_SOT_Tobi(scanlist,res,R_ch = 'averagein2value',LIAs = ['zi','zi2','srlockin','srlockin2'],median=False,normalization=False,ch_pol='None',ch_var='data_01', ch_x=None,ignorLines = [],logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/'):
    """
    ONLY works with dict-type data and not list (old).
    12.05.26 NOT YET WORKING!! I WANT TO combine x and y of harmonics already here....
    Parameters
    ----------
    scanlist : TYPE
        DESCRIPTION.
    dict_name : TYPE
        DESCRIPTION.
    median : TYPE, optional
        DESCRIPTION. The default is False.
    normalization : TYPE, optional
        DESCRIPTION. The default is False.
    ch_x : TYPE, optional
        DESCRIPTION. The default is 'actuator_1_1'.
    ignorLines : TYPE, optional
        DESCRIPTION. The default is [].
    logfilepath : TYPE, optional
        DESCRIPTION. The default is '../Data/Scanlists_S1/'.
    datapathprefix : TYPE, optional
        DESCRIPTION. The default is '../Data/Data_S1/'.

    Returns
    -------
    result : TYPE
        DESCRIPTION.

    """
    
    #IDEA: Open file and iterate over keys, build up dict of all channels (real/imag), ONLY for LIA data, get_edges -> get_theta and then use a phase-array to get sum/diff
    # NOTE 12.11: First open lines in f, then extract all data file for file according to keys
    # generate the logfilename
    logfilename = logfilepath + scanlist
    # open data  
    
    lineTotal = -2
    lineCounter = 0
    relay = []
    raw_data = {}
    firstfirstscan = True
    for key in res.keys():
        name = res[key]
        
                    
        #print(name,key)
        namelements = name.split("/")
        #print(namelements)
        if len(namelements) < 2:
            break
        if 'actuator' in key:
            while ch_x == None:
                if 'smaract' in name and 'actuator' in key:
                    ch_x=key
                    print('ch_x',key)
                    dict_name = namelements[-2] + namelements[-1]
                    print(namelements[-1])
                    if '1' in namelements[-1]:
                        dict_name = 'x'
                    elif '2' in namelements[-1]:
                        dict_name = 'y'
                    actuator = dict_name
        else:
            dict_name = namelements[-2] + namelements[-1]
            print('Iterating over all scans for',key,dict_name)
        ch_var = key
        firstscan = True
        firstneg = True
        firstpos = True
        f = open(logfilename,'r')
        for line in f:
            # loop over all lines in logfile
            #print(line)
            if lineCounter == lineTotal: # read only up to lineTotal files
                break 
            
            lineCounter += 1
            
            if lineCounter in ignorLines: # do not read (i.e. ignore) this file
                #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
                continue
            
            lineElements = line.split("\t")      
            
            filename = lineElements[0].split('/')[-1] # last part of filename
            filename = filename.strip('\n') # strip newline character at end of line
            
            field = float(lineElements[1]) # if present, is next after filename
            pol = float(lineElements[-1]) # last element (may be index 1 or 2)
            relay.append(pol)
            # construct datapath from prefix and date directory
            datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
            
            # init if first scan is read  
            if firstscan:
                if firstfirstscan:
                    x = data_load(datapath + filename, ch_x)
                    firstfirstscan = False
                firstscan = False
                
                var_neg = np.zeros_like(x)
                var_pos = np.zeros_like(x)
                n_pos = 0
                n_neg = 0
            
    
            # read relay position or magnet...
            if ch_pol == 'None':
                p = pol
                if p % 2 == 0:
                    p = -1
                #p = -1*p # changed on the 15.11.2020; now it has the same directionality as using the relay position. Before that, using ch_pol lead to a sign change...
            else:
                p = data_load(datapath + filename, ch_pol)[0]
            #p = data_load(datapath + filename, ch_pol)[0]
            print('ch_var',ch_var)
            var = data_load(datapath + filename, ch_var)
            # replace all NaN values with the interpolation of the neighbour values. Might cause problems if too many NaNs are given or if data points are far away and not linear.
            nans, z = nan_helper(var)
            
            var[nans] = np.interp(z(nans), z(~nans), var[~nans])
            
            if p <= 0.0: # p=2 (or p<0) means negative field, p even means relay on; 15.11.2020: Notice how we define the current along the -x direction in this case... This means the magnetic moments in Pt are parallel to the y-direction.
                if firstneg:
                    firstneg=False
                    var_neg = var
                else:
                    var_neg = np.vstack((var_neg,var))   
                n_neg   += 1
            else: # p=1 means relay on or positive field
                if firstpos:
                    firstpos=False
                    var_pos = var
                else:
                    var_pos = np.vstack((var_pos,var))
                n_pos   += 1
            #if boolian_percentile:
        raw_data[dict_name] = [var_pos, var_neg]	
        f.close()
        
    #find edges first to get phase:
    if R_ch in raw_data.keys():
        R_pos = np.mean(raw_data[R_ch][0],axis = 0)
        R_neg = np.mean(raw_data[R_ch][1],axis = 0)
    else:
        raise Exception('Reflectivity channel not recorded, change R_ch !')
    x_pos = np.mean(raw_data[actuator][0],axis = 0)    
    x_neg = np.mean(raw_data[actuator][1],axis = 0)
    edges_pos,width_pos = find_edges_width(x_pos,R_pos)
    edges_neg,width_neg = find_edges_width(x_neg,R_neg)
    phases = {}
    harmonics = ['1', '2', '3']
    for LI in LIAs:
        for harm in harmonics:
            if LI+'x' + harm in raw_data.keys():
                print('LIA ', LI, harm,' harmonic')
                phi_pos = np.deg2rad(find_phase(x_pos,np.mean(raw_data[LI + 'x' + harm][0],axis = 0),np.mean(raw_data[LI + 'y' + harm][0],axis = 0),edges_pos,'pos'))
                phi_neg = np.deg2rad(find_phase(x_pos,np.mean(raw_data[LI + 'x' + harm][1],axis = 0),np.mean(raw_data[LI + 'y' + harm][1],axis = 0),edges_pos,'neg'))
                print(np.rad2deg(phi_pos),np.rad2deg(phi_neg))
                #d1_pos = np.sqrt(raw_data[LI + 'x1'][0]**2 + raw_data[LI + 'y1'][0]**2) * np.sign(np.rad2deg(np.arctan(raw_data[LI + 'y1'][0]/raw_data[LI + 'x1'][0])))
                #d1_neg = np.sqrt(raw_data[LI + 'x1'][1]**2 + raw_data[LI + 'y1'][1]**2) * np.sign(raw_data[LI + 'x1'][1])
                d1_pos = (raw_data[LI + 'x' + harm][0]*np.cos(phi_pos)+raw_data[LI + 'y' + harm][0]*np.sin(phi_pos))
                d1_neg= (raw_data[LI + 'x' + harm][1]*np.cos(phi_neg)+raw_data[LI + 'y' + harm][1]*np.sin(phi_neg))
                phases[LI + '_' + harm] = [phi_pos,phi_neg]
                plt.show()
                
    for keys in raw_data.keys():
        if median==True:
            res_pos = np.median(var_pos,axis=0)
            res_neg = np.median(var_neg,axis=0)
        else: # use the mean if the median is not used...
            res_pos = np.mean(var_pos,axis=0)
            res_neg = np.mean(var_neg,axis=0)
        #print(var_pos)
        #print(n_neg)
        # calculate median
        
        
    """
        

    #Normalize using the reflectivity
    #if normalization:
        

    
    # calculate half the difference
    #difference = (res_pos-res_neg)/2
    # calculate average, i.e. any effects that do not depend on reversing current (or field)
    #summation = (res_pos+res_neg)/2
    #res_std = (np.std(var_pos,axis=0)+np.std(var_neg,axis=0))/2 ############## Old way of error calc (pre 03.05.2023)
    
    res_std=(np.sqrt((np.std(var_pos,axis=0))**2+(np.std(var_neg,axis=0))**2))/2  ########### mean of standard deviation for negative and positive field measurements ########### 4*res_std^2=res_pos_std^2 + res_neg_std^2 
    if 'y1' in dict_names or 'y2' in dict_names:
        #phi_pos = find_phase(x,x1,y1,edges,'pos')
        print('do')


    result = [x,difference,summation,res_std,res_pos,res_neg,len(var_pos)]
    #print(len(res_pos))
    #print(len(var_pos))
    """
    return raw_data


def linescan_calc_Tobi(scanlist,ignorLines=[],do_phasearray = False, ch_pol='None',ch_x='None',setup=1,convert_dict_to_list = True,rm_channels = []):
    """
    Imports averaged data from data channels for given logfilenameShort.
    
    Attributes
    ----------
    scanlist : str
        File name of log-file containing individual moke measurements
    ignorLines : List
        List of scan lines to be ignored for averages
    ch_pol : str
        Channel Polarity (1,2,2,1), if None reading from logfile
    ch_x : str
        Define which data channel be used as x-axis. e.g. actuator_1_1. if None use the last one found
    setup : int
        Define BigMOKE setup 1 (green) or 2 (IR)
    convert_dict_to_list : bool
        If true: Output the data as a list (as before) or as a dictionary

    Returns
    -------
    res : list or dict (depending of convert_dict_to_list)
        Returning the data corresponding to structure of supplied nxs files.

    """
    single_scan = False
    if '.nxs' in scanlist:
        print('No scanlist provdied, but single measurement')
        single_scan = True
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    if setup==2:
        path = '../Data/Scanlists_S2/'
        dpprefix = '../Data/Data_S2/'
    res = get_channels(scanlist,logfilepath = path, datapathprefix = dpprefix)
    #print('\n\n')
    if rm_channels:
        channels_str = []
        for ch in rm_channels:
            channels_str.append(str(ch))
    else:
        channels_str = ['abc']
    my_dict = {}     
    
    
    pyhystlongi = False
    if not do_phasearray:
         for key in res.keys():
             name = res[key]
             #print('name=',name)
             if ch_x == None:
                 
                 if 'smaract' in name and 'actuator' in key:
                     ch_x=key
                     print('ch_x',key)
             if 'pyhystlongi' in name:
                print('PyHystLongi measurement!')
                pyhystlongi = True        
             
             #print(name,key)
             namelements = name.split("/")
             #print(namelements)
             if len(namelements) < 2:
                 break
             dict_name = namelements[-2] + namelements[-1]
             
             print(key)
             #20260305: Added case to be able to import pyhystlongi data
             #-----
             if pyhystlongi:
                 if 'pyhystlongi/field' in name:
                     H =  data_load(dpprefix+scanlist, key) 
                     my_dict['field_corr'] = H
                 elif 'pyhystlongi/result1' in name:
                     DC_Kerr =  data_load(dpprefix+scanlist, key) 
                     my_dict['DC_Kerr'] = DC_Kerr
             else:
                 #----- move block 1 tab back to revert changes above
                 if 'data' in key:
                     if single_scan:
                         
                         if key[-2::] not in channels_str:
                             data =  data_load(dpprefix+scanlist, key) 
                             my_dict[dict_name] = data
                         else:
                             my_dict[dict_name] = np.zeros(10)
                         if key == 'data_01':
                             x = data_load(dpprefix+scanlist, 'actuator_1_1')
                             my_dict['x'] = x
                     else: #Regular import using scanlist
                         if key[-2::] not in channels_str:
                             data = data_calculation_SOT(scanlist, ch_x=ch_x,ch_pol=ch_pol,ch_var=key,ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 
                             my_dict[dict_name] = data
                         else:
                             my_dict[dict_name] = np.zeros(10)
                         if key == 'data_01':
                             x = data[0]
                             my_dict['x'] = x
    elif do_phasearray:
        #to be continued
        print('bla')
        raw_data = data_calculation_SOT_Tobi(scanlist,res)
        for key in raw_data.keys():
            pass
            

                        
    if convert_dict_to_list:
        numLI = 0
        keys = list(my_dict.keys())
        result = []
        result_keys =[]
        if 'x' in keys:
            result.append(my_dict['x'])
            result_keys.append('x')
        if 'averagein2value' in keys:
            result.append(my_dict['averagein2value'])  
            result_keys.append('I2')
        if 'averagein1value' in keys:
            result.append(my_dict['averagein1value'])
            result_keys.append('I')

        if 'averagein3value' in keys:
            result.append(my_dict['averagein3value'])  
            result_keys.append('IBD3')
        if 'averagein6value' in keys:
            result.append(my_dict['averagein6value'])  
            result_keys.append('IBD6')
        if 'zi2x1' in keys:
            numLI += 1
            result.append(my_dict['zi2x1'])
            result.append(my_dict['zi2y1'])  
            result.append(my_dict['zi2x2'])
            result.append(my_dict['zi2y2'])  
            LI_name =  'l%s' %numLI                                 
            result_keys.append(LI_name + 'x1')
            result_keys.append(LI_name + 'y1')
            result_keys.append(LI_name + 'x2')
            result_keys.append(LI_name + 'y2')
        if 'zix1' in keys:
            numLI += 1
            result.append(my_dict['zix1'])
            result.append(my_dict['ziy1'])  
            result.append(my_dict['zix2'])
            result.append(my_dict['ziy2'])  
            LI_name =  'l%s' %numLI                                 
            result_keys.append(LI_name + 'x1')
            result_keys.append(LI_name + 'y1')
            result_keys.append(LI_name + 'x2')
            result_keys.append(LI_name + 'y2')
        if 'srlockinx1' in keys:
            numLI += 1
            result.append(my_dict['srlockinx1'])
            result.append(my_dict['srlockiny1'])  
            result.append(my_dict['srlockinx2'])
            result.append(my_dict['srlockiny2'])  
            LI_name =  'l%s' %numLI                                 
            result_keys.append(LI_name + 'x1')
            result_keys.append(LI_name + 'y1')
            result_keys.append(LI_name + 'x2')
            result_keys.append(LI_name + 'y2')
        if 'srlockin_2x1' in keys:
            numLI += 1
            result.append(my_dict['srlockin_2x1'])
            result.append(my_dict['srlockin_2y1'])  
            result.append(my_dict['srlockin_2x2'])
            result.append(my_dict['srlockin_2y2'])  
            LI_name =  'l%s' %numLI                                 
            result_keys.append(LI_name + 'x1')
            result_keys.append(LI_name + 'y1')
            result_keys.append(LI_name + 'x2')
            result_keys.append(LI_name + 'y2')
        if 'pyrelaisswitchvar' in keys:
            result.append(my_dict['pyrelaisswitchvar'])  
            result_keys.append('relaypos')
        if 'magnetfield_longitudinal_corr' in keys:
            result.append(my_dict['magnetfield_longitudinal_corr'])  
            result_keys.append('field')
        if 'magnetfield_polar_corr' in keys:
            result.append(my_dict['magnetfield_polar_corr'])  
            result_keys.append('field')
        print('\n Selected List as output: \n Order = ', result_keys)
        res = result
    else:
        res = my_dict
    return res
    

def linescan_SNE_SOT(logfilenameShort,ignorLines=[],ch_pol='None',ch_x='actuator_1_1',setup_spec=None,xlab=r'H$_{ext}$',setup=1,std=False,field=True,new = False):
    """
    Imports averaged data from data channels for given logfilenameShort.

    Attributes
    ----------
    logfilenameShort : str
        File name of log-file containing individual moke measurements
    ignorLines : List
        List of scan lines to be ignored for averages
    ch_pol : str
        Channel Polarity (1,2,2,1), if None reading from logfile
    ch_x : str
        Define which data channel be used as x-axis
    setup_spec : str
        Specify data channel order, "None", "2LI", etc. see below
    xlab : str
        ??
    setup : int
        Define BigMOKE setup 1 (green) or 2 (IR)
    field : bool
        Define whether field or relay was switched
    new: bool
        Define if its old (before 07.25) measurement or new: I think old was usually x1,y1 etc on data_03 data_04, and new starts from data_04,data_05...
        
    --Standard (setup_spec = None)--
    Returns: results : List  
        x,I,I2,x1,y1,x2,y2,relaypos,H (corresponding to data channels: 02[0], 02, 01, 03, 04, 05 , 06, 07, 08)
    
    03.06.25
    -- Both Lock-ins + single BD (setup_spec = '2LI+avgSingle') --
    Returns: results : List
        x,I,I2,I_BD_avg6,x1_ZI,y1_ZI,x2_ZI,y2_ZI,x1_SR,y1_SR,x2_SR,y2_SR,relaypos,H (see below for channels)

    -- Both Lock-ins (setup_spec = '2LI') --
    Returns: results : List
        x,I,I2,x1_ZI,y1_ZI,x2_ZI,y2_ZI,x1_SR,y1_SR,x2_SR,y2_SR,relaypos,H (see below for channels)
        
    
    """
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    if setup==2:
        path = '../Data/Scanlists_S2/'
        dpprefix = '../Data/Data_S2/'
    #I2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #I = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #x1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #y1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #x2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #y2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #relaypos = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_09',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    
    #l2x2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #l2y2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #l2x1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_10',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #l2y1 = data_calculation_SOTf(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_11',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)

    #I2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #I = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #x1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #y1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #x2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #y2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #relaypos = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    
    I = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    I2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    relaypos = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x = I[0]
    nrstr = 7
    if new:
        I2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        I = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        x1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        y1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        x2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        y2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        relaypos = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        x = I[0]
        nrstr += 1
    if field:
        H = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08' ,ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        #result = x,I,I2,x1,y1,x2,y2,l2x2,l2y2,l2x1,l2y1,relaypos,H
        result = x,I,I2,x1,y1,x2,y2,relaypos,H
    else:
        #result = x,I,I2,x1,y1,x2,y2,l2x2,l2y2,l2x1,l2y1,relaypos
        result = x,I,I2,x1,y1,x2,y2,relaypos
        

    if setup_spec == '2LI+avgSingle':
        print('setup_spec' + setup_spec+ 'supplied, trying to import data for two lock-ins + single BD. CHECK DATA CHANNELS')
        I2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        I = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        I_BD_avg6 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #averagein6
        x1_ZI = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #ZI
        y1_ZI = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #ZI
        x2_ZI = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #ZI
        y2_ZI = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #ZI
        x1_SR = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 
        y1_SR = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_09',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 
        x2_SR = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_10',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 
        #x2_SR = np.zeros(len(y1_SR))
        y2_SR = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_11',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 

        relaypos = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_13',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        
        if field:
            H = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_12',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
            result = x,I,I2,I_BD_avg6,x1_ZI,y1_ZI,x2_ZI,y2_ZI,x1_SR,y1_SR,x2_SR,y2_SR,relaypos,H
        else:
            result = x,I,I2,I_BD_avg6,x1_ZI,y1_ZI,x2_ZI,y2_ZI,x1_SR,y1_SR,x2_SR,y2_SR,relaypos

    if setup_spec == '2LI':
        print('setup_spec' + setup_spec+ 'supplied, trying to import data for two lock-ins. CHECK DATA CHANNELS')
        I2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        I = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        x1_ZI = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #ZI
        y1_ZI = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #ZI
        x2_ZI = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #ZI
        y2_ZI = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) #ZI
        x1_SR = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 
        y1_SR = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 
        x2_SR = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_9',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 
        y2_SR = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_10',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix) 

        relaypos = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_12',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        
        if field:
            H = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_11',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
            result = x,I,I2,x1_ZI,y1_ZI,x2_ZI,y2_ZI,x1_SR,y1_SR,x2_SR,y2_SR,relaypos,H
        else:
            result = x,I,I2,x1_ZI,y1_ZI,x2_ZI,y2_ZI,x1_SR,y1_SR,x2_SR,y2_SR,relaypos

    
    
    
    
    
    # plotting
    return result






# 30.01.2022 EK adapted linescan_SNE and data_calculation for SHE measurements 


def data_calculation_SHE(logfilenameShort,median=False,normalization=False, ch_x='actuator_1_1',ch_pol='data_12',ch_var='data_01',ignorLines = [],logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/'):
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    firstscan = True
    firstneg = True
    firstpos = True
    lineTotal = -2
    lineCounter = 0
    
    for line in f:
        # loop over all lines in logfile
        
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        lineElements = line.split("\t")      
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        if firstscan:
            firstscan = False
            x = data_load(datapath + filename, ch_x)
            var_neg = np.zeros_like(x)
            var_pos = np.zeros_like(x)
            n_pos = 0
            n_neg = 0

        # read relay position or magnet...
        if ch_pol == 'None':
            p = pol
            if p % 2 == 0:
                p = -1
            p = -1*p # changed on the 15.11.2020; now it has the same directionality as using the relay position. Before that, using ch_pol lead to a sign change...
        else:
            p = data_load(datapath + filename, ch_pol)[0]
        
        var = data_load(datapath + filename, ch_var)
        # replace all NaN values with the interpolation of the neighbour values. Might cause problems if too many NaNs are given or if data points are far away and not linear.
        nans, z = nan_helper(var)
        var[nans] = np.interp(z(nans), z(~nans), var[~nans])
        
        if p <= 0.0: # p=2 (or p<0) means negative field, p even means relay on; 15.11.2020: Notice how we define the current along the -x direction in this case... This means the magnetic moments in Pt are parallel to the y-direction.
            if firstneg:
                firstneg=False
                var_neg = var
            else:
                var_neg = np.vstack((var_neg,var))   
            n_neg   += 1
        else: # p=1 means relay on or positive field
            if firstpos:
                firstpos=False
                var_pos = var
            else:
                var_pos = np.vstack((var_pos,var))
            n_pos   += 1
        #if boolian_percentile:
			
    f.close()
    #print(n_pos)
    #print(n_neg)
    # calculate median
    if median==True:
        res_pos = np.median(var_pos,axis=0)
        res_neg = np.median(var_neg,axis=0)
    else: # use the mean if the median is not used...
        res_pos = np.mean(var_pos,axis=0)
        res_neg = np.mean(var_neg,axis=0)
    
    # calculate half the difference
    difference = (res_pos-res_neg)/2
    # calculate average, i.e. any effects that do not depend on reversing current (or field)
    summation = (res_pos+res_neg)/2
    #res_std = (np.std(var_pos,axis=0)+np.std(var_neg,axis=0))/2
    res_std = 0.5*np.sqrt(np.std(var_pos,axis=0)**2+np.std(var_neg,axis=0)**2)/np.sqrt(len(var_neg))
    # result = [x,difference,summation,res_std] #old one, changed on 2020/07/15
    result = [x,difference,summation,res_std,res_pos,res_neg]
    return result





def linescan_SNE_SHE(logfilenameShort,ignorLines=[],ch_pol='data_07',ch_x='actuator_1_1',titletext='$j$',xlab=r'H$_{ext}$',setup=1,std=False,field=True):
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    if setup==2:
        path = '../Data/Scanlists_S2/'
        dpprefix = '../Data/Data_S2/'
    I2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    I = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    Imon = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x1 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y1 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2x2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_12',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2y2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_13',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2x1 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_10',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    l2y1 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_11',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x = I[0]
    if field:
        H = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_09',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        result = x,I,I2,Imon,x1,y1,x2,y2,H,l2x2,l2y2,l2x1,l2y1
    else:
        result = x,I,I2,Imon,x1,y1,x2,y2,H,l2x2,l2y2,l2x1,l2y1
    # plotting
    return result

# 11.03.2022 EK adapted linescan_SNE_SOT for the 2nd setup (now with magnet) with 1 LIA

def linescan_SNE_SOT_2(logfilenameShort,ignorLines=[],ch_pol='data_08',ch_x='actuator_1_1',titletext='$j$',xlab=r'H$_{ext}$',setup=1,std=False,field=False):
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    if setup==2:
        path = '../Data/Scanlists_S2/'
        dpprefix = '../Data/Data_S2/'
    I2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    I = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y1 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y2 = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)

    x = I[0]
    if field:
        H = data_calculation_SOT(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        result = x,I,I2,x1,y1,x2,y2,H
    else:
        result = x,I,I2,x1,y1,x2,y2
    # plotting
    return result

# 07.04.2022 EK adapted linescan_SNE_SHE for the 2nd setup (now with magnet) with 1 LIA

def linescan_SNE_SHE_2(logfilenameShort,ignorLines=[],ch_pol='data_09',ch_x='actuator_1_1',titletext='$j$',xlab=r'H$_{ext}$',setup=1,std=False,field=False):
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    if setup==2:
        path = '../Data/Scanlists_S2/'
        dpprefix = '../Data/Data_S2/'
    I2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_01',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    I = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_02',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x1 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_03',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y1 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_04',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_05',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    y2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_06',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #l2x2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_07',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #l2y2 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #l2x1 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_10',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    #l2y1 = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_11',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
    x = I[0]
    if field:
        H = data_calculation_SHE(logfilenameShort, ch_x=ch_x,ch_pol=ch_pol,ch_var='data_08',ignorLines = ignorLines,logfilepath = path, datapathprefix = dpprefix)
        result = x,I,I2,x1,y1,x2,y2,H #l2x2,l2y2,l2x1,l2y1,H
    else:
        result = x,I,I2,x1,y1,x2,y2, #l2x2,l2y2,l2x1,l2y1
    # plotting
    return result

def find_impurities_peaks(theta_DL,peakheight = 1,do_plot = True):
    from scipy.interpolate import make_smoothing_spline
    from scipy.signal import find_peaks
    def locate_imp(peaks,x):
        imps_interval = []
        sign = np.gradient(spl(x))[peaks]<0
        mins = peaks[sign]
        maxs = peaks[np.invert(sign)]
        combos = np.zeros((len(mins)*len(maxs),3))
        i = 0
        for mini in mins:
            for maxi in maxs:
                combos[i] = [int(mini),int(maxi),np.abs(maxi-mini)]
                i += 1
        sorted_indices = np.argsort(combos[:,2])
        combos = combos[sorted_indices]
        num_imp = min(len(mins),len(maxs))
        
        if not (len(mins) == 1 and len(maxs) == 1):
            for i in range(num_imp):
                imps_interval.append([int(min(combos[i][0:2])),int(max(combos[i][0:2]))])
        else:
            print('edges found!!')
        return imps_interval,combos
    mask = np.zeros(len(theta_DL), dtype=bool)
    y = theta_DL
    x = np.arange(0,len(y),1)
    lam = 1
    spl = make_smoothing_spline(x, y, lam=lam)
    peaks,_ = find_peaks(np.abs(np.gradient(spl(x))), height=peakheight*0.02*5*max(np.abs(np.gradient(spl(x)))))
    imps_intervals,combos = locate_imp(peaks,x)
    for ints in imps_intervals:
        mask[ints[0]-2:ints[1]+2] = True
        
    if do_plot:
        plt.plot(peaks,np.gradient(spl(x))[peaks] , "x")
        #plt.savefig(path_findimp+'\\'+ 'find_peaks'+str(current) +'.png' ,bbox_inches='tight')
        plot_mask = np.invert(mask)
        plt.scatter(x[plot_mask],y[plot_mask])
        plt.plot(x,np.gradient(spl(x)), color = 'g', label = 'dtheta/dx') 
        plt.plot(x, spl(x), '-.', label=fr'$\lambda=${lam}',color = 'r')
        plt.show()
    return mask

def find_impurities(theta_DL,Ledge,Redge,standard_noise = 0.01,dIfac = 10,do_elaborate_print = False):
    
    len_width = Redge-Ledge
    check_pos = [Ledge, Ledge + int(len_width/4), Redge] #starting point of sweep to find impurity
    check_sweep = [1, 1 , -1] #direction of sweep to find impurity
    ctheta = [[] for pos in check_pos ]
    check_mean = np.zeros(len(check_pos))
    #mask = np.array([])
    masks = []
    impurity_edge = 0
    
    for i in range(len(check_pos)):
        if do_elaborate_print:
            print('Checking spot', i)
        impurity_found = False
        end_of_device = False
        inter = 2
        while not(impurity_found) or end_of_device:
            
            
            new_pos = check_pos[i] +check_sweep[i]*inter
            if new_pos == len(theta_DL) or new_pos == -1:
                #print('end of data reached')
                break
            
            #print(new_pos)
            ctheta[i].append(theta_DL[new_pos])
            sig = np.std(ctheta[i])
            impurity_found = sig > 5*standard_noise
            
            #print(sig)
            inter += 1
            
            if new_pos == Ledge or new_pos == Redge:
                end_of_device = True
                #raise(Exception('took too long'))
        if end_of_device:
            if do_elaborate_print:
                print('End of device reached. No impurity found!')
            masks.append(np.zeros(len(theta_DL), dtype=bool))
        else:
            
            if np.abs(new_pos - impurity_edge) > 4:
                if do_elaborate_print:
                    print('Impurity found from position %s to %s' %(check_pos[i],new_pos))
                impurity_edge = new_pos
                mask = np.zeros(len(theta_DL), dtype=bool)
                mask[int(check_pos[i]):int(impurity_edge)] = True
                #masks.append(np.arange(int(check_pos[i]),int(impurity_edge),1))
                #print(mask)
                masks.append(mask)
            elif i == 2:
                if do_elaborate_print:
                    print('Found the same impurity edge, creating mask!')
                    print(new_pos)
                impurity_edge = new_pos
                mask = np.zeros(len(theta_DL), dtype=bool)
                mask[int(impurity_edge):int(check_pos[i])] = True
                masks.append(mask)
            else:
                masks.append(np.zeros(len(theta_DL), dtype=bool))
    #print(len(masks))
    mean_thetas = [np.mean(c_thetai) for c_thetai in ctheta]
    mean_theta = np.mean(mean_thetas)
    dI = dIfac*standard_noise*(max(theta_DL[Ledge:Redge])-min(theta_DL[Ledge:Redge]))
    
    true_impurities = np.where(np.abs(mean_thetas-mean_theta) >dI)[0]
    if do_elaborate_print:
        print(mean_thetas,mean_theta,dI)
        print(true_impurities)
    if true_impurities.size > 0:
        #print('impurities')
        mask = None
        for pos in true_impurities:
            #print(pos,'position')
            #mask = np.append(mask,masks[pos])
            mask = masks[pos]
            if any(mask):
                mask = mask | masks[pos]
        if do_elaborate_print:
            masks[2]
    else:
        mask = np.zeros(len(theta_DL), dtype=bool)

        
    
    return mask
## TODO: adapt scripts to be used with 1 or 2 LIA (just add some IFs :))

def find_edges_width_old(position,reflex):
    def derivatives(x,y): ##Calculation of the first and second derivatives of the intensity diode's DC signal
        h=x[1]-x[0]
        dy,ddy,newpos=[0]*(len(x)-2),[0]*(len(x)-2),[0]*(len(x)-2)
        
        for i in range(1,(len(x)-1)):
            dy[i-1]=(y[i+1]-y[i-1])/(2*h)
            ddy[i-1]=(y[i+1]-2-y[i]+y[i-1])/(h*h)
            newpos[i-1]=x[i]
        return newpos,dy,ddy
    tck = interpolate.splrep(position, reflex, s=0) ## determination of interpolation parameters (nodes,..)
    position_interp= np.arange(position[0], position[-1], (position[1]-position[0])/10.) ## Increasing number of points for the new (interpolated) data
    reflex_interp = interpolate.splev(position_interp, tck, der=0) ## Creating new data points by applying the interp parameters to the new grid
    
    
    dy, d2y= [], []
    newpos, dy, d2y= derivatives(position_interp,reflex_interp)
    min_index = dy.index(min(dy))
    max_index = dy.index(max(dy))
    i = 1
    while np.abs(max_index - min_index) < 150:
        if min_index < int(len(dy)/2):
            min_index = dy.index(np.sort(dy)[i])
        elif max_index > int(len(dy)/2):
            max_index =  dy.index(np.sort(dy)[-i])
        #print(np.abs(max_index - min_index))
        i += 1
        if i > 100:
            break
    x1=newpos[min_index]
    
    x2=newpos[max_index]
    width = x2 -x1
    if (width<0):
        a=x1
        x1=x2
        x2=a
        width=-width
    x1=round(x1, 2)
    x2=round(x2, 2)
    width=round(width, 2)
    edges=[x1,x2]
    return edges, width

from scipy import interpolate, signal
import numpy as np

def find_edges_width(position, reflex):
    #CLAUDE 09.04.26
    def derivatives(x, y):
        h = x[1] - x[0]
        dy  = [(y[i+1] - y[i-1]) / (2*h)       for i in range(1, len(x)-1)]
        ddy = [(y[i+1] - 2*y[i] + y[i-1]) / (h*h) for i in range(1, len(x)-1)]
        return list(x[1:-1]), dy, ddy

    # Interpolate for sub-step resolution
    tck = interpolate.splrep(position, reflex, s=0)
    step = (position[1] - position[0]) / 10.0
    pos_interp = np.arange(position[0], position[-1], step)
    ref_interp = interpolate.splev(pos_interp, tck, der=0)

    newpos, dy, _ = derivatives(pos_interp, ref_interp)
    newpos = np.array(newpos)
    dy     = np.array(dy)

    mid    = len(dy) // 2
    dy_range = np.ptp(dy)
    prominence = dy_range * 0.1
    min_dist   = max(10, len(dy) // 50)

    # ── Left half: look for a large positive peak (rising edge) ──────────────
    left_dy   = dy[:mid]
    lpos_idx, lpos_props = signal.find_peaks( left_dy, prominence=prominence, distance=min_dist)
    lneg_idx, lneg_props = signal.find_peaks(-left_dy, prominence=prominence, distance=min_dist)

    if len(lpos_idx) > 0 and len(lneg_idx) > 0:
        # Both polarities present — pick whichever has the stronger peak
        best_pos = lpos_idx[np.argmax(lpos_props['prominences'])]
        best_neg = lneg_idx[np.argmax(lneg_props['prominences'])]
        left_idx = best_pos if dy[best_pos] >= -dy[best_neg] else best_neg
    elif len(lpos_idx) > 0:
        left_idx = lpos_idx[np.argmax(lpos_props['prominences'])]
    elif len(lneg_idx) > 0:
        left_idx = lneg_idx[np.argmax(lneg_props['prominences'])]
    else:
        left_idx = int(np.argmax(np.abs(left_dy)))   # fallback: sharpest change

    # ── Right half: look for a large negative peak (falling edge) ────────────
    right_dy   = dy[mid:]
    rpos_idx, rpos_props = signal.find_peaks( right_dy, prominence=prominence, distance=min_dist)
    rneg_idx, rneg_props = signal.find_peaks(-right_dy, prominence=prominence, distance=min_dist)

    if len(rneg_idx) > 0 and len(rpos_idx) > 0:
        best_neg = rneg_idx[np.argmax(rneg_props['prominences'])]
        best_pos = rpos_idx[np.argmax(rpos_props['prominences'])]
        right_idx = mid + (best_neg if -dy[mid+best_neg] >= dy[mid+best_pos] else best_pos)
    elif len(rneg_idx) > 0:
        right_idx = mid + rneg_idx[np.argmax(rneg_props['prominences'])]
    elif len(rpos_idx) > 0:
        right_idx = mid + rpos_idx[np.argmax(rpos_props['prominences'])]
    else:
        right_idx = mid + int(np.argmax(np.abs(right_dy)))  # fallback

    x1    = round(float(newpos[left_idx]),  2)
    x2    = round(float(newpos[right_idx]), 2)
    width = round(x2 - x1, 2)
    return [x1, x2], width

def data_read_calc(logfilenameShort,median=False,normalization=False, ch_x='actuator_1_1',ch_pol='data_12',ch_var='data_01',ignorLines = [],logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/'):
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    firstscan = True
    firstneg = True
    firstpos = True
    lineTotal = -2
    lineCounter = 0
    relay = []
    for line in f:
        # loop over all lines in logfile
        #print(line)
        if lineCounter == lineTotal: # read only up to lineTotal files
            break 
        
        lineCounter += 1
        
        if lineCounter in ignorLines: # do not read (i.e. ignore) this file
            #print('*** ignoring '+line.split('/')[-1].strip('\n')+' ***')
            continue
        
        lineElements = line.split("\t")      
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        field = float(lineElements[1]) # if present, is next after filename
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        relay.append(pol)
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        # init if first scan is read  
        
        if firstscan:
            
            firstscan = False
            x = data_load(datapath + filename, ch_x)
            var_neg = np.zeros_like(x)
            var_pos = np.zeros_like(x)
            n_pos = 0
            n_neg = 0

        # read relay position or magnet...
        if ch_pol == 'None':
            p = pol
            if p % 2 == 0:
                p = -1
            #p = -1*p # changed on the 15.11.2020; now it has the same directionality as using the relay position. Before that, using ch_pol lead to a sign change...
        else:
            p = data_load(datapath + filename, ch_pol)[0]
        #p = data_load(datapath + filename, ch_pol)[0]
        
        var = data_load(datapath + filename, ch_var)
        # replace all NaN values with the interpolation of the neighbour values. Might cause problems if too many NaNs are given or if data points are far away and not linear.
        nans, z = nan_helper(var)
        
        var[nans] = np.interp(z(nans), z(~nans), var[~nans])
        
        if p <= 0.0: # p=2 (or p<0) means negative field, p even means relay on; 15.11.2020: Notice how we define the current along the -x direction in this case... This means the magnetic moments in Pt are parallel to the y-direction.
            if firstneg:
                firstneg=False
                var_neg = var
            else:
                var_neg = np.vstack((var_neg,var))   
            n_neg   += 1
        else: # p=1 means relay on or positive field
            if firstpos:
                firstpos=False
                var_pos = var
            else:
                var_pos = np.vstack((var_pos,var))
            n_pos   += 1
        #if boolian_percentile:
			
    f.close()
    #print(var_pos)
    #print(n_neg)
    # calculate median
    if median==True:
        res_pos = np.median(var_pos,axis=0)
        res_neg = np.median(var_neg,axis=0)
    else: # use the mean if the median is not used...
        res_pos = np.mean(var_pos,axis=0)
        res_neg = np.mean(var_neg,axis=0)

    #Normalize using the reflectivity
    #if normalization:
        

    
    # calculate half the difference
    difference = (res_pos-res_neg)/2
    # calculate average, i.e. any effects that do not depend on reversing current (or field)
    summation = (res_pos+res_neg)/2
    #res_std = (np.std(var_pos,axis=0)+np.std(var_neg,axis=0))/2 ############## Old way of error calc (pre 03.05.2023)
    
    res_std=(np.sqrt((np.std(var_pos,axis=0))**2+(np.std(var_neg,axis=0))**2))/2  ########### mean of standard deviation for negative and positive field measurements 
                                                                                  ########### 4*res_std^2=res_pos_std^2 + res_neg_std^2 
    

    result = [x,difference,summation,res_std,res_pos,res_neg,len(var_pos)]
    #print(len(res_pos))
    #print(len(var_pos))
    return result

def get_channels(Scanlist,logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/'):
    """

    Parameters
    ----------
    filename : str
        Give the full file path to the nexus (nxs) file: e.g. Z:/projects/MOKE_lab/Scanning/Data/Data_S1/20250815/moke_15h51m23.815.nxs'

    Returns
    -------
    data_chs : list 
        Listing all the data channels available, from data_01 to data_0x
    ch_names : list
        Listing their long_names as seen in the salsa interface

    """ 
    if '.nxs' in Scanlist:
        filename = Scanlist
        print('Single measurement provided, NOT using Scanlist!')
    else:
        print('Opening first file of Scanlist to identify Data Channels.')
        logfilename = logfilepath + Scanlist
        # open data  
        lineCounter = 0
        lineTotal = 1
        f = open(logfilename,'r')
        for line in f:
            # loop over all lines in logfile
            #print(line)
    
            if lineCounter == lineTotal: # read only up to lineTotal files
                break 
            lineElements = line.split("\t")      
            filenames = lineElements[0].split('/') # last part of filename
            filename = filenames[-2] + '/'+ filenames[-1]
            filename = filename.strip('\n') # strip newline character at end of line
            lineCounter += 1

    filename = datapathprefix + filename
    
    with h5py.File(filename,'r') as f: # handles file closing also in case of error
        
        scans = f.keys()   #returns a list of all the available keys in the dictionary.
        first_scan = list(f.keys())[0]

        # Path to the scan_data group
        scan_data_path = f[first_scan]['scan_data']
        
        # Count number of datasets (data channels) in scan_data
        data_chs = [name for name in scan_data_path if isinstance(scan_data_path[name], h5py.Dataset)]
        #data_chs = data_channels[1:-3] #Should be fine if its always the actuator first and then 4 random stuff at the end (integration times etc)
        ch_names = []
        numscans=len(scans)
        
        if numscans > 0: # yes there is data
            for scan in scans:
                for data_ch in data_chs:
                    key=scan+'/scan_data/'+data_ch
                    #print(key) # for debug only
                    datanew = np.array(f[key])
                    dataset = f[key]
                    description = dataset.attrs.get('long_name', 'No description attribute found').decode('UTF-8')
                    print(f"Description of '{data_ch}':", description)
                   
                    ch_names.append(description)
                    if data_ch == 'integration_times':
                        datanew = np.array(f[key])
                        print("Integration time per datapoint = %s s " %np.mean(datanew))
                        break
       
        res = dict(zip(data_chs, ch_names))
    return res