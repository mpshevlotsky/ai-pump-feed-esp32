"""Data objects for web API request/response handling.

All classes use __slots__ for memory efficiency on ESP32.
No MicroPython-specific imports — CPython-compatible.
"""

try:
    from typing import Optional
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Action objects — signal side effects to firmware layer
# ---------------------------------------------------------------------------

class FactoryResetAction:
    """Signal to perform factory reset — delete config and reboot."""
    __slots__ = ()


class ScanAction:
    """Signal to start BLE scan."""
    __slots__ = ()


class AutoConnectAction:
    """Signal to start auto-connect orchestration."""
    __slots__ = ()


class FeedModeAction:
    """Signal to activate feed mode via BLE."""
    __slots__ = ()


class ReadSettingsAction:
    """Signal to read pump settings via BLE."""
    __slots__ = ()


class ReadRemoteScenesAction:
    """Signal to read ConfiguredScenes from a specific pump via BLE."""
    __slots__ = ('address', 'addr_type')

    def __init__(self, address: str, addr_type: int) -> None:
        self.address: str = address
        self.addr_type: int = addr_type


class SyncSettingsAction:
    """Signal to start settings sync background flow."""
    __slots__ = ()


class ConnectAction:
    """BLE target connection parameters."""
    __slots__ = ('address', 'addr_type')

    def __init__(self, address: str, addr_type: int) -> None:
        self.address: str = address
        self.addr_type: int = addr_type


class WifiAction:
    """WiFi connection parameters for config update."""
    __slots__ = ('ssid', 'password')

    def __init__(self, ssid: str, password: str) -> None:
        self.ssid: str = ssid
        self.password: str = password


class MqttAction:
    """MQTT connection parameters for config update."""
    __slots__ = ('broker', 'port', 'user', 'password')

    def __init__(self, broker: str, port: int,
                 user: str, password: str) -> None:
        self.broker: str = broker
        self.port: int = port
        self.user: str = user
        self.password: str = password


# ---------------------------------------------------------------------------
# API response
# ---------------------------------------------------------------------------

class AutoConnectProgress:
    """Progress state for auto-connect background flow."""
    __slots__ = ('phase', 'found', 'current', 'total',
                 'device_name', 'target_address', 'devices', 'error')

    def __init__(self) -> None:
        self.phase: str = 'scanning'
        self.found: int = 0
        self.current: int = 0
        self.total: int = 0
        self.device_name: str = ''
        self.target_address: Optional[str] = None
        self.devices: list = []
        self.error: Optional[str] = None


class SyncProgress:
    """Progress state for settings sync background flow."""
    __slots__ = ('phase', 'found', 'current', 'total',
                 'device_name', 'synced', 'error', 'results')

    def __init__(self) -> None:
        self.phase: str = 'scanning'
        self.found: int = 0
        self.current: int = 0
        self.total: int = 0
        self.device_name: str = ''
        self.synced: int = 0
        self.error: Optional[str] = None
        self.results: list = []


class ApiResponse:
    """HTTP API response with optional action for firmware to execute.

    body: dict or list, JSON-serializable.
    action: None or an action object (ScanAction, ConnectAction, etc.).
    """
    __slots__ = ('status_code', 'body', 'action')

    def __init__(self, status_code: int, body: dict,
                 action: object = None) -> None:
        self.status_code: int = status_code
        self.body: dict = body
        self.action: object = action
