@echo off
REM Is gunleri 16:30-da "TFS Defect Monitor" task-ini qurur (komputer sonuludurse, acilanda isleyir).
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a = New-ScheduledTaskAction -Execute \"%~dp0run_monitor.bat\" -WorkingDirectory \"%~dp0.\"; $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 16:30; $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30); Register-ScheduledTask -TaskName \"TFS Defect Monitor\" -Action $a -Trigger $t -Settings $s -Description \"Yeni defektleri template ile yoxlayir ve muelliflere gonderir\" -Force | Out-Null; Get-ScheduledTask -TaskName \"TFS Defect Monitor\" | Select-Object TaskName,State | Format-List"
echo.
pause
