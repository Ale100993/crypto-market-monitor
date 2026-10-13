@echo off
cd /d "%~dp0"
if not exist "logs" mkdir logs
".venv\Scripts\python.exe" crypto_monitor.py >> "logs\monitor.log" 2>&1
exit /b %ERRORLEVEL%
