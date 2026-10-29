@echo off
chcp 65001 >nul
cd /d "%~dp0..\src"
python "%~dp0..\src\setup_sv_desktop.py"
echo.
pause
