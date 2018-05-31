#	nterprise TAPS - Cisco phone deployment tool and Webex Teams bot

	nterprise TAPS Phone Deployment Service
	Version: 1.2.1

   	2018-05-31 - Added interactive bot functionality with /commands

	Written by: Yli Schwartzman
	Email:	yli.schwartzman@nfrastructure.com
	Copyright 2018 Zones nfrastructure

#	Summary:
	This program can be used to deploy enhanced TAPS-like deployment services to a CUCM system
	The service is presented as an XML service to the phone display

	The AutoRegistration device templates in CUCM should be configured with
	the 'Idle' service URL pointed to this web service in the following format:
		http://<webserverfqdn>:<flaskPort>/taps?=#DEVICENAME#
	Also be sure to set the Idle timer to a reasonable value such as 5-10 seconds
	This ensures the service automatically shows up, but also allows you to exit and have time to dial if needed
	You could also optionally configure an IP Phone Service instead of using the Idle URL
	Be sure all your phones (especially the auto-reg phone template) have Web Access enabled
		This is needed to gather Serial numbers, CDP, etc

	A 'Support' softkey is provided to automatically call whatever the 'supportDN' configured below is
	This helps techs quickly access support during deployment

#	Procedure:
		Enter the desired extension matching the CUCM template you wish to apply to the phone
			If you have a 'dnPrefix' configured, you only need to enter the remaining digits
			Enter any additional custom data fields as required for your deployment
		If there are no matches, or no matches match your phone model you will see an error.
		If there is only one valid match it will automatically proceed.
		If there are multiple matches:
			You will be presented with a list of all phones with that as their PRIMARY extension
				Devices with secondary lines matching will not be shown
			Scroll and select the correct tempate to apply to this phone
		If successful, the phone will reboot and come up with the new configuration
		If unsuccessful, the phone will reboot and auto-register again.

#	Requirements:
	Run on Windows Server or PC (2012+ recommended)
		IIS
		CUCM AXL and RIS Schema
			Download from CUCM server 'plugins' page
			Currently tested with v10.5 - v12.0
		Script & Files
		/nterprise-taps
			nterprise-taps.py
			nterprise-taps.db  (Created after first runtime)
				/axl/...	(Extract CUCM AXL files here)
				/static
					/css
						nterprise-taps.css
					/img
						greencheck.png
						redx.png
					/js
						sortable.js
					/fonts
				/templates
					nterprise-taps-log.html
	Required Python version and module dependencies:
		SQLite3
		Python 3.5.x
			pip
			ssl,csv,random,time,uuid,requests,datetime,re
			flask
			flask_sqlalchemy
			suds
			zeep
 			lxml >> set STATICBUILD=true && pip install lxml


#	TAPS Undo Service
		To create an optional service for converting configured phones back into BAT Templates
		Setup an IP Phone Service with Enterprise Subscription
		Point to Standard XML Service URL:
			<tapsURL>/tapsUndo?name=#DEVICENAME#
		Select the service on the phone to initate the TAPS UNDO process.


#	LOGGING
		Log records of all transactions are kept in a sqlite3 database file
		They are visible as a sortable table at this URL:
			<tapsURL>/tapsLog
		They are downloadable as a CSV at this URL:
			<tapsURL>/tapsLog.csv


#	WEBEX TEAMS BOT
	   	You can add the nterpriseTAPS@sparkbot.io bot to a room for your project to monitor status and request details
		Each time an operation is completed (Successful TAPS, TAPS-Undo, or a major Error) the details are posted to the
		space with quick links to assist in troubleshooting.
		Additionnally you can request /help from the bot and ask questions on the project, stats, search for a previously completed
		phone extension, etc
