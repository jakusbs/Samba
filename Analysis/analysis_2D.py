""" The scripts in analysis_2D are programmed to load the data measured by the scanserver. There are several different scripts for loading the data, calculating the sum and difference with respect to relais position 
and further analysis and plotting.

"""

# import standard numpy packages needed for the scripts in this folder.
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import h5py
from scipy.optimize import minimize
from scipy import interpolate
import os
import scipy
from  scipy import ndimage
import matplotlib.colors as mcolors

def nan_helper(y):
    return np.isnan(y), lambda z: z.nonzero()[0]

def read_data(filename, data_channel):
    """ opens file to read specific data channel
        filename: name of hdf5 or nexus file
        data_channel: string describing the data to read, e.g. 'data_01' 
        This script just reads a single channel from a single hdf5 file.
        """

    with h5py.File(filename,'r') as f: # handles file closing also in case of error
        scans = f.keys()
        numscans=len(scans) # if multiple scans are written into a single file, they are averaged all togther.
        
        if numscans > 0: # yes there is data
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
            
        else: # no scans found
            data = np.zeros(1) # default value if there are no scans
            print('WARNING: read_data: no scans in file {:s}'.format(filename))

    return data

def plot_image(data,tit='', xlab='x [$\mu m$]',ylab='y [$\mu m$]',cmap='RdBu',color_label='signal [mV]',size1=12,size2=12,mirror=False):
    """script to plot an image from a data entry(data). Allows to add title (tit), x and y labels (xlab,ylab), colormaps (cmap), a label for the colorbar (color_label) as well as the size of the image (size1,size2).
    It further allows for mirroring the image (mirror)."""
    # find the scaling for the colorbar and if nothing else is given, just set it to 1.
    pmin = np.min(data)
    pmax = np.max(data)
    if abs(pmin)>=abs(pmax):
    	val = abs(pmin)
    elif abs(pmin)<=abs(pmax):
    	val = abs(pmax)
    elif pmin == pmax: # this would mean that all the values are identical.
        val = 1e-3
    else:				# that would mean that none of the above situations take place and that should not happen
        val = 1
    plt.figure(figsize=(size1,size2))
    if mirror:
        plt.imshow(np.flip(data,1),cmap=cmap,interpolation='None',vmin=-val,vmax=val) # extent = 
    else:
        plt.imshow(data,cmap=cmap,interpolation='None',vmin=-val,vmax=val) # extent = 
    plt.title(tit)
    plt.xlabel(xlab)
    plt.ylabel(ylab)
    plt.colorbar(fraction=0.046, pad=0.04,label=color_label) # colorbar which fits the image approximately
    plt.show()
    return

def plot_image_extent(x,y,data,tit='', xlab='x [$\mu m$]',ylab='B [mT]',cmap='RdBu',color_label='signal [mV]'):
    """the same plotting script as plot_image, but now the image is scaled according to the extent of the data. This script also asks for an x and y input."""
    pmin = np.min(data)
    pmax = np.max(data)
    if abs(pmin)>=abs(pmax):
        val = abs(pmin)
    elif abs(pmin)<=abs(pmax):
        val = abs(pmax)
    elif pmin == pmax:
        val = 1e-3
    else:
        val = 1
    extent = [np.min(x),np.max(x),np.min(y),np.max(y)] # find the dimension of the image.
    if extent[0] == extent[1]: # if min and max of x are the same, it just sets the extent to the same value with opposite sign. Not entirely clear when that should be the case.
        extent[0] = -extent[1]
    elif extent[2] == extent[3]:
        extent[2] = -extent[3]
    plt.figure(figsize=(8,8))
    plt.imshow(data,cmap=cmap,interpolation='None',vmin=-val,vmax=val,extent=extent,aspect='auto') # extent = 
    plt.title(tit)
    plt.xlabel(xlab)
    plt.ylabel(ylab)
    plt.colorbar(fraction=0.046, pad=0.04,label=color_label)
    plt.show()
    return

def load_channel(logfilenameShort,ignorLines=[] ,ch_x='actuator_1_1',ch_y='actuator_2_1',ch_var='data_10',setup = 1):
    """ Data that is saved as a scanlist is loaded altogther. We are loading all scans given in a file logfilenameShort and load them into a three dimensional array, where the array positions are given by 
    x,y and j, where j is the number of scans. This thus creates a 3D array of all the scans done for a given channel ch_var.
    ch_x gives the data channel which is continuously scanned (e.g. line scan)
    ch_y is the data channel which changes after a total scan of x and is made into a 2D array
    ch_var is the variable channel
    filename and datapathprefix are hardcoded, which should have been changed at some point."""
    path = '../Data/Scanlists_S1/'
    dpprefix = '../Data/Data_S1/'
    if setup==2:
        path = '../Data/Scanlists_S2/'
        dpprefix = '../Data/Data_S2/'
    print(logfilenameShort)
    if '.nxs' in logfilenameShort:
        file = dpprefix + logfilenameShort
        print('Single measurement provided, NOT using Scanlist!')    
        var = read_data(file, ch_var)
        var = np.zeros([var.shape[0],var.shape[1]])
        x = np.zeros_like(var)
        y = np.zeros_like(var)
        firstscan=False
        var[:,:] = read_data(file, ch_var)
        x[:,:] = read_data(file, ch_x) #x position
        for i in range(0,len(x[0,:,0])): # y position has to be shaped into 2D array
            y[:,i] = read_data(file, ch_y) # y position
            # interpolate all NaNs in the y direction
            nans, z = nan_helper(var[:,i]) # interpolate all the nans with adjacent numbers.
            var[nans,i] = np.interp(z(nans), z(~nans), var[~nans,i])
    filename = path+logfilenameShort
    datapathprefix = dpprefix  
    f = open(filename,'r') 
    lineCounter= 0
    file=[] #create an empty array 
    relay = []
    #creates an array with all the files
    for line in f: 
        lineCounter += 1
        lineElements = line.split('\t')
        pol = float(lineElements[-1]) # last element (may be index 1 or 2)
        relay.append(pol)
        filename = lineElements[0].split('/')[-1]
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        file.append (datapath+filename)
    files = np.append([],file)
    #print(files) #DEBUG
    'stack the given datachannel into different arrays'
    firstscan = True
    for j in range (0,lineCounter):
        try:
            #print(j) 'just to check if it is working properly'
            if j in ignorLines:
                continue
            if firstscan:
                var = read_data(files[j], ch_var)
                var = np.zeros([var.shape[0],var.shape[1],lineCounter])
                x = np.zeros_like(var)
                y = np.zeros_like(var)
                firstscan=False
            var[:,:,j] = read_data(files[j], ch_var)
            x[:,:,j] = read_data(files[j], ch_x) #x position
            for i in range(0,len(x[0,:,0])): # y position has to be shaped into 2D array
                y[:,i,j] = read_data(files[j], ch_y) # y position
                # interpolate all NaNs in the y direction
                nans, z = nan_helper(var[:,i,j]) # interpolate all the nans with adjacent numbers.
                var[nans,i,j] = np.interp(z(nans), z(~nans), var[~nans,i,j])
        except RuntimeError:
                print(ch_var+ ' line '+ str(j)+ ' gives RuntimeError, consider ignoring it')
                np.append(ignorLines,j)
                continue
    """the script returns the read-out channel as well as x and y."""
    return x,y,var,ignorLines,relay

def data_calculation2D(data,median=False,ignorLines = [],
                       shift_data = False,line0_i = 0, scan0_i = 0):
    if shift_data:
        data = drift_correction_2D_Tobi_new(data,line0_i = line0_i,scan0_i = scan0_i,plotting=False)
    
    pos = np.where((np.array(data['relay']) == 1 ))[0]  
    neg = np.where((np.array(data['relay']) == 2 ))[0]  
    npos =len(pos)
    nneg = len(neg)
    if npos != nneg:
        num_posneg = min([npos,nneg])
        print('Uneven number of pos/neg scans: Going from %s to %s' %(max([npos,nneg]),num_posneg))
        pos = pos[0:num_posneg]
        neg = neg[0:num_posneg]
    firstscan = True
    
        
    keys = list(data.keys())
    keys.remove('relay')
    extra_keys = ['diff', 'sum', 'err', 'pos', 'neg']
    
    ndata = {
        key: {subkey: None for subkey in extra_keys}
        for key in keys
    }
    for key in data.keys():
        if key != 'relay':
            #Size xsteps, ysteps, number of scans (4 per cycle)
            var_neg = np.zeros_like(data[key][:,:,0:nneg])
            var_pos = np.zeros_like(data[key][:,:,0:npos])
            
            i = 0
            for scan in pos:
                var = data[key][:,:,scan]
                nans, z = nan_helper(var)
                var[nans] = np.interp(z(nans), z(~nans), var[~nans])
                
                var_pos[:,:,i] = var
                
                i += 1
            i = 0
            for scan in neg:
                var = data[key][:,:,scan]
                nans, z = nan_helper(var)
                var[nans] = np.interp(z(nans), z(~nans), var[~nans])
                var_neg[:,:,i] = var
                
                i += 1
            if median==True:
                res_pos = np.median(var_pos,axis=2)
                res_neg = np.median(var_neg,axis=2)
            else: # use the mean if the median is not used...
                res_pos = np.mean(var_pos,axis=2)
                res_neg = np.mean(var_neg,axis=2)
                    
            # calculate half the difference
            ndata[key]['diff'] = (res_pos-res_neg)/2
            # calculate average, i.e. any effects that do not depend on reversing current (or field)
            ndata[key]['sum'] = (res_pos+res_neg)/2
            #res_std = (np.std(var_pos,axis=0)+np.std(var_neg,axis=0))/2 ############## Old way of error calc (pre 03.05.2023)
            
            ndata[key]['err']=(np.sqrt((np.std(var_pos,axis=2))**2+(np.std(var_neg,axis=2))**2))/2  ########### mean of standard deviation for negative and positive field measurements 
                                                                                          ########### 4*res_std^2=res_pos_std^2 + res_neg_std^2 
            ndata[key]['pos'] = res_pos
            ndata[key]['neg'] = res_neg
    return ndata

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

def find_calib_slope(path1,spec_calibration = None):
    ##Calibration data+ Resistivities+ Phase
    print(spec_calibration)
    try: 
        
        if spec_calibration:
            print(path1+'\\calibration' + spec_calibration + '.txt')
            file = open(path1+'\\calibration' + spec_calibration + '.txt', 'r')
            
            print('Using the calibration file: calibration' + spec_calibration + '.txt')
        else:
            file = open(path1+'\\calibration' + '.txt', 'r')
            print("Using Calibration file" , path1+'\\calibration' + '.txt') 
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
     
    sln = calibrate(np.linspace(0,25,6),calibration_data,plotting=True)
    sln = 1/(sln)*np.pi/180.0*1e6 ##(µrad/mV)
    print('Using slope %2.f [µrad/mV]' %sln)
    return sln


def data_eval2D(data,path1, li = 'zi2',I_ch = 'averagein2value', do_plot = False, do_thermoreflectance =False, phases = None,spec_calibration = None):
    sln = find_calib_slope(path1,spec_calibration = spec_calibration)
    if phases:
        phase,phase2 = phases
        print('Inputed First Harmonic Phase: %2.f° \n' %phase )
        print('Inputed Second Harmonic Phase: %2.f° \n' %phase2 )
    else:
        edges, width= get_edges_2D(data,I_ch = I_ch)
        phase = find_phase2D(data['x']['pos'],data[li+'x1']['pos'],data[li+'y1']['pos'],edges = edges,ch = 'pos', do_plot = do_plot)
        phase2 = find_phase2D(data['x']['pos'],data[li+'x2']['pos'],data[li+'y2']['pos'],edges = edges,ch = 'pos', do_plot = do_plot)
        print('Calculated First Harmonic Phase: %2.f° \n' %phase )
        print('Calculated Second Harmonic Phase: %2.f° \n' %phase2 )
    analyzed_data ={}
    #FIRST HARMONIC
    theta_rad=phase*np.pi/180.0
    analyzed_data['theta_pos']=(data[li+'x1']['pos']*np.cos(theta_rad)+data[li+'y1']['pos']*np.sin(theta_rad))*sln 
    analyzed_data['theta_neg']=(data[li+'x1']['neg']*np.cos(theta_rad)+data[li+'y1']['neg']*np.sin(theta_rad))*sln 
    
    analyzed_data['theta_sum'] = 0.5*(analyzed_data['theta_pos'] + analyzed_data['theta_neg'])
    analyzed_data['theta_diff'] = 0.5*(analyzed_data['theta_pos'] - analyzed_data['theta_neg'])
    
    #SECOND HARMONIC
    theta_rad2=phase2*np.pi/180.0
    theta2_pos=(data[li+'x2']['pos']*np.cos(theta_rad2)+data[li+'y2']['pos']*np.sin(theta_rad2))*sln 
    theta2_neg=(data[li+'x2']['neg']*np.cos(theta_rad2)+data[li+'y2']['neg']*np.sin(theta_rad2))*sln 
    
    analyzed_data['theta2_sum'] = 0.5*(theta2_pos + theta2_neg)
    analyzed_data['theta2_diff'] = 0.5*(theta2_pos - theta2_neg)
    analyzed_data['theta2_pos'] = theta2_pos
    analyzed_data['theta2_neg'] = theta2_neg
    analyzed_data['R'] = data[I_ch]['sum']
    analyzed_data['x'] = data['x']['sum']
    analyzed_data['y'] = data['y']['sum']
    
    if do_thermoreflectance:
        analyzed_data['TR'] = analyzed_data['theta2_sum']/(data[I_ch]['sum']) #divide by sln, to go back to mV
        analyzed_data['TR'][0,0] = analyzed_data['TR'][1,0]
    return analyzed_data
    

def find_phase2D(x,x1,y1,edges,ch,do_plot = False):
    mean_ledge = np.mean(edges[0])
    mean_redge = np.mean(edges[1])
    mask = np.invert((x <mean_redge)*(x > mean_ledge))
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

def find_edges_width(position,reflex):
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


def find_edges_width2D(position, reflex):
    """
    Works for:
        position, reflex -> 1D arrays
        position, reflex -> 2D arrays (n_scans, N_points)
    """

    def _single_trace(pos, ref):

        def derivatives(x, y):
            h = x[1] - x[0]
            dy = np.zeros(len(x) - 2)
            ddy = np.zeros(len(x) - 2)
            newpos = np.zeros(len(x) - 2)

            for i in range(1, len(x) - 1):
                dy[i - 1] = (y[i + 1] - y[i - 1]) / (2 * h)
                ddy[i - 1] = (y[i + 1] - 2 * y[i] + y[i - 1]) / (h * h)
                newpos[i - 1] = x[i]

            return newpos, dy, ddy

        # ---- interpolation ----
        tck = interpolate.splrep(pos, ref, s=0)
        position_interp = np.arange(pos[0], pos[-1], (pos[1] - pos[0]) / 10.0)
        reflex_interp = interpolate.splev(position_interp, tck, der=0)

        newpos, dy, d2y = derivatives(position_interp, reflex_interp)

        min_index = np.argmin(dy)
        max_index = np.argmax(dy)

        i = 1
        while np.abs(max_index - min_index) < 150:
            if min_index < len(dy) // 2:
                min_index = np.argsort(dy)[i]
            elif max_index > len(dy) // 2:
                max_index = np.argsort(dy)[-i]
            i += 1
            if i > 100:
                break

        x1 = newpos[min_index]
        x2 = newpos[max_index]
        width = x2 - x1

        if width < 0:
            x1, x2 = x2, x1
            width = -width

        x1 = round(x1, 2)
        x2 = round(x2, 2)
        width = round(width, 2)

        return [x1, x2], width

    # -------- 1D case --------
    if position.ndim == 1:
        return _single_trace(position, reflex)

    # -------- 2D case --------
    l_edges_all = []
    r_edges_all = []
    widths_all = []

    for i in range(position.shape[0]):
        edges, width = _single_trace(position[i, :][1:-2], reflex[i, :][1:-2])
        l_edges_all.append(edges[0])
        r_edges_all.append(edges[1])
        widths_all.append(width)

    return np.array([l_edges_all,r_edges_all]), np.array(widths_all)


def get_edges_2D(D,I_ch = 'averagein2value'):
    
    """
    if self.calc_info.dchanneltype == '2LI+avgSingle':
        x,I,I2,I_BD_avg6,l1x1,l1y1,l1x2,l1y2,l2x1,l2y1,l2x2,l2y2,relaypos,H=self.data
        
    elif len(self.data) == 10 :
        x,I,I2,IBD,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
    else:
        x,I,I2,l1x1,l1y1,l1x2,l1y2,relaypos,H=self.data
    """ 
    
    
    reflec = D[I_ch]['pos']      # shape (n, N)
    x = D['x']['pos']            # shape (n, N)
    
    mask = np.argsort(x, axis=1)
    
    xsort = np.take_along_axis(x, mask, axis=1)
    reflecsort = np.take_along_axis(reflec, mask, axis=1)
    fig = plt.figure()   
    edges, width = find_edges_width2D(xsort,reflecsort)
    print('Edges',edges, '\n')
    dev_center = (edges[0]+edges[1])/2
    return edges, dev_center

def load_2Ddata_Tobi(logfilenameShort,ignorLines=[],ch_x='actuator_1_1',ch_y='actuator_2_1',setup=1 ,return_dict = False):
    
    res = get_channels(logfilenameShort)
    x,y,var,ignorLines,relay = load_channel(logfilenameShort,ignorLines,ch_x,ch_y,ch_var='data_01',setup = 1)
    print(relay)
    v = np.zeros([var.shape[0],var.shape[1],var.shape[2],len(res)-3])
    my_dict= {}
    for key in res.keys():
        name = res[key]

        namelements = name.split("/")

        if len(namelements) < 2:
            break
        dict_name = namelements[-2] + namelements[-1]
        
        
        i = 0
        if 'data' in key:
            # load x and y (they do not change) as well as var and ignorLines (ignorLines appends more values eventually)
            x,y,data,ignor_Lines,relay = load_channel(logfilenameShort,ignorLines,ch_x,ch_y,ch_var=key,setup = 1)
            v[:,:,:,i] = data
            my_dict[dict_name] = data
            i += 1
            if key == 'data_01':
                my_dict['x'] = x
                my_dict['y'] = y
                my_dict['relay'] = relay
        if return_dict == True:
            
            result = my_dict
            
        else:
            result = x,y,v,relay
            
        
    return result

from matplotlib.gridspec import GridSpec
def plot_2D_compare_auto(data, pltkeys, px_x, px_y, fs=14, cmap_first='magma',figname = None, savepath = None, ifMonitor = False):
    """
    Plot 2D comparison with automatic aspect ratio handling.
    Better for wide or tall images.
    """
    if ifMonitor:
        key_labels = {
            'theta_pos':  r"$R^{1\omega}(R^+)$ [a.u.]",
            'theta_neg':  r"$R^{1\omega}(R^-)$ [a.u.]",
            'theta_sum':  r"$R^{1\omega}(sum)$ [a.u.]",
            'theta_diff': r"$r^{1\omega}(diff)$ [a.u.]",
            'theta2_pos': r"$R^{2\omega}(R^+)$ [a.u.]",
            'theta2_neg': r"$R^{2\omega}(R^-)$ [a.u.]",
            'theta2_sum': r"$R^{2\omega}(sum)$ [a.u.]",
            'theta2_diff':r"$R^{2\omega}(diff)$ [a.u.]",
            'R':          r"$R$ [a.u.]",
            'R2w':        r"$R^{2\omega}$ [a.u.]",
            'TR':         r"$R^{2\omega} / R$ [a.u.]",
            'averagein2value': r"$R$ [a.u.]"
        }
    else:
        key_labels = {
            'theta_pos':  r"$\theta^{1\omega}(R^+)$ [nrad]",
            'theta_neg':  r"$\theta^{1\omega}(R^-)$ [nrad]",
            'theta_sum':  r"$\theta^{1\omega}(sum)$ [nrad]",
            'theta_diff': r"$\theta^{1\omega}(diff)$ [nrad]",
            'theta2_pos': r"$\theta^{2\omega}(R^+)$ [nrad]",
            'theta2_neg': r"$\theta^{2\omega}(R^-)$ [nrad]",
            'theta2_sum': r"$\theta^{2\omega}(sum)$ [nrad]",
            'theta2_diff':r"$\theta^{2\omega}(diff)$ [nrad]",
            'R':          r"$R$ [a.u.]",
            'R2w':        r"$R^{2\omega}$ [a.u.]",
            'TR':         r"$R^{2\omega} / R$ [a.u.]",
            'averagein2value': r"$R$ [a.u.]"
        }
    
    if isinstance(pltkeys, str):
        pltkeys = [pltkeys]
    
    nplots = len(pltkeys)
    sample = data[pltkeys[0]]
    ny, nx = sample.shape[:2]
    if 'theta' in pltkeys:
        nz = 2
    else:
        nz = 1
    
    # Physical aspect ratio
    data_ratio = (ny * px_y) / (nx * px_x)
    
    # Constrain the subplot dimensions
    max_plot_width = 8
    max_plot_height = 4
    
    if data_ratio > 1:  # Tall image
        plot_height = min(max_plot_height, max_plot_width * data_ratio)
        plot_width = plot_height / data_ratio
    else:  # Wide image
        plot_width = max_plot_width
        plot_height = plot_width * data_ratio
    
    # Add margins for labels
    fig_width = plot_width + 2  # Extra space for colorbar and margins
    fig_height = plot_height * nplots + 1.5 * nplots + 1  # Extra for titles and spacing
    
    fig = plt.figure(figsize=(fig_width, fig_height), dpi=300)
    
    gs = GridSpec(nplots, 2, figure=fig,
                  width_ratios=[plot_width, 0.3],
                  hspace=0.2,
                  wspace=0.1,
                  left=0.12,
                  right=0.92,
                  top=0.96,
                  bottom=0.06)
    
    xarr = np.round(np.mean(data['x'],axis = 0),1)
    yarr = np.round(np.mean(data['y'],axis = 1),1)
    for i, key in enumerate(pltkeys):
        ax  = fig.add_subplot(gs[i, 0])
        cax = fig.add_subplot(gs[i, 1])
        
        arr = data[key]
        cmap = cmap_first if i == 0 else 'viridis'
        if nz > 1:
            extent = [xarr[0], xarr[-1], yarr[-1], yarr[0]]
        else:
            extent = [0, nx * px_x, 0, ny * px_y]
        if 'theta' in key:
            try:
                norm = mcolors.TwoSlopeNorm(
                    vmin=arr.min(),
                    vcenter=0,
                    vmax=arr.max()
                )
            except Exception:
                norm = None
        else:
            norm = None
        im = ax.imshow(
            np.flip(arr,axis = 0),
            origin="lower",
            extent= extent,
            aspect='equal',
            cmap=cmap, norm = norm
        )
        
        if i == nplots - 1:
            ax.set_xlabel(r"x [$\mu$m]", fontsize=fs)
        if nz > 1:
            ax.set_xticks(np.linspace(xarr[0], xarr[-1], 5))  # 5 ticks along x
            ax.set_yticks(np.linspace(yarr[0], yarr[-1], 5))  # 5 ticks along y
        ax.set_ylabel(r"y [$\mu$m]", fontsize=fs)
        ax.set_title(key_labels.get(key, key), fontsize=fs, pad=12)
        ax.tick_params(labelsize=fs-2)
        
        cbar = fig.colorbar(im, cax=cax)
        cbar.ax.tick_params(labelsize=fs-2)
    
    px_x_nm = px_x * 1000
    px_y_nm = px_y * 1000
    fig.suptitle( 
        f"%s\nPixel size: {px_x_nm:.0f} nm × {px_y_nm:.0f} nm"%figname,
        fontsize=fs + 2,
        y=0.99+1/(30*nz)
    )
    
    plt.tight_layout(rect=[0, 0, 1, 0.98])  # Leave space for suptitle
    if savepath:
        plt.savefig(savepath+ '\\'+plotname+'.png',pad_inches = 0.1)
        plt.savefig(savepath+ '\\'+plotname+'.eps',pad_inches = 0.1)
    plt.show()


def show_2Ddata(result,data_type,scancut = 0,scanaxis = 'x'):
    scans,scan_len,num_scans = np.shape(result[data_type])
    colors = plt.cm.magma(np.linspace(0.1, 0.8, num_scans))
    if type(scancut) != int:
        i = 0
        print('Using only measurement 1 and all linescans')
        for cut in scancut:
            plt.plot(result[scanaxis][cut,:,i],result[data_type][cut,:,i],color = colors[i], label = i)
        
    for i in range(num_scans):
        plt.plot(result[scanaxis][scancut,:,i],result[data_type][scancut,:,i],color = colors[i], label = i)
    plt.legend(fontsize = 8)
    plt.xlabel(scanaxis)
    plt.ylabel(data_type)
    plt.grid()
    plt.show()
"""
def data_calculation2D(data,relay,median=False,normalization=False,ignorLines = [],):
    # generate the logfilename
    
    
    
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
  """      
def load_2Ddata_singlescan(logfilenameShort,ignorLines=[],ch_x='actuator_1_1',ch_y='actuator_2_1',setup=1 ,return_dict = False):
    
    res = get_channels(logfilenameShort)
    x,y,var,ignorLines,relay = load_channel(logfilenameShort,ignorLines,ch_x,ch_y,ch_var='data_01',setup = 1)
    print(relay)
    v = np.zeros([var.shape[0],var.shape[1],var.shape[2],len(res)-3])
    my_dict= {}
    for key in res.keys():
        name = res[key]

        namelements = name.split("/")

        if len(namelements) < 2:
            break
        dict_name = namelements[-2] + namelements[-1]
        
        
        i = 0
        if 'data' in key:
            # load x and y (they do not change) as well as var and ignorLines (ignorLines appends more values eventually)
            x,y,data,ignor_Lines,relay = load_channel(logfilenameShort,ignorLines,ch_x,ch_y,ch_var=key,setup = 1)
            v[:,:,:,i] = data
            my_dict[dict_name] = data
            i += 1
            if key == 'data_01':
                my_dict['x'] = x
                my_dict['y'] = y
                my_dict['relay'] = relay
        if return_dict == True:
            
            result = my_dict
        else:
            result = x,y,v,relay
    return result
            
def load_data(logfilenameShort,ignorLines=[],two_lockins=True,ch_x='actuator_1_1',ch_y='actuator_2_1',magnet=False,SNE=False):
    """load all data channels using the load_data script
    The different cases are for different scan definitions in the Scanserver software, where channels have changed and thus have to be read out in a different way.
    the first two ifs are the old format, where x1,x2,y1,y2 of the first lockin are loaded first, followed by the relais and x1,x2,y1,y2 of the second lockin. Finally, the 
    intensity and balanced diode DC signal is loaded."""
    if two_lockins == True: # use the following order: bal x,bal y,bal_2x,bal_2y,relais, int x,int_y,2x,2y,int,bal
        channel_array = ['data_01','data_03','data_02','data_04','data_05','data_06','data_08','data_07','data_09','data_10','data_11']

    else: 
        channel_array = ['data_01','data_03','data_02','data_04','data_05','data_06','data_07']
    if magnet: 
        if two_lockins == True: # use the following order: bal x,bal y,2x,2y,relais, int x,y,2x,2y,int,bal, Hall probe
            channel_array = ['data_01','data_03','data_02','data_04','data_05','data_06','data_08','data_07','data_09','data_10','data_11','data_12']
        else: 
            channel_array = ['data_01','data_03','data_02','data_04','data_05','data_06','data_07','data_08']
    """ newer scan definitons in Scanserver:
    we first load the balanced and intensity DC signal, followed by the first lockin x1,y1,x2,y2, the second lockin second harmonic x2,y2, the relais and then the first harmonic of the second (intensity) lockin"""
    if SNE:
        if two_lockins == True: # use the following order: bal,int,bal_x,bal_y,bal_2x,bal_2y,int_2x,int_2y,relais,int_x,int_y
            channel_array = ['data_01','data_02','data_03','data_04','data_05','data_06','data_07','data_08','data_12','data_09','data_10'] ## new measurements
            #channel_array = ['data_01','data_02','data_03','data_04','data_05','data_06','data_07','data_08','data_09','data_10','data_11'] ## old measurements
        else:
            channel_array = ['data_01','data_02','data_03','data_04','data_05','data_06','data_07','data_08']
    if SNE and magnet: # use the following order: bal,int,bal_x,bal_y,bal_2x,bal_2y,int_2x,int_2y,relais,int_x,int_y, Hall probe
        channel_array = ['data_01','data_02','data_03','data_04','data_05','data_06','data_07','data_08','data_09','data_10','data_11','data_12']
    """now load all data by the given channel arrays defined above."""
    x,y,var,ignorLines = load_channel(logfilenameShort,ignorLines,ch_x,ch_y,ch_var=channel_array[0])
    v = np.zeros([var.shape[0],var.shape[1],var.shape[2],len(channel_array)])
    for i in range(0,len(channel_array)):
        # load x and y (they do not change) as well as var and ignorLines (ignorLines appends more values eventually)
        x,y,v[:,:,:,i],ignorLines = load_channel(logfilenameShort,ignorLines,ch_x,ch_y,ch_var=channel_array[i])
    #Remove all the ignorLines entry from the array:
    Counter = 0
    for i in range(0,len(v[0,0,:,0])):
        if i in ignorLines:
            continue
        else:
            v[:,:,Counter,:] = v[:,:,i,:]
            Counter += 1
    v = v[:,:,0:Counter,:]
    #stack all the variables on top of each other:
    result = x,y,v
    return result

def drift_correction_2D(x,y,v,ch_int=10,comp_nr=0,plotting=True,debug=False,spx = [12,12]):
    """Corrects for drift in different images by taking the intensity channel and compares different 
    images with each other, Does linewise comparison to first image"""
    # channel number in array is one lower...
    ch_int = ch_int-1 
    nr_scans = len(v[0,0,:,ch_int])
    vec_nr_scans = np.linspace(1,nr_scans,nr_scans)
    # spx = [12,12] # maximum shift in x and y direction from center, should be integer value
    # define comparison image, first scan is standard and cut the outer 4 pixel at each side (e.g. roughly 1mum dist.)
    v0 = v[spx[0]:-spx[0],spx[1]:-spx[1],comp_nr,ch_int]
    # define a matrix for each shift and write a second one with the respective shifts in it
    intensity_matrix = np.zeros([spx[0]*2,spx[1]*2,nr_scans])
    shifts = np.zeros([nr_scans,2])
    # go through all images and shift the images with respect to each other
    for i in range(1,nr_scans):
        for j in range(0,spx[0]*2): #x-coordinate
            for k in range(0,spx[1]*2): # y-coordinate
                intensity_matrix[j,k,i] = np.mean(np.mean(abs(v[j:-((spx[0]*2)-j),k:-((spx[1]*2)-k),i,ch_int]-v0),axis=1))
        # pictures for debugging:
        if debug:
        	plt.imshow(intensity_matrix[:,:,i],interpolation='none')
        	plt.colorbar()
        	plt.show()
        shifts[i,:] = np.argwhere(intensity_matrix[:,:,i] == np.min(intensity_matrix[:,:,i]))
    # symmetrize the shifts around the zero value:
    for i in range(0,nr_scans):
        if i == comp_nr:
            continue
        shifts[i,0] -= spx[0]
        shifts[i,1] -= spx[1]
    boarders = [np.min(shifts[:,0]),np.max(shifts[:,0]),np.min(shifts[:,1]),np.max(shifts[:,1])]
    minmax = [int((boarders[1]-boarders[0])),int((boarders[3]-boarders[2]))]
    #define new matrices for x,y,v with reduced dimensionality
    xnew = np.zeros([len(x[:,0,0])-minmax[0],len(x[0,:,0])-minmax[1],len(x[0,0,:])])
    ynew = np.zeros([len(y[:,0,0])-minmax[0],len(y[0,:,0])-minmax[1],len(y[0,0,:])])
    vnew = np.zeros([len(v[:,0,0,0])-minmax[0],len(v[0,:,0,0])-minmax[1],len(v[0,0,:,0]),len(v[0,0,0,:])])
    for i in range(0,nr_scans):
        for j in range(0,len(x[:,0,0])-minmax[0]):
            for k in range(0,len(x[0,:,0])-minmax[1]):
                xnew[j,k,i] = x[int(abs(boarders[0])+j+shifts[i,0]),int(abs(boarders[2])+k+shifts[i,1]),i]
                ynew[j,k,i] = y[int(abs(boarders[0])+j+shifts[i,0]),int(abs(boarders[2])+k+shifts[i,1]),i]
                vnew[j,k,i,:] = v[int(abs(boarders[0])+j+shifts[i,0]),int(abs(boarders[2])+k+shifts[i,1]),i,:]
    if plotting:
        plt.figure(figsize=(8,6))
        plt.plot(vec_nr_scans,shifts[:,0],'-bx',label='x shift [px]')
        plt.plot(vec_nr_scans,shifts[:,1],'-ro',label='y shift [px]')
        plt.ylim([-spx[0],spx[0]])
        plt.xlabel('Nr. of scans')
        plt.ylabel('drift [px]')
        plt.legend()
        plt.show()
    x = xnew
    y = ynew
    v = vnew
    return x, y, v

def drift_correction_2D_Tobi_new(data,line0_i,scan0_i = 4,plotting=False,R_shift ='averagein3value' ):
    """
    

    Parameters
    ----------
    data : dict
        containing all data in dictionary and 3D array: [numy] x [numx] x [numscans]
    line0_i : int
        line0_i is the linescan number of scan scan0_i, which defines with its center coordinate the 0-point to shift all the other data to
    scan0_i : int
        Which number of scan to use. From this scan line0_i is used to center all data around. The default is 4.
    plotting : TYPE, optional
        DESCRIPTION. The default is True.
        
    Raises
    ------
    Exception
        DESCRIPTION.

    Returns
    -------
    data_new : dict
        containing all NEW data in dictionary and 3D array: [numy] x [NEWnumx] x [numscans]: 
        Mind you, that the linescan-data was cut so that the data same e.g. edge at any region and scan is always (more or less) at the same index (to allow for averaging and sum/diff)

    """
    
    try:
        if R_shift in data.keys():
            I = np.copy(data[R_shift])
            intkey = R_shift
            print('using', R_shift)
        else:
            if 'averagein2value' in data.keys():
                I = np.copy(data['averagein2value'])
                intkey = 'averagein2value'
                print('using averagein2')
            elif 'averagein3value' in data.keys():
                I =  np.copy(data['averagein3value'])
                intkey = 'averagein3value'
                print('using averagein3')
            elif 'averagein6value' in data.keys():
                I =  np.copy(data['averagein6value'])
                intkey = 'averagein6value'
                print('using averagein6')
            else:
                raise Exception('No Intensity measurement provided!')
    except: Exception('Intensity measurement found!')
    x =  np.copy(data['x'])
    y =  np.copy(data['y'])
    xlines,ylines,nscans = np.shape(I)    
    y_scan= True
    if y_scan:
        nlines = xlines
        mid_ind = int(ylines/2)
        num_points = ylines
        dx = np.round(data['x'][0,1,0] -data['x'][0,0,0],2)
    else:
        nlines = ylines
        mid_ind = int(xlines/2)
        num_points = xlines

    # Get 0 position
    grad = np.abs(np.gradient(I[line0_i,1:-2,scan0_i]))
    right0 = (np.mean(np.where(grad[0:mid_ind] > max(grad[0:mid_ind]/2))[0]))+1
    left0 = (np.mean(np.where(grad[mid_ind::] > max(grad[mid_ind::]/2))[0]+mid_ind+1))
    midins = np.zeros((nlines,nscans))
    max_shift = np.zeros(nscans)
    min_shift = np.zeros(nscans)
    for scan in range(nscans):
        rightin = np.zeros(nlines)
        leftin = np.zeros(nlines)
        for line in range(nlines):    
            grad = np.abs(np.gradient(I[line,1:-2,scan]))
            leftind = (np.mean(np.where(grad[0:mid_ind] > max(grad[0:mid_ind]/2))[0]))+1
            rightind = (np.mean(np.where(grad[mid_ind::] > max(grad[mid_ind::]/2))[0]+mid_ind))+1
            #plt.plot(x[line,:,0],I[line,:,0])
            
            
            rightin[line] = (rightind)
            leftin[line] = (leftind)

            if plotting:
                plt.plot(I[line,:,scan],label = line + scan)

                
        
        rightin = rightin-right0
        leftin = leftin-left0
        midin = np.round(np.mean([leftin,rightin],axis = 0))
        midin = np.array(midin,dtype = int)
        midins[:,scan] = midin #[lines:scans]
        #print(leftin,rightin)
        """
        fig = plt.figure(figsize = [8,8])
        plt.scatter(x[:,:,0], y[:,:,0], c=I[:,:,0],marker = 's', s=600, cmap='viridis')
        plt.colorbar(label='Z value')
        plt.xlabel('X')
        plt.ylabel('Y')
        plt.title('Not shifted')
        plt.show()"""
        max_shift[scan] = max(midin)
        #print(midin)
        min_shift[scan] = min(midin)
        #print(scan)
        #Shifting x coordinates
        for line in range(nlines):
            x[line,:,scan] = x[line,:,scan]- midin[line]*dx
            #plt.plot(x[line,:,0], I[line,:,0])
        """
        fig = plt.figure(figsize = [8,8])
        plt.scatter(x[:,:,scan], y[:,:,scan], c=I[:,:,scan],marker = 's', s=600, cmap='viridis')
        plt.ylim([-4,4])
        plt.xlim([-8,8])
        plt.colorbar(label='Z value')
        plt.xlabel('X')
        plt.ylabel('Y')
        plt.title('shifted scan %s' %scan)
        plt.show()
        """
    if plotting:
        plt.xlim([0,num_points])
        plt.legend(fontsize = 4)
        plt.title('raw lines')
        plt.show()
    maxli = -min(min_shift)
    maxri = -max(max_shift)-1
    keys = list(data.keys())

    keys.remove('relay')

    shape = (nlines, int(ylines + maxri -maxli), nscans) 

    ndata = {key: np.zeros(shape) for key in keys}
    #Cutting the data to allow for summation/difference of multiple scans
    for scan in range(nscans):
        for line in range(nlines):
            shift = midins[line,scan]
            
            #print(int(maxli+shift),ylines + int(maxri+shift), ylines + int(maxri+shift) - int(maxli+shift))
            for key in keys:
                
                ndata[key][line,:,scan] = data[key][line,int(maxli+shift):int(maxri+shift),scan]
                
            if plotting:
                plt.plot(ndata[R_shift][line,:,scan],label = line + scan)
                #plt.plot(I[line,int(maxli+shift):int(maxri+shift),scan])
                #ycut = y[:,max_shift:min_shift,0]
                #Icut = I[:,max_shift:min_shift,0]
    if plotting:
        plt.xlim([0,num_points])
        plt.legend(fontsize = 4)
        plt.title('shifted lines')
        plt.show()        
    ndata['relay'] = data['relay']
    print('Shifted and cut data from length %s to %s' %(ylines,ylines + int(maxri+shift) - int(maxli+shift)) )
    return ndata



def simple_binner(x,y,v,var_channel=5,average='mean',change_I_dir=False):
    """Averages over all the measurements using the first entry of the relais channel. Choose between mean and median averaging by entering mean or median"""
    #define all the variables
    firstscan=True
    n = len(v[0,0,:,0]) # number of total measurements
    n_pos = 0
    n_neg = 0
    #calculate the number of neeeded slots in each array. 
    for i in range(0,n):
        p = v[0,0,i,var_channel-1]
        if (p % 2 == 0) or (p <= 0):
            n_neg += 1
        else:
            n_pos += 1
    # redefine the variable arrays depending on the length
    var_neg = np.zeros_like(v[:,:,0:n_neg,:])
    var_pos = np.zeros_like(v[:,:,0:n_pos,:])
    # fill the arrays:
    n_pos = 0
    n_neg = 0
    #calculate the number of neeeded slots in each array. We define the current along the -x-direction, meaning that magnetic moments in Pt are along the +y-direction.
    for i in range(0,n):
        p = v[0,0,i,var_channel-1]
        if (p % 2 == 0) or (p <= 0):
            var_neg[:,:,n_neg,:] = v[:,:,i,:]
            n_neg += 1
        else:
            var_pos[:,:,n_pos,:] = v[:,:,i,:]
            n_pos += 1
    # do the averaging depending on what method has been chosen
    if average == 'mean':
        avg_neg = np.mean(var_neg,axis=2)
        avg_pos = np.mean(var_pos,axis=2)
    elif average == 'median':
        avg_neg = np.median(var_neg,axis=2)
        avg_pos = np.median(var_pos,axis=2)
    else:
        print('You did not choose an appropriate variable for averaging: chose either \'mean\' or \'median\'')
    #finally, do sum and difference for the arrays
    if change_I_dir:
        summation = (avg_neg+avg_pos)/2
        difference = (avg_pos-avg_neg)/2
    else:    
        summation = (avg_neg+avg_pos)/2
        difference = (avg_neg-avg_pos)/2
    return summation, difference

def analyse_relais(logfilenameShort,ignorLines=[],two_lockins=True,ch_x='actuator_1_1',ch_y='actuator_2_1',plotting=True,drift=True,debug=False,size1=12,size2=12,change_I_dir=False,mirror=False,SNE=False):
    x,y,v = load_data(logfilenameShort,ignorLines=ignorLines,ch_x=ch_x,ch_y=ch_y,SNE=SNE)
    if drift:
        x,y,v = drift_correction_2D(x,y,v,plotting=plotting,debug=debug)
    s,d = simple_binner(x,y,v,change_I_dir=change_I_dir)
    if two_lockins:
        titlestring = ['balanced x ','balanced y ','balanced 2x ','balanced 2y ','relais ','intensity x ','intensity y ','intensity 2x ','intensity 2y ','intensity DC ','balanced diode DC ','measured field ']
    else:
        titlestring = ['balanced x ','balanced y ','balanced 2x ','balanced 2y ','relais ','intensity DC ','balanced diode DC ','measured field ']
    
    if plotting:
        for i in range(0,len(s[0,0,:])):
            plot_image(s[:,:,i],tit=titlestring[i]+'sum',size1=size1,size2=size2, mirror=mirror)
            plot_image(d[:,:,i],tit=titlestring[i]+'difference',size1=size1,size2=size2,mirror=mirror)
    return s,d,x,y,v

def analyse_magnet(logfilenameShort,ignorLines=[],two_lockins=True,ch_x='actuator_1_1',ch_y='actuator_2_1',plotting=True,drift=True,debug=False):
    x,y,v = load_data(logfilenameShort,ignorLines=ignorLines,two_lockins=two_lockins,magnet=True,ch_x=ch_x,ch_y=ch_y)
    if drift:
        x,y,v = drift_correction_2D(x,y,v,plotting=plotting,debug=debug)
    s,d = simple_binner(x,y,v)
    if two_lockins:
        titlestring = ['balanced x ','balanced y ','balanced 2x ','balanced 2y ','relais ','intensity x ','intensity y ','intensity 2x ','intensity 2y ','intensity DC ','balanced diode DC ','measured field ']
    else:
        titlestring = ['balanced x ','balanced y ','balanced 2x ','balanced 2y ','relais ','intensity DC ','balanced diode DC ','measured field ']
    
    if plotting:
        for i in range(0,len(s[0,0,:])):
            plot_image_extent(y,np.rot90(s[:,:,11]),np.rot90(s[:,:,i]),tit=titlestring[i]+'sum')
            plot_image_extent(y,np.rot90(s[:,:,11]),np.rot90(d[:,:,i]),tit=titlestring[i]+'difference')
    return s,d,x,y,v

def intensity_binner(s,d,ch_bin=10,method='summation',int_val=[0.9,1.0]):
    """Binner saves ones in a matrix where some given channel ch_bin with given method (summation or difference)
    lies within the interval int_val [i0,i1]
    this script can be used to find the structure between the intensity intervall or for example the gold contacts of transparent samples in a lower intensity intervall depending on the reflectivity"""
    ch_bin =ch_bin-1
    if method == 'difference':
        res_bin = d[:,:,ch_bin]
    elif method == 'summation':
        res_bin = s[:,:,ch_bin]
    else:
        print('something went wrong with choosing the summation or difference channel on which to bin...')
    matrix = np.zeros_like(res_bin)
    limit = np.max(abs(res_bin[:,:]))
    for i in range(0,len(s[:,0,0])):
        for j in range(0,len(s[0,:,0])):
            if abs(res_bin[i,j])>= int_val[0]*limit and abs(res_bin[i,j]) < int_val[1]*limit:
                matrix[i,j] = 1
    return matrix

def data_to_txt(dir,logfilenameShort,result):
    """takes the data from the analyse_relais script and generates corresponding text files
    the script can be used to export processed data from python into txt files which again can be loaded by other programs like origin or matlab."""
    name = logfilenameShort.replace(".txt", "")
    s,d,x,y,v = result
    directory = dir+name+'\\'
    if not os.path.exists(directory):
        os.makedirs(directory)
    "ignore the raw data v for now and only save the sums and differences as well as x and y"
    for i in range(0,len(s[0,0,:])):
        np.savetxt(directory+'s'+str(i)+'.txt',s[:,:,i])
        np.savetxt(directory+'d'+str(i)+'.txt',d[:,:,i])
    np.savetxt(directory+'x'+'.txt',x[:,:,0])
    np.savetxt(directory+'y'+'.txt',y[:,:,0])
    return

def larmor(B,g = -2.00231930436182):
    """ calculates the Larmor frequency for a magnetic field B (in T) """
    mub = 9.274009994*1e-24
    hbar = 1.054571800*1e-34
    omega_L = np.zeros_like(B)
    omega_L = g*mub*B/hbar
    return omega_L

def Hanle(B,tau,g=-2.00231930436182): # this has the wrong sign... shoud be - omega...
    """calculates the symmetric Hanle effect"""
    omega_L = larmor(B,g)
    Kerr_angle = omega_L*tau/((omega_L*tau)**2+1)
    return Kerr_angle

def Hanle_2(B,tau,g=-2.00231930436182):
    """calculates the antisymmetric Hanle effect"""
    omega_L = larmor(B,g)
    Kerr_angle = 1/((omega_L*tau)**2+1)
    return Kerr_angle

def load_channel2(logfilenameShort,ignorLines=[] ,ch_x='actuator_1_1',ch_y='actuator_2_1',ch_var='data_10'):
    """extract the data from a txt file with j 2D scans and stack it in a matrix'
    'ch_x gives the data channel which is continuously scanned (e.g. line scan)'
    'ch_y is the data channel which changes after a total scan of x and is made into a 2D array'
    'ch_var is the variable channel
    SAME SCRIPT AS load_channel BUT WITH ADJUSTED DATA PATH. SEE ORIGINAL SCRIPT FOR MORE COMMMENTS"""
    filename = '../Data/Scanlists_S2/'+logfilenameShort
    datapathprefix = '../Data/Data_S2/'
    f = open(filename,'r') 
    lineCounter= 0
    file=[] #create an empty array 
    #creates an array with all the files
    for line in f: 
        lineCounter += 1
        lineElements = line.split('\t')
        filename = lineElements[0].split('/')[-1]
        datapath = datapathprefix + lineElements[0].split('/')[-2] + '/'
        file.append (datapath+filename)
    files = np.append([],file)
    #print(files) #DEBUG
    'stack the given datachannel into different arrays'
    firstscan = True
    for j in range (0,lineCounter):
        try:
            #print(j) 'just to check if it is working properly'
            if j in ignorLines:
                continue
            if firstscan:
                var = read_data(files[j], ch_var)
                var = np.zeros([var.shape[0],var.shape[1],lineCounter])
                x = np.zeros_like(var)
                y = np.zeros_like(var)
                firstscan=False
            var[:,:,j] = read_data(files[j], ch_var)
            x[:,:,j] = read_data(files[j], ch_x) #x position
            for i in range(0,len(x[0,:,0])): # y position has to be shaped into 2D array
                y[:,i,j] = read_data(files[j], ch_y) # y position
                # interpolate all NaNs in the y direction
                nans, z = nan_helper(var[:,i,j])
                var[nans,i,j] = np.interp(z(nans), z(~nans), var[~nans,i,j])
        except RuntimeError:
                print(ch_var+ ' line '+ str(j)+ ' gives RuntimeError, consider ignoring it')
                np.append(ignorLines,j)
                continue
    return x,y,var,ignorLines

def load_data2(logfilenameShort,ignorLines=[],ch_x='actuator_1_1',ch_y='actuator_2_1',setup=1):
    """load all data channels using the load_data script
    data channels are bal,int,x1,y1,x2,y2,int_x2,int_y2,relais
    this script was written while the lockin reading out the intensity diode did only have one channel, reading out the second harmonic signal."""
    channel_array = ['data_01','data_02','data_03','data_04','data_05','data_06','data_07','data_08','data_09']
    if setup==2: # that means that the additional lock-in channels were available.
        channel_array = ['data_01','data_02','data_03','data_04','data_05','data_06','data_07','data_08','data_09','data_10','data_11']
    x,y,var,ignorLines = load_channel2(logfilenameShort,ignorLines,ch_x,ch_y,ch_var=channel_array[0])
    v = np.zeros([var.shape[0],var.shape[1],var.shape[2],len(channel_array)])
    for i in range(0,len(channel_array)):
        # load x and y (they do not change) as well as var and ignorLines (ignorLines appends more values eventually)
        x,y,v[:,:,:,i],ignorLines = load_channel2(logfilenameShort,ignorLines,ch_x,ch_y,ch_var=channel_array[i])
    #Remove all the ignorLines entry from the array:
    Counter = 0
    for i in range(0,len(v[0,0,:,0])):
        if i in ignorLines:
            continue
        else:
            v[:,:,Counter,:] = v[:,:,i,:]
            Counter += 1
    v = v[:,:,0:Counter,:]
    #stack all the variables on top of each other:
    result = x,y,v
    return result

def analyse_relais2(logfilenameShort,ignorLines=[],ch_x='actuator_1_1',ch_y='actuator_2_1',plotting=True,drift=True,debug=False,slope=1.0,setup=1):
    x,y,v = load_data2(logfilenameShort,ignorLines=ignorLines,ch_x=ch_x,ch_y=ch_y,setup=setup)
    if drift:
        x,y,v = drift_correction_2D(x,y,v,ch_int=1,plotting=plotting,debug=debug,spx = [3,3])
    s,d = simple_binner(x,y,v,var_channel=9)
    titlestring = ['balanced diode DC ','intensity DC ','balanced x ','balanced y ','balanced 2x ','balanced 2y ','intensity 2x ','intensity 2y ','relais ','intensity x','intensity y']
    if plotting:
        for i in range(0,len(s[0,0,:])-1):
            plot_image(s[:,:,i]/(slope)*np.pi/180.0*1e6,tit=titlestring[i]+'sum',color_label=r' $\theta_k [$mu$rad]$')
            plot_image(d[:,:,i]/(slope)*np.pi/180.0*1e6,tit=titlestring[i]+'difference',color_label=r'$\theta_k [$mu$rad]$')
    return x,y,v,s,d

def analyse_relais_SNE(logfilenameShort,ignorLines=[],ch_x='actuator_1_1',ch_y='actuator_2_1',plotting=True,drift=True,debug=False,slope=1.0,magnet=False,ch_bin=9):
    """script to read out the SNE data on the first setup."""
    x,y,v = load_data(logfilenameShort,ignorLines=ignorLines,ch_x=ch_x,ch_y=ch_y,SNE=True,two_lockins=True,magnet=magnet)
    if drift:
        x,y,v = drift_correction_2D(x,y,v,ch_int=1,plotting=plotting,debug=debug)
    s,d = simple_binner(x,y,v,var_channel=ch_bin)
    titlestring = ['balanced diode DC ','intensity DC ','balanced x ','balanced y ','balanced 2x ','balanced 2y ','intensity 2x ','intensity 2y ','relais ','intensity x ','intensity y ','magnetic field [mT] ', 'laser intensity']
    if plotting:
        for i in range(0,len(s[0,0,:])):
            plot_image(s[:,:,i]/(slope)*np.pi/180.0*1e6,tit=titlestring[i]+'sum',color_label=r' $\theta_k [$mu$rad]$')
            plot_image(d[:,:,i]/(slope)*np.pi/180.0*1e6,tit=titlestring[i]+'difference',color_label=r'$\theta_k [$mu$rad]$')
    return x,y,v,s,d


def analyse_relais_SNE_2(logfilenameShort,ignorLines=[],ch_x='actuator_1_1',ch_y='actuator_2_1',plotting=True,drift=True,debug=False,slope=1.0,magnet=False,ch_bin=7):
    """script to read out the SNE data on the second setup (only one LIA at the moment)."""
    x,y,v = load_data(logfilenameShort,ignorLines=ignorLines,ch_x=ch_x,ch_y=ch_y,SNE=True,two_lockins=False,magnet=magnet)
    if drift:
        x,y,v = drift_correction_2D(x,y,v,ch_int=1,plotting=plotting,debug=debug)
    s,d = simple_binner(x,y,v,var_channel=ch_bin)
    titlestring = ['balanced diode DC ','intensity DC ','balanced x ','balanced y ','balanced 2x ','balanced 2y ','relais ', 'laser intensity']
    if plotting:
        for i in range(0,len(s[0,0,:])):
            plot_image(s[:,:,i]/(slope)*np.pi/180.0*1e6,tit=titlestring[i]+'sum',color_label=r' $\theta_k [$mu$rad]$')
            plot_image(d[:,:,i]/(slope)*np.pi/180.0*1e6,tit=titlestring[i]+'difference',color_label=r'$\theta_k [$mu$rad]$')
    return x,y,v,s,d


def SNE_polar(polar_results,current,show_all=False,slope=1.0,CTR=1.0,filter_var=0,filter_var_T=0,cutoff=0.0,min_intensity=0.15,boundary=0,theta=69.0,theta2=0.0,mean=0.0):
    """this script takes a data set in the form of x,y,v,s,d (x position, y position, v: raw data, s: summation of the given data, d: difference of the data)
	and calculates the balanced diode signal (var) as well as the temperature (T).
	both signals are rotated by theta (for the balanced diode) and theta2 (for the intensity diode). The temperature is scaled by 1/CTR and the variable is multiplied by the slope (slope).
	furthermore, the current is appended to the output and the resulting temperature and spin maps can be shown by setting show_all=True. The script only considers signals that lie above a
	minimum intensity min_intensity (in V). The boundary variable cuts away all points at the boarder of the image. 
	The results are filtered for averaging, where we use a gaussian filter and consider pixels within a radius filter_var and filter_var_T for the balanced diode as well as the temperature.
	Regions of interest are defined as having a Kerr signal that is a factor cutoff smaller than the maximum of the absolute value of the given Kerr rotation.
    We finally save the results of the polar scans for both positive and negative Kerr rotation (see at bottom for list of variables that are saved)."""
    x,y,v,s,d = polar_results
    condition = np.zeros_like(s[:,:,1])
    for i in range(0,len(s[:,0,1])):
       for j in range(0,len(s[0,:,1])):
            if s[i,j,1] >= min_intensity:
                condition[i,j] = 1 
    theta = (theta)*np.pi/180.0
    theta2 = (theta2)*np.pi/180.0
    var = (np.cos(theta)*s[:,:,4]+np.sin(theta)*s[:,:,5])*slope
    var = var-np.mean(var)
    var = scipy.ndimage.filters.gaussian_filter(var, filter_var, mode='nearest')
    T  = (np.cos(theta2)*s[:,:,6]+np.sin(theta2)*s[:,:,7])/CTR/s[:,:,1]
    T = scipy.ndimage.filters.gaussian_filter(T, filter_var_T, mode='nearest')
    [gTx,gTy] = np.gradient(T) # gives the gradient in K/pixel
    for i in range(0,len(x[0,0,:])): # test if x or y is an empty array due to some measurements problems...
        if x[0,0,i] != 0 and y[0,0,i] != 0:
            j=i
            break;
    [gxx,gxy] = np.gradient(x[:,:,j])
    [gyx,gyy] = np.gradient(y[:,:,j])
    gTx = gTx/gxy
    gTy = gTy/gyx
    vecgT = np.sqrt(gTx**2+gTy**2)*condition
    thetaSNE = []
    nablaT = []
    b = np.max(np.max(np.abs(var)))
    consider = np.zeros_like(var)
    limit = cutoff*b
    for i in range(boundary,len(var[:,0])-boundary):
        for j in range(boundary,len(var[0,:])-boundary):
            if np.abs(var[i,j]*condition[i,j]) >= limit:
                consider[i,j] = 1
                thetaSNE = np.append(thetaSNE,var[i,j])
                nablaT = np.append(nablaT,vecgT[i,j])
    if show_all==True:
        plt.rc('xtick',labelsize=8)
        plt.rc('ytick',labelsize=8)
        fig = plt.figure(figsize=(10/2.54, 10/2.54))
        # plot the main image
        plt.imshow(var*consider,cmap='RdBu',vmin=-b,vmax=b)
        #plt.imshow(T,cmap='Blues')
        plt.colorbar()
        plt.contour(condition,colors='k')
        plt.savefig('spin_acc.eps') 
        plt.show()

        fig2 = plt.figure(figsize=(10/2.54, 10/2.54))
        # plot the main image
        plt.imshow(vecgT*consider,cmap='Blues')
        plt.colorbar()
        plt.contour(condition,colors='k')
        plt.savefig('reflect.eps')
        plt.show()
        
        fig3 = plt.figure(figsize=(10/2.54, 10/2.54))
        # plot the main image
        plt.imshow(T,cmap='Reds')
        plt.colorbar()
        plt.contour(condition,colors='k')
        plt.savefig('temp.eps')
        plt.show()
        

        
        
    print(var)
    
    # give other data points
    p = []
    pT = []
    n = []
    nT = []
    for j in range(0,len(var[:,0])):
        for i in range(0,len(var[0,:])):
            if var[j,i]*consider[j,i] >0:
                p = np.append(p,var[j,i])
                pT = np.append(pT,vecgT[j,i])
            elif  var[j,i]*consider[j,i] <0:
                n = np.append(n,var[j,i])
                nT = np.append(nT,vecgT[j,i])
    # save all the data: [current(0) nablaTmax_pos(1) nablaT_pos(2) nablaTstd_pos(3) SNEmax_pos(4) SNE_pos(5)
    #                     SNEstd_pos(6) nablaTmax_neg(7) nablaT_neg(8) nablaTstd_neg(9) SNEmax_neg(10) SNE_neg(11)
    #                     SNEstd_neg(12), SNE/nablaTstd_pos(13), SNE/nablaTstd_neg(14)]
    polar_results = [current,np.max(pT),np.mean(pT),np.std(pT),np.max(p),np.mean(p),np.std(p),np.max(nT),np.mean(nT),np.std(nT),np.min(n),np.mean(n),np.std(n),np.std(p/pT),np.std(n/nT),len(p),len(n)]
    return polar_results


def SNE_polar_histodata(polar_results,current,show_all=False,slope=1.0,CTR=1.0,filter_var=0,filter_var_T=0,cutoff=0.0,min_intensity=0.15,boundary=0,theta=69.0,theta2=0.0,mean=0.0):
    """this script takes a data set in the form of x,y,v,s,d (x position, y position, v: raw data, s: summation of the given data, d: difference of the data)
	and calculates the balanced diode signal (var) as well as the temperature (T).
	both signals are rotated by theta (for the balanced diode) and theta2 (for the intensity diode). The temperature is scaled by 1/CTR and the variable is multiplied by the slope (slope).
	furthermore, the current is appended to the output and the resulting temperature and spin maps can be shown by setting show_all=True. The script only considers signals that lie above a
	minimum intensity min_intensity (in V). The boundary variable cuts away all points at the boarder of the image. 
	The results are filtered for averaging, where we use a gaussian filter and consider pixels within a radius filter_var and filter_var_T for the balanced diode as well as the temperature.
	Regions of interest are defined as having a Kerr signal that is a factor cutoff smaller than the maximum of the absolute value of the given Kerr rotation.
    We finally save the results of the polar scans for both positive and negative Kerr rotation (see at bottom for list of variables that are saved)."""
    x,y,v,s,d = polar_results
    condition = np.zeros_like(s[:,:,1])
    for i in range(0,len(s[:,0,1])):
       for j in range(0,len(s[0,:,1])):
            if s[i,j,1] >= min_intensity:
                condition[i,j] = 1 
    theta = (theta)*np.pi/180.0
    theta2 = (theta2)*np.pi/180.0
    var = (np.cos(theta)*s[:,:,4]+np.sin(theta)*s[:,:,5])*slope
    var = var-np.mean(var)
    var = scipy.ndimage.filters.gaussian_filter(var, filter_var, mode='nearest')
    T  = (np.cos(theta2)*s[:,:,6]+np.sin(theta2)*s[:,:,7])/CTR/s[:,:,1]
    T = scipy.ndimage.filters.gaussian_filter(T, filter_var_T, mode='nearest')
    [gTx,gTy] = np.gradient(T) # gives the gradient in K/pixel
    for i in range(0,len(x[0,0,:])): # test if x or y is an empty array due to some measurements problems...
        if x[0,0,i] != 0 and y[0,0,i] != 0:
            j=i
            break;
    [gxx,gxy] = np.gradient(x[:,:,j])
    [gyx,gyy] = np.gradient(y[:,:,j])
    gTx = gTx/gxy
    gTy = gTy/gyx
    vecgT = np.sqrt(gTx**2+gTy**2)*condition
    thetaSNE = []
    nablaT = []
    b = np.max(np.max(np.abs(var)))
    consider = np.zeros_like(var)
    limit = cutoff*b
    for i in range(boundary,len(var[:,0])-boundary):
        for j in range(boundary,len(var[0,:])-boundary):
            if np.abs(var[i,j]*condition[i,j]) >= limit:
                consider[i,j] = 1
                thetaSNE = np.append(thetaSNE,var[i,j])
                nablaT = np.append(nablaT,vecgT[i,j])
    if show_all==True:
        plt.rc('xtick',labelsize=16)
        plt.rc('ytick',labelsize=16)
        fig = plt.figure(figsize=(12*1.5, 9.25*1.5))
        # plot the main image
        plt.imshow(var*consider,cmap='RdBu',vmin=-b,vmax=b)
        #plt.imshow(T,cmap='Blues')
        plt.colorbar()
        plt.contour(condition,colors='k')
        plt.show()
        fig2 = plt.figure(figsize=(12*1.5, 9.25*1.5))
        # plot the main image
        plt.imshow(vecgT*consider,cmap='Blues')
        plt.colorbar()
        #plt.contour(condition,colors='k')
        plt.show()
    # give other data points
    p = []
    pT = []
    n = []
    nT = []
    for j in range(0,len(var[:,0])):
        for i in range(0,len(var[0,:])):
            if var[j,i]*consider[j,i] >0:
                p = np.append(p,var[j,i])
                pT = np.append(pT,vecgT[j,i])
            elif  var[j,i]*consider[j,i] <0:
                n = np.append(n,var[j,i])
                nT = np.append(nT,vecgT[j,i])
    # save all the data: [current(0) nablaTmax_pos(1) nablaT_pos(2) nablaTstd_pos(3) SNEmax_pos(4) SNE_pos(5)
    #                     SNEstd_pos(6) nablaTmax_neg(7) nablaT_neg(8) nablaTstd_neg(9) SNEmax_neg(10) SNE_neg(11)
    #                     SNEstd_neg(12), SNE/nablaTstd_pos(13), SNE/nablaTstd_neg(14)]
    polar_results = [current,np.max(pT),np.mean(pT),np.std(pT),np.max(p),np.mean(p),np.std(p),np.max(nT),np.mean(nT),np.std(nT),np.min(n),np.mean(n),np.std(n),np.std(p/pT),np.std(n/nT),p,pT,n,nT,var*consider,vecgT*consider,consider,condition]
    return polar_results

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

def drift_correction_2D_edges(data, line0_i = 10, scan0_i=0, R_shift='averagein3value', plotting=False):
    """
    Drift correction using edge detection via find_edges_width2D on the reflectivity map.

    Parameters
    ----------
    data : dict
        Dictionary containing all data as 3D arrays [nlines x npoints x nscans].
    line0_i : int
        Line index from scan0_i whose center position defines the zero-shift reference.
    scan0_i : int
        Scan index used to define the reference edge center. Default is 0.
    R_shift : str
        Key in data to use as reflectivity signal for edge detection. Default is 'averagein3value'.
    plotting : bool
        If True, plots raw and shifted data overlays. Default is False.

    Returns
    -------
    ndata : dict
        Dictionary with all arrays cropped to the common valid overlap region after shifting.
        Shape: [nlines x NEW_npoints x nscans].
    """

    # ── Load intensity and spatial arrays ──────────────────────────────────────
    if R_shift in data:
        I = np.copy(data[R_shift])
    else:
        raise KeyError(f"Key '{R_shift}' not found in data. Available keys: {list(data.keys())}")

    x = np.copy(data['x'])
    nlines, npoints, nscans = I.shape
    dx = round(data['x'][0, 1, 0] - data['x'][0, 0, 0], 2)

# ── Find edge centers for every line in every scan ─────────────────────────
    midins = np.zeros((nlines, nscans))  # store physical x positions of edge centre

    for scan in range(nscans):
        I_scan   = I[:, :, scan]
        pos_scan = x[:, :, scan]

        edges, _ = find_edges_width2D(pos_scan, I_scan)  # edges: [2, nlines], in physical x
        left_edges  = edges[0]   # [nlines], physical x
        right_edges = edges[1]   # [nlines], physical x

        center_phys = (left_edges + right_edges) / 2.0   # physical x of edge centre
        print(scan,scan0_i)
        if scan == scan0_i:
            ref_center_phys = center_phys[line0_i]       # reference in physical x

        midins[:, scan] = center_phys  # store physical x centre

    # ── Compute per-line shifts in index units relative to physical reference ──
    shifts = np.zeros((nlines, nscans), dtype=int)
    for scan in range(nscans):
        # How far (in physical units) each line's centre is from the reference
        phys_shift = ref_center_phys - midins[:, scan]
        # Convert to nearest integer number of indices
        shifts[:, scan] = np.round(phys_shift / dx).astype(int)

    # ── Determine the common valid crop window ─────────────────────────────────
    # After shifting line l of scan s by shifts[l,s], the valid index range in
    # the original array is:
    #   original start : max(0,  -shift)   → maps to shifted index 0 + shift ... 
    # It's easier to think in "shifted coordinates":
    #   shifted_index = original_index + shift
    # Valid shifted range for each (line,scan): [shift, npoints-1+shift] ∩ global window
    # We want the intersection across all lines & scans → no NaNs anywhere.
    global_min = int(np.max(  shifts))          # crop from the left
    global_max = int(np.min(npoints + shifts))  # crop from the right (exclusive)

    if global_max <= global_min:
        raise ValueError(
            f"No common overlap found after shifting. "
            f"global_min={global_min}, global_max={global_max}. "
            "Check that all scans share a detectable edge."
        )

    new_npoints = global_max - global_min

    # ── Build output dict ──────────────────────────────────────────────────────
    keys = [k for k in data.keys() if k != 'relay']
    ndata = {key: np.zeros((nlines, new_npoints, nscans)) for key in keys}

    for scan in range(nscans):
        for line in range(nlines):
            s = shifts[line, scan]
            orig_start = global_min - s
            orig_end   = global_max - s
            for key in keys:
                ndata[key][line, :, scan] = data[key][line, orig_start:orig_end, scan]
            
            # ── Shift the x coordinates so edges align in physical space ──
            ndata['x'][line, :, scan] = ndata['x'][line, :, scan] + s * dx

    ndata['relay'] = data['relay']
    if scan0_i >= nscans:
        raise ValueError(f"scan0_i={scan0_i} out of range (nscans={nscans})")
    # ── Optional plotting ──────────────────────────────────────────────────────
    if plotting:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        for scan in range(nscans):
            for line in range(nlines):
                axes[0].plot(x[line, :, scan],I[line, :, scan], alpha=0.4, linewidth=0.8)
        axes[0].set_title('Raw reflectivity (all lines, all scans)')
        axes[0].set_xlabel('Point index')

        for scan in range(nscans):
            for line in range(nlines):
                axes[1].plot(ndata['x'][line, :, scan],ndata[R_shift][line, :, scan], alpha=0.4, linewidth=0.8)
        axes[1].set_title('Shifted & cropped reflectivity')
        axes[1].set_xlabel('Point index (cropped)')

        plt.tight_layout()
        plt.show()

    print(f"Shifted and cropped data from {npoints} → {new_npoints} points "
          f"(removed {npoints - new_npoints} points, "
          f"left crop: {global_min}, right crop: {npoints - global_max})")

    return ndata

def compare_shifted_reflectivity(d_old, d, I_ch='averagein3value'):
    if len(np.shape(d_old['y'])) == 3:
        nx_old, ny_old, nscans_old = np.shape(d_old['y'])
        print(np.shape(d_old['y']))
        nx, ny, nscans = np.shape(d['y'])
        print(nx, ny)
        if nscans != nscans_old:
            raise Exception('Not same amount of scans!')

        fig, axes = plt.subplots(nscans, 1, figsize=(8, 4 * nscans))
        if nscans == 1:
            axes = [axes]  # Ensure axes is always iterable

        for scan in range(nscans):
            ax = axes[scan]

            y_old = d_old['y'][:, :, scan]
            y_old = y_old.reshape(nx_old, ny_old, 1)
            y_old = np.unique(y_old)
            x_old = d_old['x'][:, :, scan]
            print('not reshaped')
            x_old = x_old.reshape(nx_old, ny_old, 1)
            edges_old, widths_old = find_edges_width2D(x_old, d_old[I_ch][:, :, scan])
            ax.scatter([edges_old[0]], y_old, color='r', marker='>')
            ax.scatter([edges_old[1]], y_old, color='r', marker='<', label='raw')

            y = d['y'][:, :, scan]
            y = y.reshape(nx, ny, 1)
            y = np.unique(y)
            x = d['x'][:, :, scan]
            x = x.reshape(nx, ny, 1)
            edges, widths = find_edges_width2D(x, d[I_ch][:, :, scan])
            ax.scatter([edges[0]], y, color='g', marker='|')
            ax.scatter([edges[1]], y, color='g', marker='|', label='shifted')

            ax.legend()
            ax.set_title('Scan %s' % scan)
            ax.set_xlabel(r'x[$\mu$m]')
            ax.set_ylabel(r'y[$\mu$m]')
            ax.grid()

        # Gather x-limits from all subplots and apply the same range to all
        all_xlims = [ax.get_xlim() for ax in axes]
        x_min = min(lim[0] for lim in all_xlims)
        x_max = max(lim[1] for lim in all_xlims)
        for ax in axes:
            ax.set_xlim(x_min, x_max)

        plt.tight_layout()
        plt.show()

    else:
        y_old = d_old['y']
        y_old = np.unique(y_old)
        x_old = d_old['x']
        edges_old, widths_old = find_edges_width2D(x_old, d_old[I_ch])
        plt.scatter([edges_old[0]], y_old, color='r', marker='>')
        plt.scatter([edges_old[1]], y_old, color='r', marker='<', label='raw')

        y = d['y']
        y = np.unique(y)
        x = d['x']
        edges, widths = find_edges_width2D(x, d[I_ch])
        plt.scatter([edges[0]], y, color='g', marker='|')
        plt.scatter([edges[1]], y, color='g', marker='|', label='shifted')

        plt.legend()
        plt.xlabel(r'x[$\mu$m]')
        plt.ylabel(r'y[$\mu$m]')
        plt.grid()
        plt.show()
        
def data_to_csv(D,name):
    csv_filename = name + '.csv'
    df = pd.DataFrame(D) 

    
    df.to_csv(self.path1 +'//'+ csv_filename, index=False,sep=';')
    print('Data saved at %s' %self.path1)