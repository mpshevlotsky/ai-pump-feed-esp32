#!/usr/bin/env bash
# Interactive bootstrap script for flashing AI Pump Bridge firmware.
# Guides the user through the entire process: environment setup, firmware
# download, and flashing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
TOOLS_DIR="$SCRIPT_DIR/tools"

# MicroPython firmware — update these when upgrading
FIRMWARE_VERSION="20251209-v1.27.0"
FIRMWARE_FILE="ESP32_GENERIC_S3-${FIRMWARE_VERSION}.bin"
FIRMWARE_URL="https://micropython.org/resources/firmware/${FIRMWARE_FILE}"

echo ""
echo "=== AI Pump Bridge — Firmware Flasher ==="
echo ""

# ── Find Python ──────────────────────────────────────────────────────────

find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            echo "$cmd"
            return
        fi
    done
    echo ""
}

PYTHON="$(find_python)"
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python not found."
    echo "Install Python 3.9+ from https://www.python.org/downloads/"
    exit 1
fi

echo "Python: $($PYTHON --version 2>&1)"

# ── Set up virtual environment ───────────────────────────────────────────

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

echo "Installing tools..."
pip install -q esptool mpremote
echo "Tools ready."

# ── Download MicroPython firmware if needed ──────────────────────────────

mkdir -p "$TOOLS_DIR"

if ! ls "$TOOLS_DIR"/*.bin 1>/dev/null 2>&1; then
    echo ""
    echo "MicroPython firmware not found in tools/."
    echo "Downloading $FIRMWARE_FILE ..."

    if command -v curl >/dev/null 2>&1; then
        curl -L --fail -o "$TOOLS_DIR/$FIRMWARE_FILE" "$FIRMWARE_URL"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$TOOLS_DIR/$FIRMWARE_FILE" "$FIRMWARE_URL"
    else
        echo ""
        echo "ERROR: curl or wget is required to download firmware."
        echo "Download manually from:"
        echo "  $FIRMWARE_URL"
        echo "Place the .bin file into: $TOOLS_DIR/"
        exit 1
    fi

    echo "Downloaded: $FIRMWARE_FILE"
fi

# ── Connect the board ────────────────────────────────────────────────────

echo ""
echo "Connect the ESP32-S3 board via USB and press Enter."
read -r

# ── Ask for serial port ──────────────────────────────────────────────────

echo "Enter serial port (e.g. /dev/ttyACM0) or press Enter for auto-detect:"
read -r PORT

PORT_ARGS=()
if [ -n "$PORT" ]; then
    PORT_ARGS=(--port "$PORT")
fi

# ── Flash ────────────────────────────────────────────────────────────────

echo ""
python "$SCRIPT_DIR/esp.py" --flash --libs --deploy "${PORT_ARGS[@]}"

echo ""
echo "=== Done ==="
echo "Connect to WiFi network 'AI-Pump-Bridge' and open http://192.168.4.1"
echo "to configure the device."
