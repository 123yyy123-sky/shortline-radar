@echo off
rem Shortline Radar launcher.
rem Keep this file ASCII-only: cmd.exe parses it in the system codepage,
rem so Chinese text here would be garbled. All messages come from python.
chcp 65001 >nul
cd /d "%~dp0"

python app.py %*
if errorlevel 1 pause
