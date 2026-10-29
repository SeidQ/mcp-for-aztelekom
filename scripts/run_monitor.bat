@echo off
REM Gundelik defekt yoxlamasi. Task Scheduler bunu cagirir. Parollar: secrets.env
chcp 65001 >nul
cd /d "%~dp0..\src"
set PY=%LOCALAPPDATA%\Python\pythoncore-3.15-64\python.exe
if not exist "%PY%" set PY=python
if not defined MONITOR_CHANNELS set MONITOR_CHANNELS=outlook
"%PY%" "%~dp0..\src\defect_monitor.py" %* >> "%~dp0..\src\defect_monitor.log" 2>&1
