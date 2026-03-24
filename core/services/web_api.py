"""Web API business logic — pure, hardware-independent.

Handles request validation, response building, and action signaling.
No asyncio, no MicroPython imports, no I/O.
"""

try:
    from typing import Optional
except ImportError:
    pass

from core.models.api import (
    ApiResponse,
    AutoConnectAction,
    AutoConnectProgress,
    ConnectAction,
    FactoryResetAction,
    FeedModeAction,
    MqttAction,
    ReadRemoteScenesAction,
    ReadSettingsAction,
    ScanAction,
    SyncProgress,
    SyncSettingsAction,
    WifiAction,
)


_DEFAULT_MQTT_PORT = 1883
_DEFAULT_ADDR_TYPE = 1


class WebApiHandler:
    """Pure business logic for HTTP API endpoints.

    Receives parsed data, returns ApiResponse objects.
    Side effects are signaled via action objects in the response.
    Firmware layer executes actions after sending the HTTP response.
    """

    def __init__(self) -> None:
        self._devices: list = []
        self._auto_progress: Optional[AutoConnectProgress] = None
        self._sync_progress: Optional[SyncProgress] = None

    def handle_status(self, wifi_status: dict, ble_target: Optional[str],
                      ble_busy: bool, mqtt_status: dict,
                      free_mem: int = 0,
                      mesh_prefix: Optional[str] = None,
                      feed_duration: int = 0) -> ApiResponse:
        """Build system status response."""
        body = {
            'wifi': wifi_status,
            'ble': {'target': ble_target, 'busy': ble_busy,
                    'mesh_prefix': mesh_prefix},
            'mqtt': mqtt_status,
            'free_mem': free_mem,
            'feed_duration': feed_duration,
        }
        return ApiResponse(200, body)

    def handle_devices(self) -> ApiResponse:
        """Return cached device list."""
        return ApiResponse(200, {'devices': self._devices})

    def update_devices(self, devices: list) -> None:
        """Update cached device list after BLE scan."""
        self._devices = devices

    def handle_scan(self, ble_available: bool,
                    ble_busy: bool) -> ApiResponse:
        """Validate BLE scan request."""
        if not ble_available:
            return ApiResponse(500, {'error': 'BLE not available'})
        if ble_busy:
            return ApiResponse(400, {'error': 'BLE busy'})
        return ApiResponse(202, {'status': 'scanning'}, ScanAction())

    def handle_auto_connect(self, ble_available: bool) -> ApiResponse:
        """Validate and start auto-connect flow."""
        if not ble_available:
            return ApiResponse(500, {'error': 'BLE not available'})
        if self._is_ble_flow_active():
            return ApiResponse(400, {'error': 'BLE operation in progress'})
        self._auto_progress = AutoConnectProgress()
        return ApiResponse(202, {'status': 'started'}, AutoConnectAction())

    def handle_auto_connect_status(self) -> ApiResponse:
        """Return current auto-connect progress."""
        p = self._auto_progress
        if p is None:
            return ApiResponse(200, {'phase': 'idle'})
        body: dict = {'phase': p.phase, 'found': p.found}
        if p.phase == 'syncing':
            body['current'] = p.current
            body['total'] = p.total
            body['device_name'] = p.device_name
        elif p.phase == 'done':
            body['target'] = p.target_address
        elif p.phase == 'choose':
            body['devices'] = p.devices
        elif p.phase == 'error':
            body['error'] = p.error
        return ApiResponse(200, body)

    @property
    def auto_connect_progress(self) -> Optional[AutoConnectProgress]:
        """Read access for background task."""
        return self._auto_progress

    def handle_sync_settings(self, ble_available: bool) -> ApiResponse:
        """Validate and start settings sync flow."""
        if not ble_available:
            return ApiResponse(500, {'error': 'BLE not available'})
        if self._is_ble_flow_active():
            return ApiResponse(400, {'error': 'BLE operation in progress'})
        self._sync_progress = SyncProgress()
        return ApiResponse(202, {'status': 'started'}, SyncSettingsAction())

    def handle_sync_status(self) -> ApiResponse:
        """Return current sync progress."""
        p = self._sync_progress
        if p is None:
            return ApiResponse(200, {'phase': 'idle'})
        body: dict = {'phase': p.phase, 'found': p.found}
        if p.phase == 'syncing':
            body['current'] = p.current
            body['total'] = p.total
            body['device_name'] = p.device_name
        elif p.phase == 'done':
            body['synced'] = p.synced
            body['results'] = p.results
        elif p.phase == 'error':
            body['error'] = p.error
        return ApiResponse(200, body)

    @property
    def sync_progress(self) -> Optional[SyncProgress]:
        """Read access for sync background task."""
        return self._sync_progress

    def handle_connect(self, body: Optional[dict]) -> ApiResponse:
        """Validate connect request and extract parameters."""
        if body is None or 'address' not in body:
            return ApiResponse(400, {'error': 'Missing address'})
        address = body['address']
        addr_type = body.get('addr_type', _DEFAULT_ADDR_TYPE)
        return ApiResponse(
            200,
            {'status': 'ok', 'address': address},
            ConnectAction(address, addr_type),
        )

    def handle_feed_mode(self, ble_available: bool) -> ApiResponse:
        """Validate feed mode preconditions.

        Returns error ApiResponse or ApiResponse with FeedModeAction.
        Firmware must execute the BLE operation and call
        build_feed_mode_response() with the result.
        """
        if not ble_available:
            return ApiResponse(500, {'error': 'BLE not available'})
        return ApiResponse(200, {}, FeedModeAction())

    def build_feed_mode_response(self, success: bool,
                                 message: str) -> ApiResponse:
        """Build response after feed mode BLE operation."""
        code = 200 if success else 500
        return ApiResponse(code, {'success': success, 'message': message})

    def handle_read_settings(self, ble_available: bool) -> ApiResponse:
        """Validate read settings preconditions."""
        if not ble_available:
            return ApiResponse(500, {'error': 'BLE not available'})
        return ApiResponse(200, {}, ReadSettingsAction())

    def build_read_settings_response(self, success: bool,
                                     message: str) -> ApiResponse:
        """Build response after read settings BLE operation."""
        code = 200 if success else 500
        return ApiResponse(code, {'success': success, 'message': message})

    def handle_read_remote_scenes(self, body: Optional[dict],
                                  ble_available: bool) -> ApiResponse:
        """Validate remote scenes read request."""
        if not ble_available:
            return ApiResponse(500, {'error': 'BLE not available'})
        if body is None or 'address' not in body:
            return ApiResponse(400, {'error': 'Missing address'})
        address = body['address']
        addr_type = body.get('addr_type', _DEFAULT_ADDR_TYPE)
        return ApiResponse(200, {}, ReadRemoteScenesAction(address, addr_type))

    def build_read_remote_scenes_response(self, success: bool,
                                          message: str) -> ApiResponse:
        """Build response after remote scenes read."""
        code = 200 if success else 500
        body = {'success': success, 'message': message}
        if success:
            body['scenes_json'] = message
        return ApiResponse(code, body)

    def handle_wifi_get(self, wifi_status: dict) -> ApiResponse:
        """Return WiFi status."""
        return ApiResponse(200, wifi_status)

    def handle_wifi_set(self, body: Optional[dict]) -> ApiResponse:
        """Validate WiFi config update."""
        if body is None or 'ssid' not in body:
            return ApiResponse(400, {'error': 'Missing ssid'})
        ssid = body['ssid']
        password = body.get('password', '')
        return ApiResponse(
            200,
            {'status': 'ok', 'ssid': ssid},
            WifiAction(ssid, password),
        )

    def handle_mqtt_get(self, mqtt_config: dict,
                        is_connected: bool) -> ApiResponse:
        """Build MQTT status from config and connection state."""
        body = {
            'broker': mqtt_config.get('broker', ''),
            'port': mqtt_config.get('port', _DEFAULT_MQTT_PORT),
            'user': mqtt_config.get('user', ''),
            'configured': bool(mqtt_config.get('broker')),
            'connected': is_connected,
        }
        return ApiResponse(200, body)

    def handle_factory_reset(self, body: Optional[dict],
                             expected_token: str) -> ApiResponse:
        """Validate reset token and signal factory reset."""
        token = body.get('token') if body else None
        if token != expected_token:
            return ApiResponse(403, {'error': 'Invalid reset token'})
        return ApiResponse(200, {'status': 'resetting'}, FactoryResetAction())

    def handle_mqtt_set(self, body: Optional[dict]) -> ApiResponse:
        """Validate MQTT config update."""
        if body is None or 'broker' not in body:
            return ApiResponse(400, {'error': 'Missing broker'})
        broker = body['broker']
        port = body.get('port', _DEFAULT_MQTT_PORT)
        user = body.get('user', '')
        password = body.get('password', '')
        return ApiResponse(
            200,
            {'status': 'ok'},
            MqttAction(broker, port, user, password),
        )

    # -- Private methods -------------------------------------------------------

    def _is_ble_flow_active(self) -> bool:
        """Check if any BLE background flow is in progress."""
        p = self._auto_progress
        if p and p.phase in ('scanning', 'syncing'):
            return True
        s = self._sync_progress
        if s and s.phase in ('scanning', 'syncing'):
            return True
        return False
