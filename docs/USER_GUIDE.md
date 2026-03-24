# AI Pump Feed Bridge — User Guide

## Overview

AI Pump Feed Bridge is an ESP32-S3-DevKitC-1 based device that controls
Aqua Illumination aquarium pumps (such as AI Nero, Axis, etc.) over BLE.
Management is available via web interface and MQTT.

## Initial Setup

On first boot (or after a factory reset) the device starts in Access Point mode:

1. Connect to the **AI-Pump-Bridge** WiFi network (no password).
2. Open **http://192.168.4.1** in a browser.
3. Configure:
   - **WiFi** — your home network SSID and password.
   - **Pump** — click **Connect to AI Pumps** (see below).
   - **MQTT** (optional) — broker address and credentials.
4. After saving WiFi settings, the device switches to client mode
   and connects to the specified network.

## Connecting to Pumps

The device automatically starts scanning for nearby AI pumps as soon as
it powers on. By the time you open the web interface, it may have already
discovered your pumps and read their settings.

### Quick Connect

Click the **Connect to AI Pumps** button in the Actions panel.
The device will:

1. Scan for nearby pumps (may take 10–25 seconds).
2. Read settings from any newly discovered pumps.
3. Automatically select a pump if all found pumps belong to the same
   mesh network — no further action needed.

After a successful connection, the **Activate Feed Mode** and
**Sync Settings** buttons become available.

### Multiple Mesh Networks

If the device finds pumps belonging to different mesh networks
(e.g., pumps in separate aquariums), it cannot automatically decide
which group to use. In this case, the **Pump Scanner** panel opens
with a list of all found pumps, each labeled with its mesh network.
Select the pump you want to control and click **Select Pump**.

### Re-connecting

Once a pump is selected, the button label changes to
**Re-connect to pumps**. Use it if you have replaced a pump,
changed your aquarium setup, or need to switch to a different pump.

### Sync Settings

If you change pump settings in the official myAI / Mobius app
(e.g., Feed Mode speed or duration), click **Sync Settings** to
update the device with the new values.

## LED Indicator (onboard RGB LED)

| State | LED pattern |
|---|---|
| Booting | Blue (solid) |
| AP mode | Blue (slow blink) |
| WiFi connecting | Yellow (pulse) |
| WiFi connected (no MQTT) | Green (slow blink) |
| WiFi + MQTT connected | Green (solid) |
| BLE operation in progress | Cyan (fast blink) |
| Error | Red (double flash) |

## Factory Reset

A factory reset deletes all saved settings (WiFi, MQTT, pump selection)
and reboots the device. After reset the device starts in Access Point mode
for reconfiguration.

### Method 1: BOOT Button (hardware)

Works regardless of WiFi or firmware state — the most reliable option.

1. Press and **hold** the **BOOT** button on the ESP32-S3-DevKitC-1 board.
2. While holding BOOT, press and release the **RESET** (EN) button to
   reboot the device.
3. Keep holding BOOT for **at least 3 seconds** after the reboot.
4. The device deletes the configuration and reboots automatically.
5. The **AI-Pump-Bridge** WiFi network will appear — the device is ready
   for reconfiguration.

> **Note:** releasing BOOT before 3 seconds will result in a normal boot
> with no reset.

### Method 2: Web Interface (software)

Available when the web interface is reachable (via WiFi or AP mode).

1. Open the device web interface in a browser.
2. Scroll down and expand the **Factory Reset** section.
3. Click **Factory Reset**.
4. Confirm the action in the browser dialog.
5. The device will erase all settings and reboot. The **AI-Pump-Bridge**
   WiFi network will appear once the reboot completes.

## Feed Mode

### Via Web Interface

Open the device web interface and press the **Activate Feed Mode** button.
This activates Feed Mode on all pumps in the mesh network.

### Via Home Assistant

If MQTT is configured and connected, the device automatically appears
in Home Assistant. No additional setup is required beyond having an
MQTT broker that both Home Assistant and the device connect to.

**Activating Feed Mode:** use the device entity in automations,
dashboards, or scripts to trigger Feed Mode remotely — for example,
on a schedule or as part of a feeding routine.

**Monitoring pump status:** Home Assistant displays the current pump
state in real time:

| State | Meaning |
|---|---|
| `idle` | Pump is in normal operation |
| `activating` | Feed Mode command is being sent via BLE |
| `running` | Feed Mode is active, pump speed is reduced |
| `error` | Feed Mode activation failed |
