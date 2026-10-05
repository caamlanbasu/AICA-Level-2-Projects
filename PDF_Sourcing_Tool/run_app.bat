@echo off
rem ============================================================
rem  PDF Sourcing Tool - double-click to start the application.
rem  The first run sets everything up (needs internet, a few minutes).
rem ============================================================
setlocal
title PDF Sourcing Tool
cd /d "%~dp0"

if exist ".venv\setup_ok.txt" goto :setup
echo ============================================================
echo   PDF Sourcing Tool - first-time setup
echo ============================================================
:setup
call "%~dp0_setup.bat"
if errorlevel 1 goto :failed

start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:failed
echo.
echo The application was NOT started.
pause
exit /b 1
