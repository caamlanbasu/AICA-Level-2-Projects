@echo off
rem ============================================================
rem  PDF Sourcing Tool - build a Windows .exe
rem  Double-click this file. Keep it next to main.py, _setup.bat
rem  and build_exe.py. The build itself is done by build_exe.py.
rem ============================================================
setlocal
title Build PDF Sourcing Tool
cd /d "%~dp0"

echo ============================================================
echo   PDF Sourcing Tool - build Windows .exe
echo ============================================================
echo.

echo [1/4] Preparing Python and the dependencies ...
call "%~dp0_setup.bat"
if errorlevel 1 goto :failed

echo [2/4] Installing the newest PyInstaller ...
"%VENV_PY%" -m pip install --upgrade pyinstaller --quiet
if errorlevel 1 goto :fail_pyinstaller

"%VENV_PY%" build_exe.py
if errorlevel 1 goto :failed

echo.
if exist "dist\PDF Sourcing Tool.exe" explorer /select,"%~dp0dist\PDF Sourcing Tool.exe"
if not exist "dist\PDF Sourcing Tool.exe" explorer "%~dp0dist"
pause
exit /b 0

:fail_pyinstaller
echo.
echo PROBLEM: PyInstaller could not be installed. Check your internet connection.
goto :failed

:failed
echo.
echo The build did NOT finish.
pause
exit /b 1
