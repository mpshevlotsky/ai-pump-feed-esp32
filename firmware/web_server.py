"""Async HTTP server — transport layer for REST API + static file serving.

Uses MicroPython's asyncio.start_server() for non-blocking HTTP.
Business logic delegated to core.services.web_api.WebApiHandler.
"""

import asyncio
import binascii
import gc
import json
import os
import config

from core.models.api import (
    ApiResponse,
    AutoConnectAction,
    ConnectAction,
    FactoryResetAction,
    MqttAction,
    ScanAction,
    SyncSettingsAction,
    WifiAction,
)
from core.services.web_api import WebApiHandler

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Callable, Optional, Tuple
    from asyncio import StreamReader, StreamWriter
    from wifi_manager import WiFiManager
    from ble_pump import BLEManager
    from mqtt_client import MQTTManager


# ---------------------------------------------------------------------------
# HTTP constants
# ---------------------------------------------------------------------------

_HTTP_STATUS = {
    200: b"HTTP/1.0 200 OK\r\n",
    202: b"HTTP/1.0 202 Accepted\r\n",
    400: b"HTTP/1.0 400 Bad Request\r\n",
    403: b"HTTP/1.0 403 Forbidden\r\n",
    404: b"HTTP/1.0 404 Not Found\r\n",
    500: b"HTTP/1.0 500 Internal Server Error\r\n",
}

_JSON_CT = b"Content-Type: application/json\r\n"
_HTML_CT = b"Content-Type: text/html; charset=utf-8\r\n"
_YAML_CT = b"Content-Type: text/yaml; charset=utf-8\r\n"
_CONN_CLOSE = b"Connection: close\r\n\r\n"

_PORT = 80
_MAX_BODY = 1024
_CHUNK_SIZE = 512
_RECONNECT_DELAY_MS = 500
_FACTORY_RESET_DELAY_MS = 500
_AP_SHUTDOWN_DELAY_MS = 60_000
_AUTO_SYNC_RETRIES = 1
_AUTO_SCAN_IDLE_MS = 5_000


# ---------------------------------------------------------------------------
# WebServer
# ---------------------------------------------------------------------------

class WebServer:
    """Async HTTP transport layer.

    Delegates business logic to WebApiHandler (core/).
    Handles HTTP parsing, response writing, and side effect execution.

    Lifecycle: __init__() -> initialize(wifi, ble) -> start() -> ... -> stop()
    """

    def __init__(self) -> None:
        self._wifi: Optional[WiFiManager] = None
        self._ble: Optional[BLEManager] = None
        self._mqtt: Optional[MQTTManager] = None
        self._on_feed_mode: Optional[Callable] = None
        self._server: object = None
        self._api: WebApiHandler = WebApiHandler()
        self._bg_tasks: list = []
        self._reset_token: str = ''

    def initialize(self, wifi_manager: WiFiManager, ble_manager: BLEManager,
                   mqtt_manager: Optional[MQTTManager] = None,
                   *, on_feed_mode: Callable) -> None:
        """Inject subsystem references and generate CSRF token."""
        self._wifi = wifi_manager
        self._ble = ble_manager
        self._mqtt = mqtt_manager
        self._on_feed_mode = on_feed_mode
        self._reset_token = binascii.hexlify(os.urandom(16)).decode()

    async def start(self) -> None:
        """Start HTTP server on port 80."""
        self._server = await asyncio.start_server(
            self._handle_client, '0.0.0.0', _PORT)
        print("Web: listening on port %d" % _PORT)

    async def stop(self) -> None:
        """Stop HTTP server and cancel background tasks."""
        for task in self._bg_tasks:
            task.cancel()
        self._bg_tasks.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        print("Web: stopped")

    def _spawn_task(self, coro: object) -> None:
        """Create a tracked background task. Prune finished tasks."""
        self._bg_tasks = [t for t in self._bg_tasks if not t.done()]
        self._bg_tasks.append(asyncio.create_task(coro))

    # -- Request handling ---------------------------------------------------

    async def _handle_client(self, reader: StreamReader,
                             writer: StreamWriter) -> None:
        """Handle a single HTTP connection."""
        try:
            method, path, body = await _parse_request(reader)
            if method is None:
                return
            if path == '/' or path == '/index.html':
                await self._serve_static(
                    writer, 'firmware/static/index.html',
                    replace_pair=(b'__RESET_TOKEN__',
                                  self._reset_token.encode()))
            elif path == '/openapi.yaml':
                await self._serve_static(writer,
                                         'firmware/static/openapi.yaml',
                                         _YAML_CT)
            elif path.startswith('/api/'):
                await self._route_api(method, path, body, writer)
            else:
                await _send_response(writer, 404,
                                     b'{"error":"Not found"}')
        except Exception as e:
            print("Web: error: %s" % e)
            try:
                await _send_response(writer, 500,
                                     b'{"error":"Internal error"}')
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _route_api(self, method: str, path: str,
                         body: bytes, writer: StreamWriter) -> None:
        """Route API requests through WebApiHandler."""
        if method == 'GET':
            result = self._handle_get(path)
        elif method == 'POST':
            result = await self._handle_post(path, _parse_json(body))
        else:
            await _send_response(writer, 400,
                                 b'{"error":"Method not allowed"}')
            return

        if result is None:
            await _send_response(writer, 404,
                                 b'{"error":"Not found"}')
            return

        self._execute_action(result.action)
        await _send_json(writer, result.status_code, result.body)

    def _handle_get(self, path: str) -> Optional[ApiResponse]:
        """Route GET requests to WebApiHandler."""
        if path == '/api/status':
            prefix = self._ble.mesh_prefix if self._ble else None
            prefix_hex = prefix.hex() if prefix else None
            feed_dur = (config.get_max_feed_duration(prefix_hex)
                        if prefix_hex else 0)
            return self._api.handle_status(
                self._wifi.get_status() if self._wifi else {},
                self._ble.target_address if self._ble else None,
                self._ble.is_busy if self._ble else False,
                self._mqtt.get_status() if self._mqtt else {},
                gc.mem_free(),
                mesh_prefix=prefix_hex,
                feed_duration=feed_dur,
            )
        if path == '/api/devices':
            return self._api.handle_devices()
        if path == '/api/wifi':
            return self._api.handle_wifi_get(
                self._wifi.get_status() if self._wifi else {})
        if path == '/api/mqtt':
            cfg = config.load()
            return self._api.handle_mqtt_get(
                cfg.get('mqtt', {}),
                self._mqtt.is_connected if self._mqtt else False,
            )
        if path == '/api/auto-connect-status':
            return self._api.handle_auto_connect_status()
        if path == '/api/sync-status':
            return self._api.handle_sync_status()
        return None

    async def _handle_post(self, path: str,
                           body: Optional[dict]) -> Optional[ApiResponse]:
        """Route POST requests to WebApiHandler."""
        if path == '/api/auto-connect':
            return self._api.handle_auto_connect(
                self._ble is not None)
        if path == '/api/sync-settings':
            return self._api.handle_sync_settings(
                self._ble is not None)
        if path == '/api/scan':
            return self._api.handle_scan(
                self._ble is not None,
                self._ble.is_busy if self._ble else False,
            )
        if path == '/api/connect':
            return self._api.handle_connect(body)
        if path == '/api/feed-mode':
            return await self._handle_feed_mode()
        if path == '/api/read-settings':
            return await self._handle_read_settings()
        if path == '/api/read-remote-scenes':
            return await self._handle_read_remote_scenes(body)
        if path == '/api/wifi':
            return self._api.handle_wifi_set(body)
        if path == '/api/mqtt':
            return self._api.handle_mqtt_set(body)
        if path == '/api/factory-reset':
            return self._api.handle_factory_reset(body, self._reset_token)
        return None

    async def _handle_feed_mode(self) -> ApiResponse:
        """Handle feed mode — delegates to Supervisor callback."""
        result = self._api.handle_feed_mode(self._ble is not None)
        if result.action is None:
            return result
        success, message = await self._on_feed_mode()
        return self._api.build_feed_mode_response(success, message)

    async def _handle_read_settings(self) -> ApiResponse:
        """Handle read settings — requires async BLE operation."""
        result = self._api.handle_read_settings(self._ble is not None)
        if result.action is None:
            return result
        success, message = await self._ble.read_scenes()
        return self._api.build_read_settings_response(success, message)

    async def _handle_read_remote_scenes(self,
                                         body: Optional[dict]) -> ApiResponse:
        """Handle remote scenes read — requires async BLE operation."""
        result = self._api.handle_read_remote_scenes(
            body, self._ble is not None)
        if result.action is None:
            return result
        action = result.action
        success, message = await self._ble.read_remote_scenes(
            action.address, action.addr_type)
        if success:
            config.save_feed_mode_duration(
                message,
                self._ble.last_mesh_ipv6 if self._ble else None)
        return self._api.build_read_remote_scenes_response(success, message)

    # -- Side effect execution ----------------------------------------------

    def _execute_action(self, action: object) -> None:
        """Execute side effects based on action type."""
        if action is None:
            return
        if isinstance(action, AutoConnectAction):
            self._spawn_task(_run_auto_connect(self._ble, self._api))
        elif isinstance(action, SyncSettingsAction):
            self._spawn_task(self._run_sync_settings())
        elif isinstance(action, ScanAction):
            self._spawn_task(self._run_scan())
        elif isinstance(action, ConnectAction):
            self._ble.set_target(action.address, action.addr_type)
            _save_pump_config(action.address, action.addr_type)
        elif isinstance(action, WifiAction):
            _save_wifi_config(action.ssid, action.password)
            if self._wifi:
                self._spawn_task(
                    _deferred_wifi_connect(self._wifi, action.ssid,
                                           action.password))
        elif isinstance(action, MqttAction):
            _save_mqtt_config(action)
            if self._mqtt:
                self._spawn_task(
                    _deferred_mqtt_reconnect(self._mqtt, action))
        elif isinstance(action, FactoryResetAction):
            self._spawn_task(_deferred_factory_reset())

    async def _run_scan(self) -> None:
        """Background BLE scan — updates cached device list incrementally."""
        try:
            devices = await self._ble.scan(
                on_found=self._api.update_devices,
            )
            self._api.update_devices(devices)
            print("Web: scan found %d devices" % len(devices))
        except Exception as e:
            print("Web: scan error: %s" % e)

    async def _run_sync_settings(self) -> None:
        """Sync: scan -> read scenes from all discovered pumps."""
        p = self._api.sync_progress
        try:
            devices = await self._ble.scan(
                on_found=lambda devs: setattr(p, 'found', len(devs)),
                idle_timeout_ms=_AUTO_SCAN_IDLE_MS)
            self._api.update_devices(devices)
            if not devices:
                p.phase = 'error'
                p.error = 'No pumps found'
                return
            p.found = len(devices)
            p.phase = 'syncing'
            p.total = len(devices)
            for i, dev in enumerate(devices):
                p.current = i + 1
                p.device_name = dev.get('name', '')
                await self._sync_one_device(dev, p)
            p.phase = 'done'
            print("Web: sync done, %d pump(s)" % p.synced)
        except Exception as e:
            print("Web: sync error: %s" % e)
            p.phase = 'error'
            p.error = str(e)

    async def _sync_one_device(self, dev: dict,
                               progress: object) -> None:
        """Connect to one device, read scenes with retry, save to config."""
        addr = dev['address']
        addr_type = dev.get('addr_type', 1)
        success = False
        message = ''
        for attempt in range(_AUTO_SYNC_RETRIES + 1):
            success, message = await self._ble.read_remote_scenes(
                addr, addr_type)
            if success:
                break
            print("Web: sync %s attempt %d failed: %s"
                  % (addr, attempt + 1, message))
        if not success:
            print("Web: sync %s failed: %s" % (addr, message))
            return
        config.save_feed_mode_duration(message, self._ble.last_mesh_ipv6)
        progress.synced += 1
        progress.results.append({
            'name': dev.get('name', addr),
            'scenes_json': message,
        })

    # -- Static file serving ------------------------------------------------

    async def _serve_static(self, writer: StreamWriter, path: str,
                            content_type: bytes = _HTML_CT,
                            replace_pair: Optional[tuple] = None) -> None:
        """Serve a static file.

        If replace_pair is (old, new), reads entire file and performs
        byte replacement (used for CSRF token injection).
        Otherwise reads in chunks to save memory.
        """
        try:
            f = open(path, 'rb')
        except OSError:
            await _send_response(writer, 404,
                                 b'{"error":"File not found"}')
            return
        try:
            writer.write(_HTTP_STATUS[200])
            writer.write(content_type)
            writer.write(_CONN_CLOSE)
            if replace_pair:
                data = f.read()
                writer.write(
                    data.replace(replace_pair[0], replace_pair[1]))
            else:
                while True:
                    chunk = f.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    writer.write(chunk)
            await writer.drain()
        finally:
            f.close()


# ---------------------------------------------------------------------------
# Auto-connect helpers (stateless)
# ---------------------------------------------------------------------------

async def _run_auto_connect(ble: object, api: object) -> None:
    """Auto-connect: scan -> sync all -> check mesh -> select target."""
    p = api.auto_connect_progress
    try:
        # Phase 1: Scan for devices
        devices = await ble.scan(
            on_found=lambda devs: _update_auto_progress(p, len(devs)),
            idle_timeout_ms=_AUTO_SCAN_IDLE_MS)
        api.update_devices(devices)
        if not devices:
            p.phase = 'error'
            p.error = 'No pumps found'
            return
        p.found = len(devices)
        # Phase 2: Sync all devices (read scenes + learn mesh)
        mesh_map, _ = await _auto_sync_and_discover(ble, devices, p)
        # Phase 3: Check mesh consensus (only if all devices have mesh info)
        if any(d['address'] not in mesh_map for d in devices):
            p.phase = 'choose'
            p.devices = _build_choose_list(devices, mesh_map)
            print("Web: auto-connect — incomplete mesh data")
            return
        same, prefix_hex = _check_mesh_consensus(mesh_map)
        if not same:
            p.phase = 'choose'
            p.devices = _build_choose_list(devices, mesh_map)
            print("Web: auto-connect — different meshes")
            return
        # Same mesh — auto-select first pump
        first = devices[0]
        mesh_bytes = bytes.fromhex(prefix_hex) if prefix_hex else None
        ble.set_target(
            first['address'], first.get('addr_type', 1), mesh_bytes)
        _save_pump_config(
            first['address'], first.get('addr_type', 1), mesh_bytes)
        p.phase = 'done'
        p.target_address = first['address']
        print("Web: auto-connect done, target=%s" % first['address'])
    except Exception as e:
        print("Web: auto-connect error: %s" % e)
        p.phase = 'error'
        p.error = str(e)


def _update_auto_progress(progress: object, found: int) -> None:
    """Callback for incremental scan progress."""
    progress.found = found


async def _auto_sync_and_discover(ble: object, devices: list,
                                  progress: object) -> tuple:
    """Read scenes from each device, learning mesh prefix along the way.

    Returns (mesh_map: dict[addr, prefix_hex], any_success: bool).
    Each read_remote_scenes call performs handshake (learns mesh) + reads
    scenes in a single BLE connection, avoiding double-connect overhead.
    """
    progress.phase = 'syncing'
    progress.total = len(devices)
    mesh_map: dict = {}
    any_success = False
    for i, dev in enumerate(devices):
        progress.current = i + 1
        progress.device_name = dev.get('name', '')
        addr = dev['address']
        addr_type = dev.get('addr_type', 1)
        ok = False
        for attempt in range(_AUTO_SYNC_RETRIES + 1):
            success, message = await ble.read_remote_scenes(addr, addr_type)
            if success:
                ipv6 = ble.last_mesh_ipv6
                if ipv6:
                    mesh_map[addr] = bytes(ipv6[:8]).hex()
                config.save_feed_mode_duration(message, ipv6)
                any_success = True
                ok = True
                break
            print("Web: sync %s attempt %d failed: %s"
                  % (addr, attempt + 1, message))
        if not ok:
            print("Web: skipping %s — sync failed" % addr)
    return (mesh_map, any_success)


def _check_mesh_consensus(mesh_map: dict) -> tuple:
    """Check if all devices share the same mesh prefix.

    Returns (same: bool, prefix_hex: Optional[str]).
    """
    prefixes = set(mesh_map.values())
    if len(prefixes) <= 1:
        return (True, prefixes.pop() if prefixes else None)
    return (False, None)


def _build_choose_list(devices: list, mesh_map: dict) -> list:
    """Build device list with mesh prefix info for user selection."""
    return [
        {'name': d['name'], 'address': d['address'],
         'addr_type': d.get('addr_type', 1),
         'rssi': d.get('rssi', 0),
         'mesh_prefix': mesh_map.get(d['address'])}
        for d in devices
    ]


# ---------------------------------------------------------------------------
# Deferred operations (async, called via _spawn_task)
# ---------------------------------------------------------------------------

async def _deferred_factory_reset() -> None:
    """Delete config.json and reboot after HTTP response transmits."""
    await asyncio.sleep_ms(_FACTORY_RESET_DELAY_MS)
    try:
        os.remove('config.json')
        print('Web: FACTORY RESET — config deleted')
    except OSError:
        print('Web: FACTORY RESET — no config to delete')
    import machine
    machine.reset()


async def _deferred_wifi_connect(wifi: WiFiManager, ssid: str,
                                 password: str) -> None:
    """Delayed WiFi reconnect — gives HTTP response time to transmit.

    When transitioning from AP to STA, keeps AP alive so the user's
    browser can learn the new IP, then shuts AP down after a delay.
    """
    await asyncio.sleep_ms(_RECONNECT_DELAY_MS)
    ap_was_active = wifi.state == 'ap_active'
    try:
        success = await wifi.connect_client(ssid, password,
                                            keep_ap=ap_was_active)
        if success and ap_was_active:
            print("Web: AP kept alive for redirect (%ds)"
                  % (_AP_SHUTDOWN_DELAY_MS // 1000))
            await asyncio.sleep_ms(_AP_SHUTDOWN_DELAY_MS)
            wifi.stop_ap()
    except Exception as e:
        print("Web: WiFi reconnect error: %s" % e)


async def _deferred_mqtt_reconnect(mqtt: MQTTManager,
                                   action: MqttAction) -> None:
    """Stop, reconfigure, and reconnect MQTT after settings change."""
    await asyncio.sleep_ms(_RECONNECT_DELAY_MS)
    try:
        await mqtt.stop()
        mqtt.initialize(
            broker=action.broker,
            port=action.port,
            user=action.user,
            password=action.password,
            device_id=mqtt.device_id,
        )
        await mqtt.start()
    except Exception as e:
        print("Web: MQTT reconnect error: %s" % e)


# ---------------------------------------------------------------------------
# Config persistence helpers (stateless)
# ---------------------------------------------------------------------------

def _save_pump_config(address: str, addr_type: int,
                      mesh_prefix: Optional[bytes] = None) -> None:
    """Persist pump target and mesh prefix to config.json."""
    cfg = config.load()
    pump = cfg.setdefault('pump', {})
    pump['address'] = address
    pump['addr_type'] = addr_type
    pump['mesh_prefix'] = (mesh_prefix.hex() if mesh_prefix
                            else None)
    config.save(cfg)


def _save_wifi_config(ssid: str, password: str) -> None:
    """Persist WiFi credentials to config.json."""
    cfg = config.load()
    wifi = cfg.setdefault('wifi', {})
    wifi['ssid'] = ssid
    wifi['password'] = password
    config.save(cfg)


def _save_mqtt_config(action: MqttAction) -> None:
    """Persist MQTT settings to config.json."""
    cfg = config.load()
    mqtt = cfg.setdefault('mqtt', {})
    mqtt['broker'] = action.broker
    mqtt['port'] = action.port
    mqtt['user'] = action.user
    mqtt['password'] = action.password
    config.save(cfg)


# ---------------------------------------------------------------------------
# Pure helpers (stateless)
# ---------------------------------------------------------------------------

async def _parse_request(reader: StreamReader) -> Tuple[Optional[str],
                                                   Optional[str],
                                                   Optional[bytes]]:
    """Parse HTTP request: method, path, body.

    Returns (method, path, body) or (None, None, None) on error.
    """
    try:
        line = await reader.readline()
        if not line:
            return None, None, None
        parts = line.decode().strip().split(' ', 2)
        if len(parts) < 2:
            return None, None, None
        method = parts[0]
        path = parts[1]

        content_length = 0
        while True:
            header = await reader.readline()
            if not header or header == b'\r\n':
                break
            h = header.decode().lower()
            if h.startswith('content-length:'):
                content_length = int(h.split(':', 1)[1].strip())

        body = b''
        if content_length > 0:
            body = await reader.read(min(content_length, _MAX_BODY))

        return method, path, body
    except Exception:
        return None, None, None


def _parse_json(body: bytes) -> Optional[dict]:
    """Parse JSON body. Returns dict or None on failure."""
    if not body:
        return None
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None


async def _send_json(writer: StreamWriter, status_code: int,
                     data: dict) -> None:
    """Send JSON HTTP response."""
    body = json.dumps(data).encode()
    status_line = _HTTP_STATUS.get(status_code, _HTTP_STATUS[500])
    writer.write(status_line)
    writer.write(_JSON_CT)
    writer.write(_CONN_CLOSE)
    writer.write(body)
    await writer.drain()


async def _send_response(writer: StreamWriter, status_code: int,
                         body: bytes = b'',
                         content_type: bytes = _JSON_CT) -> None:
    """Send raw HTTP response."""
    status_line = _HTTP_STATUS.get(status_code, _HTTP_STATUS[500])
    writer.write(status_line)
    writer.write(content_type)
    writer.write(_CONN_CLOSE)
    if body:
        writer.write(body)
    await writer.drain()
