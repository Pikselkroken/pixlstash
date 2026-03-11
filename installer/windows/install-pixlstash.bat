@echo off
setlocal EnableDelayedExpansion

set "APP_DIR=%~1"
if "%APP_DIR%"=="" (
    echo Error: App directory must be provided as the first argument.
    exit /b 1
)

:: ---- Locate Python -------------------------------------------------------
set "PYTHON_CMD="
py -3.12 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.12"

if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo Error: Python 3.10+ is required but was not found.
    echo Please install Python from https://www.python.org/ and run the installer again.
    exit /b 1
)

echo Using Python: %PYTHON_CMD%

:: ---- Create virtual environment ------------------------------------------
set "VENV_DIR=%APP_DIR%\venv"
if not exist "%VENV_DIR%\" (
    echo Creating virtual environment in %VENV_DIR%
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Error: Failed to create virtual environment.
        exit /b 1
    )
)

set "PIP_EXE=%VENV_DIR%\Scripts\pip.exe"
if not exist "%PIP_EXE%" (
    echo Error: pip not found in virtual environment: %PIP_EXE%
    exit /b 1
)

:: ---- Find the wheel to install -------------------------------------------
set "WHEEL_DIR=%APP_DIR%\dist"
set "WHEEL_FILE="
for /f "delims=" %%F in ('dir /b /o-d "%WHEEL_DIR%\pixlstash-*.whl" 2^>nul') do (
    if not defined WHEEL_FILE set "WHEEL_FILE=%%F"
)

if not defined WHEEL_FILE (
    echo Error: No pixlstash wheel found in %WHEEL_DIR%
    exit /b 1
)

echo Installing wheel: %WHEEL_DIR%\%WHEEL_FILE%
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 exit /b 1

"%PIP_EXE%" install --upgrade "%WHEEL_DIR%\%WHEEL_FILE%"
if errorlevel 1 (
    echo Error: pip install failed.
    exit /b 1
)

echo.
echo PixlStash installation completed successfully.
exit /b 0
