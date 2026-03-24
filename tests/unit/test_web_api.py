"""Unit tests for core.services.web_api module."""

from core.models.api import (
    ConnectAction,
    FactoryResetAction,
    FeedModeAction,
    MqttAction,
    ReadRemoteScenesAction,
    ReadSettingsAction,
    ScanAction,
    SyncSettingsAction,
    WifiAction,
)
from core.services.web_api import WebApiHandler


# ---------------------------------------------------------------------------
# handle_status
# ---------------------------------------------------------------------------

class TestHandleStatus:

    def test_full_status(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_status(
            wifi_status={'ssid': 'test', 'ip': '1.2.3.4'},
            ble_target='AA:BB:CC',
            ble_busy=False,
            mqtt_status={'state': 'connected'},
            free_mem=50000,
            mesh_prefix='fd1a2b3c4d5e6f70',
            feed_duration=360,
        )
        assert result.status_code == 200
        assert result.body['wifi']['ssid'] == 'test'
        assert result.body['ble']['target'] == 'AA:BB:CC'
        assert result.body['ble']['busy'] is False
        assert result.body['ble']['mesh_prefix'] == 'fd1a2b3c4d5e6f70'
        assert result.body['mqtt']['state'] == 'connected'
        assert result.body['free_mem'] == 50000
        assert result.body['feed_duration'] == 360
        assert result.action is None

    def test_empty_subsystems(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_status({}, None, False, {})
        assert result.status_code == 200
        assert result.body['ble']['target'] is None
        assert result.body['ble']['mesh_prefix'] is None
        assert result.body['wifi'] == {}
        assert result.body['free_mem'] == 0
        assert result.body['feed_duration'] == 0


# ---------------------------------------------------------------------------
# handle_devices
# ---------------------------------------------------------------------------

class TestHandleDevices:

    def test_empty_by_default(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_devices()
        assert result.status_code == 200
        assert result.body['devices'] == []

    def test_after_update(self) -> None:
        handler = WebApiHandler()
        devices = [{'name': 'pump1', 'address': 'AA:BB'}]
        handler.update_devices(devices)
        result = handler.handle_devices()
        assert result.status_code == 200
        assert len(result.body['devices']) == 1
        assert result.body['devices'][0]['name'] == 'pump1'


# ---------------------------------------------------------------------------
# handle_scan
# ---------------------------------------------------------------------------

class TestHandleScan:

    def test_ble_not_available(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_scan(False, False)
        assert result.status_code == 500
        assert 'error' in result.body
        assert result.action is None

    def test_ble_busy(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_scan(True, True)
        assert result.status_code == 400
        assert 'error' in result.body
        assert result.action is None

    def test_success(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_scan(True, False)
        assert result.status_code == 202
        assert result.body['status'] == 'scanning'
        assert isinstance(result.action, ScanAction)


# ---------------------------------------------------------------------------
# handle_connect
# ---------------------------------------------------------------------------

class TestHandleConnect:

    def test_none_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_connect(None)
        assert result.status_code == 400
        assert result.action is None

    def test_missing_address(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_connect({'foo': 'bar'})
        assert result.status_code == 400
        assert result.action is None

    def test_valid_connect(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_connect({'address': 'AA:BB:CC'})
        assert result.status_code == 200
        assert isinstance(result.action, ConnectAction)
        assert result.action.address == 'AA:BB:CC'
        assert result.action.addr_type == 1

    def test_custom_addr_type(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_connect(
            {'address': 'AA:BB', 'addr_type': 0})
        assert result.action.addr_type == 0

    def test_response_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_connect({'address': 'AA:BB:CC'})
        assert result.body['status'] == 'ok'
        assert result.body['address'] == 'AA:BB:CC'


# ---------------------------------------------------------------------------
# handle_feed_mode / build_feed_mode_response
# ---------------------------------------------------------------------------

class TestHandleFeedMode:

    def test_ble_not_available(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_feed_mode(False)
        assert result.status_code == 500
        assert 'error' in result.body
        assert result.action is None

    def test_ble_available(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_feed_mode(True)
        assert isinstance(result.action, FeedModeAction)

    def test_build_success_response(self) -> None:
        handler = WebApiHandler()
        result = handler.build_feed_mode_response(True, 'activated')
        assert result.status_code == 200
        assert result.body['success'] is True
        assert result.body['message'] == 'activated'
        assert result.action is None

    def test_build_failure_response(self) -> None:
        handler = WebApiHandler()
        result = handler.build_feed_mode_response(False, 'Timeout')
        assert result.status_code == 500
        assert result.body['success'] is False
        assert result.body['message'] == 'Timeout'


# ---------------------------------------------------------------------------
# handle_wifi_get / handle_wifi_set
# ---------------------------------------------------------------------------

class TestHandleWifiGet:

    def test_returns_status(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_wifi_get({'ssid': 'net', 'ip': '1.2.3.4'})
        assert result.status_code == 200
        assert result.body['ssid'] == 'net'

    def test_empty_status(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_wifi_get({})
        assert result.status_code == 200
        assert result.action is None


class TestHandleWifiSet:

    def test_none_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_wifi_set(None)
        assert result.status_code == 400
        assert result.action is None

    def test_missing_ssid(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_wifi_set({'password': '123'})
        assert result.status_code == 400

    def test_valid_wifi(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_wifi_set(
            {'ssid': 'MyNet', 'password': 'pass123'})
        assert result.status_code == 200
        assert isinstance(result.action, WifiAction)
        assert result.action.ssid == 'MyNet'
        assert result.action.password == 'pass123'

    def test_default_empty_password(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_wifi_set({'ssid': 'Open'})
        assert result.action.password == ''

    def test_response_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_wifi_set({'ssid': 'Net'})
        assert result.body['status'] == 'ok'
        assert result.body['ssid'] == 'Net'


# ---------------------------------------------------------------------------
# handle_mqtt_get / handle_mqtt_set
# ---------------------------------------------------------------------------

class TestHandleMqttGet:

    def test_configured(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_mqtt_get(
            {'broker': '192.168.1.1', 'port': 1883, 'user': 'admin'},
            True,
        )
        assert result.status_code == 200
        assert result.body['broker'] == '192.168.1.1'
        assert result.body['port'] == 1883
        assert result.body['user'] == 'admin'
        assert result.body['configured'] is True
        assert result.body['connected'] is True

    def test_not_configured(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_mqtt_get({}, False)
        assert result.body['broker'] == ''
        assert result.body['port'] == 1883
        assert result.body['configured'] is False
        assert result.body['connected'] is False

    def test_partial_config(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_mqtt_get({'broker': 'mqtt.local'}, False)
        assert result.body['configured'] is True
        assert result.body['user'] == ''


class TestHandleMqttSet:

    def test_none_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_mqtt_set(None)
        assert result.status_code == 400
        assert result.action is None

    def test_missing_broker(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_mqtt_set({'user': 'admin'})
        assert result.status_code == 400

    def test_valid_mqtt(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_mqtt_set({
            'broker': '192.168.1.1',
            'port': 8883,
            'user': 'admin',
            'password': 'secret',
        })
        assert result.status_code == 200
        assert isinstance(result.action, MqttAction)
        assert result.action.broker == '192.168.1.1'
        assert result.action.port == 8883
        assert result.action.user == 'admin'
        assert result.action.password == 'secret'

    def test_default_values(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_mqtt_set({'broker': 'mqtt.local'})
        assert result.action.port == 1883
        assert result.action.user == ''
        assert result.action.password == ''

    def test_response_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_mqtt_set({'broker': 'x'})
        assert result.body['status'] == 'ok'


# ---------------------------------------------------------------------------
# handle_read_settings / build_read_settings_response
# ---------------------------------------------------------------------------

class TestHandleReadSettings:

    def test_ble_not_available(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_read_settings(False)
        assert result.status_code == 500
        assert 'error' in result.body
        assert result.action is None

    def test_ble_available(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_read_settings(True)
        assert result.status_code == 200
        assert isinstance(result.action, ReadSettingsAction)

    def test_build_success(self) -> None:
        handler = WebApiHandler()
        result = handler.build_read_settings_response(True, 'ok')
        assert result.status_code == 200
        assert result.body['success'] is True
        assert result.body['message'] == 'ok'

    def test_build_failure(self) -> None:
        handler = WebApiHandler()
        result = handler.build_read_settings_response(False, 'Timeout')
        assert result.status_code == 500
        assert result.body['success'] is False


# ---------------------------------------------------------------------------
# handle_read_remote_scenes / build_read_remote_scenes_response
# ---------------------------------------------------------------------------

class TestHandleReadRemoteScenes:

    def test_ble_not_available(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_read_remote_scenes(
            {'address': 'AA:BB'}, False)
        assert result.status_code == 500
        assert 'error' in result.body
        assert result.action is None

    def test_none_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_read_remote_scenes(None, True)
        assert result.status_code == 400
        assert result.action is None

    def test_missing_address(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_read_remote_scenes({'foo': 'bar'}, True)
        assert result.status_code == 400
        assert result.action is None

    def test_valid_request(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_read_remote_scenes(
            {'address': 'AA:BB:CC', 'addr_type': 0}, True)
        assert result.status_code == 200
        assert isinstance(result.action, ReadRemoteScenesAction)
        assert result.action.address == 'AA:BB:CC'
        assert result.action.addr_type == 0

    def test_default_addr_type(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_read_remote_scenes(
            {'address': 'AA:BB:CC'}, True)
        assert result.action.addr_type == 1

    def test_build_success(self) -> None:
        handler = WebApiHandler()
        scenes_json = '[{"id":1,"timeout":360,"mode":0,"speed":1000}]'
        result = handler.build_read_remote_scenes_response(True, scenes_json)
        assert result.status_code == 200
        assert result.body['success'] is True
        assert result.body['scenes_json'] == scenes_json

    def test_build_failure(self) -> None:
        handler = WebApiHandler()
        result = handler.build_read_remote_scenes_response(
            False, 'No scenes found')
        assert result.status_code == 500
        assert result.body['success'] is False
        assert 'scenes_json' not in result.body


# ---------------------------------------------------------------------------
# handle_factory_reset
# ---------------------------------------------------------------------------

class TestHandleFactoryReset:

    def test_valid_token(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_factory_reset(
            {'token': 'secret123'}, 'secret123')
        assert result.status_code == 200
        assert result.body == {'status': 'resetting'}
        assert isinstance(result.action, FactoryResetAction)

    def test_wrong_token(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_factory_reset(
            {'token': 'wrong'}, 'secret123')
        assert result.status_code == 403
        assert result.action is None

    def test_missing_token(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_factory_reset(
            {'other': 'data'}, 'secret123')
        assert result.status_code == 403
        assert result.action is None

    def test_none_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_factory_reset(None, 'secret123')
        assert result.status_code == 403
        assert result.action is None

    def test_empty_body(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_factory_reset({}, 'secret123')
        assert result.status_code == 403
        assert result.action is None


# ---------------------------------------------------------------------------
# handle_auto_connect (BLE flow guard)
# ---------------------------------------------------------------------------

class TestHandleAutoConnect:

    def test_ble_not_available(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_auto_connect(False)
        assert result.status_code == 500
        assert result.action is None

    def test_success(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_auto_connect(True)
        assert result.status_code == 202
        assert result.body['status'] == 'started'

    def test_blocked_by_auto_connect_in_progress(self) -> None:
        handler = WebApiHandler()
        handler.handle_auto_connect(True)
        result = handler.handle_auto_connect(True)
        assert result.status_code == 400
        assert 'in progress' in result.body['error']

    def test_blocked_by_sync_in_progress(self) -> None:
        handler = WebApiHandler()
        handler.handle_sync_settings(True)
        result = handler.handle_auto_connect(True)
        assert result.status_code == 400
        assert 'in progress' in result.body['error']

    def test_allowed_after_auto_connect_done(self) -> None:
        handler = WebApiHandler()
        handler.handle_auto_connect(True)
        handler.auto_connect_progress.phase = 'done'
        result = handler.handle_auto_connect(True)
        assert result.status_code == 202


# ---------------------------------------------------------------------------
# handle_sync_settings / handle_sync_status
# ---------------------------------------------------------------------------

class TestHandleSyncSettings:

    def test_ble_not_available(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_sync_settings(False)
        assert result.status_code == 500
        assert result.action is None

    def test_success(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_sync_settings(True)
        assert result.status_code == 202
        assert isinstance(result.action, SyncSettingsAction)
        assert result.body['status'] == 'started'

    def test_blocked_by_sync_in_progress(self) -> None:
        handler = WebApiHandler()
        handler.handle_sync_settings(True)
        result = handler.handle_sync_settings(True)
        assert result.status_code == 400
        assert 'in progress' in result.body['error']

    def test_blocked_by_auto_connect_in_progress(self) -> None:
        handler = WebApiHandler()
        handler.handle_auto_connect(True)
        result = handler.handle_sync_settings(True)
        assert result.status_code == 400
        assert 'in progress' in result.body['error']

    def test_allowed_after_sync_done(self) -> None:
        handler = WebApiHandler()
        handler.handle_sync_settings(True)
        handler.sync_progress.phase = 'done'
        result = handler.handle_sync_settings(True)
        assert result.status_code == 202

    def test_creates_fresh_progress(self) -> None:
        handler = WebApiHandler()
        handler.handle_sync_settings(True)
        handler.sync_progress.phase = 'done'
        handler.sync_progress.synced = 3
        handler.handle_sync_settings(True)
        assert handler.sync_progress.phase == 'scanning'
        assert handler.sync_progress.synced == 0


class TestHandleSyncStatus:

    def test_idle_when_no_sync(self) -> None:
        handler = WebApiHandler()
        result = handler.handle_sync_status()
        assert result.status_code == 200
        assert result.body['phase'] == 'idle'

    def test_scanning_phase(self) -> None:
        handler = WebApiHandler()
        handler.handle_sync_settings(True)
        handler.sync_progress.found = 2
        result = handler.handle_sync_status()
        assert result.body['phase'] == 'scanning'
        assert result.body['found'] == 2

    def test_syncing_phase(self) -> None:
        handler = WebApiHandler()
        handler.handle_sync_settings(True)
        p = handler.sync_progress
        p.phase = 'syncing'
        p.current = 1
        p.total = 3
        p.device_name = 'NERO 5'
        result = handler.handle_sync_status()
        assert result.body['phase'] == 'syncing'
        assert result.body['current'] == 1
        assert result.body['total'] == 3
        assert result.body['device_name'] == 'NERO 5'

    def test_done_phase(self) -> None:
        handler = WebApiHandler()
        handler.handle_sync_settings(True)
        p = handler.sync_progress
        p.phase = 'done'
        p.synced = 2
        p.results = [{'name': 'NERO 3', 'scenes_json': '[]'}]
        result = handler.handle_sync_status()
        assert result.body['phase'] == 'done'
        assert result.body['synced'] == 2
        assert len(result.body['results']) == 1

    def test_error_phase(self) -> None:
        handler = WebApiHandler()
        handler.handle_sync_settings(True)
        handler.sync_progress.phase = 'error'
        handler.sync_progress.error = 'No pumps found'
        result = handler.handle_sync_status()
        assert result.body['phase'] == 'error'
        assert result.body['error'] == 'No pumps found'
