@echo off
title Install Guest Duplicates App Dependencies
echo.
echo Installing required Python packages...
pip install flask flask-cors oracledb apscheduler requests python-dotenv
echo.
echo Done! Run run.bat to start the app.
pause
