@echo off
rem ============================================================
rem  Shared setup used by run_app.bat and build_exe.bat.
rem  You do not need to run this file yourself.
rem
rem  1. creates the virtual environment (.venv)
rem  2. installs the dependencies from requirements.txt
rem  3. checks once that the application really starts
rem ============================================================
cd /d "%~dp0"
set "VENV_PY=.venv\Scripts\python.exe"
set "SETUP_OK=.venv\setup_ok.txt"

if not exist "main.py" goto :no_source
if not exist "requirements.txt" goto :no_source
if not exist "pdf_sourcing\ui.py" goto :no_source

rem ---- virtual environment -------------------------------------
if exist "%VENV_PY%" goto :have_venv
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :make_venv
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if not defined PY goto :no_python
:make_venv
echo     Creating the virtual environment (.venv) ...
%PY% -m venv .venv
if errorlevel 1 goto :fail_venv
if not exist "%VENV_PY%" goto :fail_venv
:have_venv

rem ---- dependencies and start-up check (first run only) --------
if exist "%SETUP_OK%" goto :setup_done
echo     Installing the dependencies. The first time takes a few minutes ...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail_deps

echo     Checking that the application starts (a window opens and closes by itself) ...
if exist "selftest_result.txt" del "selftest_result.txt"
"%VENV_PY%" main.py --selftest
if errorlevel 1 goto :fail_selftest
type "selftest_result.txt"
echo ok> "%SETUP_OK%"
:setup_done
exit /b 0

rem ---- Problems ------------------------------------------------
:no_source
echo PROBLEM: main.py, requirements.txt or the pdf_sourcing folder was not found.
echo Keep all the files from the zip together in one folder and run again.
exit /b 1

:no_python
echo PROBLEM: Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" in the installer, then run this file again.
exit /b 1

:fail_venv
echo PROBLEM: the virtual environment (.venv) could not be created.
echo Delete the .venv folder if it exists and run this file again.
exit /b 1

:fail_deps
echo.
echo PROBLEM: a package could not be installed. The lines above name the package.
echo Check your internet connection. If pip says "No matching distribution found",
echo open requirements.txt, remove the ==version part on that line, and run again.
exit /b 1

:fail_selftest
echo.
echo PROBLEM: the application did not start. The error is shown above and
echo saved in selftest_result.txt. Please send that file.
exit /b 1
