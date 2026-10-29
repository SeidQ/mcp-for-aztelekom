@echo off
REM Sinaq: butun mailleri yalniz MONITOR_ME unvanina (sizə) gonderir, marker deyismir
chcp 65001 >nul
cd /d "%~dp0"
call run_monitor.bat --to-me
type "%~dp0..\src\defect_monitor.log" | more +0 >nul
powershell -NoProfile -Command "Get-Content -Tail 8 -Encoding UTF8 '%~dp0..\src\defect_monitor.log'"
pause
