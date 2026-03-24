"""
Entry point — Supervisor that owns and manages all subsystems.

Architecture (from CLAUDE.md OOP MODEL):
    Supervisor
    ├── WiFiManager
    ├── BLEManager
    ├── MQTTManager
    ├── WebServer
    └── LedIndicator
"""

import asyncio
import gc
import time
import machine
import config

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import List, Optional

from wifi_manager import WiFiManager
from ble_pump import BLEManager
from mqtt_client import MQTTManager
from web_server import WebServer
from led_indicator import LedIndicator


# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------

_LOOP_INTERVAL_S = 1
_HEALTH_CHECK_S = 30
_GC_INTERVAL_S = 60
_DIAG_INTERVAL_S = 60
_WDT_TIMEOUT_MS = 120_000
_MEM_WARNING_BYTES = 10_000
_DEFAULT_FEED_DURATION_SEC = 600


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

class Supervisor:
    """Top-level supervisor — creates, initializes, and runs all subsystems.

    Lifecycle: __init__() -> start() -> run_forever(wdt)
    """

    def __init__(self) -> None:
        self._wifi: WiFiManager = WiFiManager()
        self._ble: BLEManager = BLEManager()
        self._mqtt: MQTTManager = MQTTManager()
        self._web: WebServer = WebServer()
        self._led: LedIndicator = LedIndicator()
        self._feed_end_ms: int = 0

    async def start(self) -> None:
        """Load config, initialize subsystems, start them."""
        cfg = config.load()
        device_id = _make_device_id()
        print("Supervisor: device_id=%s" % device_id)

        # Initialize (no I/O, set parameters only)
        wifi_cfg = cfg.get('wifi', {})
        self._wifi.initialize(
            ssid=wifi_cfg.get('ssid'),
            password=wifi_cfg.get('password', ''),
        )

        pump_cfg = cfg.get('pump', {})
        prefix_hex = pump_cfg.get('mesh_prefix')
        self._ble.initialize(
            target_address=pump_cfg.get('address'),
            addr_type=pump_cfg.get('addr_type', 1),
            mesh_prefix=(bytes.fromhex(prefix_hex)
                         if prefix_hex else None),
        )

        mqtt_cfg = cfg.get('mqtt', {})
        self._mqtt.initialize(
            broker=mqtt_cfg.get('broker'),
            port=mqtt_cfg.get('port', 1883),
            user=mqtt_cfg.get('user'),
            password=mqtt_cfg.get('password'),
            device_id=device_id,
        )

        self._web.initialize(self._wifi, self._ble, self._mqtt,
                              on_feed_mode=self._execute_feed_mode)
        self._led.initialize()

        # Start subsystems (I/O — LED first for boot indication)
        await self._led.start()
        await self._wifi.start()
        await self._ble.start()
        await self._mqtt.start()
        await self._web.start()

        if self._mqtt.is_connected:
            self._mqtt.publish_feed_event('idle')

        gc.collect()
        print("Supervisor: all started, free=%d bytes" % gc.mem_free())

    async def run_forever(self, wdt: Optional[object] = None) -> None:
        """Main loop — 1s for MQTT responsiveness, periodic health checks.

        Robust: catches all exceptions to prevent supervisor crash.
        WDT fed every iteration — resets ESP32 if loop hangs.
        """
        health_c = 0
        slow_c = 0

        while True:
            try:
                await asyncio.sleep(_LOOP_INTERVAL_S)
                if wdt:
                    wdt.feed()

                # LED status (every 1s)
                self._led.set_states(self._collect_led_states())

                # Feed mode countdown (every 1s)
                if self._feed_end_ms and time.ticks_diff(
                        time.ticks_ms(), self._feed_end_ms) >= 0:
                    self._feed_end_ms = 0
                    self._mqtt.publish_feed_event('idle')

                # MQTT commands (every 1s)
                if self._mqtt.is_connected:
                    cmd = self._mqtt.check_messages()
                    if cmd == 'feed_mode':
                        await self._handle_feed_command()

                # Health checks (every 30s)
                health_c += _LOOP_INTERVAL_S
                if health_c >= _HEALTH_CHECK_S:
                    health_c = 0
                    await self._wifi.check_connection()
                    was_connected = self._mqtt.is_connected
                    await self._mqtt.check_connection()
                    if not was_connected and self._mqtt.is_connected:
                        self._mqtt.publish_feed_event('idle')

                # GC + diagnostics (every 60s)
                slow_c += _LOOP_INTERVAL_S
                if slow_c >= _GC_INTERVAL_S:
                    slow_c = 0
                    self._gc_and_diagnostics()

            except MemoryError:
                gc.collect()
                print("Supervisor: MemoryError, free=%d" % gc.mem_free())
            except Exception as e:
                print("Supervisor: loop error: %s" % e)

    async def _handle_feed_command(self) -> None:
        """Handle Feed Mode command from MQTT."""
        print("Supervisor: MQTT feed mode command")
        await self._execute_feed_mode()

    async def _execute_feed_mode(self) -> tuple:
        """Execute Feed Mode — shared by MQTT and HTTP paths.

        Returns (success: bool, message: str).
        """
        self._mqtt.publish_feed_event('activating')
        old_addr = self._ble.target_address
        old_prefix = self._ble.mesh_prefix
        success, message = await self._ble.activate_feed_mode()
        if success and (self._ble.target_address != old_addr
                        or self._ble.mesh_prefix != old_prefix):
            self._save_pump_config()
        self._mqtt.publish_state(success, message)
        if success:
            duration = self._get_feed_duration()
            self._feed_end_ms = time.ticks_add(
                time.ticks_ms(), duration * 1000)
            self._mqtt.publish_feed_event('running', duration=duration)
        else:
            self._mqtt.publish_feed_event('error', message=message)
        return (success, message)

    def _get_feed_duration(self) -> int:
        """Max Feed Mode duration from config, or default 10 min."""
        fm = config.load().get('feed_mode', {})
        if not fm:
            return _DEFAULT_FEED_DURATION_SEC
        return max(fm.values(), default=_DEFAULT_FEED_DURATION_SEC)

    def _save_pump_config(self) -> None:
        """Persist pump target after fallback address change."""
        cfg = config.load()
        pump = cfg.setdefault('pump', {})
        pump['address'] = self._ble.target_address
        pump['addr_type'] = self._ble.target_addr_type
        prefix = self._ble.mesh_prefix
        pump['mesh_prefix'] = prefix.hex() if prefix else None
        config.save(cfg)

    def _collect_led_states(self) -> List[str]:
        """Map subsystem states to LED indicator state names."""
        states = []
        wifi = self._wifi.state

        if wifi == 'ap_active':
            states.append('ap_mode')
        elif wifi == 'connecting':
            states.append('wifi_connecting')
        elif wifi == 'connected':
            if self._mqtt.is_connected:
                states.append('connected')
            else:
                states.append('wifi_only')

        if wifi == 'error' or self._mqtt.state == 'error':
            states.append('error')

        if self._ble.is_busy:
            states.append('ble_active')

        return states

    def _gc_and_diagnostics(self) -> None:
        """Periodic GC, memory check, and MQTT diagnostics."""
        gc.collect()
        free = gc.mem_free()
        if free < _MEM_WARNING_BYTES:
            print("Supervisor: LOW MEMORY %d bytes" % free)
        if self._mqtt.is_connected:
            wifi_status = self._wifi.get_status()
            self._mqtt.publish_diagnostics(
                wifi_rssi=wifi_status.get('rssi'))


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _make_device_id() -> str:
    """Generate short device ID from chip unique ID (last 4 hex chars)."""
    uid = machine.unique_id()
    return '%02x%02x' % (uid[-2], uid[-1])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    supervisor = Supervisor()
    await supervisor.start()

    # Enable WDT only after successful startup
    wdt = machine.WDT(timeout=_WDT_TIMEOUT_MS)
    print("Supervisor: WDT enabled (%ds)" % (_WDT_TIMEOUT_MS // 1000))

    await supervisor.run_forever(wdt)


try:
    asyncio.run(_main())
except KeyboardInterrupt:
    print("Supervisor: stopped by user")
except Exception as e:
    print("Supervisor: FATAL: %s" % e)
    print("Supervisor: resetting in 5s...")
    import time
    time.sleep(5)
    machine.reset()
