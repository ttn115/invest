@echo off
REM ============================================================
REM  Daily Scan Scheduler Wrapper
REM
REM  Called by Windows Task Scheduler. Runs scripts/daily_scan.py
REM  pipeline: crypto -> us -> tw -> tw-post -> predict
REM
REM  NOTE: This file is intentionally ASCII-only. cmd.exe reads .bat
REM  using the system ANSI codepage (cp950 on zh-TW Windows), so UTF-8
REM  Chinese comments get mangled and break REM lines. Chinese docs live
REM  in scripts/daily_scan.py docstring instead.
REM
REM  Logs:
REM    logs\daily_scan_stdout.log  - raw stdout/stderr (appended here)
REM    logs\daily_scan.log         - structured log via loguru (rotating)
REM
REM  Manual test:
REM    scripts\run_daily_scan.bat
REM    scripts\run_daily_scan.bat --step predict
REM ============================================================

setlocal

set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%" || exit /b 1

REM Force UTF-8 so Chinese/emoji output does not crash on cp950
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if not exist "logs" mkdir "logs"
set "LOGFILE=logs\daily_scan_stdout.log"

echo ============================================== >> "%LOGFILE%"
echo [%DATE% %TIME%] daily scan start >> "%LOGFILE%"

REM Args passed to this bat are forwarded to daily_scan.py (e.g. --step predict)
"C:\Python314\python.exe" "scripts\daily_scan.py" %* >> "%LOGFILE%" 2>&1
set "EXITCODE=%ERRORLEVEL%"

echo [%DATE% %TIME%] daily scan end exit=%EXITCODE% >> "%LOGFILE%"

endlocal & exit /b %EXITCODE%
