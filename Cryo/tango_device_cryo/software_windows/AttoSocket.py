from PyAttoDRY import AttoDRY
import time
import socket
import threading

# define the host and port (host is this computer)
host = '192.168.1.8'
port = 11000
con = True

# create socket.
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind((host, port))
		

data, addr = s.recvfrom(128)
print('The server started and ready to accept requests from intermag-d15...')
print('The IP address and port are: ' + str(addr))

# keep the communication channel open...
while con == True:
	# receive the data...
	data, addr = s.recvfrom(128)
	data  = data.decode('utf-8')
	# print(data) # for debugging
	if data == 'ON':
		print('Connect to the AttoDRY')
		AttoDRY.begin(setup_version=1)
		AttoDRY.Connect(COMPort='COM4')
		# little trick to let the attoDRY initialize properly without loosing connection...
		for i in range(0,10):
			s.sendto(data.encode('utf-8'),addr)
			time.sleep(1.0)
		IN = AttoDRY.isDeviceInitialised()
		CN = AttoDRY.isDeviceConnected()
		# state that it is initialized and connected:
		if IN==1:
			print('The AttoDRY device is initialized... ')
		if CN==1:
			print('... and connected.')

	elif data == 'Read':
		iCF = AttoDRY.isControllingField()
		time.sleep(0.01)
		iCT = AttoDRY.isControllingTemperature()
		time.sleep(0.01)
		iCP = AttoDRY.isPersistentModeSet()
		time.sleep(0.01)
		gMF = AttoDRY.getMagneticField()
		time.sleep(0.01)
		gST = AttoDRY.getSampleTemperature()
		time.sleep(0.01)
		package = 'ReadA'+str(iCF)+'B'+str(iCT)+'C'+str(iCP)+'D'+str(gMF)+'E'+str(gST)+'F'
		#print(package)
		s.sendto(package.encode('utf-8'),addr)
		#print(package)

# write attributes....
	elif data[:1] == 'W':
		if data[:4] == 'W001':
			AttoDRY.setUserMagneticField(float(data[5:]))
		elif data[:4] == 'W002':
			AttoDRY.setUserTemperature(float(data[5:]))
		elif data == 'W003':
			AttoDRY.toggleMagneticFieldControl()
		elif data == 'W004':
			AttoDRY.toggleFullTemperatureControl()
		elif data == 'W005':		
			AttoDRY.togglePersistentMode()

	# s.sendto(data.encode('utf-8'),addr)

	if data == 'OFF':
		# disconnects and ends everything...
		AttoDRY.Disconnect()
		AttoDRY.end()
		# close the connection...
		s.sendto(data.encode('utf-8'),addr)
		con = False
		s.close()