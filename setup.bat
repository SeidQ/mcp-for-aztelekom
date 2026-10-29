@echo off
setlocal
cd /d "%~dp0"
echo.
echo === mcp-for-aztelekom: first-time setup ===
echo.

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
  echo ERROR: Python 3.11+ not found. Install it from python.org and tick "Add to PATH".
  goto fail
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Creating .venv ...
  %PYCMD% -m venv .venv || goto fail
) else (
  echo [1/5] .venv already exists
)

echo [2/5] Installing packages - this can take a few minutes ...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-desktop.txt || goto fail
copy /y requirements.txt ".venv\req.snapshot" >nul
copy /y requirements-desktop.txt ".venv\reqd.snapshot" >nul

echo [3/5] Installing Playwright Chromium ...
".venv\Scripts\python.exe" -m playwright install chromium || goto fail

echo [4/5] secrets.env ...
if not exist "src\secrets.env" (
  copy "src\secrets.env.example" "src\secrets.env" >nul
  echo     src\secrets.env created - fill in your credentials.
  start notepad "src\secrets.env"
) else (
  echo     src\secrets.env already exists - left untouched
)

echo [5/5] Registering servers in Claude desktop config ...
".venv\Scripts\python.exe" scripts\register_mcp.py || goto fail

echo.
echo DONE. Now:
echo   1. Fill src\secrets.env ^(PORTAL_*, TFS_*^) and save it
echo   2. Fully quit Claude desktop ^(tray icon - Quit^) and open it again
echo Updates are automatic: every time Claude starts, run.bat does a git pull.
echo.
pause
exit /b 0

:fail
echo.
echo SETUP FAILED. See the error above.
pause
exit /b 1
