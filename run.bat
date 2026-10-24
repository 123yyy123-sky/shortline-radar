@echo off
rem Trading Desk launcher.
rem Keep this file ASCII-only: cmd.exe parses it in the system codepage,
rem so Chinese text here would be garbled. All messages come from python.
chcp 65001 >nul
cd /d "%~dp0"

rem Find a usable interpreter. Order: python -> py -3.
rem The Microsoft Store "python.exe" stub also matches `where python` but it
rem only opens the Store, so verify the version string instead of trusting it.
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info>=(3,7) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run

py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,7) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run

echo.
echo   Python 3.7+ not found.
echo   Install it from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:run
%PY% app.py %*
if errorlevel 1 pause
