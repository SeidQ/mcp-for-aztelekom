@echo off
chcp 65001 >nul
cd /d "%~dp0..\src"
python "%~dp0..\src\migrate_secrets.py"
echo.
pause
