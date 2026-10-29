@echo off
REM Sinaq: hec ne gondermir, netice defect_monitor_preview.html faylinda
chcp 65001 >nul
cd /d "%~dp0"
call run_monitor.bat --dry-run
start "" "%~dp0..\src\defect_monitor_preview.html"
