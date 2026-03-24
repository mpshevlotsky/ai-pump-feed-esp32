"""
BLE Central for Aqua Illumination pump control.

Connect-on-demand pattern: connects only during command execution (~5-10 sec),
then disconnects to free the pump for the official myAI/Mobius app.

Requires: aioble (install via: import mip; mip.install('aioble'))
"""

import bluetooth
import aioble
import asyncio
import gc
import time

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Callable, List, Optional, Tuple

from core.protocol import (
    FsciCodec, SERVICE_GENERAL, SERVICE_OTAP,
    CHAR_RX_DATA, CHAR_RX_FINAL, CHAR_TX_DATA, CHAR_TX_FINAL,
    CHAR_OTAP_COMMAND, STATUS_SUCCESS, status_name,
    parse_response_status, parse_scenes_response, parse_mesh_addresses,
    to_hex,
)


# ---------------------------------------------------------------------------
# Constants (all timeouts in milliseconds)
# ---------------------------------------------------------------------------

# Scanning
_SCAN_DURATION_MS = 20_000         # full BLE scan duration
_QUICK_SCAN_MS = 5_000             # short scan for feed mode fallback

# Connection
_CONNECT_TIMEOUT_MS = 10_000       # BLE connection attempt timeout
_FALLBACK_RETRIES: int = 2         # retry saved addr after fallback scan

# MTU negotiation
_MTU_SIZE = 512                    # requested MTU
_MTU_OVERHEAD = 3                  # ATT header: opcode(1) + handle(2)
_MTU_DEFAULT = 23                  # BLE 4.0 minimum
_MTU_EFF_MIN = 20                  # minimum effective payload

# Protocol timeouts
_CCCD_PAUSE_MS = 100               # pause between sequential CCCD writes
_OTAP_TIMEOUT_MS = 2_000           # OTAP indication after subscribe
_HANDSHAKE_TIMEOUT_MS = 3_000      # GET MeshLocalAddresses response
_COMMAND_TIMEOUT_MS = 5_000        # SET Feed Mode response


class _MeshMismatch(str):
    """Sentinel for mesh mismatch — compare with ``is``, not ``==``."""
    __slots__ = ()


_MESH_MISMATCH = _MeshMismatch("Mesh mismatch")


# ---------------------------------------------------------------------------
# BLEManager — connect-on-demand BLE Central
# ---------------------------------------------------------------------------

class BLEManager:
    """Connect-on-demand BLE Central for AI pump control.

    Lifecycle: __init__() -> initialize(addr) -> start() -> ... -> stop()

    Not a persistent connection — connects only for ~5-10 seconds per command,
    then disconnects to free the pump for the official myAI/Mobius app.
    """

    def __init__(self) -> None:
        self._codec: FsciCodec = FsciCodec()
        self._target_addr: Optional[str] = None
        self._target_addr_type: int = 1  # default random (most BLE peripherals)
        self._mesh_prefix: Optional[bytes] = None  # first 8 bytes of mesh IPv6
        self._last_mesh_ipv6: Optional[bytes] = None
        self._busy: bool = False

    def initialize(self, target_address: Optional[str] = None,
                   addr_type: int = 1,
                   mesh_prefix: Optional[bytes] = None) -> None:
        """Set target pump address and type. No I/O.

        addr_type: 0=public, 1=random. AI pumps use random addresses.
        mesh_prefix: 8-byte Thread mesh prefix (learned from prior connection).
        """
        self._target_addr = target_address
        self._target_addr_type = addr_type
        self._mesh_prefix = mesh_prefix

    async def start(self) -> None:
        """BLE subsystem ready. Radio activated on demand to avoid
        conflicts with WiFi/asyncio on ESP32 shared radio."""
        print("BLE: ready (on-demand activation)")

    async def stop(self) -> None:
        """Ensure BLE radio is off."""
        try:
            bluetooth.BLE().active(False)
        except Exception as e:
            print("BLE: stop error: %s" % e)

    @property
    def is_busy(self) -> bool:
        """True while a BLE operation is in progress."""
        return self._busy

    @property
    def target_address(self) -> Optional[str]:
        return self._target_addr

    @target_address.setter
    def target_address(self, addr: Optional[str]) -> None:
        self._target_addr = addr

    def set_target(self, addr: str, addr_type: int = 1,
                   mesh_prefix: Optional[bytes] = None) -> None:
        """Set target address and type. Resets mesh prefix unless provided."""
        self._target_addr = addr
        self._target_addr_type = addr_type
        self._mesh_prefix = mesh_prefix

    @property
    def target_addr_type(self) -> int:
        return self._target_addr_type

    @property
    def mesh_prefix(self) -> Optional[bytes]:
        return self._mesh_prefix

    @property
    def last_mesh_ipv6(self) -> Optional[bytes]:
        """Full 16-byte mesh IPv6 from last handshake (unique per pump)."""
        return self._last_mesh_ipv6

    async def scan(self, duration_ms: int = _SCAN_DURATION_MS,
                   first_only: bool = False,
                   on_found: Optional[Callable] = None,
                   idle_timeout_ms: int = 0) -> List[dict]:
        """Scan for AI pumps filtered by SERVICE_GENERAL UUID.

        Returns list of dicts: [{"name": str, "address": str, "rssi": int}]
        If first_only=True, stops scanning after the first pump is found.
        If on_found is provided, called with current device list on each
        new discovery so callers can update results incrementally.
        If idle_timeout_ms > 0, stops early when no new device is found
        for that many ms after at least one device was discovered.
        """
        if self._busy:
            return []
        self._busy = True
        devices: List[dict] = []
        svc_uuid = bluetooth.UUID(SERVICE_GENERAL)
        bluetooth.BLE().active(True)
        last_found_ms: int = 0
        try:
            async with aioble.scan(
                duration_ms, interval_us=30_000,
                window_us=30_000, active=True,
            ) as scanner:
                async for result in scanner:
                    now_ms = time.ticks_ms()
                    if idle_timeout_ms and devices and last_found_ms:
                        if time.ticks_diff(now_ms, last_found_ms) > idle_timeout_ms:
                            break
                    if not _has_service(result, svc_uuid):
                        continue
                    addr = _format_addr(result.device.addr)
                    if not any(d["address"] == addr for d in devices):
                        devices.append({
                            "name": result.name() or "AI Pump",
                            "address": addr,
                            "addr_type": result.device.addr_type,
                            "rssi": result.rssi,
                        })
                        last_found_ms = now_ms
                        if on_found:
                            on_found(devices)
                        if first_only:
                            return devices
        finally:
            bluetooth.BLE().active(False)
            gc.collect()
            self._busy = False
        return devices

    async def activate_feed_mode(self) -> Tuple[bool, str]:
        """Feed Mode with mesh-aware fallback and retry.

        1. Try saved address (mesh-validated via handshake).
        2. On fail, scan and try other pumps in correct mesh.
        3. If saved addr failure was transient, retry up to 2 times.
        """
        saved_fail = None
        if self._target_addr is not None:
            success, msg = await self._ble_connect_and_run(
                self._target_addr, self._target_addr_type,
                self._feed_mode_action)
            if success:
                self._learn_mesh_prefix()
                return (success, msg)
            saved_fail = msg
            print("BLE: saved addr failed (%s), scanning..." % msg)
        devices = await self.scan(duration_ms=_QUICK_SCAN_MS)
        for dev in devices:
            if dev['address'] == self._target_addr:
                continue
            success, msg = await self._ble_connect_and_run(
                dev['address'], dev['addr_type'], self._feed_mode_action)
            if msg is _MESH_MISMATCH:
                print("BLE: %s wrong mesh, skip" % dev['address'])
                continue
            if success:
                self._target_addr = dev['address']
                self._target_addr_type = dev['addr_type']
                self._learn_mesh_prefix()
                print("BLE: target updated to %s" % dev['address'])
                return (success, msg)
        if self._target_addr and saved_fail is not _MESH_MISMATCH:
            result = await self._retry_saved_addr()
            if result is not None:
                return result
        return (False, "No pump found" if not devices
                else "No matching pump in mesh")

    async def read_scenes(self) -> Tuple[bool, str]:
        """Connect-on-demand: read scene settings from target pump."""
        if self._target_addr is None:
            return (False, "No target address configured")
        return await self.read_remote_scenes(
            self._target_addr, self._target_addr_type)

    async def read_remote_scenes(self, addr: str,
                                 addr_type: int = 1) -> Tuple[bool, str]:
        """Connect to a pump, read its ConfiguredScenes (Feed Mode)."""
        return await self._ble_connect_and_run(
            addr, addr_type, self._remote_scenes_action)

    # -- Private methods -------------------------------------------------------

    async def _retry_saved_addr(self) -> Optional[Tuple[bool, str]]:
        """Retry saved address up to _FALLBACK_RETRIES times."""
        for i in range(_FALLBACK_RETRIES):
            print("BLE: retry saved %d/%d" % (i + 1, _FALLBACK_RETRIES))
            success, msg = await self._ble_connect_and_run(
                self._target_addr, self._target_addr_type,
                self._feed_mode_action)
            if success:
                self._learn_mesh_prefix()
                return (success, msg)
            if msg is _MESH_MISMATCH:
                break
        return None

    async def _ble_connect_and_run(self, addr: str, addr_type: int,
                                   action: Callable) -> Tuple[bool, str]:
        """BLE lifecycle: busy -> connect -> setup -> action -> disconnect."""
        if self._busy:
            return (False, "Busy")
        self._busy = True
        self._last_mesh_ipv6 = None
        bluetooth.BLE().active(True)
        try:
            print("BLE: connecting to %s (type=%d)" % (addr, addr_type))
            device = aioble.Device(addr_type, addr)
            connection = await device.connect(timeout_ms=_CONNECT_TIMEOUT_MS)
            try:
                chars, eff_mtu = await self._setup_connection(connection)
                return await action(chars, eff_mtu)
            finally:
                try:
                    await connection.disconnect()
                except Exception as e:
                    print("BLE: disconnect error: %s" % e)
                print("BLE: disconnected")
        except asyncio.TimeoutError:
            return (False, "Connection timeout")
        except aioble.DeviceDisconnectedError:
            return (False, "Device disconnected unexpectedly")
        except Exception as e:
            return (False, str(e))
        finally:
            bluetooth.BLE().active(False)
            gc.collect()
            self._busy = False

    def _learn_mesh_prefix(self) -> None:
        """Save mesh prefix from last handshake if not yet known."""
        if self._mesh_prefix is None and self._last_mesh_ipv6 is not None:
            self._mesh_prefix = bytes(self._last_mesh_ipv6[:8])
            print("BLE: learned mesh prefix %s" % to_hex(self._mesh_prefix))

    def _is_mesh_match(self) -> bool:
        """True if mesh matches target or no validation data available."""
        if self._mesh_prefix and self._last_mesh_ipv6:
            return bytes(self._last_mesh_ipv6[:8]) == self._mesh_prefix
        return True

    async def _feed_mode_action(self, chars: Tuple,
                                eff_mtu: int) -> Tuple[bool, str]:
        """Check mesh, then send Feed Mode command."""
        if not self._is_mesh_match():
            return (False, _MESH_MISMATCH)
        return await _execute_feed_mode(self._codec, chars, eff_mtu)

    async def _remote_scenes_action(self, chars: Tuple,
                                    eff_mtu: int) -> Tuple[bool, str]:
        """Read scenes and return as JSON string."""
        return await _read_scenes_as_json(self._codec, chars, eff_mtu)

    async def _setup_connection(self, connection: object) -> Tuple[Tuple, int]:
        """MTU -> discover -> CCCDs -> OTAP indication -> handshake.

        Returns (chars_tuple, effective_mtu).
        chars_tuple: (rx_data, rx_final, tx_data, tx_final, otap_cmd)
        """
        # MTU negotiation
        try:
            mtu = await connection.exchange_mtu(mtu=_MTU_SIZE)
        except Exception:
            mtu = _MTU_DEFAULT
        eff_mtu = max(mtu - _MTU_OVERHEAD, _MTU_EFF_MIN)
        print("BLE: MTU=%d effective=%d" % (mtu, eff_mtu))

        # Discover services and characteristics
        chars = await _discover_chars(connection)
        rx_data, rx_final, tx_data, tx_final, otap_cmd = chars

        # Subscribe CCCDs and wait for OTAP indication
        await _subscribe_cccds(rx_data, rx_final, otap_cmd)

        # Handshake: GET MeshLocalAddresses
        hs_pkt = self._codec.build_handshake_packet()
        print("BLE: handshake TX %s" % to_hex(hs_pkt))
        await _send_packet(hs_pkt, tx_data, tx_final, eff_mtu)
        hs_resp = await _wait_response(rx_final, _HANDSHAKE_TIMEOUT_MS)
        if hs_resp is None:
            raise Exception("Handshake timeout")
        print("BLE: handshake RX (%d bytes) %s" % (len(hs_resp), to_hex(hs_resp)))
        addrs = parse_mesh_addresses(hs_resp)
        self._last_mesh_ipv6 = addrs[0] if addrs else None

        return chars, eff_mtu

# ---------------------------------------------------------------------------
# Pure helpers (stateless, hardware-independent)
# ---------------------------------------------------------------------------

async def _discover_chars(connection: object) -> Tuple:
    """Discover GATT services and resolve 5 required characteristics.

    Returns tuple: (rx_data, rx_final, tx_data, tx_final, otap_cmd)
    """
    general = await connection.service(bluetooth.UUID(SERVICE_GENERAL))
    if general is None:
        raise Exception("General service not found")
    otap = await connection.service(bluetooth.UUID(SERVICE_OTAP))
    if otap is None:
        raise Exception("OTAP service not found")
    rx_data = await general.characteristic(bluetooth.UUID(CHAR_RX_DATA))
    rx_final = await general.characteristic(bluetooth.UUID(CHAR_RX_FINAL))
    tx_data = await general.characteristic(bluetooth.UUID(CHAR_TX_DATA))
    tx_final = await general.characteristic(bluetooth.UUID(CHAR_TX_FINAL))
    otap_cmd = await otap.characteristic(bluetooth.UUID(CHAR_OTAP_COMMAND))
    for label, ch in [("RX_DATA", rx_data), ("RX_FINAL", rx_final),
                      ("TX_DATA", tx_data), ("TX_FINAL", tx_final),
                      ("OTAP_CMD", otap_cmd)]:
        if ch is None:
            raise Exception("Characteristic %s not found" % label)
    print("BLE: discovered 5 characteristics")
    return (rx_data, rx_final, tx_data, tx_final, otap_cmd)


async def _subscribe_cccds(rx_data: object, rx_final: object,
                           otap_cmd: object) -> None:
    """Subscribe CCCDs sequentially and wait for OTAP indication.

    Order is critical: RX_DATA -> RX_FINAL -> OTAP_COMMAND.
    OTAP CCCD is mandatory — without it the pump ignores SET commands.
    """
    await rx_data.subscribe(notify=True)
    await asyncio.sleep_ms(_CCCD_PAUSE_MS)
    await rx_final.subscribe(notify=True)
    await asyncio.sleep_ms(_CCCD_PAUSE_MS)
    await otap_cmd.subscribe(indicate=True, notify=False)

    # Pump sends OTAP indication after CCCD write
    try:
        await otap_cmd.indicated(timeout_ms=_OTAP_TIMEOUT_MS)
        print("BLE: OTAP indication received")
    except asyncio.TimeoutError:
        print("BLE: OTAP indication timeout (continuing)")


async def _execute_feed_mode(codec: FsciCodec, chars: Tuple,
                             eff_mtu: int) -> Tuple[bool, str]:
    """Read settings, send Feed Mode SET, return (success, status_name)."""
    rx_data, rx_final, tx_data, tx_final, _ = chars
    await _read_feed_mode_settings(
        codec, rx_data, rx_final, tx_data, tx_final, eff_mtu)
    print("BLE: sending Feed Mode")
    pkt = codec.build_feed_mode_packet()
    print("BLE: TX %s" % to_hex(pkt))
    await _send_packet(pkt, tx_data, tx_final, eff_mtu)
    resp = await _wait_response(rx_final, _COMMAND_TIMEOUT_MS)
    if resp is None:
        return (False, "Command timeout")
    print("BLE: RX %s" % to_hex(resp))
    status = parse_response_status(resp)
    name = status_name(status)
    print("BLE: status=%s (0x%02X)" % (name, status))
    return (status == STATUS_SUCCESS, name)


async def _read_scenes_as_json(codec: FsciCodec, chars: Tuple,
                                eff_mtu: int) -> Tuple[bool, str]:
    """Read ConfiguredScenes and return as JSON string."""
    rx_data, rx_final, tx_data, tx_final, _ = chars
    scenes = await _read_feed_mode_settings(
        codec, rx_data, rx_final, tx_data, tx_final, eff_mtu)
    if not scenes:
        return (False, "No scenes found")
    import json
    result = []
    for scene_id, timeout_sec, pump_mode, speed in scenes:
        result.append({
            'id': scene_id, 'timeout': timeout_sec,
            'mode': pump_mode, 'speed': speed,
        })
    return (True, json.dumps(result))


async def _read_feed_mode_settings(codec: FsciCodec, rx_data: object,
                                   rx_final: object, tx_data: object,
                                   tx_final: object,
                                   eff_mtu: int) -> List[Tuple[int, int, int, int]]:
    """Read Feed Mode settings from ConfiguredScenes.

    Sends GET ConfiguredScenes(400), parses response, logs and returns
    scene data. Non-fatal — errors are logged, returns empty list.

    Returns list of tuples: (scene_id, timeout_sec, pump_mode, speed_raw).
    """
    try:
        pkt = codec.build_get_scenes_packet()
        print("BLE: GET scenes TX %s" % to_hex(pkt))
        await _send_packet(pkt, tx_data, tx_final, eff_mtu)
        resp = await _wait_multi_response(
            rx_data, rx_final, _COMMAND_TIMEOUT_MS)
        if resp is None:
            print("BLE: GET scenes timeout")
            return []
        print("BLE: GET scenes RX (%d bytes) %s" % (len(resp), to_hex(resp)))
        scenes = parse_scenes_response(resp)
        if not scenes:
            print("BLE: no configured scenes found")
            return []
        for scene_id, timeout_sec, pump_mode, speed in scenes:
            label = "FeedMode" if scene_id == 1 else "Scene(%d)" % scene_id
            print("BLE: %s: speed=%d%%, timeout=%ds (%dm%ds), mode=%d"
                  % (label, speed // 10, timeout_sec,
                     timeout_sec // 60, timeout_sec % 60, pump_mode))
        return scenes
    except Exception as e:
        print("BLE: read settings error: %s" % e)
        return []


async def _send_packet(packet: bytes, tx_data_ch: object,
                       tx_final_ch: object, mtu: int) -> None:
    """Send FSCI packet with MTU-based chunking.

    Intermediate chunks -> TX_DATA (write no response).
    Last/only chunk -> TX_FINAL (write no response).
    """
    offset = 0
    total = len(packet)
    while offset < total:
        end = min(offset + mtu, total)
        chunk = packet[offset:end]
        if end >= total:
            await tx_final_ch.write(chunk, response=False)
        else:
            await tx_data_ch.write(chunk, response=False)
        offset = end


async def _wait_response(rx_final_ch: object,
                         timeout_ms: int) -> Optional[bytes]:
    """Wait for response frame on RX_FINAL notification.

    Handshake and Feed Mode responses fit in a single BLE packet
    (max 33 bytes), so only RX_FINAL fires — no RX_DATA chunking.
    """
    try:
        data = await rx_final_ch.notified(timeout_ms=timeout_ms)
        return bytes(data)
    except asyncio.TimeoutError:
        return None


async def _wait_multi_response(rx_data_ch: object, rx_final_ch: object,
                               timeout_ms: int) -> Optional[bytes]:
    """Wait for a multi-packet FSCI response (RX_DATA chunks + RX_FINAL).

    Pump splits large responses: intermediate chunks on RX_DATA,
    final chunk on RX_FINAL. Alternates checking both characteristics
    with short timeouts to accumulate all parts.
    """
    import time
    parts = []
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while True:
        remaining = time.ticks_diff(deadline, time.ticks_ms())
        if remaining <= 0:
            return b''.join(parts) if parts else None
        try:
            data = await rx_data_ch.notified(
                timeout_ms=min(remaining, 50))
            chunk = bytes(data)
            parts.append(chunk)
            print("BLE: RX_DATA chunk %d bytes, head: %s"
                  % (len(chunk), to_hex(chunk[:16])))
            continue
        except asyncio.TimeoutError:
            pass
        remaining = time.ticks_diff(deadline, time.ticks_ms())
        if remaining <= 0:
            return b''.join(parts) if parts else None
        try:
            data = await rx_final_ch.notified(
                timeout_ms=min(remaining, 200))
            final_chunk = bytes(data)
            # Drain any rx_data notifications buffered during rx_final wait
            try:
                while True:
                    extra = await rx_data_ch.notified(timeout_ms=10)
                    late = bytes(extra)
                    parts.append(late)
                    print("BLE: RX_DATA late chunk %d bytes, head: %s"
                          % (len(late), to_hex(late[:16])))
            except asyncio.TimeoutError:
                pass
            parts.append(final_chunk)
            print("BLE: RX_FINAL chunk %d bytes (parts=%d), head: %s"
                  % (len(final_chunk), len(parts),
                     to_hex(final_chunk[:16])))
            return b''.join(parts)
        except asyncio.TimeoutError:
            pass


def _has_service(scan_result: object, svc_uuid: object) -> bool:
    """Check if scan result advertises the given service UUID."""
    for s in scan_result.services():
        if s == svc_uuid:
            return True
    return False


def _format_addr(addr_bytes: bytes) -> str:
    """Format BLE address bytes as 'AA:BB:CC:DD:EE:FF'."""
    return ":".join("%02X" % b for b in addr_bytes)
