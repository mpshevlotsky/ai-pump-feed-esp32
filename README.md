# AI Pump Bridge — ESP32 BLE Gateway for Aqua Illumination Pumps

ESP32-based BLE-to-WiFi bridge that enables Home Assistant integration
for Aqua Illumination aquarium pumps (Nero 3, Nero 5, Axis, etc.).

The primary function is **Feed Mode activation** — a one-command operation
that temporarily adjusts pump settings according to Feed Mode presets
configured by the user in the myAI / Mobius app (typically reducing pump
speed for a set duration during fish feeding).

## Project Purpose

This project provides a MicroPython firmware for ESP32-S3 that acts as a
WiFi-to-BLE bridge, bringing Aqua Illumination pumps into the Home Assistant
ecosystem via MQTT.

Aqua Illumination pumps communicate exclusively via Bluetooth Low Energy (BLE).
There is no WiFi API, no public protocol documentation, and no official
Home Assistant integration. This firmware bridges the gap.

**Example use cases:**

- **Automated feeding** — when an auto feeder dispenses food on schedule,
  Home Assistant activates Feed Mode to reduce pump speed so that food
  is not scattered across the tank and fish can eat comfortably
- **ATO protection** — simultaneously disabling the Auto Top-Off (ATO) system
  during Feed Mode to prevent false triggers caused by temporary water level
  changes during feeding

## How It Works

```
                    WiFi                          BLE
 Home Assistant ◄──────────► ESP32 Gateway ◄──────────► AI Pump (Master)
   MQTT / REST                MicroPython                    │
                                                             │ mesh
                                                             ▼
                                                        All Pumps
                                                       (Feed Mode)
```

The firmware uses a **connect-on-demand** strategy:
- Normally idle (no BLE connection) — the pump remains available for the myAI/Mobius app
- On Feed Mode request: connects via BLE (~3s), sends command (~1s), disconnects (~1s)
- Total operation time: ~5 seconds

This means the ESP32 gateway **does not interfere** with the official myAI/Mobius app.

## Features

- **Feed Mode activation** via REST API, MQTT, or embedded web interface
- **Home Assistant integration** with MQTT Auto-Discovery (zero-config)
- **Web UI** for device setup (WiFi, MQTT, pump selection)
- **REST API** for integration with any automation platform
- **Connect-on-demand BLE** — no persistent connection, no conflict with myAI app
- **Mesh propagation** — connecting to one pump activates Feed Mode on all pumps in the network

## Hardware Requirements

- **ESP32-S3 with PSRAM** (recommended: ESP32-S3-DevKitC-1)
- Aqua Illumination pump (Nero 3, Nero 5, Axis, etc.)
- WiFi network
- MQTT broker (Mosquitto) — for Home Assistant integration

### Hardware Compatibility

| Module | Board Example | Status | Notes |
|--------|---------------|--------|-------|
| ESP32-S3 + PSRAM 2MB | ESP32-S3-DevKitC-1 | **Works** | Recommended. Stable BLE+WiFi, no memory issues |
| ESP32-D0WD-V3 | ESP32-DevKitC-32E | **Does NOT work** | BLE devices not found, hangs, WDT resets |

**Why ESP32-S3 is required:**

This firmware runs WiFi (HTTP server + MQTT client) and BLE (scan + connect + GATT)
simultaneously. The original ESP32 (D0WD-V3) has a single shared radio for WiFi
and BLE with primitive time-division multiplexing — under concurrent load, BLE scans
miss advertisements, connections time out, and the system becomes unresponsive.

ESP32-S3 advantages:
- **Improved WiFi/BLE coexistence controller** — reliable concurrent radio operation
- **Bluetooth 5.0** (vs 4.2) — better throughput and connection stability
- **PSRAM** — eliminates memory pressure during BLE GATT discovery + WiFi stack

## Getting Started

- **[Flashing & Installation](docs/FLASHING.md)** — Python setup, firmware flashing, initial device setup
- **[User Guide](docs/USER_GUIDE.md)** — web interface, pump connection, Feed Mode, factory reset

## Project Structure

```
ai-pump-feed-esp32/
├── firmware/                    # ESP32 MicroPython firmware
│   ├── boot.py                  #   WiFi initialization
│   ├── main.py                  #   Application entry point
│   ├── config.py                #   Configuration management
│   ├── ble_pump.py              #   BLE Central: scan, connect, feed mode
│   ├── mqtt_client.py           #   MQTT client with HA Auto-Discovery
│   ├── web_server.py            #   REST API + web UI server
│   ├── wifi_manager.py          #   WiFi AP/Client mode management
│   ├── led_indicator.py         #   Onboard RGB LED status indicator
│   └── static/
│       ├── index.html           #   Web interface
│       └── openapi.yaml         #   REST API specification
├── core/                        # Platform-independent logic (CPython-compatible)
│   ├── protocol/
│   │   └── fsci.py              #   FSCI framing, CRC16, packet building
│   ├── services/
│   │   └── web_api.py           #   Web API business logic
│   └── models/
│       ├── api.py               #   API data models
│       └── led_state.py         #   LED state definitions
├── tests/unit/                  # Unit tests
├── docs/                        # Documentation
├── esp.py                       # Deployment tool (flash & upload)
├── flash.sh / flash.bat         # One-click flash scripts
└── config.example.json          # Configuration template
```

## Home Assistant Integration

### MQTT (Recommended)

The device publishes HA MQTT Auto-Discovery messages on startup.
A `button.ai_pump_feed_mode` entity appears automatically in HA.

### REST API (Alternative)

```yaml
# configuration.yaml
rest_command:
  pump_feed_mode:
    url: "http://<ESP32_IP>/api/feed-mode"
    method: POST
```

### Example Automation: Disable ATO During Feed Mode

This automation triggers when Feed Mode transitions to `running` state,
disables the ATO switch, waits for the feeding period to end, and re-enables it.

```yaml
automation:
  - alias: "Disable ATO during Feed Mode"
    description: >
      Turns off Auto Top-Off after Feed Mode is successfully activated,
      then re-enables it after the feeding period
    triggers:
      - trigger: state
        entity_id:
          - event.ai_pump_bridge_ai_pump_feed_mode_event
        attribute: event_type
        to: running
    conditions:
      - condition: template
        value_template: >
          {{ trigger.from_state.attributes.event_type == 'activating' }}
    actions:
      - action: switch.turn_off
        entity_id: switch.ato_pump
      - delay:
          minutes: 14
      - action: switch.turn_on
        entity_id: switch.ato_pump
    mode: single
```

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with
Aqua Illumination** in any way. All product names and trademarks are the
property of their respective owners.

This project **does not replace** the official myAI or Mobius applications.
Its sole purpose is smart home integration — activating Feed Mode presets
that the user has previously configured in the official app.

**USE AT YOUR OWN RISK.** The author makes no warranties of any kind and
assumes no liability for any damage to equipment, livestock, or property
arising from the use of this software. By using this project you accept
full responsibility for any consequences.


## License

MIT

## Acknowledgments

- Created for the aquarium hobby community

---

*Project started: 2026-02-28*
