@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "PY_LAUNCHER="

where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY_LAUNCHER=py -3.11"
)

if not defined PY_LAUNCHER (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY_LAUNCHER=python"
  )
)

if not defined PY_LAUNCHER goto missing_python

if exist "%VENV_PY%" (
  "%VENV_PY%" --version >nul 2>nul
  if not errorlevel 1 goto ensure_package
  echo Existing virtual environment is invalid. Repairing it...
  %PY_LAUNCHER% -m venv --clear .venv
  if errorlevel 1 goto setup_failed
) else (
  echo First run: creating virtual environment...
  %PY_LAUNCHER% -m venv .venv
  if errorlevel 1 goto setup_failed
)

:ensure_package
"%VENV_PY%" -c "import cloudriver_manager, playwright" >nul 2>nul
if not errorlevel 1 goto run

echo First run: installing CloudRiver Manager and browser support...
"%VENV_PY%" -m pip install -e ".[browser]"
if errorlevel 1 goto setup_failed

:run
"%VENV_PY%" -m cloudriver_manager
if errorlevel 1 goto run_failed
goto done

:missing_python
echo Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/ and run this file again.
pause
goto done

:setup_failed
echo.
echo Setup failed. Check the network and the error above, then run start.cmd again.
pause
goto done

:run_failed
echo.
echo CloudRiver Manager stopped with an error. Check the message above.
pause

:done
endlocal
