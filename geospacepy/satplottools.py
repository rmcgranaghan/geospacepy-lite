# -*- coding: utf-8 -*-
"""
Dial plots for spacecraft data using matplotlib.
Also, routines for converting between spacecraft
or geophysical coordinate systems and cartesian
coordinates. 
Author: Liam M. Kilcommons
"""
from numpy import *
import numpy as np
import bisect
import pdb
import datetime
import logging
import matplotlib
from matplotlib.colors import Normalize, LogNorm
from scipy import interpolate
from scipy import ndimage
log = logging.getLogger('dmsp.satplottools')
log.setLevel(logging.DEBUG)
#import matplotlib.pyplot as pp
#import readDMSPAmpr
#
#def self_test():
#    pp.figure()
#    ax=draw_dialplot(pp.axes())
#    dmspdat = readDMSPAmpr.readDMSP(137)
#    thisdmsp = dmspdat['DMSP-F16']
#    pdat = thisdmsp[:,[1,2,4,5,6]]
#    pdat[:,4] = pdat[:,4]*-1.
#    plot_data = vector_plot(ax,pdat)    
#    pp.show()
#    return plot_data

#def mauteRules(min_lat,lat_minres,lt_minres):
	#produces LAT, LT arrays that are n x 2 numpy arrays (where n = number of bins)
	#designed to be used to specify bin edges in hist2d. The bins are designed to 
	#have equal solid angle coverage for a fixed altitude
	#min_lat is the lowest latitude that will be included in the bins
	#if min_lat < 0 then the bins will be for southern hemisphere data
	#lat_minres is the width of the smallest bin in latitude
	#lt_minres is the width of the smallest bin in localtime
	#n_lat_rings = min_lat./lat_minres

def dipole_tilt_angle(dts):
	"""
	Computes the dipole tilt angle (in degrees) given a single datetime or array of datetimes
	Approximation from:
	M. Nowada, J.-H. Shue, C.T. Russell, Effects of dipole tilt angle on geomagnetic activity, 
	Planetary and Space Science, Volume 57, Issue 11, September 2009, Pages 1254-1259, 
	ISSN 0032-0633, http://dx.doi.org/10.1016/j.pss.2009.04.007.
	"""
	if isinstance(dts,np.ndarray):
		dts = dts.flatten().tolist()	
	else:
		dts = [dts] if not isinstance(dts,list) else dts

	phis=[]
	for dt in dts:
		doy = dt.timetuple().tm_yday
		ut_hr = dt.hour+dt.minute/60.+dt.second/60./60.
		#dipole tilt angle due to the time of year
		phi_year = 23.4*np.cos((doy-172.)*2*np.pi/365.25)
		phi_uthr = 11.2*np.cos((ut_hr-16.72)*2*np.pi/24.)
		phi = phi_year+phi_uthr
		phis.append(phi)

	if len(phis)==1:
		return phis[0]
	else:
		return np.array(phis).reshape(dts.shape)

def feature_find(x,nsigma=2.0):
    """
    Filter a timeseries for 'interesting' data by fitting an AR1 autoregressive model to it
    and then returning a index array that selects only point which deviate from
    the autoregressive prediction by nsigma innovation standard deviations (i.e. are 
    uncharacteristically innovative compared to the stationary model)
    """
    x = x.flatten()
    fin = np.isfinite(x)
    fininds = np.flatnonzero(fin)
    x = x[fin]
    
    #ar1 model: x_t+1 - mu_x = r1*(x_t-mu_x)+epsilon_t+1
    mu_x = np.nanmean(x)
    r1 = np.corrcoef(x[1:],x[:-1])
    r1 = r1[0,1] # Pick the right component of the matrix
    sigma_epsilon = np.sqrt((1-r1**2))*np.nanstd(x)
    status = "R1 autocorrelation coeff for x is %f\n, standard deviation in x is %f\n, in innovation epsilon is %f" % (r1,
            np.nanstd(x),sigma_epsilon)
    #print status
    #Predict the next value given the previous without any contribution from error
    predx = x.copy()
    predx[1:] = r1*(x[:-1]-mu_x)+mu_x
    #Assign true to all finite values of x which passed the test
    high_innovation = np.abs(x - predx) > nsigma*sigma_epsilon
    #print np.abs(x-predx)
    #print "%d/%d points passed filter" % (np.count_nonzero(high_innovation),len(x))
    return fininds[high_innovation]

def sathat(ut,pos,secondCoord='Longitude',lattype='geocentric',up_is_geodetic=False):
	"""
	Estimates the direction of the along and cross track unit vectors of a spacecraft
	in an east, north, up coordinate system.

	PARAMETERS
	----------
	ut - numpy.ndarray (m rowns, 1 column)
		Spacecraft Timestamps in UT second of day
	pos - numpy.ndarray( m rows, 2 columns )
		Spacecraft locations in Lat,Lon or Lat,Localtime depending on secondCoord
	secondCoord - string, {'Localtime','Longitude'}
		Second coordinate of position arrays
	lattype - string, {'geocentric','geodetic'}
		Which style of latitude to use 
	up_is_geodetic = bool
		If True, applies an additional transformation, which is appropriate if the satellite defines 'up'
		as geodetic normal, i.e. normal to the ellipse, and you would like the resulting unit vectors
		to transform the data into Z being radial, i.e. geocentric for you
	
	RETURNS
	-------
	s_along - numpy.ndarray (m-1 rows, 3 columns )
		Along track unit vector in ENU
	s_across - numpy.ndarray (m-1 rows, 3 columns )
		Across track left unit vector in ENU
	s_up - numpy.ndarray (m-1 rows, 3 columns)
		Geocentric upward unit vector in ENU

	NOTES
	-----
	Assumes no altitude change 
	The algorithm is based on spherical trig
	The returned vectors are estimated for time ut2
	I'm aware that Localtime and longitude are not
	always simply related by a factor of 12/180, but
	since we only work with the difference between
	sequential longitudes or local times, this conversion
	is accurate enough.
	"""
	import random
	#Define some useful constants and lambdas
	ecc_earth=.081819221456

	#Geocentric to Geodetic
	gc2gd = lambda gclat: np.arctan2(np.tan(gclat/180*np.pi),(1.-ecc_earth**2.))/np.pi*180
	
	#Geodetic to Geocentric
	gd2gc = lambda gdlat: np.arctan2(np.tan(gdlat/180*np.pi),1./(1.-ecc_earth**2.))/np.pi*180

	if lattype=='geocentric':
		gclat = pos[:,0]
		gdlat = gc2gd(gclat)
	elif lattype=='geodetic':
		gdlat = pos[:,0]
		gclat = gd2gc(gdlat)
	else:
		raise ValueError('%s is not a valid selection for lattype' % (lattype))

	ut1 = ut.flatten()[:-1]
	ut2 = ut.flatten()[1:]
	dt = ut2-ut1

	#Not sure why this problem occurs, but the algorithm
	#gives strange values when the two adjacent points are very close
	#together (i.e. delta_t < 1 second)

	colat1 = 90-gclat[:-1]
	colat2 = 90-gclat[1:]
	if secondCoord.lower()=='localtime':
		lon1 = pos[:-1,1]*180./12.
		lon2 = pos[1:,1]*180./12.
	else:
		lon1 = pos[:-1,1]
		lon2 = pos[1:,1]

	#Correct for earth rotation
	#0.0041780741 degrees per seconds from Wolfram Alpha
	lon1 = lon1 - (ut2-ut1)*0.0041780741  #Diff is forward difference

	arc_a = colat1*pi/180. #colat of point 1 in rad
	arc_b = colat2*pi/180. #colat of point 2 in rad
	ang_C = (lon2-lon1)*pi/180. #angle subtended by arc connecting points 1 and 2 
	  
	#Do some spherical trig
	cos_arc_c = cos(arc_a)*cos(arc_b) + sin(arc_a)*sin(arc_b)*cos(ang_C)
	sin_arc_c = sqrt(1.-cos_arc_c**2)
	cos_ang_A = (cos(arc_a)*sin(arc_b) - sin(arc_a)*cos(arc_b)*cos(ang_C))/sin_arc_c
	sin_ang_A = sin(arc_a)*(sin(ang_C)/sin_arc_c)
	cos_ang_B = (cos(arc_b)*sin(arc_a)-sin(arc_b)*cos(arc_a)*cos(ang_C))/sin_arc_c
	sin_ang_B = sin(arc_b)/sin_arc_c*sin(ang_C)

	#pdb.set_trace()
	# We assume a circular cross section and no alitude change, this is now ENU in whatever the original 
	# geo(detic,centric) coordinate system was. 
	all_zeros = np.zeros_like(sin_ang_A)
	all_ones = np.ones_like(sin_ang_A)

	#At point 1
	s_along_1 = column_stack((sin_ang_B,cos_ang_B,all_zeros)) #Along track
	s_cross_1 = column_stack((-1*cos_ang_B,sin_ang_B,all_zeros)) #Cross track (+ to the left)
	#s_up_1 = column_stack((all_zeros,all_zeros,all_ones))
	
	#At point 2
	s_along_2 = column_stack((sin_ang_A,-1*cos_ang_A,all_zeros)) #Along track
	s_cross_2 = column_stack((cos_ang_A,sin_ang_A,all_zeros)) #Cross track (+ to the left)
	#s_up_2 = column_stack((all_zeros,all_zeros,all_ones))

	#average
	s_along = (s_along_1+s_along_2)/2
	s_cross = (s_cross_1+s_cross_2)/2
	s_up = column_stack((all_zeros,all_zeros,all_ones))
	
	#Deal with the missing value from finite difference
	#(arrays will have same length as input time, lat and lon arrays)
	#s_along = np.row_stack((s_along[0,:],s_along))
	#s_cross = np.row_stack((s_cross[0,:],s_cross))
	#s_up = np.row_stack((s_up[0,:],s_up))

	s_along = np.row_stack((s_along,s_along[-1,:]))
	s_cross = np.row_stack((s_cross,s_cross[-1,:]))
	s_up = np.row_stack((s_up,s_up[-1,:]))

	#s_along[:,0] = moving_average(s_along[:,0],4)
	#s_along[:,1] = moving_average(s_along[:,1],4)
	#s_cross[:,0] = moving_average(s_cross[:,0],4)
	#s_cross[:,1] = moving_average(s_cross[:,1],4)

	if up_is_geodetic:
		# Now we apply the additional rotation that will apply the geodetic to geocentric transform
		# This is a rotation about the eastward direction by the difference between the geocentric and geodetic latitudes
		for r in np.arange(len(s_along[:,0])):	
			dth = (gdlat[r]-gclat[r])/180*pi
			c = np.cos(dth)
			s = np.sin(dth)
			if r<10:
				print gdlat[r] - gclat[r]
			rotmat = np.array([[1,   0,  0], [0,   c,  -1*s],[0,s,  c]])
			s = random.randint(0,86400/3)
			if s==1:
				print "Index %d: gdlat=%.3f gclat=%.3f" % (r,gdlat[r],gclat[r])
				print(str(rotmat))
			
			# Now we do the matrix product of the rotation matrix and each row of the along, across, and upward unit vectors

			s_along[r,:] = np.dot(rotmat,s_along[r,:])
			s_cross[r,:] = np.dot(rotmat,s_cross[r,:])
			s_up[r:,] = np.dot(rotmat,s_up[r,:])

	return s_along,s_cross,s_up
	
def print_passes(times,latitude,north_crossings,south_crossings):
	"""
	Adjunct to parse_passes, prints out crossings dict of lists in a readable format.
	Helps with debugging.
	"""
	f = pp.figure()
	pass


def simple_passes(latitude):
	npts = len(latitude.flatten())
	entered_north = []
	entered_south = []
	
	for k in range(1,npts):
		#poleward crossing
		if latitude[k-1] < 0. and latitude[k] >= 0.:
			entered_north.append(k)
			print "Entered Northern Hemisphere: ind:%d,lat:%.3f" % (k,latitude[k])
		elif latitude[k-1] > 0. and latitude[k] <= 0.:
			entered_south.append(k)
			print "Entered Southern Hemisphere: ind:%d,lat:%.3f" % (k,latitude[k])

	xings = entered_north+entered_south
	xings.sort()
	
	return xings

def parse_passes(times,latitude,boundary_lat=50.,orbital_period=90*60.):
	"""
	Divides spacecraft datapoints into passes:

	PARAMETERS
	----------

	times - numpy.ndarray(dtype=object) 
		timestamps of datapoints as python datetimes
	latitude - numpy.ndarray(dtype=float)
		latitude of spacecraft at timestamps in times
	boundary_lat - float
		latitude at which to begin or end each pass
	orbital_period - float
		the approximate orbital period in seconds
		used to check if a pass has been missed
	
	RETURNS
	-------

	north_crossings - list of dictionaries with keys
					['s_time':datetime.datetime,
					 'e_time':datetime.datetime,
					 's_ind': integer
					 'e_ind': integer
					 's_frac':float,
					 'e_frac':float,
					 's_dt':float,
					 'e_dt':float,
					 's_dl':float,
					 'e_dl':float]
					s_time is closest timestamp after poleward traveling crossing of boundary_lat
					e_time is closest timestamp before equatorward traveling crossing of boundary_lat
					s_ind is index into times for s_time
					e_ind is index into times for e_time
					s_frac is defined as:
						estimated true crossing time = s_frac*(times(s_ind)-times(s_ind-1)) + times(s_ind)
					e_frac is defined as:
						estimated true crossing time = e_frac*(times(e_ind+1)-times(e_ind)) + times(e_ind)
					s_dt - is the difference in time (in seconds) between the two points over which the poleward boundary crossing occured
					e_dt - is the difference in time (in seconds) between the two points over which the equatorward boundary crossing occured
					
	south_crossings - same as north_crossings but for southern hemisphere
	

	NOTES
	-----

	If there are datagaps and a pass start is found without a corresponding end or visa-versa
	the code will give the corresponding dictionary keys the value None.


	""" 
	north_crossings = {'s_time':[],'e_time':[],
					 's_ind':[],'e_ind':[],
					 's_frac':[],'e_frac':[],
					 's_dt':[],'e_dt':[],
					 's_dl':[],'e_dl':[]}
	south_crossings = {'s_time':[],'e_time':[],
					 's_ind':[],'e_ind':[],
					 's_frac':[],'e_frac':[],
					 's_dt':[],'e_dt':[],
					 's_dl':[],'e_dl':[]}
	#pdb.set_trace()

	ncrossings = 0
	log.info("Beginning pass parse: %d datapoints, boundary_lat: %.1f, orbital_period: %s sec" %(len(times),boundary_lat,orbital_period))

	for t in xrange(len(latitude)-1):

		dt = (times[t+1]-times[t]).total_seconds()
		dl = latitude[t+1]-latitude[t]
		ldiff = abs(boundary_lat)-latitude[t]
		if abs(ldiff) < abs(dl) and sign(ldiff)==sign(dl): 
			#Closer to the boundary than the next point, 
			#so we have found the boundary

			#If boundary lat is lb = 50, and lat_t0 = 48 and lat_t1 = 51
			#ldiff = lb - lat_t0 = 50 - 48 = 2
			#dl = lat_t1 - lat_t0 = 51 - 48 = 3
			#frac = ldiff/dl = 2/3

			#If boundary lat is lb = 50, and lat_t0 = 51 and lat_t1 = 48
			#ldiff = lb - lat_t0 = 50 - 51 = -1
			#dl = lat_t1 - lat_t0 = 48 - 51 = -3
			#frac = ldiff/dl = 1/3

			#Edge case: mid pass data gap

			#If boundary lat is lb = 0, and lat_t0 = -5 and lat_t1 = -19
			#ldiff = lb - lat_t0 = 0 - -5 = 5			
			#dl = lat_t1 - lat_t0 = -19 - -5 = -14
			#frac = ldiff/dl = 5/-14



			#Sign of dl
			#Poleward Traveling:
			#North: 6 - 5 = 1 -> dl > 0 -> sign(dl)*sign(lat) > 0
			#South: -6 - -5 = -1 -> dl < 0 -> sign(dl)*sign(lat) > 0
			#Equatorward Traveling:
			#North: 5 - 6 = -1 -> dl < 0 -> sign(dl)*sign(lat) < 0
			#South: -5 - -6 = 1 -> dl > 0 -> sign(dl)*sign(lat) < 0
			#Edge cases:
			#S-to-N: -1 - 1 = -2 -> dl < 0 -> sign(dl)*sign(lat) > 0 -> Northward Equator Crossing -> Poleward Movement
			#N-to-S: 1 - -1 = 2 -> dl > 0 -> sign(dl)*sign(lat) > 0 -> Southward Equator Crossing -> Poleward Movement
			
			#Determine if we are moving equatorward
			#or poleward
			
			poleprod = dl*sign(latitude[t]) #+dl if moving poleward or crossing equator, -dl if moving equatorward
			eqcross = sign(latitude[t])*sign(latitude[t+1]) #-1 if crossing equator, +1 otherwise

			#Error Check
			if not poleprod > 0 and not poleprod < 0:
				raise RuntimeError('Invalid value of dl*sign(latitude[t]): %s' % (str(poleprod)))
			if not eqcross > 0 and not eqcross < 0:
				raise RuntimeError('Invalid value of sign(latitude[t])*sign(latitude[t+1]): %s' % (str(poleprod)))


			#Check for any missed crossings 
			#Add a filler if we missed one
			

			#Check last northern poleward
			if len(north_crossings['s_time']) > 2:
				last_delta = (north_crossings['s_time'][-1] - north_crossings['s_time'][-2]).total_seconds()
				if last_delta > orbital_period*1.5:
					log.debug("Difference between last two northern pass starts is %.1f s > 1.5 %.1f s orbital period" % (last_delta,orbital_period) )
					log.info("Found missing northern poleward crossing after time=%s, lat=%.3f" % \
							(str(north_crossings['s_time'][-1]),latitude[north_crossings['s_ind'][-1]]))
					north_crossings['s_ind'].insert(-2,None)
					north_crossings['s_time'].insert(-2,None)
					north_crossings['s_frac'].insert(-2,None)
					north_crossings['s_dt'].insert(-2,None)
					north_crossings['s_dl'].insert(-2,None)
			
			#Check last northern equatorward
			if len(north_crossings['e_time']) > 2:
				last_delta = (north_crossings['e_time'][-1] - north_crossings['e_time'][-2]).total_seconds()
				if last_delta > orbital_period*1.5:
					log.debug("Difference between last two northern pass ends is %.1f s > 1.5 %.1f s orbital period" % (last_delta,orbital_period) )
					log.info("%d) Found missing northern equatorward crossing after time=%s, lat=%.3f" % \
							(t,str(north_crossings['e_time'][-2]),latitude[north_crossings['e_ind'][-2]]))
					north_crossings['e_ind'].insert(-2,None)
					north_crossings['e_time'].insert(-2,None)
					north_crossings['e_frac'].insert(-2,None)
					north_crossings['e_dt'].insert(-2,None)
					north_crossings['e_dl'].insert(-2,None)
			
			#Check last southern poleward
			if len(south_crossings['s_time']) > 2:
				last_delta = (south_crossings['s_time'][-1] - south_crossings['s_time'][-2]).total_seconds()
				if last_delta > orbital_period*1.5:
					log.debug("Difference between last two southern pass starts is %.1f s > 1.5 %.1f s orbital period" % (last_delta,orbital_period) )
					log.info("%d) Found missing southern poleward crossing after time=%s, lat=%.3f" % \
							(t,str(south_crossings['s_time'][-2]),latitude[south_crossings['s_ind'][-2]]))
					south_crossings['s_ind'].insert(-2,None)
					south_crossings['s_time'].insert(-2,None)
					south_crossings['s_frac'].insert(-2,None)
					south_crossings['s_dt'].insert(-2,None)
					south_crossings['s_dl'].insert(-2,None)
			
			#Check last southern equatorward
			if len(south_crossings['e_time']) > 2:
				last_delta = (south_crossings['e_time'][-1] - south_crossings['e_time'][-2]).total_seconds()
				if last_delta > orbital_period*1.5:
					log.debug("Difference between last two southern pass ends is %.1f s > 1.5 %.1f s orbital period" % (last_delta,orbital_period) )
					log.info("%d) Found missing southern equatorward crossing after time=%s, lat=%.3f" % \
							(t,str(south_crossings['e_time'][-2]),latitude[south_crossings['e_ind'][-2]]))
					south_crossings['e_ind'].insert(-2,None)
					south_crossings['e_time'].insert(-2,None)
					south_crossings['e_frac'].insert(-2,None)
					south_crossings['e_dt'].insert(-2,None)
					south_crossings['e_dl'].insert(-2,None)

			if eqcross > 0:
				if poleprod > 0: #Moving poleward
					log.info("%d) Found Poleward Crossing of Latitude %.3f Between:\ntime = %s,lat = %.3f\ntime = %s,lat = %.3f" % \
						(t,boundary_lat,str(times[t]),latitude[t],str(times[t+1]),latitude[t+1]))

					if sign(latitude[t])>0: #North
						north_crossings['s_ind'].append(t+1)
						north_crossings['s_time'].append(times[t+1])
						north_crossings['s_frac'].append(ldiff/dl)
						north_crossings['s_dt'].append(dt)
						north_crossings['s_dl'].append(dl)
					elif sign(latitude[t])<0: #South
						south_crossings['s_ind'].append(t+1)
						south_crossings['s_time'].append(times[t+1])
						south_crossings['s_frac'].append(ldiff/dl)
						south_crossings['s_dt'].append(dt)
						south_crossings['s_dl'].append(dl)

				elif poleprod < 0: #Moving equatorward
					log.info("%d) Found Equatorward Crossing of Latitude %.3f Between:\ntime = %s,lat = %.3f\ntime = %s,lat = %.3f" % \
						(t,boundary_lat,str(times[t]),latitude[t],str(times[t+1]),latitude[t+1]))

					if sign(latitude[t])>0: #North 
						north_crossings['e_ind'].append(t)
						north_crossings['e_time'].append(times[t])
						north_crossings['e_frac'].append(ldiff/dl)
						north_crossings['e_dt'].append(dt)
						north_crossings['e_dl'].append(dl)
						
					elif sign(latitude[t])<0: #South
						south_crossings['e_ind'].append(t)
						south_crossings['e_time'].append(times[t])
						south_crossings['e_frac'].append(ldiff/dl)
						south_crossings['e_dt'].append(dt)
						south_crossings['e_dl'].append(dl)
						
			elif eqcross < 0:
				#This case should really only occur when boundary_lat is actually zero
				#or in the case of a data gap around the equator which has boundary points
				#containing the boundary_lat
				#In this case a single iteration of this loop assigns the end of a pass
				#at index t and the start of the next at index t+1
				if latitude[t]>0: #Crossed equator moving north to south
					log.info("%d) Found North-To-South Equator Crossing Between:\ntime = %s - %s\nlat = %.3f - %.3f" % \
						(t,times[t].strftime('%H:%M:%S'),times[t+1].strftime('%H:%M:%S'),latitude[t],latitude[t+1]))
					
					north_crossings['e_ind'].append(t)
					north_crossings['e_time'].append(times[t])
					north_crossings['e_frac'].append(ldiff/dl)
					north_crossings['e_dt'].append(dt)
					north_crossings['e_dl'].append(dl)

					south_crossings['s_ind'].append(t+1)
					south_crossings['s_time'].append(times[t+1])
					south_crossings['s_frac'].append(ldiff/dl)
					south_crossings['s_dt'].append(dt)
					south_crossings['s_dl'].append(dl)
				elif latitude[t]<0: #Crossed equator moving south to north 
					log.info("%d) Found South-To-North Equator Crossing Between:\ntime = %s - %s,lat = %.3f - %.3f" % \
						(t,times[t].strftime('%H:%M:%S'),times[t+1].strftime('%H:%M:%S'),latitude[t],latitude[t+1]))

					south_crossings['e_ind'].append(t)
					south_crossings['e_time'].append(times[t])
					south_crossings['e_frac'].append(ldiff/dl)
					south_crossings['e_dt'].append(dt)
					south_crossings['e_dl'].append(dl)

					north_crossings['s_ind'].append(t+1)
					north_crossings['s_time'].append(times[t+1])
					north_crossings['s_frac'].append(ldiff/dl)
					north_crossings['s_dt'].append(dt)
					north_crossings['s_dl'].append(dl)

			if len(north_crossings['s_time'])>1:
				log.debug('--Last N Poleward %s, %.3f' % (north_crossings['s_time'][-1].strftime('%H:%M:%S'),latitude[north_crossings['s_ind'][-1]]))
			if len(north_crossings['e_time'])>1:
				log.debug('--Last N Equatorward %s, %.3f' % (north_crossings['e_time'][-1].strftime('%H:%M:%S'),latitude[north_crossings['e_ind'][-1]]))
			if len(south_crossings['s_time'])>1:
				log.debug('--Last S Poleward %s, %.3f' % (south_crossings['s_time'][-1].strftime('%H:%M:%S'),latitude[south_crossings['s_ind'][-1]]))
			if len(south_crossings['e_time'])>1:
				log.debug('--Last S Equatorward %s, %.3f' % (south_crossings['e_time'][-1].strftime('%H:%M:%S'),latitude[south_crossings['e_ind'][-1]]))

			ncrossings+=1

	#Check for dangling pass ends at beginning
	if north_crossings['e_time'][0] < north_crossings['s_time'][0]:
		log.debug("Dangling northern hemisphere pass end (%s) at START of day REMOVED" % (north_crossings['e_time'][0].strftime('%H:%M:%S')))
			
		for key in north_crossings:
			if key[0]=='e':
				north_crossings[key] = north_crossings[key][1:]
				
		
	if south_crossings['e_time'][0] < south_crossings['s_time'][0]:
		log.debug("Dangling southern hemisphere pass end (%s) at START of day REMOVED" % (south_crossings['e_time'][0].strftime('%H:%M:%S')))
		
		for key in south_crossings:
			if key[0]=='e':
				south_crossings[key] = south_crossings[key][1:]
		
	#Check for dangling pass starts at end
	if north_crossings['s_time'][-1] > north_crossings['e_time'][-1]:
		log.debug("Dangling northern hemisphere pass start (%s) at END of day REMOVED" % (south_crossings['s_time'][-1].strftime('%H:%M:%S')))
		
		for key in north_crossings:
			if key[0]=='s':
				north_crossings[key] = north_crossings[key][0:-1]
		
		
	if south_crossings['s_time'][-1] > south_crossings['e_time'][-1]:
		log.debug("Dangling southern hemisphere pass start (%s) at END of day REMOVED" % (south_crossings['s_time'][-1].strftime('%H:%M:%S')))
		for key in south_crossings:
			if key[0]=='s':
				south_crossings[key] = south_crossings[key][0:-1]
			

	#check_passes(times,latitude,north_crossings,south_crossings)

	#Now that all of the shuffling is done split the dict of lists
	#into lists of dicts so that we get 1 dict per pass
	north_passes = []
	south_passes = []
	
	#pdb.set_trace()
	log.debug("Now creating 1 dict for each northern and southern pass and placing in return list")
	for n in range(min([len(north_crossings['s_time']),len(north_crossings['e_time'])]) ):
		passdict = {}
		for key in north_crossings:
			passdict[key]=north_crossings[key][n]
		north_passes.append(passdict)
	
	for s in range(min([len(south_crossings['s_time']),len(south_crossings['e_time'])])):
		passdict = {}
		for key in south_crossings:
			passdict[key]=south_crossings[key][s]
		south_passes.append(passdict)

	return north_passes, south_passes

def check_passes(times,latitude,north_crossings,south_crossings):
	import matplotlib.pyplot as pp
	f = pp.figure(figsize=(8,6))
	a = pp.axes()
	a.plot(times,latitude,'k-')
	a.hold(True)

	a.plot(times[north_crossings['s_ind']],latitude[north_crossings['s_ind']],'g^',label='North Poleward Crossing')
	a.plot(times[south_crossings['s_ind']],latitude[south_crossings['s_ind']],'r^',label='South Poleward Crossing')

	a.plot(times[north_crossings['e_ind']],latitude[north_crossings['e_ind']],'gv',label='North Equatorward Crossing')
	a.plot(times[south_crossings['e_ind']],latitude[south_crossings['e_ind']],'rv',label='South Equatorward Crossing')
	
	a.legend()
	f.autofmt_xdate()
	return f

def timepos_ticklabels(ax,t,lat,ltlon,fs=10):
	"""
	Make Multi-Line Tick Labels for Spacecraft Data with time, lat and localtime/longitude
	"""
	lat,ltlon,t = lat.flatten(),ltlon.flatten(),t.flatten()
	using_datetimes = isinstance(t.tolist()[0],datetime.datetime)
	if using_datetimes:
		#Need to convert datetimes to matplotlib dates if we are listing with datetimes
		mplt = np.array(matplotlib.dates.date2num(t.tolist()))
	else:
		mplt = t
	ticks = ax.get_xticks()
	#print ticks with multiple lines
	newlab=[]
	for tick in ticks:
		ind = (np.abs(mplt-tick)).argmin()
		if using_datetimes:
			newlab.append('%s\n%.1f\n%.1f' % (t[ind].strftime('%H:%M'),lat[ind],ltlon[ind]))
		else:
			newlab.append('%.1f\n%.1f\n%.1f' % (t[ind],lat[ind],ltlon[ind]))
	ax.set_xticklabels(newlab)
	matplotlib.artist.setp(ax.get_xmajorticklabels(),size=fs,rotation=0)

def multiline_timelabels(ax,tdata,xdata,strffmt='%H:%M',xfmt=['%.1f']):
	"""
	Adds additional lines to the labels of an existing axes. 
	tdata - data that was passed to the plot function, must be an array of datetime objects
	xdata - any number of columns of additional data to be added to labels. Must have same number
				of rows as tdata.
	strffmt - format specification to datetime.datetime.strftime for time labels
	xfmt - list of formats for each column of xdata
	"""
	#Manually create the tick labels
	#There is probably a better way to do this with FuncFormatter, but I couldn't 
	#figure out how to get all of the relavent lat and LT information into it
	from matplotlib import dates as mpldates

	#Get the tick marks
	xticks = ax.get_xticks()
	xticks_datetime = array(mpldates.num2date(xticks))
	xlabels = []
	for l in range(len(xticks)):
		tick = xticks_datetime[l]
		tick = tick.replace(tzinfo=None) #Remove the timezone so we can compare the two types
		ind = None
		for k in range(len(tdata)): #Can't get nonzero to work on this??
			if tdata[k] == tick:
				ind = k 
		if ind is not None: #Sometimes tick is not found if it wants to tickmark outside of data range
			tickstr = tick.strftime(strffmt)
			if len(xdata.shape)>1: #If more than one column of additional data
				for c in xrange(len(xdata[0,:])):
					tickstr+="\n"
					tickstr+=xfmt[c] % (xdata[ind,c])
			else: 
				tickstr+="\n"
				tickstr+=xfmt[0] % (xdata[ind])
			xlabels.append(tickstr)
		else:
			xlabels.append(tick.strftime(strffmt))

	ax.set_xticklabels(xlabels)

	return ax

def draw_dialplot(ax,minlat=50,padding=3,fslt=10,fslat=12,southern_hemi=False):
	"""
	Draws the dialplot and labels the latitudes

		minlat : {60,50,40}, optional
			Latitude of largest ring of dialplot
		padding : int, optional
			Amount of extra space to put around the plot
			Used with xlim so is in plot units
		fslt : int,optional
			Font size for hour labels
		fslat : int,optional
			Font size for latitude labels
		southern_hemi : bool,optional
			Defualts to False, put negative signs on latitude labels
	"""
	phi = linspace(0,2*pi,3000)

	ax.figure.set_facecolor('white')
	thecolor = 'grey'
	thelinestyle='solid'
	thezorder = -100 #make sure the lines are in the background of the plot
	#Circles 
	if minlat == 60:
		ax.plot(30*cos(phi),30*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(20*cos(phi),20*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(10*cos(phi),10*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)            
	elif minlat == 50:
		ax.plot(40*cos(phi),40*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(30*cos(phi),30*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(20*cos(phi),20*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(10*cos(phi),10*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
	elif minlat == 40:
		ax.plot(50*cos(phi),50*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(40*cos(phi),40*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(30*cos(phi),30*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(20*cos(phi),20*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
		ax.plot(10*cos(phi),10*sin(phi),color=thecolor,linestyle=thelinestyle,zorder=thezorder)
	#Labels    
	tcolor = 'red'    
	
	r_text = 90-minlat+2; th_text = 3*pi/2
	x = r_text*cos(th_text)
	y = r_text*sin(th_text)
	ax.text(x-1,y,'0',fontsize=fslt,color=tcolor)
		
	r_text = 90-minlat+3; th_text = 7*pi/4
	x = r_text*cos(th_text)
	y = r_text*sin(th_text)            
	ax.text(x-1,y,'3',fontsize=fslt,color=tcolor)

	r_text = 90-minlat+2; th_text = 0*pi/2;
	x = r_text*cos(th_text)
	y = r_text*sin(th_text)
	ax.text(x-1,y,'6',fontsize=fslt,color=tcolor)

	r_text = 90-minlat+2; th_text = pi/4;
	x = r_text*cos(th_text)
	y = r_text*sin(th_text)
	ax.text(x-1,y,'9',fontsize=fslt,color=tcolor)
 
	r_text = 90-minlat+2; th_text = 1*pi/2;
	x = r_text*cos(th_text)
	y = r_text*sin(th_text)
	ax.text(x-1,y,'12',fontsize=fslt,color=tcolor)
	
	r_text = 90-minlat+2; th_text = 3*pi/4;
	x = r_text*cos(th_text)
	y = r_text*sin(th_text)
	ax.text(x-1,y,'15',fontsize=fslt,color=tcolor)

	r_text = 90-minlat+2; th_text = 2*pi/2;
	x = r_text*cos(th_text)
	y = r_text*sin(th_text)
	ax.text(x-1,y,'18',fontsize=fslt,color=tcolor)

	r_text = 90-minlat+2; th_text = 5*pi/4;
	x = r_text*cos(th_text)
	y = r_text*sin(th_text)
	ax.text(x-2,y,'21',fontsize=fslt,color=tcolor)

	# line([0 0],[-50 50],'Color',[0.7 0.7 0.7],'LineWidth',1.5); 
	# line([-50 50],[0 0],'Color',[0.7 0.7 0.7],'LineWidth',1.5)
	for i in xrange(1,25):
		th = (i-1)*pi/12;
		r_min = 10; 
		r_max = 90-minlat;
		ax.plot([r_min*cos(th), r_max*cos(th)],[r_min*sin(th), r_max*sin(th)],color=thecolor,linestyle=thelinestyle,zorder=thezorder,
			 linewidth=1)
	sh = r'-' if southern_hemi else ''
	ax.text( 6,-5,sh+r'$80^o$',fontsize=fslat,color=tcolor);
	ax.text(16,-5,sh+r'$70^o$',fontsize=fslat,color=tcolor);
	ax.text(26,-5,sh+r'$60^o$',fontsize=fslat,color=tcolor);
	
	if minlat < 60:
		ax.text(36,-5,r'$50^o$',fontsize=fslat,color=tcolor);
			   
	if minlat < 50:
		ax.text(46,-5,r'$40^o$',fontsize=fslat,color=tcolor);

	ax.set_frame_on(False)
	ax.axes.get_yaxis().set_visible(False)
	ax.axes.get_xaxis().set_visible(False)
	ax.axis('tight')
	ax.set_xlim([-1*(90.-minlat+padding),(90.-minlat+padding)])
	ax.set_ylim([-1*(90.-minlat+padding),(90.-minlat+padding)])
	return ax 
	
def latlt2polar(lat,lt,hemisphere):
	"""
	Converts an array of latitude and lt points to polar for a top-down dialplot (latitude in degrees, LT in hours)
	i.e. makes latitude the radial quantity and MLT the azimuthal 

	get the radial displacement (referenced to down from northern pole if we want to do a top down on the north, 
		or up from south pole if visa-versa)
	"""
	if hemisphere=='N':
		r = 90.-lat
	elif hemisphere=='S':
		r = 90.-(-1*lat)
	else:
		raise ValueError('%s is not a valid hemisphere, N or S, please!' % (hemisphere))
	#convert lt to theta (azimuthal angle) in radians
	theta = lt/24. * 2*pi - pi/2
	
	#the pi/2 rotates the coordinate system from
	#theta=0 at negative y-axis (local time) to
	#theta=0 at positive x axis (traditional polar coordinates)
	return r,theta

def latlon2polar(lat,lon,hemisphere):
	"""
	Converts an array of latitude and lt points to polar for a top-down dialplot (latitude in degrees, LT in hours)
	i.e. makes latitude the radial quantity and MLT the azimuthal 
	"""
	#Get the radial displacement (referenced to down from northern pole if we want to do a top down on the north, 
	#   or up from south pole if visa-versa)
	if hemisphere=='N':
		r = 90.-lat
	elif hemisphere=='S':
		r = 90.-(-1*lat)
	else:
		raise ValueError('%s is not a valid hemisphere, N or S, please!' % (hemisphere))
	#convert lt to theta (azimuthal angle) in radians
	theta = lon/360. * 2*pi - pi/2
	
	#make sure theta is positive
	theta[theta<0.] = theta[theta<0.]+2*pi

	#the pi/2 rotates the coordinate system from
	#theta=0 at negative y-axis (local time) to
	#theta=0 at positive x axis (traditional polar coordinates)
	return r,theta

def latlt2cart(lat,lt,hemisphere):
	"""
	Latitude and local time to cartesian for a top-down dialplot
	"""
	r,theta = latlt2polar(lat,lt,hemisphere)
	return r*cos(theta),r*sin(theta)

def latlon2cart(lat,lon,hemisphere):
	"""
	Latitude and longitude to cartesian for a top-down dialplot
	"""
	r,theta = latlon2polar(lat,lon,hemisphere)
	return r*cos(theta),r*sin(theta)

def hairplot(ax,lat,lt,C,hemisphere,max_size=10,max_val=None,vmin=None,vmax=None,ref_units=None,dialplot=True,horizontal=False,min_displayed=0.,**kwargs):
	"""
	Makes top-down polar plots with vertical lines to indicate intensity and color along spacecraft track.
	Can handle either an array of colors or a variable of colors.
	"""
	#Draw the background dialplot on
	if dialplot:
		draw_dialplot(ax)

	if max_val is None:
		max_val = np.nanmax(np.abs(C))

	if vmax is None:
		vmax = nanmax(C)
	if vmin is None:
		vmin = nanmin(C)

	X,Y = latlt2cart(lat,lt,hemisphere)
	if not horizontal:
		X1 = zeros_like(X)
		Y1 = C/max_val*max_size
	else:
		Y1 = zeros_like(Y)
		X1 = C/max_val*max_size

	#Implement filtering very small values out
	above_min = np.abs(C) > min_displayed

	norm = Normalize(vmin=vmin,vmax=vmax)
	Q = ax.quiver(X[above_min],Y[above_min],X1[above_min],Y1[above_min],C[above_min],
		angles='xy',units='xy',width=.4,scale_units='xy',scale=1,alpha=.75,headwidth=0,headlength=0,norm=norm,**kwargs)

	#Q appears to not actually create a mappable??
	mappable = matplotlib.cm.ScalarMappable(norm=Q.norm,cmap=Q.cmap)
	mappable.set_array(C[above_min])

	key_label = str(max_val)+'[%s]' % (ref_units) if ref_units is not None else ''
	if ref_units is not None:
		ax.quiverkey(Q, 0.45, 0, max_size, key_label,color=mappable.to_rgba(vmax),labelpos='E')

	return mappable

def crosstrackplot(ax,lat,lt,vcross,hemisphere,max_size=10,
		max_val=None,vmin=None,vmax=None,ref_units=None,key_label=None,
		dialplot=True,horizontal=False,min_displayed=0.,
		leftorright='left',label_start_end=False,
		alpha=.75,single_color=None,key_pos=(.05,.05),**kwargs):
	"""
	Makes top-down polar plots with lines perpendicular to the 
	spacecraft track to indicate intensity and color along spacecraft track.
	Can handle either an array of colors or a variable of colors.

	If single_color is not None, then will not make a colorbar
	"""
	#Draw the background dialplot on
	if dialplot:
		draw_dialplot(ax)

	if max_val is None:
		max_val = np.nanmax(np.abs(vcross))

	if vmax is None:
		vmax = nanmax(vcross)
	if vmin is None:
		vmin = nanmin(vcross)

	X,Y = latlt2cart(lat,lt,hemisphere)
	dX,dY = np.diff(X),np.diff(Y)
	#Fix length
	dX,dY=np.concatenate(([dX[0]],dX)),np.concatenate(([dY[0]],dY))
	
	#Unit Vector Along Track
	alongX = dX/np.sqrt(dX**2+dY**2)
	alongY = dY/np.sqrt(dX**2+dY**2)

	#Cross Track Unit Vector
	acrossX = alongY if hemisphere == 'S' else -1*alongY
	acrossY = -1*alongX if hemisphere == 'S' else alongX 

	if leftorright == 'right':
		acrossY=-1*acrossY
		acrossX=-1*acrossX

	Y1 = vcross/max_val*max_size*acrossY
	X1 = vcross/max_val*max_size*acrossX

	#Implement filtering very small values out
	above_min = np.abs(vcross) > min_displayed

	if not single_color:
		norm = Normalize(vmin=vmin,vmax=vmax)
		Q = ax.quiver(X[above_min],Y[above_min],X1[above_min],Y1[above_min],vcross[above_min],
			angles='xy',units='xy',width=.4,scale_units='xy',scale=1,alpha=alpha,headwidth=0,headlength=0,norm=norm,**kwargs)

		#Q appears to not actually create a mappable??
		mappable = matplotlib.cm.ScalarMappable(norm=Q.norm,cmap=Q.cmap)
		mappable.set_array(vcross[above_min])
	
	else:
		Q = ax.quiver(X[above_min],Y[above_min],X1[above_min],Y1[above_min],
			color=single_color,angles='xy',units='xy',width=.4,scale_units='xy',scale=1,alpha=alpha,headwidth=0,headlength=0,**kwargs)

	if ref_units is not None:
		key_label_pre = str(max_val)+'[%s]' % (ref_units)
		if key_label is not None:
			key_label = key_label_pre+' (%s)' % (key_label)
		else:
			key_label = key_label
		keycolor = mappable.to_rgba(vmax) if single_color is None else single_color
		ax.quiverkey(Q, key_pos[0], key_pos[1], max_size, key_label,color=keycolor,labelpos='E',coordinates='figure')

	return mappable if single_color is None else Q


def vector_plot(ax,data,satname='dmsp',color='blue',latlim=-50.,max_vec_len=12.,max_magnitude=1000.,min_displayed=0.,
	reference_vector_len=500.,reference_vector_label="500nT",labeljustify='left',labeltrack=True,fontsize=8,skip=2,plottime=False,
	timejustify='left',ntimes=1,col5isnorth=True,spacecraft_coords=False,alpha=.55,width=.2,secondCoord='localtime'):
	"""
	Makes top-down dialplots of spacecraft tracks and vector data.

	PARAMETERS
	----------
		ax : matplotlib.axes
			Thing we're going to plot on
		data : numpy.ndarray
			n x 5 array of spacecraft data
			column 1 = time (UT sec of day)
			column 2 = latitude (magnetic or otherwise)
			column 3 = localtime (magnetic or local solar)
			column 4 = eastward component of vector data
			column 5 = northward component of vector data
		satname : str, optional
			Text to place at end of spacecraft track
		latlim : float, optional
			Largest ring of dial plot (set to negative to indicate 
				that data is for southern hemisphere)
		secondCoord : str, optional
			localtime or longitude
		max_vec_len : float, optional
			Maximum length of vector in plot coordinates (i.e. degrees latitude)
		max_magnitude : float, optional
			Value of sqrt(Vec_east**2+Vec_north**2) that will be associated with
			a vector of length max_vec_len on plot (scaling factor)
		min_displayed : float, optional
			Value of sqrt(Vec_east**2+Vec_north**2) that represents the threshold for 
			'noise' level measurements. The code will not display vectors below this
			value to reduce visual clutter.
		reference_vector_len : float, optional 
			The size of the reference vector in the lower left of the plot
		reference_vector_label : str, optional
			Label for reference vector
		labeljustify : {'left','right','auto'}, optional
			Where to position the satname at the end of the track
		labeltrack : bool, optional
			Draw the label at the end of track if True
		fontsize : int, optional
			Size of fonts for labels 
		skip : int, optional
			Cadence of vectors to plot, i.e. skip=2 plots every other vector, except for the ten largest 
		plottime : boolean, optional
			Plot the start time of the pass at the first point
		col2isnorth : boolean, optional
			Assume that the 5th column is northward, if false, assumes it's radial (i.e. equatorward, i.e. Apex d2)	

	"""
	#if labeltext=='default':
	labeltext=satname
	
	if latlim > 0:
		inlatlim = data[:,1] > latlim
	elif latlim < 0:
		inlatlim = data[:,1] < latlim
	plot_data = data[inlatlim,:]
	if len(plot_data) == 0:
		print "Warning: vector_plot called with no data in display region"
		return
	#convert to colat
	plot_data[:,1] = 90-abs(plot_data[:,1])
	#convert mlt to radians
	if secondCoord.lower() == 'localtime':
		plot_data[:,2] = plot_data[:,2]/24. * 2*pi - pi/2
	elif secondCoord.lower() == 'longitude':
		plot_data[:,2] = plot_data[:,2]/180. * pi

	#the pi/2 rotates the coordinate system from
	#theta=0 at negative y-axis (local time) to
	#theta=0 at positive x axis (traditional polar coordinates)
	
	#calculate the vector magnitudes
	magnitudes = sqrt(plot_data[:,3]**2+plot_data[:,4]**2)
   
	#calculate the scaling factors (the percentage of the maximum length each vector will be
	#maximum length corresponds to a magnitude equal to data_range(2)
   
	sfactors = (magnitudes)/(max_magnitude)
   
	#normalize so each vector has unit magnitude
	plot_data[:,3] = plot_data[:,3]/magnitudes;
	plot_data[:,4] = plot_data[:,4]/magnitudes;
   
	#stretch all vectors to the maximum vector length and then scale them
	#by each's individual scale factor
	plot_data[:,3] = (plot_data[:,3]*sfactors)*max_vec_len;
	plot_data[:,4] = (plot_data[:,4]*sfactors)*max_vec_len;
   
	#finally rotate the coordinate system for the datavar from E_hat,N_hat to r_hat,theta_hat
   
	#first if in the northern hemisphere, and we're using a coordinate system
	#that eastward northward (i.e. GEO), instead of eastward equatorward (i.e. Apex) 
	#flip the sign of the N_hat component to be radially outward (away from the pole)
   
	if(latlim > 0) and col5isnorth:
		plot_data[:,4] = -1*plot_data[:,4]
	
	X = plot_data[:,1]*cos(plot_data[:,2])        
	Y = plot_data[:,1]*sin(plot_data[:,2])
	r_hat = column_stack((cos(plot_data[:,2]),sin(plot_data[:,2])))
	th_hat = column_stack((-1*sin(plot_data[:,2]),cos(plot_data[:,2]))) 
	X1 = plot_data[:,4]*r_hat[:,0]+plot_data[:,3]*th_hat[:,0]    
	Y1 = plot_data[:,4]*r_hat[:,1]+plot_data[:,3]*th_hat[:,1]    

	#Make a mask that will remove any values that are below the minimum
	#magnitude to display
	g = magnitudes>min_displayed

	#Set up an order so the largest values get plotted first
	C = argsort(magnitudes[g])[::-1]

	#Keep the largest 5% of data and then use the skipping
	#to thin the vectors for speed and reduced clutter
	#n_largest_to_keep = ceil(.05*len(magnitudes))
	#Just keep the 10 largest magntiude vectors
	C = concatenate((C[:10],C[range(10,len(C),skip)]))
	
	ax.hold(True)
	ax.quiver(X[C],Y[C],X1[C],Y1[C],angles='xy',units='xy',width=width,color=color,scale_units='xy',scale=1,label=labeltext,alpha=alpha)
	if labeltrack:
		if labeljustify=='right':
			ax.text(X[-1],Y[-1],satname,color=color,va='top',ha='right',fontsize=fontsize)
		elif labeljustify=='left':
			ax.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
		elif labeljustify=='auto':
			if len(X) > 1:
				if (X[-1] > 0 and X[-2] < X[-1]) or (X[-1] < 0 and X[-2] > X[-1]):
					ax.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
				elif (X[-1] > 0 and X[-2] > X[-1]) or (X[-1] < 0 and X[-2] < X[-1]):
					ax.text(X[-1],Y[-1],satname,color=color,va='top',ha='right',fontsize=fontsize)
				else:
					ax.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
			else:
				ax.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
	#plot the reference arrow
	ref_sfactor = reference_vector_len/max_magnitude
	ref_X1 = (sqrt(reference_vector_len**2/2)/reference_vector_len*ref_sfactor)*max_vec_len
	ref_Y1 = (sqrt(reference_vector_len**2/2)/reference_vector_len*ref_sfactor)*max_vec_len
	ax.quiver(-abs(latlim)+15,-abs(latlim)+5,ref_X1,ref_Y1,angles='xy',units='xy',width=.2,color='black',scale_units='xy',scale=1,label=reference_vector_label)
	ax.text(-abs(latlim)+15,-abs(latlim)+5,reference_vector_label,color='black',va='top',size=8)  
	if plottime:
		for d in linspace(0,len(plot_data)-1,ntimes).tolist():
			t = datetime.datetime(2000,1,1)+datetime.timedelta(seconds=plot_data[d,0])
			ax.text(X[d],Y[d],t.strftime('%X'),color=color,va='top',ha=timejustify,fontsize=fontsize,alpha=.75)
	return ax

def vector_component_plot(ax_e,ax_n,data,satname='dmsp',color='blue',latlim=-50.,max_vec_len=12.,max_magnitude=1000.,reference_vector_len=500.,
	reference_vector_label="500nT",labeljustify='left',labeltrack=True,fontsize=8,skip=2,cmap='bwr',plottime=False,timejustify='left',ntimes=1):
	"""
	Makes top-down dialplots of spacecraft tracks and vector data.

	PARAMETERS
	----------
		ax_e : matplotlib.axes
			Thing we're going to plot eastward component of vector on
		ax_n : matplotlib.axes
			Thing we're going to plot eastward component of vector on
		data : numpy.ndarray
			n x 5 array of spacecraft data
			column 1 = time (UT sec of day)
			column 2 = latitude (magnetic or otherwise)
			column 3 = localtime (magnetic or local solar)
			column 4 = eastward component of vector data
			column 5 = northward component of vector data
		satname : str, optional
			Text to place at end of spacecraft track
		latlim : float, optional
			Largest ring of dial plot (set to negative to indicate 
				that data is for southern hemisphere)
		max_vec_len : float, optional
			Maximum length of vector in plot coordinates (i.e. degrees latitude)
		max_magnitude : float, optional
			Value of sqrt(Vec_east**2+Vec_north**2) that will be associated with
			a vector of length max_vec_len on plot (scaling factor)
		reference_vector_len : float, optional 
			The size of the reference vector in the lower left of the plot
		reference_vector_label : str, optional
			Label for reference vector
		labeljustify : {'left','right','auto'}, optional
			Where to position the satname at the end of the track
		labeltrack : bool, optional
			Draw the label at the end of track if True
		fontsize : int, optional
			Size of fonts for labels
		skip : int, optional
			Cadence of vectors to plot, i.e. skip=2 plots every other vector, except for the ten largest 
		plottime : boolean, optional
			Plot the start time of the pass at the first point 

	"""
	#if labeltext=='default':
	labeltext=satname
	
	if latlim > 0:
		inlatlim = data[:,1] > latlim
	elif latlim < 0:
		inlatlim = data[:,1] < latlim
	plot_data = data[inlatlim,:]
	if len(plot_data) == 0:
		print "Warning: vector_plot called with no data in display region"
		return
	#convert to colat
	plot_data[:,1] = 90-abs(plot_data[:,1])
	#convert mlt to radians
	plot_data[:,2] = plot_data[:,2]/24. * 2*pi - pi/2
	#the pi/2 rotates the coordinate system from
	#theta=0 at negative y-axis (local time) to
	#theta=0 at positive x axis (traditional polar coordinates)
	
	#calculate the vector magnitudes
	magnitudes = sqrt(plot_data[:,3]**2+plot_data[:,4]**2);
   
	#calculate the scaling factors (the percentage of the maximum length each vector will be
	#maximum length corresponds to a magnitude equal to data_range(2)
   
	sfactors = (magnitudes)/(max_magnitude)
   
	#normalize so each vector has unit magnitude
	scaled_data_e = plot_data[:,3]/magnitudes;
	scaled_data_n = plot_data[:,4]/magnitudes;
   
	#stretch all vectors to the maximum vector length and then scale them
	#by each's individual scale factor
	scaled_data_e = (scaled_data_e*sfactors)*max_vec_len;
	scaled_data_n = (scaled_data_n*sfactors)*max_vec_len;
   
	#finally rotate the coordinate system for the datavar from E_hat,N_hat to r_hat,theta_hat
   
	#first if in the northern hemisphere, flip the sign of the N_hat component to be radially outward (away from the pole)
   
	if(latlim > 0):
		scaled_data_n = -1*scaled_data_n
	
	X = plot_data[:,1]*cos(plot_data[:,2])        
	Y = plot_data[:,1]*sin(plot_data[:,2])
	r_hat = column_stack((cos(plot_data[:,2]),sin(plot_data[:,2])))
	th_hat = column_stack((-1*sin(plot_data[:,2]),cos(plot_data[:,2]))) 
	

	Y_E = ones_like(X)*scaled_data_e
	X_E = zeros_like(X)

	Y_N = ones_like(X)*scaled_data_n
	X_N = zeros_like(X)

	#Set up an order so the largest values get plotted first
	C_N = argsort(scaled_data_n)[::-1]
	C_E = argsort(scaled_data_e)[::-1]

	C_N = concatenate((C_N[:10],C_N[range(10,len(C_N),skip)]))
	C_E = concatenate((C_E[:10],C_E[range(10,len(C_E),skip)]))

	#Use the original component data as the color
	ax_e.quiver(X[C_E],Y[C_E],X_E[C_E],Y_E[C_E],plot_data[C_E,3],angles='xy',units='xy',width=.2,
		clim=[-.5*max_magnitude,.5*max_magnitude],scale_units='xy',scale=1,label=labeltext,alpha=.5,cmap=cmap)
	ax_n.quiver(X[C_N],Y[C_N],X_N[C_N],Y_N[C_N],plot_data[C_N,4],angles='xy',units='xy',width=.2,
		clim=[-.5*max_magnitude,.5*max_magnitude],scale_units='xy',scale=1,label=labeltext,alpha=.5,cmap=cmap)
	
	if labeltrack:
		if labeljustify=='right':
			ax_e.text(X[-1],Y[-1],satname,color=color,va='top',ha='right',fontsize=fontsize)
			ax_n.text(X[-1],Y[-1],satname,color=color,va='top',ha='right',fontsize=fontsize)
			
		elif labeljustify=='left':
			ax_e.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
			ax_n.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
			
		elif labeljustify=='auto':
			if len(X) > 1:
				if (X[-1] > 0 and X[-2] < X[-1]) or (X[-1] < 0 and X[-2] > X[-1]):
					ax_e.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
					ax_n.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
					
				elif (X[-1] > 0 and X[-2] > X[-1]) or (X[-1] < 0 and X[-2] < X[-1]):
					ax_e.text(X[-1],Y[-1],satname,color=color,va='top',ha='right',fontsize=fontsize)
					ax_n.text(X[-1],Y[-1],satname,color=color,va='top',ha='right',fontsize=fontsize)
				else:
					ax_e.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
					ax_n.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
					
			else:
				ax_e.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)
				ax_n.text(X[-1],Y[-1],satname,color=color,va='top',ha='left',fontsize=fontsize)

	#plot the reference arrow
	ref_sfactor = reference_vector_len/max_magnitude
	ref_X1 = (sqrt(reference_vector_len**2/2)/reference_vector_len*ref_sfactor)*max_vec_len
	ref_Y1 = (sqrt(reference_vector_len**2/2)/reference_vector_len*ref_sfactor)*max_vec_len
	ax_e.quiver(-abs(latlim)+15,-abs(latlim)+5,ref_X1,ref_Y1,angles='xy',units='xy',width=.2,color='black',scale_units='xy',scale=1,label=reference_vector_label)
	ax_e.text(-abs(latlim)+15,-abs(latlim)+5,reference_vector_label,color='black',va='top',size=8) 
	if plottime:
		for d in linspace(0,len(plot_data)-1,ntimes).tolist():
			t = datetime.datetime(2000,1,1)+datetime.timedelta(seconds=plot_data[d,0])
			ax_e.text(X[d],Y[d],t.strftime('%X'),color=color,va='top',ha=timejustify,fontsize=fontsize,alpha=.75)
			ax_n.text(X[d],Y[d],t.strftime('%X'),color=color,va='top',ha=timejustify,fontsize=fontsize,alpha=.75)   
	return ax_e,ax_n 

def greatCircleDist(location1,location2,lonorlt='lt'):
	#Returns n angular distances in radians between n-by-2 numpy arrays
	#location1, location2 (calculated row-wise so diff between 
	#location1[0,] and location2[0,]
	#assuming that these arrays have the columns lat[deg],localtime[hours] 
	#and that they are points on a sphere of constant radius
	#(the points are at the same altitude)
	azi2rad = pi/12. if lonorlt=='lt' else pi/180
	wrappt = 24. if lonorlt=='lt' else 360.
	#Bounds check
	over = location1[:,1] > wrappt
	under = location1[:,1] < 0.
	location1[over,1]=location1[over,1]-wrappt
	location1[under,1]=location1[under,1]+wrappt
	
	if location1.ndim == 1 or location2.ndim == 1:    
		dphi = abs(location2[1]-location1[1])*azi2rad
		a = (90-location1[0])/360*2*pi #get the colatitude in radians
		b = (90-location2[0])/360*2*pi
		C =  np.pi - np.abs(dphi - np.pi)#get the angular distance in longitude in radians
	else:
		dphi = abs(location2[:,1]-location1[:,1])*azi2rad
		a = (90-location1[:,0])/360*2*pi #get the colatitude in radians
		b = (90-location2[:,0])/360*2*pi
		C =  np.pi - np.abs(dphi - np.pi)#get the angular distance in longitude in radians
	return arccos(cos(a)*cos(b)+sin(a)*sin(b)*cos(C))
 
def greatCircleMidpoint(location1,location2,angDist='compute',lonorlt='lt'):
	#Finds the midpoint lat and lt or lon between two locations along a great circle arc
	#Can pass angDist as an array to speed up process if already computed,
	#otherwise computes as needed using above function
	azi2rad = np.pi/12. if lonorlt=='lt' else np.pi/180
	wrappt = 24. if lonorlt=='lt' else 360.
	#Bounds check
	over = location1[:,1] > wrappt
	under = location1[:,1] < 0.
	location1[over,1]=location1[over,1]-wrappt
	location1[under,1]=location1[under,1]+wrappt
	
	if location1.ndim == 1 or location2.ndim == 1:        
		a = (90-location1[0])*azi2rad
		b = (90-location2[0])*azi2rad
		if angDist is 'compute':
			c = greatCircleDist(location1,location2,lonorlt=lonorlt)
		else:
			c = angDist
		C = (location2[1]-location1[1])/24*2*np.pi
		azi1 = location1[1]
	else:
		a = (90-location1[:,0])*azi2rad
		b = (90-location2[:,0])*azi2rad
		if angDist is 'compute':            
			c = greatCircleDist(location1,location2,lonorlt=lonorlt)
		else:
			c = angDist
		C = (location2[:,1]-location1[:,1])/24*2*np.pi
		azi1 = location1[:,1]

	#original: g = arccos((cos(b)-cos(c)*cos(a)*sin(c/2))*sin(c/2)/sin(c)+cos(a)*cos(c/2))
	cos_g = cos(a)*cos(c/2)+((cos(b)-cos(a)*cos(c))/sin(c))*sin(c/2)
	g = arccos(cos_g)
	sin_I = (sin(c/2)*sin(b)*sin(C)/(sin(c)*sin(g)))
	I = arcsin(sin_I)
	lat_mid = 90-g/(2*pi)*360 
	azi_mid = azi1+I/(2*pi)*24
	#lt_mid[lt_mid>=24] = lt_mid[lt_mid>=24]-24
		#print "(%.2f-%.2f:%.2f,%.2f-%.2f:%.2f,g=%.2f,I=%.2f)" % (location1[0],location2[0],lat_mid,location1[1],location2[1],lt_mid,g/(2*pi)*360,I/(2*pi)*24)
	return lat_mid, azi_mid 

def azi_difference(azi1,azi2, lonorlt='lt'):
	"""Difference between two longitudes or local times (azi2-azi1), taking into account
		wrapping"""
	azi2rad = np.pi/12. if lonorlt=='lt' else np.pi/180.
	return np.arctan2(np.sin(azi2*azi2rad-azi1*azi2rad), np.cos(azi2*azi2rad-azi1*azi2rad))/azi2rad
	
def cubic_bez_arc(lat,azi1,azi2,lonorlt='lt'):
	"""Returns the control point locations for a cubic bezier curve approximation of the arc between
		azi1 and azi2 at a radius of 90-abs(lat)"""
	#From: http://hansmuller-flex.blogspot.com/2011/04/approximating-circular-arc-with-cubic.html
	#The derivation of the control points for an arc of less than 90 degrees is a little more complicated.  
	#If the arc is centered around the X axis, then the length of the tangent line is r * tan(a/2), 
	#instead of just r.   The magnitude of the vector from each arc endpoint to its control point is k * r * tan(a/2).
	k = 4/3*(np.sqrt(2)-1) #Magic number for bezier curve arc approximations
	
	azi2rad = np.pi/12. if lonorlt=='lt' else np.pi/180.
	maxazi = 24. if lonorlt=='lt' else 360.

	r = 90.-np.abs(lat)
	theta = azi_difference(azi1,azi2,lonorlt=lonorlt)
	midpoint = azi1+theta/2.
	tangent_len = r*np.tan(theta*azi2rad/2) # Length of tangent line
	r_cp = np.sqrt(r**2+(tangent_len/2)**2)
	th_cp = theta*k/2

	cp_lat = (90.-r_cp)*np.sign(lat)
	cp1_lonorlt = midpoint-th_cp
	cp2_lonorlt = midpoint+th_cp
	return cp_lat,cp1_lonorlt,cp2_lonorlt 

def polarbinplot(ax,bin_edges,bin_values,hemisphere='N',lonorlt='lt',**kwargs):
	"""
		Plots a collection of bins in polar coordinates on a dialplot
		bin_edges - numpy.ndarray
			Must have shape: n x 4 with columns:
			bin_lat_start,bin_lat_end,bin_lonlt_start,bin_lonlt_end 
		lonorlt - 'lon' or 'lt'
			Use longitude or localtime as azimuthal coordinate
	"""	
	from matplotlib.path import Path
	from matplotlib.patches import PathPatch
	from matplotlib.collections import PatchCollection

	#Compute number of bins
	nbins = len(bin_edges[:,0])

	azifun = latlon2cart if lonorlt=='lon' else latlt2cart
	azi2rad = np.pi/12. if lonorlt=='lt' else np.pi/180.

	#Get the color scale extents
	vmax = nanmax(bin_values) if 'vmax' not in kwargs else kwargs['vmax']
	vmin = nanmin(bin_values) if 'vmin' not in kwargs else kwargs['vmin']

	logscale = False
	if not logscale:
		norm = Normalize(vmin=vmin,vmax=vmax)
	else:
		norm = LogNorm(vmin=vmin,vmax=vmax)

	#mappable = matplotlib.cm.ScalarMappable(norm=norm,cmap=)
	#mappable.set_array(bin_values)	
	hemifac = -1. if hemisphere == 'S' else 1.
	#Control point for 4 point bezier curves
	#Will put control points at the edge of the plot and at mlt1 and mlt2, 
	#so that the curve approximates a circular arc
	
	cp12_lat,cp12_lonorlt1,cp12_lonorlt2 = cubic_bez_arc(bin_edges[:,0],bin_edges[:,2],bin_edges[:,3],lonorlt=lonorlt)
	cp34_lat,cp34_lonorlt1,cp34_lonorlt2 = cubic_bez_arc(bin_edges[:,1],bin_edges[:,3],bin_edges[:,2],lonorlt=lonorlt)
	
	#for n in range(5):
	#	print "lat: "+str(bin_edges[n,0])+" lt1: "+str(bin_edges[n,2])+" lt2: "+str(bin_edges[n,3])
	#	print "lat_cp: "+str(cp12_lat[n])+" lt1_cp: "+str(cp12_lonorlt1[n])+" lt2_cp: "+str(cp12_lonorlt2[n])

	X1,Y1 = azifun(bin_edges[:,0],bin_edges[:,2],hemisphere)
	X12c1,Y12c1 = azifun(cp12_lat,cp12_lonorlt1,hemisphere)
	X12c2,Y12c2 = azifun(cp12_lat,cp12_lonorlt2,hemisphere)
	X2,Y2 = azifun(bin_edges[:,0],bin_edges[:,3],hemisphere)
	X3,Y3 = azifun(bin_edges[:,1],bin_edges[:,3],hemisphere)
	X34c1,Y34c1 = azifun(cp34_lat,cp34_lonorlt1,hemisphere)
	X34c2,Y34c2 = azifun(cp34_lat,cp34_lonorlt2,hemisphere)
	X4,Y4 = azifun(bin_edges[:,1],bin_edges[:,2],hemisphere)
	
	control_codes = [Path.MOVETO,Path.CURVE4,Path.CURVE4,Path.CURVE4,
					Path.LINETO,Path.CURVE4,Path.CURVE4,Path.CURVE4,Path.LINETO]
	
	patches = [] #Path patches which make up the plot
	color_arr = []
	for ib in range(nbins):
		if np.isfinite(bin_values[ib]):
			verticies = [(X1[ib],Y1[ib]),(X12c1[ib],Y12c1[ib]),(X12c2[ib],Y12c2[ib]),(X2[ib],Y2[ib]),
							(X3[ib],Y3[ib]),(X34c1[ib],Y34c1[ib]),(X34c2[ib],Y34c2[ib]),(X4[ib],Y4[ib]),(X1[ib],Y1[ib])]

			patches.append(PathPatch(Path(verticies,control_codes))) #Bool argument is explicitly closed shape
			color_arr.append(bin_values[ib])
	#Now make it into a Collection (which is a subclass of ScalarMappable)
	mappable = PatchCollection(patches,
		cmap=matplotlib.cm.jet if 'cmap' not in kwargs else kwargs['cmap'],
		alpha=.9 if 'alpha' not in kwargs else kwargs['alpha'],
		edgecolor="None" if 'edgecolor' not in kwargs else kwargs['edgecolor'],
		norm=norm)
	
	mappable.set_array(np.array(color_arr).flatten())

	ax.add_collection(mappable)

	return mappable

def rolling_window(a, window):
	"""Make for the lack of a decent moving average"""
	shape = a.shape[:-1] + (a.shape[-1] - window + 1, window)
	strides = a.strides + (a.strides[-1],)
	return np.lib.stride_tricks.as_strided(a, shape=shape, strides=strides)

def moving_average(x,window_size):
	"""Creates a weighted average smoothed version of x using the weights in window"""
	return np.nanmean(rolling_window(np.concatenate((x[:window_size/2],x,x[-window_size/2+1:])),window_size),-1)

def moving_median(x,window_size):
	"""Creates a weighted average smoothed version of x using the weights in window"""
	return np.nanmedian(rolling_window(np.concatenate((x[:window_size/2],x,x[-window_size/2+1:])),window_size),-1)

	