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
REM
REM  Task Scheduler setup (to recreate on a new machine), PowerShell,
REM  replace REPO_DIR with the repo path:
REM    schtasks /Create /TN "StockInvest Daily Scan" /SC DAILY /ST 18:30 /F /TR "REPO_DIR\scripts\run_daily_scan.bat"
REM    $t = Get-ScheduledTask -TaskName "StockInvest Daily Scan"; $s = $t.Settings
REM    $s.DisallowStartIfOnBatteries = $false   # also run on battery
REM    $s.StopIfGoingOnBatteries     = $false   # do not abort when unplugged
REM    $s.StartWhenAvailable         = $true    # catch up a missed 18:30 run
REM    $s.ExecutionTimeLimit         = "PT2H"   # per-step timeouts total ~50 min
REM    Set-ScheduledTask -TaskName "StockInvest Daily Scan" -Settings $s
REM  18:30 is chosen because TWSE T86/MI_MARGN data is published ~18:00.
REM  Logon mode stays "Interactive only": running while logged off would
REM  require storing the Windows password in the task.
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
