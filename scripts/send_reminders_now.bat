@echo off
REM Yalniz xatirlatmalari indi gonderir (yeni defekt penceresi bos: --since = indiki vaxt). Netice: src\defect_monitor.log
chcp 65001 >nul
for /f "tokens=*" %%t in ('powershell -NoProfile -Command "Get-Date -Format ''yyyy-MM-dd HH:mm''"') do set NOW=%%t
call "%~dp0run_monitor.bat" --since "%NOW%"
echo Bitdi. Netice: src\defect_monitor.log
timeout /t 5 >nul
