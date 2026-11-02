@echo off
setlocal EnableDelayedExpansion

set "APP_DIR=%~1"
if "%APP_DIR%"=="" (
    echo Error: App directory must be provided as the first argument.
    exit /b 1
)

:: ---- Locate Python -------------------------------------------------------
:: The first candidate that is Python 3.11 or newer wins (pyproject.toml's
:: requires-python). A 3.10 on PATH must not be picked: pip refuses the wheel.
set "PY_FLOOR_CHECK=import sys; sys.exit(sys.version_info < (3, 11))"
set "PYTHON_CMD="
for %%C in ("py -3.12" "py -3.11" "py -3" "python") do (
    if not defined PYTHON_CMD (
        %%~C -c "%PY_FLOOR_CHECK%" >nul 2>&1
        if not errorlevel 1 set "PYTHON_CMD=%%~C"
    )
)

if not defined PYTHON_CMD (
    echo Error: Python 3.11 or newer is required but was not found.
    python --version 2>nul
    echo Please install Python from https://www.python.org/ and run the installer again.
    exit /b 1
)

echo Using Python: %PYTHON_CMD%

:: ---- Create virtual environment ------------------------------------------
:: A venv left by an earlier install on an older Python cannot take the wheel,
:: so rebuild it. Only the venv goes; the library lives elsewhere.
set "VENV_DIR=%APP_DIR%\venv"
if exist "%VENV_DIR%\" (
    "%VENV_DIR%\Scripts\python.exe" -c "%PY_FLOOR_CHECK%" >nul 2>&1
    if errorlevel 1 (
        echo Existing virtual environment is not Python 3.11+; recreating it.
        rmdir /s /q "%VENV_DIR%"
    )
)
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
