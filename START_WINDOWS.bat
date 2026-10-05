@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python scripts\launch_local.py %*
) else (
    py -3.12 scripts\launch_local.py %*
)
if errorlevel 1 (
    echo See docs\06_LOCAL_SETUP.html for help.
    pause
    exit /b 1
)
