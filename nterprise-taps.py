#!/usr/bin/python
#	
#	nterprise TAPS Phone Deployment Service
#	Version: 1.2.1
#
#   2018-05-31 - Added interactive bot functionality with /commands
#
#	Written by: Yli Schwartzman
#	Email:	yli.schwartzman@nfrastructure.com
#	Copyright 2018 Zones nfrastructure
#
#	This program can be used to deploy enhanced TAPS-like deployment services to a CUCM system
#	The service is presented as an XML service to the phone display
#
#	The AutoRegistration device templates in CUCM should be configured with
#	the 'Idle' service URL pointed to this web service in the following format:
#		http://<webserverfqdn>:<flaskPort>/taps?=#DEVICENAME#
#	Also be sure to set the Idle timer to a reasonable value such as 5-10 seconds
#	This ensures the service automatically shows up, but also allows you to exit and have time to dial if needed
#	You could also optionally configure an IP Phone Service instead of using the Idle URL
#	Be sure all your phones (especially the auto-reg phone template) have Web Access enabled
#		This is needed to gather Serial numbers, CDP, etc
#
#	A 'Support' softkey is provided to automatically call whatever the 'supportDN' configured below is
#	This helps techs quickly access support during deployment
#
#	Procedure:
#		Enter the desired extension matching the CUCM template you wish to apply to the phone
#			If you have a 'dnPrefix' configured, you only need to enter the remaining digits
#			Enter any additional custom data fields as required for your deployment
#		If there are no matches, or no matches match your phone model you will see an error.
#		If there is only one valid match it will automatically proceed.
#		If there are multiple matches:
#			You will be presented with a list of all phones with that as their PRIMARY extension
#				Devices with secondary lines matching will not be shown
#			Scroll and select the correct tempate to apply to this phone
#		If successful, the phone will reboot and come up with the new configuration
#		If unsuccessful, the phone will reboot and auto-register again.
#
#	Run on Windows Server or PC (2012+ recommended)
#		IIS
#		CUCM AXL and RIS Schema
#			Download from CUCM server 'plugins' page
#			Currently tested with v10.5 - v12.0
#		Script & Files
#		/nterprise-taps
#			nterprise-taps.py
#			nterprise-taps.db  (Created after first runtime)
#				/axl/...	(Extract CUCM AXL files here)
#				/static
#					/css
#						nterprise-taps.css
#					/img
#						greencheck.png
#						redx.png
#					/js
#						sortable.js
#					/fonts
#				/templates
#					nterprise-taps-log.html
#	Required Python version and module dependencies:
#		SQLite3
#		Python 3.5.x
#			pip
#			ssl,csv,random,time,uuid,requests,datetime,re
#			flask
#			flask_sqlalchemy
#			suds
#			zeep
# 			lxml >> set STATICBUILD=true && pip install lxml
#
#
#
#	TAPS Undo Service
#		To create an optional service for converting configured phones back into BAT Templates
#		Setup an IP Phone Service with Enterprise Subscription
#		Point to Standard XML Service URL:
#			<tapsURL>/tapsUndo?name=#DEVICENAME#
#		Select the service on the phone to initate the TAPS UNDO process.
#
#
#	LOGGING
#		Log records of all transactions are kept in a sqlite3 database file
#		They are visible as a sortable table at this URL:
#			<tapsURL>/tapsLog
#		They are downloadable as a CSV at this URL:
#			<tapsURL>/tapsLog.csv
#
#

import ssl,csv,random,time,uuid,requests,datetime
import re
from lxml import etree, html
from flask_sqlalchemy import SQLAlchemy
from flask.ext.sqlalchemy import get_debug_queries
from suds.xsd.doctor import Import
from suds.xsd.doctor import ImportDoctor
from suds.transport.https import HttpAuthenticated
from suds.client import Client
from zeep import Transport
from zeep import Client as zClient
from zeep.helpers import serialize_object
import logging
import logging.config
from flask import *
import pdb

app = Flask(__name__)

#===================================================================================#
nterpriseLogo = 'https://mycompany.com/img/end-to-end/phonetic-dark.jpg'

# GLOBAL VARIABLES AND CUSTOM PARAMETERS

customerName = 'mycompany'
customerDomain = 'mycompany.com'
projectName = 'VoIP Deployment'
# Customer ID Number
nterpriseCID = '70'
# Project ID Number
nterprisePID = '320'

# Enter the port to run this TAPS service on (Flask default is 5000)
flaskPort = 5555
# Enter the root URL and Port number for this TAPS service
tapsURL = 'http://server.mycompany.com:'+str(flaskPort)+'/'

# Web Server path to where the success and failure image files are hosted. 
imageServerPath = 'static/img/'

# Path where WSDL AXL API Schema is installed on web server
cucmVersion = '12.0'
axlPath = 'axl/schema/'+cucmVersion+'/AXLAPI.wsdl'
risPath = 'axl/ris/'+cucmVersion+'/RISService70.wsdl'

# Enter the FQDN (or IP) and Port (usually 8443) for the CUCM Publisher
cucmServer = 'cucm.mycompany.com'
cucmPort = '8443'
# Enter the username and password for the CUCM AXL user to connect
#		'Standard AXL API Admin' and 'Standard CCM Server Monitoring' Roles required
cucmUsername = 'username'
cucmPassword = 'password'
# Enter the FQDN (or IP) and Port (usually 8443) for the Unity Publisher
unityServer = 'unity.mycompany.lab'
unityPort = '8443'
# Enter the username and password for the Unity user to connect
#		'System Administrator' Roles required
unityUsername = 'username'
unityPassword = 'password'

# If you have long extensions with the same prefix you can enter it here so that
# you only have to enter, for example, the last 4 digits of a 10-digit extension
dnPrefix = '555'

# Enter the AutoReg Extension Prefix to prevent TAPS-Undo of auto-reg phones
autoRegPrefix = '999'

# Phone number to dial for deployment support
supportDN = '5551234'

# True = Default - only matches that are BAT templates will be presented, typical for a deployment
# False = both BAT and SEP devices will be presented, ideal for phone swaps
batOnly = False

# Custom Data Collection Fields (Set 'False' to disable if not needed)
customFieldEnable = False
# Configure a label and datatype for up to 4 data collection fields
# If not using some, set the Name to '', do not disable or invalidate the field
# Data Types (Default is Alphanumeric):
#	A - Alphanumeric ASCII Text
#	T - Telephone Number
#	N - Numeric
#	U - Uppercase
#	L - Lowercase
#	P - Password
#	E - Equation / Math
customFieldName1 = 'Jack #'
customFieldType1 = 'A'

customFieldName2 = 'Room #'
customFieldType2 = 'A'

customFieldName3 = 'Asset Tag'
customFieldType3 = 'N'

customFieldName4 = 'Comment'
customFieldType4 = 'A'

# Webex Teams Monitoring API Variables
# Room 'Note to Self > TAPS'
roomId = ''
# Bot Name 'nterpriseTAPS@sparkbot.io'
botToken = ''
botEmail = 'botName@sparkbot.io'
# WebHook ID
webHookId = ''

# Register Webex Teams WebHooks
# def webHookRegister():
# 	headers = {'content-type':'application/json; charset=utf-8', 'authorization':'Bearer '+botToken}
# 	data = {'resource':'messages', 'event':'created', 'filter':'roomId='+roomId, 'targetUrl':'http://0538c75b.ngrok.io/webHook', 'name':'nterprise TAPS WebHook'}
# 	webhookRegister = requests.post('https://api.ciscospark.com/v1/webhooks', headers=headers, data=json.dumps(data))
# 	print(webhookRegister.content)
# 	return()
# webHookRegister()

#===================================================================================#

# Ignore SSL Warnings
ssl._create_default_https_context = ssl._create_unverified_context

# SOAP CONFIG
tns = 'http://schemas.cisco.com/ast/soap/'
imp = Import('http://schemas.xmlsoap.org/soap/encoding/', 'http://schemas.xmlsoap.org/soap/encoding/')
imp.filter.add(tns)

axlWsdl = 'http://localhost/'+axlPath
axlLocation = 'https://' + cucmServer + ':' + cucmPort + '/axl/'
risWsdl = 'http://localhost/'+risPath
risLocation = 'https://' + cucmServer + ':' + cucmPort + '/realtimeservice2/services/RISService70'

axlClient = Client(axlWsdl,location=axlLocation, faults=False, transport=HttpAuthenticated(username=cucmUsername,password=cucmPassword), plugins=[ImportDoctor(imp)])
risClient = zClient(wsdl=risWsdl, transport=Transport(http_auth=(cucmUsername,cucmPassword), verify=False))

# GENERAL PYTHON DEBUGGER TOOL - PUT THIS WHERE YOU WANT TO BREAK/STEP CODE
# pdb.set_trace()
#
# ENABLE ZEEP DEBUGGING
# logging.config.dictConfig({
#     'version': 1,
#     'formatters': {
#         'verbose': {
#             'format': '%(name)s: %(message)s'
#         }
#     },
#     'handlers': {
#         'console': {
#             'level': 'DEBUG',
#             'class': 'logging.StreamHandler',
#             'formatter': 'verbose',
#         },
#     },
#     'loggers': {
#         'zeep.transports': {
#             'level': 'DEBUG',
#             'propagate': True,
#             'handlers': ['console'],
#         },
#     }
# })

# ENABLE SUDS DEBUGGING
# logging.basicConfig(level=logging.INFO)
# logging.getLogger('suds').setLevel(logging.DEBUG)

# ENABLE REQUESTS DEBUGGING
# import http.client as http_client
# http_client.HTTPConnection.debuglevel = 1
# # You must initialize logging, otherwise you'll not see debug output.
# logging.basicConfig()
# logging.getLogger().setLevel(logging.DEBUG)
# requests_log = logging.getLogger('requests.packages.urllib3')
# requests_log.setLevel(logging.DEBUG)
# requests_log.propagate = True

#===================================================================================#

# SQLite3 Database Creation

# ENABLE SQLALCHEMY DEBUGGING
# logging.basicConfig()
# logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
# app.config['SQLALCHEMY_RECORD_QUERIES'] = True

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///nterprise-taps.db'
db = SQLAlchemy(app)

class nterprise_taps(db.Model):
    __tablename__ = 'nterprise_taps'
    pkid = db.Column(db.Integer, primary_key=True, autoincrement=True)
    uuid = db.Column(db.String(250), nullable=False)
    date = db.Column(db.String(250), nullable=False)
    time = db.Column(db.String(250), nullable=False)
    device_uuid = db.Column(db.String(250), nullable=True)
    device_name = db.Column(db.String(250), nullable=False)
    extension = db.Column(db.String(250), nullable=True)
    bat_device = db.Column(db.String(250), nullable=True)
    model = db.Column(db.String(250), nullable=True)
    device_pool = db.Column(db.String(250), nullable=True)
    description = db.Column(db.String(250), nullable=True)
    owner_uuid = db.Column(db.String(250), nullable=True)
    owner_userid = db.Column(db.String(250), nullable=True)
    vm_uuid = db.Column(db.String(250), nullable=True)
    vm_alias = db.Column(db.String(250), nullable=True)
    device_ip = db.Column(db.String(250), nullable=True)
    vlan = db.Column(db.String(250), nullable=True)
    serial = db.Column(db.String(250), nullable=True)
    version = db.Column(db.String(250), nullable=True)
    sidecars = db.Column(db.String(250), nullable=True)
    cdp_hostname = db.Column(db.String(250), nullable=True)
    cdp_ip = db.Column(db.String(250), nullable=True)
    cdp_port = db.Column(db.String(250), nullable=True)
    success = db.Column(db.Boolean, default=0, nullable=False)
    reason = db.Column(db.String(250), nullable=True)
    task_type = db.Column(db.String(250), nullable=True)
    custom1 = db.Column(db.String(250), nullable=True)
    custom2 = db.Column(db.String(250), nullable=True)
    custom3 = db.Column(db.String(250), nullable=True)
    custom4 = db.Column(db.String(250), nullable=True)

db.create_all()

#======================================================================================================#
#======================================================================================================#
#======================================================================================================#


# Phone Service Submit Form
@app.route('/taps', methods=['GET'])
def taps():
	deviceName = request.args.get('name')
	print(deviceName)
	mac = deviceName[3:]

	urlSuffix = 'getPhones'

	# If using Custom Fields, include them in XML Data
	if customFieldEnable == True:
		customFieldsXML = '''		<InputItem>
			<DisplayName>'''+customFieldName1+'''</DisplayName>
			<QueryStringParam>custom1</QueryStringParam>
			<DefaultValue></DefaultValue>
			<InputFlags>'''+customFieldType1+'''</InputFlags>
		</InputItem>
		<InputItem>
			<DisplayName>'''+customFieldName2+'''</DisplayName>
			<QueryStringParam>custom2</QueryStringParam>
			<DefaultValue></DefaultValue>
			<InputFlags>'''+customFieldType2+'''</InputFlags>
		</InputItem>
		<InputItem>
			<DisplayName>'''+customFieldName3+'''</DisplayName>
			<QueryStringParam>custom3</QueryStringParam>
			<DefaultValue></DefaultValue>
			<InputFlags>'''+customFieldType3+'''</InputFlags>
		</InputItem>
		<InputItem>
			<DisplayName>'''+customFieldName4+'''</DisplayName>
			<QueryStringParam>custom4</QueryStringParam>
			<DefaultValue></DefaultValue>
			<InputFlags>'''+customFieldType4+'''</InputFlags>
		</InputItem>'''
	else:
		customFieldsXML = ''

	xml = '''<?xml version='1.0' encoding='UTF-8'?>
<CiscoIPPhoneInput>
	<Title>nterprise TAPS</Title>
	<Prompt>Enter the new phone extension</Prompt>
	<URL method='get'>'''+tapsURL+urlSuffix+'''?mac='''+mac+'''</URL>
		<InputItem>
			<DisplayName>Extension</DisplayName>
			<QueryStringParam>exten</QueryStringParam>
			<DefaultValue>'''+dnPrefix+'''</DefaultValue>
			<InputFlags>T</InputFlags>
		</InputItem>
'''+customFieldsXML+'''
	<SoftKeyItem>
		<Name>Submit</Name>
		<URL>SoftKey:Submit</URL>
		<Position>1</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>&lt;&lt;</Name>
		<URL>SoftKey:&lt;&lt;</URL>
		<Position>2</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneInput>'''

	return(Response(xml, mimetype='text/xml'))

#===================================================================================#

# Search for matching devices
@app.route('/getPhones', methods=['GET'])
def getPhones():
	transactionUUID = uuid.uuid4().urn[9:]
	success = 0
	exten = str(request.args.get('exten'))
	mac = str(request.args.get('mac'))
	deviceName = 'SEP'+ mac
	custom1 = str(request.args.get('custom1'))
	custom2 = str(request.args.get('custom2'))
	custom3 = str(request.args.get('custom3'))
	custom4 = str(request.args.get('custom4'))
	oneMatch = False
	# print('Extension: '+exten)
	# print('MAC: '+mac)
	# print(custom1)
	# print(custom2)
	# print(custom3)
	# print(custom4)

	urlSuffix = 'configure'
	xmlPrefix = '''<?xml version='1.0' encoding='UTF-8'?>
<CiscoIPPhoneMenu>
	<Title>nterprise TAPS</Title>
	<Prompt>Select the correct device</Prompt>
	'''
	xmlSuffix = '''	<SoftKeyItem>
		<Name>Select</Name>
		<URL>SoftKey:Select</URL>
		<Position>1</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneMenu>'''

	# tkclass=1 means Phones only
	# tkpatternusage=2 means Directory Numbers only
	# d.isactive says whether it is a BAT template (false) or not (true)
	response = axlClient.service.executeSQLQuery(sql='SELECT d.name, d.tkmodel, tm.name AS model, d.description, n.dnorpattern, d.isactive FROM device AS d INNER join devicenumplanmap AS dmap on dmap.fkdevice=d.pkid INNER join numplan AS n on dmap.fknumplan=n.pkid INNER join typeclass AS tc on d.tkclass=tc.enum INNER join typemodel AS tm on d.tkmodel=tm.enum WHERE d.tkclass=1 and n.tkpatternusage=2 and n.dnorpattern like \''+exten+'\'')
	# print(response[0])
	# print(response[1])
	# Check that some match was returned
	if response[1]['return'] == '':
		# print('NO MATCH')
		numResults = 0
	else:
		result = response[1]['return'].row
		numResults = len(result)
		# print('Number of Results: '+str(numResults))
		response2 = axlClient.service.listPhone({'name': deviceName}, returnedTags={'name':'','model':'','description':'','ownerUserName':'','devicePoolName':''})
		# print(response2)

	reason = ''
	if numResults == 0:
		# Present an error if no matches are found
		# print('No Matches Found')
		reason = 'No Match'
		xml = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS</Title>
	<Prompt>NO MATCHES FOUND</Prompt>
	<Text>There were no matches found for this extension.
Please enter a valid extension.
Contact Support for additional assistance.</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
	</CiscoIPPhoneText>'''
	elif numResults == 1:
		# Verify that the phone models match, if not present an error
		# If successful with only one match, proceed immediately to /configure step.
		if result[0].model == response2[1]['return'].phone[0].model:
			# Don't display the same phone you are calling from as it will get deleted if chosen
			if result[0].name[3:] == mac:
				# print('This is your phone! do not display as an option')
				reason = 'Already Configured'
				xml = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS</Title>
	<Prompt>ALREADY CONFIGURED</Prompt>
	<Text>The only match is the phone you are calling from.
It is already configured.
Contact Support for additional assistance.</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
	</CiscoIPPhoneText>'''
			else:
				success = 1
				batDevice = result[0].name
				model = result[0].model
				description = result[0].description
				oneMatch = True
				# print('One Valid Match Found - Configuring Automatically')
		else:
			reason = 'Wrong Model'
			xml = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS</Title>
	<Prompt>PHONE MODEL MISMATCH</Prompt>
	<Text>There were no valid matches found.
Please check that you are using the correct extension and phone model.
Contact Support for additional assistance</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
	</CiscoIPPhoneText>'''
	elif numResults > 1:
		count = 0
		menuList = ''
		listLength = 0
		while count < len(result):
			# Verify that the phone models match
			if result[count].model == response2[1]['return'].phone[0].model:
				# Check if only BAT devices should be shown or if all matches should be presented
				if batOnly == False:
					# Don't display the same phone you are calling from as it will get deleted if chosen
					if result[count].name[3:] == mac:
						# print('This is your phone! do not display as an option')
						pass
					# Display all matching devices, include name prefix for clarity
					else:
						listLength += 1
						menuList = menuList + '''<MenuItem>
	<Name>'''+result[count].name[:-12]+''' :: '''+result[count].description+''' :: '''+result[count].model+'''</Name>
	<URL>'''+tapsURL+urlSuffix+'''?devSelected='''+result[count].name+'''&amp;mac='''+mac+'''&amp;uuid='''+transactionUUID+'''</URL>
	</MenuItem>
	'''
				else:
					# Only presents phones with isactive set to 'f' which means it is a BAT template
					if result[count].isactive == 'f':
						listLength += 1
						menuList = menuList + '''<MenuItem>
	<Name>'''+result[count].description+''' :: '''+result[count].model+'''</Name>
	<URL>'''+tapsURL+urlSuffix+'''?devSelected='''+result[count].name+'''&amp;mac='''+mac+'''&amp;uuid='''+transactionUUID+'''</URL>
	</MenuItem>
	'''
					else:
						# print('Not a BAT device')
						pass
			count += 1
		# print(menuList)
		if listLength > 0:
			# print('Results Found!')
			xml = xmlPrefix+menuList+xmlSuffix
		else:
			reason = 'Wrong Model'
			xml = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS</Title>
	<Prompt>PHONE MODEL MISMATCH</Prompt>
	<Text>There were no valid matches found.
Please check that you are using the correct extension and phone model.
Contact Support for additional assistance</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
	</CiscoIPPhoneText>'''


	serial = ''
	version = ''
	sidecars = 0
	cdp_hostname = ''
	cdp_ip = ''
	cdp_port = ''
	try:
		# Get Device IP to retrieve Serial number, Firmware version, and # of sidecars
		factory = risClient.type_factory('ns0')
		arrayOfSelectItem = factory.ArrayOfSelectItem(item=factory.SelectItem(Item=deviceName))
		q = risClient.service.selectCmDeviceExt('',{'SelectBy':'Name', 'SelectItems':arrayOfSelectItem, 'DeviceClass':'Phone'})
		# print(q)
		q = serialize_object(q)
		for k, item in q['SelectCmDeviceResult']['CmNodes'].items():
			for k, device in item[0]['CmDevices'].items():
				if deviceName in device[0]['Name']:
						# print('LOCAL: '+device[0]['Name']+' - '+device[0]['IPAddress']['item'][0]['IP']+' - '+device[0]['DirNumber']+' - '+device[0]['Description'])
						deviceIP = device[0]['IPAddress']['item'][0]['IP']
				else:
					# print('FAIL DEVICE IP SEARCH')
					pass

		# Get Phone Web Page with data and extract values

		# Function to parse for specific values in XML Phone Web Interface
		def phoneXMLParse(searchString, searchPage):
				try:
					phoneXML = requests.get('http://'+deviceIP+searchPage, timeout=1)
					# print(phoneXML.content)
					tree = etree.XML(phoneXML.content)
					t = tree.find(searchString).text
					# print(t)
				except:
					t = None
				return(t)

		deviceInfo = '/DeviceInformationX'
		portInfo = '/PortInformationX?1'

		# Values to extract:
		# ROOT Phone Settings
		serial = phoneXMLParse('serialNumber', deviceInfo)

		version = phoneXMLParse('versionID', deviceInfo)

		module0 = phoneXMLParse('addonModule0', deviceInfo)
		module1 = phoneXMLParse('addonModule1', deviceInfo)
		module2 = phoneXMLParse('addonModule2', deviceInfo)
		if module0 != None:
			sidecars += 1
		if module1 != None:
			sidecars += 1
		if module2 != None:
			sidecars += 1

		# NETWORK Settings
		cdp_hostname = phoneXMLParse('CDPNeighborDeviceId', portInfo)
		if cdp_hostname != None:
			cdp_ip = phoneXMLParse('CDPNeighborIP', portInfo)
			cdp_port = phoneXMLParse('CDPNeighborPort', portInfo)
		else:
			cdp_hostname = phoneXMLParse('LLDPNeighborDeviceId', portInfo)
			cdp_ip = phoneXMLParse('LLDPNeighborIP', portInfo)
			cdp_port = phoneXMLParse('LLDPNeighborPort', portInfo)

		# Function to parse for specific values in HTML Phone Web Interface
		def phoneHTMLParse(searchString, searchPage):
				try:
					phoneHTML = requests.get('http://'+deviceIP+searchPage, timeout=5)
					tree = html.fromstring(phoneHTML.content)
					# Removed Xpath 2.0 'lower-case' as it wasn't working - [contains(lower-case(.), ''''+searchString+'''')]
					# t = tree.xpath('''/html/body/table/tr[2]/td[2]/div/table/tr/td/b[text()[contains(., ''''+searchString+'''')]]
						# /parent::td/following-sibling::td[2]/b[text()]''')
					t = tree.xpath('''/html/body/table/tr[2]/td[2]/div/table/tr[17]/td[3]/b[text()]''')
					print(t)
				except:
					t = None
				return(t)
		# URL Path to access
		net = '/CGI/Java/Serviceability?adapter=device.statistics.configuration'
		# Values to extract:
		try:
			t = phoneHTMLParse('ID',net)
			for element in t:
				if element.text != '' and element.text != None:
					vlanID = element.text
				else:
					vlanID = None
		except:
			vlanID = None
		print(vlanID)
	except:
		# print('GET PHONE WEB INTERFACE EXCEPTION')
		raise


	# Check if Voicemail Box exists in Unity with matching extension and grab alias
	vmData = checkVM(exten)
	if int(vmData['@total']) > 0:
		vm_alias = vmData['User']['Alias']
		vm_uuid = vmData['User']['ObjectId']
	else:
		vm_alias = None
		vm_uuid = None


	# CREATE Database Entry
	task_type = 'TAPS'
	# try:
	db.session.add(nterprise_taps(uuid=transactionUUID,date=time.strftime('%Y-%m-%d'),time=time.strftime('%H:%M:%S'),task_type=task_type
		,device_name=deviceName,extension=exten,device_ip=deviceIP,vlan=vlanID,serial=serial,version=version,
		sidecars=sidecars,vm_alias=vm_alias,cdp_hostname=cdp_hostname,cdp_ip=cdp_ip,cdp_port=cdp_port,success=success,reason=reason,
		custom1=custom1,custom2=custom2,custom3=custom3,custom4=custom4,vm_uuid=vm_uuid))
	db.session.commit()

	# POST DATA TO Webex Teams MONITORING ROOM
	for row in db.session.query(nterprise_taps).filter_by(uuid=transactionUUID):
		if task_type == 'ERROR':
			sparkPOST(row)
	# except:
	# 	print('DB Create Failure')
	# 	db.session.rollback()
	db.session.close()

	if oneMatch == True:
		# If there was only one match, skip the search/select list screen
		return(redirect(url_for('configure', devSelected=result[0].name, mac=mac, uuid=transactionUUID)))
	else:
		# print(xml)
		return(Response(xml, mimetype='text/xml'))

#===================================================================================#

# General XML Error Function
def generateFailureXML(reason):
	failureXML = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS</Title>
	<Prompt>ERROR</Prompt>
	<Text>An Error Occurred.
'''+reason+'''
Please try again or contact Support for additional assistance</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
	</CiscoIPPhoneText>'''
	return(failureXML)


# Configure selected device
@app.route('/configure', methods=['GET'])
def configure():
	success = 0
	devSelected = request.args.get('devSelected')
	mac = str(request.args.get('mac'))
	deviceName = 'SEP'+mac
	transactionUUID = str(request.args.get('uuid'))
	# print('UUID: '+transactionUUID)
	# print('Device Selected: '+devSelected)
	# print('MAC: '+mac)

	# devSelected device gets deleted
	# d.name changes from dummy mac to 'mac' variable
	# d.isactive BOOL change to 't'
	response1 = axlClient.service.listPhone({'name': deviceName}, returnedTags={'name': '', 'model': '', 'description': ''})
	# print(response1)
	if response1[0] == 200 and response1[1]['return'].phone[0].name == deviceName:
			removeUUID = response1[1]['return'].phone[0]._uuid
			removeName = response1[1]['return'].phone[0].name
			# print(removeUUID+' : '+removeName)
	else:
		# print('response1 FAILURE')
		reason = 'listPhone 1 Failed'
		failureXML = generateFailureXML(reason)
		return(Response(failureXML, mimetype='text/xml'))

	# Delete the auto-registered phone device to free up the MAC address
	response2 = axlClient.service.removePhone(name=removeName)
	# print(response2)
	if response2[0] == 200 and response2[1]['return'] == removeUUID:
		# print('REMOVE SUCCESS!')
		pass
	else:
		# print('REMOVAL FAILED')
		reason = 'AutoReg Phone not Deleted 1'
		failureXML = generateFailureXML(reason)
		return(Response(failureXML, mimetype='text/xml'))

	# Verify that phone was deleted by searching and finding nothing
	response3 = axlClient.service.listPhone({'name': deviceName}, returnedTags={'name': '', 'model': '', 'description': ''})
	# print(response3)
	# print('VERIFY REMOVAL')
	if response3[1]['return'] == '':
		# print('REMOVE SUCCESS CHECK')
		pass
	else:
		# print('REMOVE FAILED CHECK')
		reason = 'AutoReg Phone not Deleted 2'
		failureXML = generateFailureXML(reason)
		return(Response(failureXML, mimetype='text/xml'))

	# Update the MAC and isActive status on the selected template
	response4 = axlClient.service.updatePhone(name=devSelected, newName=deviceName, isActive='t')
	# print(response4)
	if response4[0] == 200:
		# print('UPDATED SUCCESSFULLY!')
		device_uuid = response4[1]['return'].lower()
		# Remove start/end brackets from string
		device_uuid = device_uuid[1:37]
	else:
		# print('FAILED UPDATE')
		reason = 'Template Update Failed'
		failureXML = generateFailureXML(reason)
		return(Response(failureXML, mimetype='text/xml'))


	# Verify that a search for the MAC address finds the newly updated phone
	response5 = axlClient.service.listPhone({'name': deviceName}, returnedTags={'name':'','model':'','description':'','ownerUserName':'','devicePoolName':''})
	result = response5[1]['return'].phone[0]
	try:
		owner_userid = result['ownerUserName'].value
		owner_uuid = result['ownerUserName']._uuid[1:37].lower()
		device_pool = result['devicePoolName'].value
	except:
		owner_userid = 'Anonymous'
		owner_uuid = None
		device_pool = ''

	print(device_pool)

	# List of phone models that only support text-based XML display
	modelTextOnly = ['Cisco 69', 'Cisco 78', 'Cisco 7937', 'Cisco 8831']

	if response5[0] == 200 and result['_uuid'][1:37].lower() == device_uuid:
		# print('UPDATED SUCCESSFULLY!')

		if result.name == deviceName:
			success = 1
			# print('SUCCESS!')
			if any(model in result.model for model in modelTextOnly):
				# print('Phone cannot display image files')
				xml = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS</Title>
	<Prompt>** SUCCESS! **</Prompt>
	<Text>The phone will now reboot:\r'''+result.description+'''</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneText>'''
			else:
				xml = '''<CiscoIPPhoneImageFile>
	<Title>nterprise TAPS</Title>
	<Prompt>'''+result.description+'''</Prompt>
	<LocationX>0</LocationX>
	<LocationY>0</LocationY>
	<URL>'''+tapsURL+imageServerPath+'''greencheck.png</URL>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneImageFile>'''

	# Show a failure screen if the phone wasn't properly updated
	else:
		success = 0
		reason = 'Updated Phone not Found'
		# print('FAILED - ERROR!')
		if any(model in result.model for model in modelTextOnly):
			# print('Phone cannot display image files')
			xml = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS</Title>
	<Prompt>** FAILED! **</Prompt>
	<Text>The phone will now reboot\rPlease try again or contact Support</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneText>'''
		else:
			xml = '''<CiscoIPPhoneImageFile>
	<Title>nterprise TAPS</Title>
	<Prompt>'''+result.description+'''</Prompt>
	<LocationX>0</LocationX>
	<LocationY>0</LocationY>
	<URL>'''+tapsURL+imageServerPath+'''redx.png</URL>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneImageFile>'''

	print(xml)

	# UPDATE Database Entry Details and Status
	# try:
	tmpQuery = db.session.query(nterprise_taps).filter_by(uuid=transactionUUID)
	print(tmpQuery)
	for row in tmpQuery:
		print(row.uuid+' :: '+row.device_name+' :: '+row.extension)
		row.bat_device = devSelected
		row.model = result.model
		row.device_pool = device_pool
		row.description = result.description
		row.success = success
		if success == 1:
			reason = 'Success!'
			row.reason = reason
		else:
			pass
		row.owner_userid = owner_userid
		row.owner_uuid = owner_uuid
		row.device_uuid = device_uuid

		# POST DATA TO Webex Teams MONITORING ROOM
		sparkPOST(row)
	# except:
	# 	print('sparkPOST Failure')

	db.session.commit()
	# except:
	# 	print('DB Update Failure')
	# 	db.session.rollback()
	db.session.close()

	return(Response(xml, mimetype='text/xml'))
	


#===================================================================================#
#===================================================================================#
#===================================================================================#



# TAPS UNDO SERVICE

# Phone TAPS Undo Service
@app.route('/tapsUndo', methods=['GET'])
def tapsUndo():
	# print('nterprise TAPS UNDO')
	deviceName = request.args.get('name')
	# print('deviceName: '+deviceName)

	xml = '''<?xml version='1.0' encoding='UTF-8'?>
<CiscoIPPhoneMenu>
	<Title>nterprise TAPS Undo</Title>
	<Prompt>Are You Sure?</Prompt>
	<MenuItem>
		<Name>Revert this Phone back to a TAPS Template (BAT)</Name>
		<URL>'''+tapsURL+'''tapsUndoExecute?deviceName='''+deviceName+'''</URL>
	</MenuItem>
	<SoftKeyItem>
		<Name>Submit</Name>
		<URL>SoftKey:Select</URL>
		<Position>1</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneMenu>'''
	# print(xml)

	return(Response(xml, mimetype='text/xml'))

#===================================================================================#

# Convert phone config back to BAT template
@app.route('/tapsUndoExecute', methods=['GET'])
def tapsUndoExecute():
	success = 0
	reason = ''
	deviceName = request.args.get('deviceName')
	# print('Device to UNDO TAPS: '+deviceName)

	response0 = axlClient.service.executeSQLQuery(sql='SELECT d.pkid, d.name, d.tkmodel, tm.name AS model, d.description, n.dnorpattern, d.isactive FROM device AS d INNER join devicenumplanmap AS dmap on dmap.fkdevice=d.pkid INNER join numplan AS n on dmap.fknumplan=n.pkid INNER join typeclass AS tc on d.tkclass=tc.enum INNER join typemodel AS tm on d.tkmodel=tm.enum WHERE d.tkclass=1 and n.tkpatternusage=2 and d.name like \''+deviceName+'\'')
	# print(response0)
	if response0[0] == 200 and response0[1]['return'] != '':
		# print('QUERY SUCCESS!')
		# Verify that you are not trying to undo an AutoReg phone and create bogus templates in the system
		# Requires properly setting the autoRegPrefix global variable
		if response0[1]['return'].row[0].dnorpattern[:len(autoRegPrefix)] == autoRegPrefix:
			reason = 'Cannot TAPS-Undo an AutoReg Phone!'
			failureXML = generateFailureXML(reason)
			return(Response(failureXML, mimetype='text/xml'))
		else:
			exten = response0[1]['return'].row[0].dnorpattern
			device_uuid = response0[1]['return'].row[0].pkid
	else:
		# print('QUERY FAILED')
		reason = 'SQL Query Failed'
		failureXML = generateFailureXML(reason)
		return(Response(failureXML, mimetype='text/xml'))

	alreadyExists = True
	while alreadyExists == True:
		# Generate new Dummy MAC
		dummyMAC = str(random.randint(100000000000,999999999999))
		# print('Dummy MAC: '+dummyMAC)
		# Verify that Dummy MAC is not already in use
		response1 = axlClient.service.listPhone({'name': 'BAT'+dummyMAC}, returnedTags={'name':'','model':'','description':''})
		# print(response1)
		if response1[1]['return'] == '':
			alreadyExists = False

	# Update the device name to BAT+dummyMAC and isActive status to False on the selected phone
	response2 = axlClient.service.updatePhone(name=deviceName, newName='BAT'+dummyMAC, isActive='f')
	# print(response2)
	if response2[0] == 200:
		print('REVERT SUCCESS!')
	else:
		# print('REVERT FAILED')
		reason = 'Phone Revert Update Failed'
		failureXML = generateFailureXML(reason)
		return(Response(failureXML, mimetype='text/xml'))

	# Verify that a search for the MAC address finds the newly reverted phone BAT template
	response3 = axlClient.service.listPhone({'name': 'BAT'+dummyMAC}, returnedTags={'name':'','model':'','description':'','ownerUserName':''})
	result3 = response3[1]['return'].phone[0]
	try:
		owner_userid = result3['ownerUserName'].value
		owner_uuid = result3['ownerUserName']._uuid[1:37].lower()
	except:
		owner_userid = 'Anonymous'
		owner_uuid = None

	if response2[0] == 200 and response3[1]['return'].phone[0].description == response0[1]['return'].row[0].description:
		# print('REVERT CHECK SUCCESS!')
		model = response3[1]['return'].phone[0].model
		description = response3[1]['return'].phone[0].description
	else:
		# print('REVERT CHECK FAILED')
		reason = 'Phone Revert Update Check Failed'
		failureXML = generateFailureXML(reason)
		return(Response(failureXML, mimetype='text/xml'))

	# Verify that the source device is now gone from CUCM (until it subsequently auto-registers again)
	response4 = axlClient.service.listPhone({'name': deviceName}, returnedTags={'name':'','model':'','description':''})
	# print(response4)	
	if response4[0] == 200 and response4[1]['return'] == '':
		# Show a success screen if the phone was properly converted
		success = 1
		# print('SUCCESS!')
		xml = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS Undo</Title>
	<Prompt>** SUCCESSFULLY REVERTED! **</Prompt>
	<Text>The phone will now reboot and auto-register.</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneText>'''
	# Show a failure screen if the phone wasn't properly converted
	else:
		success = 0
		reason = 'SEP Still Exists'
		# print('FAILED - ERROR!')
		xml = '''<CiscoIPPhoneText>
	<Title>nterprise TAPS Undo</Title>
	<Prompt>** FAILED TO REVERT! **</Prompt>
	<Text>The phone will now reboot\rPlease try again or contact Support</Text>
	<SoftKeyItem>
		<Name>Exit</Name>
		<URL>Init:Services</URL>
		<Position>3</Position>
	</SoftKeyItem>
	<SoftKeyItem>
		<Name>Support</Name>
		<URL>Dial:'''+supportDN+'''</URL>
		<Position>4</Position>
	</SoftKeyItem>
</CiscoIPPhoneText>'''
	# print(xml)

	# Check if Voicemail Box exists in Unity with matching extension and grab alias
	vmData = checkVM(exten)
	if int(vmData['@total']) > 0:
		vm_alias = vmData['User']['Alias']
		vm_uuid = vmData['User']['ObjectId']
	else:
		vm_alias = ''
		vm_uuid = ''

	# CREATE Database Entry
	if success == 1:
		reason = 'Success!'
	else:
		pass
	transactionUUID = uuid.uuid4().urn[9:]
	try:
		db.session.add(nterprise_taps(uuid=transactionUUID,date=time.strftime('%Y-%m-%d'),time=time.strftime('%H:%M:%S'),task_type='TAPS-Undo'
				,device_name=deviceName,extension=exten,owner_userid=owner_userid,owner_uuid=owner_uuid,model=model,description=description,bat_device='BAT'+dummyMAC,device_uuid=device_uuid,success=success,reason=reason,vm_alias=vm_alias,vm_uuid=vm_uuid))
		db.session.commit()
		# POST DATA TO Webex Teams MONITORING ROOM
		for row in db.session.query(nterprise_taps).filter_by(uuid=transactionUUID):
			sparkPOST(row)
	except:
		# print('DB Create Failure')
		db.session.rollback()
	db.session.close()

	return(Response(xml, mimetype='text/xml'))



#===================================================================================#
#===================================================================================#
#===================================================================================#



# TAPS LOGS

if customFieldEnable == True:
	logHeader = [('PKID','Date','Time','Type','Success','Reason','Device Name','Extension','Device Pool','Description','User ID','Model','Serial','Version','KEM'
	,'Template Name','Device IP','VLAN','Switch FQDN','Switch IP','Switch Port','Voicemail',customFieldName1,customFieldName2,customFieldName3,customFieldName4)]
else:
	logHeader = [('PKID','Date','Time','Type','Success','Reason','Device Name','Extension','Device Pool','Description','User ID','Model','Serial','Version','KEM'
	,'Template Name','Device IP','VLAN','Switch FQDN','Switch IP','Switch Port','Voicemail')]

def logQuery():
	log = []
	if customFieldEnable == True:
		log = db.session.query(nterprise_taps.pkid, nterprise_taps.date, nterprise_taps.time, nterprise_taps.task_type, nterprise_taps.success
			, nterprise_taps.reason, nterprise_taps.device_name, nterprise_taps.extension, nterprise_taps.device_pool, nterprise_taps.description, nterprise_taps.owner_userid
			, nterprise_taps.model, nterprise_taps.serial, nterprise_taps.version, nterprise_taps.sidecars, nterprise_taps.bat_device
			, nterprise_taps.device_ip, nterprise_taps.vlan, nterprise_taps.cdp_hostname, nterprise_taps.cdp_ip, nterprise_taps.cdp_port
			, nterprise_taps.vm_alias, nterprise_taps.device_uuid, nterprise_taps.owner_uuid, nterprise_taps.vm_uuid, nterprise_taps.custom1, nterprise_taps.custom2, nterprise_taps.custom3, nterprise_taps.custom4).all()
	else:
		log = db.session.query(nterprise_taps.pkid, nterprise_taps.date, nterprise_taps.time, nterprise_taps.task_type, nterprise_taps.success
			, nterprise_taps.reason, nterprise_taps.device_name, nterprise_taps.extension, nterprise_taps.device_pool, nterprise_taps.description, nterprise_taps.owner_userid
			, nterprise_taps.model, nterprise_taps.serial, nterprise_taps.version, nterprise_taps.sidecars, nterprise_taps.bat_device
			, nterprise_taps.device_ip, nterprise_taps.vlan, nterprise_taps.cdp_hostname, nterprise_taps.cdp_ip, nterprise_taps.cdp_port
			, nterprise_taps.vm_alias,nterprise_taps.device_uuid, nterprise_taps.owner_uuid, nterprise_taps.vm_uuid).all()
	db.session.close()
	# Convert from List of Tuples to List of Lists
	log = [list(e) for e in log]
	return(log)

# Print Log Table
@app.route('/tapsLog', methods=['GET'])
def tapsLog():
	log = logQuery()
	# Create Clickable Links for various items
	for row in log:
		# Only add direct links if UUID is available, otherwise link to main search.
		# Device Name Direct Link
		if row[6] != None and row[6] != 'None' and row[6] != '':
			deviceLink = ''
			if row[21] != None and row[21] != 'None' and row[21] != '':
				deviceLink = '?key='+str(row[21])
			row[6] = '<a target=_blank href=https://'+cucmServer+'/ccmadmin/phoneEdit.do'+deviceLink+'>'+str(row[6])+'</a>'
		# Extension Click-to-Dial
		if row[7] != None and row[7] != 'None' and row[7] != '':
			row[7] = '<a href=sip:'+str(row[7])+'>'+str(row[7])+'</a>'
		# Owner User ID Direct Link	
		if row[9] != None and row[9] != 'None' and row[9] != '':
			ownerLink = ''
			if row[22] != None and row[22] != 'None' and row[22] != '':
				ownerLink = '?key='+str(row[22])
			row[9] = '<a target=_blank href=https://'+cucmServer+'/ccmadmin/userEdit.do'+ownerLink+'>'+str(row[9])+'</a>'
		# IP Address Link
		if row[15] != None and row[15] != 'None' and row[15] != '':
			row[15] = '<a href=http://'+str(row[15])+'>'+str(row[15])+'</a>'
		# Voicemail Direct Link
		if row[20] != None and row[20] != 'None' and row[20] != '':
			vmLink = ''
			if row[23] != None and row[23] != 'None' and row[23] != '':
				vmLink = '?op=read&objectId='+str(row[23])
			row[20] = '<a target=_blank href=https://'+unityServer+'/cuadmin/user.do'+vmLink+'>'+str(row[20])+'</a>'
		# Remove UUIDs from list before displaying
		row.pop(23)
		row.pop(22)
		row.pop(21)
	# Links to UC Server Admin Pages
	serverLink = '<a target=_blank href=https://'+cucmServer+':'+cucmPort+'/ccmadmin>CUCM</a> :: <a target=_blank href=https://'+unityServer+':'+unityPort+'/cuadmin>Unity</a>'
	# Link to nterprise Project
	nterpriseLink = '<a target=_blank href=http://intranet.mycompany.net/index.php?option=com_pbxtools&Itemid=67&view=ftmprojwo&PID='+nterprisePID+'>'+customerName+' - '+projectName+'</a>'
	return(render_template('nterprise-taps-log.html', header=logHeader[0],log=log,nterpriseLink=nterpriseLink,serverLink=serverLink))
	

# Download CSV
@app.route('/tapsLog.csv', methods=['GET'])
def tapsLogCSV():
	log = logQuery()
	for row in log:
		# Prefix Backslash for +E.164 Numbers to prevent Excel issues
		if row[7][:1] == '+':
			row[7] = '\\'+row[7]
		row.pop(23)
		row.pop(22)
		row.pop(21)
	# Attach Header
	log[0:0] = logHeader
	# Convert to CSV
	csv = '\n'.join(','.join(map(str,row)) for row in log)
	csv = csv.replace('None','')
	return(Response(csv, mimetype='text/csv'))


#======================================================================================================#
#======================================================================================================#


# Function to check for matching Voicemail box information
def checkVM(exten):
	headers = {'Accept':'application/json'}
	vmGET = requests.get('https://'+unityServer+':'+unityPort+'/vmrest/users?query=(DtmfAccessId is '+exten+')', auth=(unityUsername,unityPassword), headers=headers, verify=False)
	vmData = json.loads(vmGET.text)
	return(vmData)


# Function to POST data to Webex Teams Monitoring Room
def sparkPOST(row):
	# Build page links if data exits
	if row.device_uuid != None or row.device_uuid != '':
		deviceLink = 'phoneEdit.do?key='+str(row.device_uuid)
	else:
		deviceLink = 'phoneEdit.do'
	if row.owner_uuid != None or row.owner_uuid != '':
		ownerLink = 'userEdit.do?key='+str(row.owner_uuid)
	else:
		ownerLink = 'userEdit.do'
	if row.vm_uuid != None or row.vm_uuid != '':
		vmLink = 'user.do?op=read&objectId='+str(row.vm_uuid)
	else:
		vmLink = ''

	if row.task_type == 'TAPS':

		if customFieldEnable == True:
			# If Custom Fields are used, include them
			customFieldsPOST = '''Custom Fields:\n
- '''+customFieldName1+''': **'''+str(row.custom1)+'''**\n
- '''+customFieldName2+''': **'''+str(row.custom2)+'''**\n
- '''+customFieldName3+''': **'''+str(row.custom3)+'''**\n
- '''+customFieldName4+''': **'''+str(row.custom4)+'''**\n'''

		else:
			customFieldsPOST = ''

		# Add Full Device Details for Successful TAPS Completion
		deviceDetails = '''- Firmware:  **'''+str(row.version)+'''**\n
- Sidecars:  **'''+str(row.sidecars)+'''**\n
- Serial Number:  **'''+str(row.serial)+'''**\n
Network:  **'''+str(row.device_ip)+'''**\n
- VLAN ID:  **'''+str(row.vlan)+'''**\n
- Switch FQDN:  **'''+str(row.cdp_hostname)+'''**\n
- Switch IP:  **'''+str(row.cdp_ip)+'''**\n
- Port:  **'''+str(row.cdp_port)+'''**\n
'''+customFieldsPOST

	else:
		deviceDetails = ''
	# Standard POST Data for all transactions
	markdown = '''Project:  **['''+customerName+''' - '''+projectName+'''](http://intranet.mycompany.net/index.php?option=com_pbxtools&Itemid=67&view=ftmprojwo&PID='''+nterprisePID+''')**\n
- Event Log:  **['''+str(row.pkid)+''']('''+tapsURL+'''tapsLog)**\n
- Event Type:  **'''+str(row.task_type)+'''**\n
- Reason Code:  **'''+str(row.reason)+'''**\n
- Date/Time:  **'''+str(row.date)+'''** - **'''+str(row.time)+'''**\n
Device:  **['''+str(row.device_name)+'''](https://'''+cucmServer+'''/ccmadmin/'''+deviceLink+''')** - **'''+str(row.model)+'''**\n
- Extension:  **['''+str(row.extension)+'''](tel:'''+str(row.extension)+''')**\n
- Device Pool:  **'''+str(row.device_pool)+'''**\n
- Description:  **'''+str(row.description)+'''**\n
- Owner UserID:  **['''+str(row.owner_userid)+'''](https://'''+cucmServer+'''/ccmadmin/'''+ownerLink+''')**\n
- Voicemail:  **['''+str(row.vm_alias)+'''](https://'''+unityServer+''':'''+unityPort+'''/cuadmin/'''+vmLink+''')**\n
- ============================
- Template Name: **'''+str(row.bat_device)+'''**\n
'''+deviceDetails+'''
Call **[Support](tel:'''+supportDN+''')**\n
____________________\n'''
	print(markdown)

	headers = {'content-type':'application/json; charset=utf-8', 'authorization':'Bearer '+botToken}
	data = {'roomId':roomId, 'markdown':markdown}
	sparkPOST = requests.post('https://api.ciscospark.com/v1/messages', headers=headers, data=json.dumps(data))
	return()


#======================================================================================================#


# WebHook Events from Webex Teams
teamsHeaders = {'content-type':'application/json; charset=utf-8', 'authorization':'Bearer '+botToken}
apiHost = 'https://api.ciscospark.com/'
apiMessages = 'https://api.ciscospark.com/v1/messages/'

@app.route('/webHook', methods=['GET','POST'])
def webHook():

	postData = request.get_json(force=True)
	print(postData['data'])
	print(postData['data']['personEmail'])

	if postData['data']['personEmail'] == botEmail:
		print('Posted by myself, CANCEL')
		return('')
	else:
		personEmail = postData['data']['personEmail']

	commands = {
	    '/project': 'Show Project Details and Parameters',
	    '/stats': 'View the current project statistics',
	    '/search _####_': 'Search Phone Status by Extension',
	    '/help': 'Get help',
	    '/hello': 'Say hello'
	}

	response = getMessage(apiMessages, postData['data']['id'])
	message = response['text']
	print(message)


	if '/help' in message:
		markdown = sendHelp(commands)
		data = {'roomId':roomId, 'markdown':markdown}
	if '/project' in message:
		markdown = sendProject()
		data = {'roomId':roomId, 'markdown':markdown}
	if '/stats' in message:
		markdown = sendStats()
		data = {'roomId':roomId, 'markdown':markdown}
	if '/search' in message:
		search = re.sub(r"^.*\/search ", "", message)
		print(search)
		markdown = sendSearch(search)
		data = {'roomId':roomId, 'markdown':markdown}
	if '/hello' in message:
		markdown = sendHello(personEmail)
		data = {'roomId':roomId, 'markdown':markdown, 'file':nterpriseLogo}

	print(markdown)
	teamsPOST = requests.post(apiMessages, headers=teamsHeaders, data=json.dumps(data))

	return('', 200)


def getMessage(apiMessages, messageId):
	apiURL = apiMessages + messageId
	print(teamsHeaders)
	page = requests.get(apiURL, headers=teamsHeaders)
	response = page.json()
	return(response)


def sendHello(personEmail):
	post = '## Hello, <@personEmail:' + personEmail + '>!'
	return(post)

def sendHelp(commands):
	post = '### Help Menu\nI understand the following commands:  \n'
	for c in commands.items():
		post = post + "* **%s**: %s \n" % (c[0], c[1])
	return(post)

def sendStats():
	# Gather baseline completion statistics
	totalTAPSSuccess = 0
	totalTAPSFail = 0
	totalTAPSUndo = 0
	for row in db.session.query(nterprise_taps).filter_by(task_type='TAPS',success=True):
		totalTAPSSuccess += 1
	for row in db.session.query(nterprise_taps).filter_by(task_type='TAPS',success=False):
		totalTAPSFail += 1
	for row in db.session.query(nterprise_taps).filter_by(task_type='TAPS-Undo',success=True):
		totalTAPSUndo += 1

	# Subtract any "undo" cases since phone was probably undone and redone so don't want to double count
	totalTAPSSuccess = totalTAPSSuccess	- totalTAPSUndo	

	# Count Totals to list by Model
	modelCount = db.session.execute('select model, count(model) from nterprise_taps group by model;')
	modelPrint = ''
	for row in modelCount:
		if row[0] is None:
			pass
		else:
			modelPrint = modelPrint + '* ' + row[0] + ':  **' + str(row[1]) + '**\n'

	# Count Totals by Device Pool
	dpCount = db.session.execute('select device_pool, count(device_pool) from nterprise_taps group by device_pool;')
	dpPrint = ''
	for row in dpCount:
		if row[0] is None:
			pass
		else:
			dpPrint = dpPrint + '* ' + row[0] + ':  **' + str(row[1]) + '**\n'

	# Phones per hour calculation

	# Total completed for current calendar day
	currentDateTime = str(datetime.datetime.now())
	currentDate = currentDateTime[:10]
	print(currentDate)
	totalTAPSToday = 0
	for row in db.session.query(nterprise_taps).filter_by(task_type='TAPS',success=True,date=currentDate):
		print(row)
		totalTAPSToday += 1
	totalTAPSUndoToday = 0
	for row in db.session.query(nterprise_taps).filter_by(task_type='TAPS-Undo',success=True,date=currentDate):
		totalTAPSUndoToday += 1
	totalTAPSToday = totalTAPSToday - totalTAPSUndoToday

	post = '''### Project Statistics:
* Total Today:  **'''+str(totalTAPSToday)+'''**\n
___________________________________\n
* Total Completed:  **'''+str(totalTAPSSuccess)+'''**\n
* Failed Attempts:  **'''+str(totalTAPSFail)+'''**\n
* TAPS Undo:  **'''+str(totalTAPSUndo)+'''**\n
___________________________________\n'''+modelPrint+'''
___________________________________\n'''+dpPrint

	return(post)

def sendProject():
	post = '''### nterprise TAPS Project Parameters:\n
* Customer: **'''+customerName+'''**\n
* Customer Domain: **'''+customerDomain+'''**\n
* Project Name: **['''+customerName+''' - '''+projectName+'''](http://intranet.mycompany.net/index.php?option=com_pbxtools&Itemid=67&view=ftmprojwo&PID='''+nterprisePID+''')**\n
* nterprise Customer ID: **'''+nterpriseCID+'''**\n
* nterprise Project ID: **'''+nterprisePID+'''**\n
* TAPS Log:  ['''+tapsURL+'''tapsLog]('''+tapsURL+'''tapsLog)\n
* CUCM Version: **'''+cucmVersion+'''**\n
* CUCM Server: [https://'''+cucmServer+'''/ccmadmin](https://'''+cucmServer+'''/ccmadmin)\n
* Unity Server: [https://'''+unityServer+'''/cuadmin](https://'''+unityServer+'''/cuadmin)\n
* DN Prefix: **'''+dnPrefix+'''**\n
* AutoReg Prefix: **'''+autoRegPrefix+'''**\n
* Support DN: **'''+supportDN+'''**\n'''
	print(post)
	return(post)

def sendSearch(search):
	row = db.session.query(nterprise_taps).filter_by(extension=search).order_by('-pkid').first()
	print(row)
	sparkPOST(row)
	return()

#===================================================================================#
#===================================================================================#

# RUN FLASK APPLICATION
if __name__ == '__main__':
	# context = ('cert.crt', 'key.key')
	app.run(host='0.0.0.0', port=flaskPort, threaded=True, debug=True)