"""
MQTT client with Home Assistant Auto-Discovery.

Publishes a HA button entity for Feed Mode activation.
Subscribes to command topic, sets flag for Supervisor to handle.
Uses umqtt.simple (built-in MicroPython library).
"""

import json
import time
import gc
import asyncio

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Optional


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

STATE_DISCONNECTED = 'disconnected'
STATE_CONNECTING = 'connecting'
STATE_CONNECTED = 'connected'
STATE_ERROR = 'error'


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_INITIAL_BACKOFF_MS = 2_000
_MAX_BACKOFF_MS = 16_000
_KEEPALIVE_S = 60
_PAYLOAD_PRESS = b'PRESS'


# ---------------------------------------------------------------------------
# MQTTManager
# ---------------------------------------------------------------------------

class MQTTManager:
    """MQTT client with HA Auto-Discovery for Feed Mode button.

    Lifecycle: __init__() -> initialize(...) -> start() -> ... -> stop()

    State machine:
        DISCONNECTED -> CONNECTING -> CONNECTED
                     -> ERROR -> (check_connection) -> CONNECTING
    """

    def __init__(self) -> None:
        self._client = None  # type: Optional[object]
        self._state: str = STATE_DISCONNECTED
        self._feed_requested: bool = False
        self._broker: Optional[str] = None
        self._port: int = 1883
        self._user: Optional[str] = None
        self._password: Optional[str] = None
        self._device_id: Optional[str] = None
        self._t_avail: Optional[bytes] = None  # topics (set in initialize)
        self._t_state: Optional[bytes] = None
        self._t_cmd: Optional[bytes] = None
        self._t_diag: Optional[bytes] = None
        self._t_event: Optional[bytes] = None
        self._t_disc: Optional[bytes] = None
        self._t_disc_event: Optional[bytes] = None
        self._uptime_start: int = 0

    def initialize(self, broker: Optional[str] = None, port: int = 1883,
                   user: Optional[str] = None, password: Optional[str] = None,
                   device_id: str = 'esp32') -> None:
        """Set MQTT parameters and build topics. No I/O."""
        self._broker = broker or None
        self._port = port
        self._user = user or None
        self._password = password or None
        self._device_id = device_id

        # Build topics once as bytes (umqtt requires bytes)
        prefix = b'ai_pump/' + device_id.encode()
        self._t_avail = prefix + b'/availability'
        self._t_state = prefix + b'/state'
        self._t_cmd = prefix + b'/feed_mode/activate'
        self._t_diag = prefix + b'/diagnostics'
        self._t_event = prefix + b'/feed_mode/event'
        self._t_disc = (b'homeassistant/button/ai_pump_'
                        + device_id.encode()
                        + b'/feed_mode/config')
        self._t_disc_event = (b'homeassistant/event/ai_pump_'
                              + device_id.encode()
                              + b'/feed_mode/config')

    async def start(self) -> None:
        """Connect to MQTT broker if configured."""
        if not self._broker:
            print("MQTT: no broker configured")
            return
        self._uptime_start = time.ticks_ms()
        await self._connect()

    async def stop(self) -> None:
        """Publish offline status and disconnect."""
        if self._client and self._state == STATE_CONNECTED:
            try:
                self._client.publish(self._t_avail, b'offline', retain=True)
                self._client.disconnect()
            except Exception as e:
                print("MQTT: disconnect error: %s" % e)
        self._cleanup_client()
        self._state = STATE_DISCONNECTED
        print("MQTT: stopped")

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == STATE_CONNECTED

    @property
    def device_id(self) -> Optional[str]:
        return self._device_id

    def get_status(self) -> dict:
        """Status dict for REST API / diagnostics."""
        return {
            'state': self._state,
            'broker': self._broker or '',
            'connected': self._state == STATE_CONNECTED,
        }

    # -- Public commands (called by Supervisor) -----------------------------

    def check_messages(self) -> Optional[str]:
        """Non-blocking check for incoming MQTT messages.

        Returns 'feed_mode' if command received, None otherwise.
        """
        if not self._client or self._state != STATE_CONNECTED:
            return None
        try:
            self._client.check_msg()
        except Exception:
            print("MQTT: connection lost")
            self._state = STATE_DISCONNECTED
            self._cleanup_client()
            return None
        if self._feed_requested:
            self._feed_requested = False
            return 'feed_mode'
        return None

    def publish_state(self, success: bool, message: str) -> None:
        """Publish Feed Mode result to state topic (retained)."""
        payload = json.dumps({
            'last_result': 'success' if success else 'error',
            'last_message': message,
        })
        self._safe_publish(self._t_state, payload.encode(), retain=True)

    def publish_feed_event(self, event_type: str, duration: int = 0,
                           message: str = '') -> None:
        """Publish Feed Mode event for HA Event entity."""
        payload = {'event_type': event_type}
        if duration:
            payload['duration'] = duration
        if message:
            payload['message'] = message
        self._safe_publish(self._t_event,
                           json.dumps(payload).encode())

    def publish_diagnostics(self, wifi_rssi: Optional[int] = None) -> None:
        """Publish device diagnostics (uptime, free memory, RSSI)."""
        uptime_s = time.ticks_diff(
            time.ticks_ms(), self._uptime_start) // 1000
        payload = json.dumps({
            'uptime': uptime_s,
            'free_mem': gc.mem_free(),
            'wifi_rssi': wifi_rssi,
        })
        self._safe_publish(self._t_diag, payload.encode())

    async def check_connection(self) -> str:
        """Check MQTT health. Reconnect if disconnected.

        Call periodically from Supervisor. Returns current state.
        """
        if not self._broker:
            return self._state

        if self._state == STATE_CONNECTED:
            try:
                self._client.ping()
            except Exception:
                print("MQTT: ping failed")
                self._state = STATE_DISCONNECTED
                self._cleanup_client()

        if self._state in (STATE_DISCONNECTED, STATE_ERROR):
            await self._connect()

        return self._state

    # -- Private: connection logic ------------------------------------------

    async def _connect(self) -> bool:
        """Connect with bounded retries and exponential backoff.

        Returns True on success, False after all retries exhausted.
        """
        backoff_ms = _INITIAL_BACKOFF_MS

        for attempt in range(_MAX_RETRIES):
            self._state = STATE_CONNECTING
            print("MQTT: connecting to %s:%d (%d/%d)" % (
                self._broker, self._port, attempt + 1, _MAX_RETRIES))

            try:
                self._init_client()
                self._client.connect()
                self._state = STATE_CONNECTED

                # Post-connect setup
                self._client.subscribe(self._t_cmd)
                self._publish_discovery()
                self._client.publish(
                    self._t_avail, b'online', retain=True)

                print("MQTT: connected")
                return True
            except Exception as e:
                print("MQTT: connect failed: %s" % e)
                self._cleanup_client()

            if attempt < _MAX_RETRIES - 1:
                print("MQTT: backoff %dms" % backoff_ms)
                await asyncio.sleep_ms(backoff_ms)
                backoff_ms = min(backoff_ms * 2, _MAX_BACKOFF_MS)

        self._state = STATE_ERROR
        print("MQTT: all %d attempts failed" % _MAX_RETRIES)
        return False

    def _init_client(self) -> None:
        """Create fresh umqtt client with LWT."""
        from umqtt.simple import MQTTClient
        client_id = b'ai_pump_' + self._device_id.encode()
        self._client = MQTTClient(
            client_id, self._broker, port=self._port,
            user=self._user, password=self._password,
            keepalive=_KEEPALIVE_S,
        )
        self._client.set_last_will(
            self._t_avail, b'offline', retain=True, qos=0)
        self._client.set_callback(self._on_message)

    def _publish_discovery(self) -> None:
        """Publish HA MQTT Auto-Discovery for button + event entities."""
        did = self._device_id
        device = {
            'identifiers': ['ai_pump_%s' % did],
            'name': 'AI Pump Bridge',
            'manufacturer': 'mpshevlotsky',
            'model': 'ESP32 BLE Gateway',
        }
        # Button entity (Feed Mode trigger)
        btn = json.dumps({
            'name': 'Feed Mode',
            'unique_id': 'ai_pump_%s_feed_mode' % did,
            'command_topic': self._t_cmd.decode(),
            'payload_press': 'PRESS',
            'availability_topic': self._t_avail.decode(),
            'state_topic': self._t_state.decode(),
            'device': device,
        })
        self._client.publish(self._t_disc, btn.encode(), retain=True)
        # Event entity (Feed Mode status)
        evt = json.dumps({
            'name': 'Feed Mode Event',
            'unique_id': 'ai_pump_%s_feed_mode_event' % did,
            'state_topic': self._t_event.decode(),
            'event_types': ['idle', 'activating', 'running', 'error'],
            'availability_topic': self._t_avail.decode(),
            'device': device,
        })
        self._client.publish(self._t_disc_event, evt.encode(),
                             retain=True)
        print("MQTT: HA discovery published")

    def _on_message(self, topic: bytes, msg: bytes) -> None:
        """MQTT message callback. Called synchronously by check_msg().

        Minimal: just sets flag. Supervisor handles the actual command.
        """
        if topic == self._t_cmd and msg == _PAYLOAD_PRESS:
            self._feed_requested = True
            print("MQTT: feed mode command received")

    def _safe_publish(self, topic: bytes, msg: bytes,
                      retain: bool = False) -> bool:
        """Publish with disconnect detection."""
        if not self._client or self._state != STATE_CONNECTED:
            return False
        try:
            self._client.publish(topic, msg, retain=retain)
            return True
        except Exception as e:
            print("MQTT: publish error: %s" % e)
            self._state = STATE_DISCONNECTED
            self._cleanup_client()
            return False

    def _cleanup_client(self) -> None:
        """Release client resources."""
        if self._client:
            try:
                self._client.disconnect()
            except Exception as e:
                print("MQTT: cleanup disconnect error: %s" % e)
            self._client = None
