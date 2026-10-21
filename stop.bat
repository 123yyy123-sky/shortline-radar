@echo off
setlocal enabledelayedexpansion
rem Stop Shortline Radar: kill whatever is listening on the port.
rem Kept ASCII-only on purpose - cmd.exe parses .bat in the system codepage.
rem
rem Usage: stop.bat [port]      (default port 8848)

rem Use the full path for timeout: a POSIX shell in PATH (git bash) would
rem otherwise shadow it with GNU timeout, which does not understand /t.
set "WAIT=%SystemRoot%\System32\timeout.exe"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8848"

set "FOUND="
for /f "tokens=5" %%P in ('netstat -ano -p TCP ^| findstr /i /c:"LISTENING" ^| findstr /c:":%PORT% "') do (
    if not defined FOUND set "FOUND=%%P"
)

if not defined FOUND (
    echo Shortline Radar is not running on port %PORT%.
    "%WAIT%" /t 3 >nul
    exit /b 0
)

echo Stopping Shortline Radar ^(PID %FOUND%, port %PORT%^) ...
taskkill /F /PID %FOUND% >nul 2>nul
if errorlevel 1 (
    echo Failed to stop PID %FOUND%.
    echo Try running this file as administrator.
    pause
    exit /b 1
)

echo Stopped. Port %PORT% is free now.
"%WAIT%" /t 2 >nul
exit /b 0
