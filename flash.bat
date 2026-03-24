@echo off
:: Interactive bootstrap script for flashing AI Pump Bridge firmware.
:: Guides the user through the entire process: environment setup, firmware
:: download, and flashing.

setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "TOOLS_DIR=%SCRIPT_DIR%tools"

:: MicroPython firmware — update these when upgrading
set "FIRMWARE_VERSION=20251209-v1.27.0"
set "FIRMWARE_FILE=ESP32_GENERIC_S3-%FIRMWARE_VERSION%.bin"
set "FIRMWARE_URL=https://micropython.org/resources/firmware/%FIRMWARE_FILE%"

echo.
echo === AI Pump Bridge — Firmware Flasher ===
echo.

:: ── Find Python ─────────────────────────────────────────────────────────

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found.
    echo Install Python 3.9+ from https://www.python.org/downloads/
    echo Make sure to check "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

python --version

:: ── Set up virtual environment ──────────────────────────────────────────

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv "%VENV_DIR%"
)

call "%VENV_DIR%\Scripts\activate.bat"

echo Installing tools...
pip install -q esptool mpremote
echo Tools ready.

:: ── Download MicroPython firmware if needed ─────────────────────────────

if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

dir /b "%TOOLS_DIR%\*.bin" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo MicroPython firmware not found in tools\.
    echo Downloading %FIRMWARE_FILE% ...
    powershell -Command "Invoke-WebRequest -Uri '%FIRMWARE_URL%' -OutFile '%TOOLS_DIR%\%FIRMWARE_FILE%'"
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: Download failed.
        echo Download manually from:
        echo   %FIRMWARE_URL%
        echo Place the .bin file into: %TOOLS_DIR%\
        pause
        exit /b 1
    )
    echo Downloaded: %FIRMWARE_FILE%
)

:: ── Connect the board ───────────────────────────────────────────────────

echo.
echo Connect the ESP32-S3 board via USB and press Enter.
pause >nul

:: ── Ask for serial port ─────────────────────────────────────────────────

echo Enter serial port (e.g. COM3) or press Enter for auto-detect:
set /p PORT=

set "PORT_ARGS="
if not "%PORT%"=="" set "PORT_ARGS=--port %PORT%"

:: ── Flash ───────────────────────────────────────────────────────────────

echo.
python "%SCRIPT_DIR%esp.py" --flash --libs --deploy %PORT_ARGS%

echo.
echo === Done ===
echo Connect to WiFi network 'AI-Pump-Bridge' and open http://192.168.4.1
echo to configure the device.
pause
