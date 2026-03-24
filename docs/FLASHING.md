# Flashing Guide

Step-by-step instructions for flashing the AI Pump Bridge firmware onto an
ESP32-S3 board.

## Prerequisites

- **ESP32-S3 board with PSRAM** (e.g. ESP32-S3-DevKitC-1)
- **USB cable** (USB-C or Micro-USB depending on the board)
- **Computer** running Windows, macOS, or Linux

## Step 1: Install Python

The deployment tools require Python 3.9 or newer.

**Windows:**

Download the installer from https://www.python.org/downloads/ and run it.
Make sure to check **"Add python.exe to PATH"** during installation.

**macOS:**

```bash
brew install python
```

Or download from https://www.python.org/downloads/

**Linux (Debian/Ubuntu):**

```bash
sudo apt update && sudo apt install python3 python3-pip python3-venv
```

## Step 2: Download the Project

Go to https://github.com/mpshevlotsky/ai-pump-feed-esp32 and click
**Code → Download ZIP**. Extract the archive and open the resulting folder.

Alternatively, you can use Git:

```bash
git clone https://github.com/mpshevlotsky/ai-pump-feed-esp32.git
cd ai-pump-feed-esp32
```

## Step 3: Connect the Board and Flash

Connect the ESP32-S3 board to your computer via USB, then run the flash
script. It will automatically set up the Python environment, download the
MicroPython firmware, and flash the device.

**Linux / macOS:**

```bash
./flash.sh
```

**Windows:**

Double-click `flash.bat` or run it from the command prompt.

The script will guide you through the process interactively.

## Step 4: Initial Configuration

After flashing, the ESP32 starts in **Access Point mode**:

1. Connect to the WiFi network `AI-Pump-Bridge` from your phone or laptop
2. Open `http://192.168.4.1` in a browser
3. Configure your WiFi network and MQTT broker settings
4. Select the target pump

The device will reboot and connect to your WiFi network.

## Updating Firmware

To update the firmware without re-flashing MicroPython, run the flash script
again — it will detect that MicroPython is already present. Or use `esp.py`
directly:

```bash
python esp.py --deploy
```

For all available options:

```bash
python esp.py --help
```

## Monitoring

To view firmware logs in real time:

```bash
python esp.py --monitor
```

Press `Ctrl+]` to exit the REPL.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Permission denied (Linux) | `sudo usermod -aG dialout $USER`, then re-login |
| Board not detected | Try a different USB cable (some are charge-only) |
| Flash fails | Hold the BOOT button on the board while starting the flash |
| Board boots but no WiFi AP | Hold the BOOT button for 3+ seconds to factory reset |
