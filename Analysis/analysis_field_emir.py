# import all the standard libraries needed for import and calculations, plotting
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
#import nexus
import scipy.optimize as optimization
import math
import h5py
import datetime
import pandas as pd
from datetime import timedelta


path = '../Data/Scanlists_S1/' 

def data_load(filename, data_channel, scan=0): # how to load each channel
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

def fieldscan_AC(logfilenameShort): # loading all channels for one measurement
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    x = data_load(logfilenameShort, 'actuator_1_1') #this should correspond the current values (magnet current)
    I = data_load(logfilenameShort,  'data_01')
    I2 = data_load(logfilenameShort, 'data_02')
    x1 = data_load(logfilenameShort, 'data_05')
    y1 = data_load(logfilenameShort, 'data_07')
    x2 = data_load(logfilenameShort, 'data_06')
    y2 = data_load(logfilenameShort, 'data_08')
    #l2x2 = data_load(logfilenameShort, 'data_10')
    #l2y2 = data_load(logfilenameShort, 'data_12')
    #l2x1 = data_load(logfilenameShort, 'data_09')
    #l2y1 = data_load(logfilenameShort, 'data_11')
    H = data_load(logfilenameShort, 'data_03')
    #result = x,I,I2,x1,y1,x2,y2,l2x2,l2y2,l2x1,l2y1,H
    result = x,I,I2,x1,y1,x2,y2,H
    return result

def direct_fieldscan_AC(logfilenameShort,data_order): # loading all channels for one measurement
    path = 'Z:/projects/MOKE_lab/Scanning/Data/Data_S1/'
    
    x = data_load(path+logfilenameShort, 'actuator_1_1') #this should correspond the current values (magnet current)
    I = data_load(path+logfilenameShort,  'data_01')
    I2 = data_load(path+logfilenameShort, 'data_02')
    x1 = data_load(path+logfilenameShort, 'data_06')
    y1 = data_load(path+logfilenameShort, 'data_07')
    x2 = data_load(path+logfilenameShort, 'data_08')
    y2 = data_load(path+logfilenameShort, 'data_09')
    if data_order == '2LI+avgSingle':
        I_singleBD = data_load(path+logfilenameShort, 'data_03')
        l2x1 = data_load(path+logfilenameShort, 'data_08')
        l2y1 = data_load(path+logfilenameShort, 'data_09')
        l2x2 = data_load(path+logfilenameShort, 'data_10')
        l2y2 = data_load(path+logfilenameShort, 'data_11')
        relay = data_load(path+logfilenameShort, 'data_12')
    H = data_load(path+logfilenameShort, 'data_04')
    #result = x,I,I2,x1,y1,x2,y2,l2x2,l2y2,l2x1,l2y1,H
    if data_order == '1LI':
        I_singleBD = data_load(path+logfilenameShort, 'data_03')
        result = x,I,I2,I_singleBD,x1,y1,x2,y2,H
    elif data_order == '2LI+avgSingle' :
        result = x,I,I2,I_singleBD,x1,y1,x2,y2,l2x1,l2y1,l2x2,l2y2,relay,H
    return result

def transport_check(logfilenameShort): # loading all channels for one measurement
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    #I = data_load(logfilenameShort, 'actuator_1_1') #this should correspond the current values (magnet current)
    T = data_load(logfilenameShort, 'data_01')
    x1 = data_load(logfilenameShort,  'data_02')
    y1 = data_load(logfilenameShort, 'data_03')
    x3 = data_load(logfilenameShort, 'data_04')
    y3 = data_load(logfilenameShort, 'data_05')
    #x2 = data_load(logfilenameShort, 'data_06')
    #y2 = data_load(logfilenameShort, 'data_08')
    #l2x2 = data_load(logfilenameShort, 'data_10')
    #l2y2 = data_load(logfilenameShort, 'data_12')
    #l2x1 = data_load(logfilenameShort, 'data_09')
    #l2y1 = data_load(logfilenameShort, 'data_11')
    #H = data_load(logfilenameShort, 'data_03')
    #result = x,I,I2,x1,y1,x2,y2,l2x2,l2y2,l2x1,l2y1,H
    result = T,x1,y1,x3,y3
    return result

def data_calculation_transport(logfilenameShort,logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/', do_list = False): #
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    #lineTotal=1
    lineCounter=0
    titles=[]
    if do_list:
        var = []
    for line in f:
        # loop over all lines in logfile

        lineCounter += 1
        
        lineElements = line.split("\t")      
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        title = str(lineElements[-1].strip('\n')) # last element (may be applied current or orientation of the device)
        #print(title)
        titles += [title]

        
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        if do_list:
            var.append(transport_check(datapath+filename))
        else:
            if lineCounter==1:
                var=transport_check(datapath+filename) #Ovdje sad trebas dodati da napravi odgovarajuci file sa podacima
            else:
                var1 = transport_check(datapath + filename) #I ovdje
                var=np.hstack((var,var1)) #In this configuration, we have 12 channels => the data for each measurement is added to the end (e.g. I2 will be the intensity diode for all scans in the text file)
			
    f.close()
    #print(lineCounter)
    result = var
    
    return result, lineCounter, titles 



def currentsweep_AC(logfilenameShort): # loading all channels for one measurement
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    x = data_load(logfilenameShort, 'actuator_1_1') #this should correspond the current values (sample current)
    I = data_load(logfilenameShort,  'data_01')
    I2 = data_load(logfilenameShort, 'data_02')
    x1 = data_load(logfilenameShort, 'data_03') 
    y1 = data_load(logfilenameShort, 'data_04')
    x2 = data_load(logfilenameShort, 'data_05')  #recheck this channel and the ones after!
    y2 = data_load(logfilenameShort, 'data_06')
    l2x2 = data_load(logfilenameShort, 'data_11')
    l2y2 = data_load(logfilenameShort, 'data_12')
    l2x1 = data_load(logfilenameShort, 'data_09')
    l2y1 = data_load(logfilenameShort, 'data_10')
    H = data_load(logfilenameShort, 'data_08')
    result = x,I,I2,x1,y1,x2,y2,l2x2,l2y2,l2x1,l2y1,H
    return result

def data_calculation_AC_current_sweep(logfilenameShort,logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/'): #
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    #lineTotal=1
    lineCounter=0
    titles=[]
    for line in f:
        # loop over all lines in logfile

        lineCounter += 1
        
        lineElements = line.split("\t")      
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        title = str(lineElements[-1].strip('\n')) # last element (may be applied current or orientation of the device)
        #print(title)
        titles += [title]

        
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        
        if lineCounter==1:
            var=currentsweep_AC(datapath+filename) #Ovdje sad trebas dodati da napravi odgovarajuci file sa podacima
        else:
            var1 = currentsweep_AC(datapath + filename) #I ovdje
            var=np.hstack((var,var1)) #In this configuration, we have 12 channels => the data for each measurement is added to the end (e.g. I2 will be the intensity diode for all scans in the text file)
			
    f.close()
    #print(lineCounter)
    result = var
    
    return result, lineCounter, titles



#Now I need to give it a file with several measurements and let it run over it

def data_calculation_AC(logfilenameShort,logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/', do_list = False): #
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    #lineTotal=1
    lineCounter=0
    titles=[]
    if do_list:
        var = []
    for line in f:
        # loop over all lines in logfile

        lineCounter += 1
        
        lineElements = line.split("\t")      
        print(lineElements)
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        title = str(lineElements[-1].strip('\n')) # last element (may be applied current or orientation of the device)
        #print(title)
        titles += [title]

        
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        if do_list:
            var.append(fieldscan_AC(datapath+filename))
            print(datapath+filename)
        else:
            if lineCounter==1:
                
                var=fieldscan_AC(datapath+filename) #Ovdje sad trebas dodati da napravi odgovarajuci file sa podacima
            else:
                var1 = fieldscan_AC(datapath + filename) #I ovdje
                var=np.hstack((var,var1)) #In this configuration, we have 12 channels => the data for each measurement is added to the end (e.g. I2 will be the intensity diode for all scans in the text file)
			
    f.close()
    #print(lineCounter)
    result = var
    
    return result, lineCounter, titles 


def fieldscan_DC(logfilenameShort): # loading all channels for one measurement
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    #I2 = data_load(logfilenameShort,  'data_01')
    I = data_load(logfilenameShort, 'data_01')
    #x = data_load(logfilenameShort, 'actuator_1_1') #this should correspond the current values (magnet current)
    H = data_load(logfilenameShort, 'data_02')
    d3 = data_load(logfilenameShort, 'data_03')
    d4 = data_load(logfilenameShort, 'data_04')
    #d6 = data_load(logfilenameShort, 'data_06')
    #there are other results coming from this measurement (result5,ms,mr,hc,hshift)
    #these can as well be incorporated..
    result = H,I
    return result


def data_calculation_DC(logfilenameShort,logfilepath = '../Data/Scanlists_S1/', datapathprefix = '../Data/Data_S1/', do_list = False): #
    # generate the logfilename
    logfilename = logfilepath + logfilenameShort
    # open data  
    f = open(logfilename,'r')
    
    #lineTotal=1
    lineCounter=0
    titles=[]
    if do_list:
        var = []
    for line in f:
        # loop over all lines in logfile

        lineCounter += 1
        
        lineElements = line.split("\t")      
        
        filename = lineElements[0].split('/')[-1] # last part of filename
        filename = filename.strip('\n') # strip newline character at end of line
        
        title = str(lineElements[-1].strip('\n')) # last element (may be applied current or orientation of the device)
        #print(title)
        titles += [title]

        
        # construct datapath from prefix and date directory
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        if do_list:
            var.append(fieldscan_DC(datapath+filename))
            print(datapath+filename)
        else:
            if lineCounter==1:
                var=fieldscan_DC(datapath+filename) #Ovdje sad trebas dodati da napravi odgovarajuci file sa podacima
            else:
                var1 = fieldscan_DC(datapath + filename) #I ovdje
                var=np.hstack((var,var1)) #In this configuration, we have 4 channels => the data for each measurement is added to the end (e.g. I2 will be the intensity diode for all scans in the text file)
			
    f.close()
    #print(lineCounter)
    result = var
    
    return result, lineCounter, titles 