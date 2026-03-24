"""
WiFi AP/Client mode manager with explicit state machine.

AP mode for initial setup (SSID "AI-Pump-Bridge", IP 192.168.4.1).
Client mode for normal operation (connects to configured network).
"""

import network
import asyncio
import time

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Optional


# ---------------------------------------------------------------------------
# States (explicit string constants for state machine)
# ---------------------------------------------------------------------------

STATE_DISCONNECTED = 'disconnected'
STATE_CONNECTING = 'connecting'
STATE_CONNECTED = 'connected'
STATE_AP_ACTIVE = 'ap_active'
STATE_ERROR = 'error'


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_AP_SSID = 'AI-Pump-Bridge'
_CONNECT_TIMEOUT_MS = 15_000
_POLL_INTERVAL_MS = 100
_MAX_RETRIES = 3
_INITIAL_BACKOFF_MS = 1_000
_MAX_BACKOFF_MS = 8_000


# ---------------------------------------------------------------------------
# WiFiManager
# ---------------------------------------------------------------------------

class WiFiManager:
    """WiFi AP/Client mode manager.

    Lifecycle: __init__() -> initialize(ssid, pw) -> start() -> ... -> stop()

    State machine:
        DISCONNECTED -> CONNECTING -> CONNECTED
                     -> ERROR -> (retry) -> CONNECTING
        DISCONNECTED -> AP_ACTIVE
    """

    def __init__(self) -> None:
        self._sta: Optional[network.WLAN] = None
        self._ap: Optional[network.WLAN] = None
        self._state: str = STATE_DISCONNECTED
        self._ssid: Optional[str] = None
        self._password: str = ''
        self._ip: Optional[str] = None

    def initialize(self, ssid: Optional[str] = None,
                   password: Optional[str] = None) -> None:
        """Set WiFi credentials. No I/O."""
        self._ssid = ssid or None
        self._password = password or ''

    async def start(self) -> None:
        """Start WiFi subsystem.

        If STA already connected (from boot.py) -> adopt it.
        If credentials available -> try client mode with retry.
        Otherwise -> AP mode.
        """
        self._sta = network.WLAN(network.STA_IF)
        self._ap = network.WLAN(network.AP_IF)

        # Adopt existing connection (boot.py may have connected)
        if self._sta.active() and self._sta.isconnected():
            self._state = STATE_CONNECTED
            self._ip = self._sta.ifconfig()[0]
            print("WiFi: already connected, IP=%s" % self._ip)
            return

        # Try client mode
        if self._ssid:
            success = await self._connect_with_retry()
            if success:
                return

        # Fallback to AP mode
        await self._start_ap()

    async def stop(self) -> None:
        """Deactivate all WiFi interfaces."""
        if self._sta:
            self._sta.active(False)
        if self._ap:
            self._ap.active(False)
        self._state = STATE_DISCONNECTED
        self._ip = None
        print("WiFi: stopped")

    # -- Public properties -------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == STATE_CONNECTED

    @property
    def ip_address(self) -> Optional[str]:
        return self._ip

    def get_status(self) -> dict:
        """Status dict for REST API / diagnostics."""
        mode = 'none'
        if self._state == STATE_CONNECTED:
            mode = 'client'
        elif self._state == STATE_AP_ACTIVE:
            mode = 'ap'

        result = {
            'state': self._state,
            'mode': mode,
            'ip': self._ip,
        }
        if self._ssid:
            result['ssid'] = self._ssid
        if self._state == STATE_CONNECTED and self._sta:
            try:
                result['rssi'] = self._sta.status('rssi')
            except (OSError, ValueError):
                pass
        return result

    # -- Public commands ---------------------------------------------------

    async def connect_client(self, ssid: str, password: str = '',
                             keep_ap: bool = False) -> bool:
        """Connect as WiFi client with new credentials.

        If keep_ap is False (default), deactivates AP before connecting.
        If keep_ap is True, AP stays active for AP→STA transition redirect.
        Returns True on success.
        """
        self._ssid = ssid
        self._password = password
        if not keep_ap and self._ap and self._ap.active():
            self._ap.active(False)
        success = await self._connect_with_retry()
        if not success and keep_ap and self._ap and self._ap.active():
            self._state = STATE_AP_ACTIVE
            self._ip = self._ap.ifconfig()[0]
        return success

    def stop_ap(self) -> None:
        """Deactivate AP interface if active."""
        if self._ap and self._ap.active():
            self._ap.active(False)
            print("WiFi: AP deactivated")

    async def start_ap_mode(self) -> None:
        """Switch to AP mode. Deactivates STA if active."""
        if self._sta and self._sta.active():
            self._sta.disconnect()
            self._sta.active(False)
        await self._start_ap()

    async def check_connection(self) -> str:
        """Check WiFi health. Call periodically from supervisor.

        If connection lost in client mode, attempts reconnect with retry.
        Returns current state string.
        """
        if self._state != STATE_CONNECTED:
            return self._state

        if not self._sta or not self._sta.isconnected():
            print("WiFi: connection lost")
            self._state = STATE_DISCONNECTED
            self._ip = None
            if self._ssid:
                await self._connect_with_retry()
        return self._state

    # -- Private: connection logic -----------------------------------------

    async def _connect_with_retry(self) -> bool:
        """Connect with bounded retries and exponential backoff.

        Returns True on success, False after all retries exhausted.
        """
        backoff_ms = _INITIAL_BACKOFF_MS

        for attempt in range(_MAX_RETRIES):
            self._state = STATE_CONNECTING
            print("WiFi: connecting to '%s' (%d/%d)" % (
                self._ssid, attempt + 1, _MAX_RETRIES))

            success = await self._try_connect()
            if success:
                self._state = STATE_CONNECTED
                self._ip = self._sta.ifconfig()[0]
                print("WiFi: connected, IP=%s" % self._ip)
                return True

            self._sta.disconnect()

            if attempt < _MAX_RETRIES - 1:
                print("WiFi: failed, backoff %dms" % backoff_ms)
                await asyncio.sleep_ms(backoff_ms)
                backoff_ms = min(backoff_ms * 2, _MAX_BACKOFF_MS)

        self._state = STATE_ERROR
        print("WiFi: all %d attempts failed" % _MAX_RETRIES)
        return False

    async def _try_connect(self) -> bool:
        """Single non-blocking connection attempt with timeout."""
        if not self._sta.active():
            self._sta.active(True)
        self._sta.connect(self._ssid, self._password)

        deadline = time.ticks_add(time.ticks_ms(), _CONNECT_TIMEOUT_MS)
        while not self._sta.isconnected():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return False
            await asyncio.sleep_ms(_POLL_INTERVAL_MS)
        return True

    async def _start_ap(self) -> None:
        """Start WiFi Access Point."""
        if self._sta and self._sta.active():
            self._sta.disconnect()
            self._sta.active(False)

        if not self._ap:
            self._ap = network.WLAN(network.AP_IF)
        self._ap.active(True)
        self._ap.config(essid=_AP_SSID)

        self._state = STATE_AP_ACTIVE
        self._ip = self._ap.ifconfig()[0]
        print("WiFi: AP mode, SSID='%s', IP=%s" % (_AP_SSID, self._ip))
