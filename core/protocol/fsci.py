"""
FSCI protocol implementation for Aqua Illumination pumps.

This module contains:
- BLE GATT UUIDs (as strings; converted to bluetooth.UUID in ble_pump.py)
- FSCI frame builder with CRC16-CCITT
- Feed Mode and handshake packet constructors
- Response parser with 21 FSCI status codes
"""

import struct

try:
    from typing import List, Optional, Tuple
except ImportError:
    pass


# ---------------------------------------------------------------------------
# BLE GATT UUIDs (string form — converted to bluetooth.UUID in ble_pump.py)
# ---------------------------------------------------------------------------

SERVICE_GENERAL = "01ff0100-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
SERVICE_OTAP = "01ff5550-ba5e-f4ee-5ca1-eb1e5e4b1ce0"

CHAR_RX_DATA = "01ff0101-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
CHAR_RX_FINAL = "01ff0102-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
CHAR_TX_DATA = "01ff0103-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
CHAR_TX_FINAL = "01ff0104-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
CHAR_OTAP_COMMAND = "01ff5551-ba5e-f4ee-5ca1-eb1e5e4b1ce0"


# ---------------------------------------------------------------------------
# FSCI frame constants
# ---------------------------------------------------------------------------

_STX = 0x02
_OP_GROUP_REQUEST = 0xDE
_OP_GROUP_CONFIRM = 0xDF
_OP_CODE_GET = 0x17
_OP_CODE_SET = 0x18

_RESERVED_GET = b'\x00\x00'
_RESERVED_SET = b'\x04\x00'

_MSG_ID_WRAP = 20000


# ---------------------------------------------------------------------------
# C2Attribute IDs
# ---------------------------------------------------------------------------

_ATTR_CONFIGURED_SCENES = 400      # 0x0190
_ATTR_CURRENT_SCENE = 401          # 0x0191
_ATTR_PUMP_OVERRIDE_MODE = 705     # 0x02C1
_ATTR_MESH_LOCAL_ADDRESSES = 1005  # 0x03ED

_SCENE_ID_FEED_MODE = 1


# ---------------------------------------------------------------------------
# FSCI status codes
# ---------------------------------------------------------------------------

STATUS_SUCCESS = 0x00

_STATUS_NAMES = {
    0x00: "Success",
    0x01: "Failed",
    0x02: "InvalidInstance",
    0x03: "InvalidElement",
    0x04: "NotPermitted",
    0x05: "InvalidMode",
    0x06: "NoMem",
    0x07: "UnsupportedAttribute",
    0x08: "EmptyEntry",
    0x09: "InvalidValue",
    0x0A: "AlreadyConnected",
    0x0B: "AlreadyCreated",
    0x0C: "NoTimers",
    0x0D: "InvalidRequest",
    0x0E: "InvalidDeviceType",
    0x0F: "InvalidPrimitiveType",
    0x10: "Timeout",
    0x11: "Busy",
    0x14: "InvalidRange",
    0x15: "InvalidSize",
    0xFF: "EntryNotFound",
}


# ---------------------------------------------------------------------------
# Prebuilt payloads (immutable, allocated once at import)
# ---------------------------------------------------------------------------

# GET MeshLocalAddresses: [attrId:2 LE][instance:1][count:1]
# count=0xFF requests all mesh-local IPv6 addresses.
_HANDSHAKE_PAYLOAD = struct.pack('<HBB',
                                _ATTR_MESH_LOCAL_ADDRESSES, 0, 0xFF)

# SET PumpOverrideMode=0, CurrentScene=1:
#   [attrId:2 LE][instance:1][count:1][itemLen:1][value...]
_FEED_MODE_PAYLOAD = (
    struct.pack('<HBBBB', _ATTR_PUMP_OVERRIDE_MODE, 0, 1, 1, 0)
    + struct.pack('<HBBB', _ATTR_CURRENT_SCENE, 0, 1, 4)
    + struct.pack('<I', _SCENE_ID_FEED_MODE)
)

# GET ConfiguredScenes: instance=0, count=6
# count=6 keeps response under MTU (215 bytes) → single BLE packet,
# avoiding aioble multi-notification data loss.
_GET_SCENES_PAYLOAD = struct.pack('<HBB',
                                  _ATTR_CONFIGURED_SCENES, 0, 6)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def crc16(data: bytes) -> int:
    """CRC16-CCITT: polynomial 0x1021, initial value 0xFFFF.

    Source: Crc.java — crc16(bArr, (short) -1) where (short)-1 = 0xFFFF.
    """
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def status_name(code: int) -> str:
    """Human-readable name for a FSCI status byte."""
    name = _STATUS_NAMES.get(code)
    if name is not None:
        return name
    return "Unknown(0x%02X)" % code


def parse_response_status(frame: Optional[bytes]) -> int:
    """Extract FSCI status code from a SET confirm response frame.

    Returns status byte (0x00 = success), or -1 if frame is invalid.
    SET confirm: opGroup=0xDF, payload=[statusByte][FF FF].
    """
    if frame is None or len(frame) < 12:
        return -1
    if frame[1] != _OP_GROUP_CONFIRM:
        return -1
    payload_len = frame[7] | (frame[8] << 8)
    if payload_len < 1:
        return -1
    return frame[9]


def _parse_scene_entry(frame: bytes, pos: int,
                       item_len: int) -> Optional[Tuple[int, int, int, int]]:
    """Parse a single scene entry at given position.

    Returns (scene_id, timeout_sec, pump_mode, speed) or None if invalid/empty.
    """
    if item_len < 23:
        return None
    scene_id = frame[pos] | (frame[pos + 1] << 8)
    if scene_id == 0:
        return None
    timeout_sec = frame[pos + 2] | (frame[pos + 3] << 8)
    pump_mode = frame[pos + 20]
    speed = frame[pos + 21] | (frame[pos + 22] << 8)
    return (scene_id, timeout_sec, pump_mode, speed)


def parse_scenes_response(frame: Optional[bytes]) -> List[Tuple[int, int, int, int]]:
    """Parse GET ConfiguredScenes confirm into list of scene tuples.

    Returns [(scene_id, timeout_sec, pump_mode, speed_raw), ...].
    speed_raw is 0-1000 (divide by 10 for percentage).
    """
    if frame is None or len(frame) < 15:
        return []
    if frame[1] != _OP_GROUP_CONFIRM:
        return []
    payload_len = frame[7] | (frame[8] << 8)
    if payload_len < 6:
        return []
    status = frame[9]
    if status != STATUS_SUCCESS:
        return []
    pos = 10
    payload_end = min(9 + payload_len, len(frame))
    scenes = []
    while pos + 5 <= payload_end:
        attr_id = frame[pos] | (frame[pos + 1] << 8)
        count = frame[pos + 3]
        item_len = frame[pos + 4]
        pos += 5
        if attr_id != _ATTR_CONFIGURED_SCENES:
            pos += count * item_len
            continue
        for _ in range(count):
            if pos + item_len > payload_end:
                pos += item_len
                continue
            entry = _parse_scene_entry(frame, pos, item_len)
            if entry is not None:
                scenes.append(entry)
            pos += item_len
    return scenes


def parse_mesh_addresses(frame: Optional[bytes]) -> List[bytes]:
    """Parse MeshLocalAddresses GET response into list of IPv6 addresses.

    Returns list of bytes objects, each 16 bytes.
    Returns empty list on error.
    """
    if frame is None or len(frame) < 15:
        return []
    if frame[1] != _OP_GROUP_CONFIRM:
        return []
    if frame[9] != STATUS_SUCCESS:
        return []
    pos = 10
    payload_end = min(9 + (frame[7] | (frame[8] << 8)), len(frame))
    addresses = []
    while pos + 5 <= payload_end:
        attr_id = frame[pos] | (frame[pos + 1] << 8)
        count = frame[pos + 3]
        item_len = frame[pos + 4]
        pos += 5
        if attr_id != _ATTR_MESH_LOCAL_ADDRESSES:
            pos += count * item_len
            continue
        for _ in range(count):
            if pos + item_len > payload_end or item_len != 16:
                pos += item_len
                continue
            addresses.append(bytes(frame[pos:pos + 16]))
            pos += item_len
    return addresses


def to_hex(data: Optional[bytes]) -> str:
    """Convert bytes to hex string for debug logging."""
    if data is None:
        return "null"
    return " ".join("%02X" % b for b in data)


# ---------------------------------------------------------------------------
# FsciCodec — stateful packet builder (owns message ID counter)
# ---------------------------------------------------------------------------

class FsciCodec:
    """Builds and parses FSCI protocol frames.

    Owns the message ID counter (the only mutable state in this module).
    Create one instance per application lifetime.
    """

    def __init__(self) -> None:
        self._msg_id: int = 0

    def build_feed_mode_packet(self) -> bytes:
        """Build FSCI SET frame: PumpOverrideMode=0, CurrentScene=1.

        Single-packet Feed Mode activation.
        The pump propagates this through mesh to all devices.
        """
        return self._build_frame(_OP_CODE_SET, _FEED_MODE_PAYLOAD)

    def build_handshake_packet(self) -> bytes:
        """Build FSCI GET frame: MeshLocalAddresses (attr 1005).

        Connection handshake — sent after CCCDs, response completes init.
        Source: PeripheralConnection.getDeviceInfo().
        """
        return self._build_frame(_OP_CODE_GET, _HANDSHAKE_PAYLOAD)

    def build_get_scenes_packet(self) -> bytes:
        """Build FSCI GET frame: ConfiguredScenes (attr 400), all instances.

        Reads all scene slots including Feed Mode (scene id=1).
        Scene data contains speed and timeout settings.
        """
        return self._build_frame(_OP_CODE_GET, _GET_SCENES_PAYLOAD)

    def _next_msg_id(self) -> int:
        self._msg_id = (self._msg_id + 1) % _MSG_ID_WRAP
        return self._msg_id

    def _build_frame(self, op_code: int, payload: bytes) -> bytes:
        """Assemble complete FSCI frame: STX + header + payload + CRC16.

        Frame layout (little-endian):
          [STX][opGroup][opCode][msgId:2][reserved:2][payloadLen:2][payload][crc:2]

        CRC covers bytes after STX through end of payload (not STX, not CRC).
        """
        msg_id = self._next_msg_id()
        reserved = _RESERVED_SET if op_code == _OP_CODE_SET else _RESERVED_GET

        # Build inner: everything between STX and CRC
        inner = bytearray(8 + len(payload))
        inner[0] = _OP_GROUP_REQUEST
        inner[1] = op_code
        struct.pack_into('<H', inner, 2, msg_id)
        inner[4] = reserved[0]
        inner[5] = reserved[1]
        struct.pack_into('<H', inner, 6, len(payload))
        inner[8:] = payload

        crc = crc16(inner)

        # Full frame: STX + inner + CRC16 LE
        frame = bytearray(1 + len(inner) + 2)
        frame[0] = _STX
        frame[1:1 + len(inner)] = inner
        struct.pack_into('<H', frame, 1 + len(inner), crc)
        return bytes(frame)
