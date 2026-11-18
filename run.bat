@echo off
rem ============================================================
rem  MCP launcher. Kiro / Claude desktop call: run.bat portal ^| tfs ^| desktop ^| testrail
rem  1) git pull   2) pip install if requirements changed   3) start server
rem  stdout is the MCP protocol: nothing may be printed here (logs go to .update.log).
rem  To disable auto-update create an empty file named .no-autoupdate in this folder.
rem  The whole body is ONE parenthesized block that ends with exit, so a git pull that
rem  replaces this file while it runs cannot corrupt execution.
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "SRV=%~1"
set "PYTHONUTF8=1"
set "GIT_TERMINAL_PROMPT=0"
set "PY=.venv\Scripts\python.exe"
set "SCRIPT="
if /i "%SRV%"=="portal"  set "SCRIPT=src\portal_mcp_server.py"
if /i "%SRV%"=="tfs"     set "SCRIPT=src\tfs_mcp_server.py"
if /i "%SRV%"=="desktop" set "SCRIPT=src\desktop_mcp_server.py"
if /i "%SRV%"=="testrail" set "SCRIPT=src\testrail_mcp_server.py"
set "UPD=1"
if exist ".no-autoupdate" set "UPD=0"
if not exist ".git" set "UPD=0"

(
  if not defined SCRIPT (
    echo usage: run.bat portal^|tfs^|desktop^|testrail 1>&2
    exit /b 2
  )
  if not exist "%PY%" (
    echo .venv not found - run setup.bat first 1>&2
    exit /b 3
  )
  if "%UPD%"=="1" (
    set "GOT=0"
    mkdir ".update.lock" 2>nul && set "GOT=1"
    if "!GOT!"=="1" (
      echo [%date% %time%] update !SRV! >>".update.log"
      git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=15 pull --ff-only <nul >>".update.log" 2>&1
      set "NEED=0"
      fc /b requirements.txt ".venv\req.snapshot" >nul 2>&1 || set "NEED=1"
      fc /b requirements-desktop.txt ".venv\reqd.snapshot" >nul 2>&1 || set "NEED=1"
      if "!NEED!"=="1" (
        "%PY%" -m pip install -q --disable-pip-version-check -r requirements.txt -r requirements-desktop.txt >>".update.log" 2>&1
        if not errorlevel 1 (
          "%PY%" -m playwright install chromium >>".update.log" 2>&1
          copy /y requirements.txt ".venv\req.snapshot" >nul
          copy /y requirements-desktop.txt ".venv\reqd.snapshot" >nul
        )
      )
      rmdir ".update.lock" 2>nul
    ) else (
      for /l %%i in (1,1,120) do if exist ".update.lock" ping -n 2 127.0.0.1 >nul
      if exist ".update.lock" rmdir ".update.lock" 2>nul
    )
  )
  "%PY%" "%SCRIPT%"
  exit /b !errorlevel!
)
