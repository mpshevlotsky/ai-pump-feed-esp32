"""Unit tests for core.protocol.fsci module."""

import struct
from core.protocol.fsci import (
    FsciCodec,
    crc16,
    status_name,
    parse_response_status,
    parse_scenes_response,
    parse_mesh_addresses,
    to_hex,
    STATUS_SUCCESS,
)


# ---------------------------------------------------------------------------
# crc16 — validated test vectors
# ---------------------------------------------------------------------------

class TestCrc16:

    def test_get_packet_crc(self) -> None:
        data = bytes.fromhex("DE17010000000400ED030001")
        assert crc16(data) == 0x35A7

    def test_set_packet_crc(self) -> None:
        data = bytes.fromhex("DE18090004000F00C10200010100910100010401000000")
        assert crc16(data) == 0xB13A

    def test_empty_input(self) -> None:
        assert crc16(b"") == 0xFFFF

    def test_single_byte(self) -> None:
        result = crc16(b"\x00")
        assert isinstance(result, int)
        assert 0 <= result <= 0xFFFF


# ---------------------------------------------------------------------------
# status_name
# ---------------------------------------------------------------------------

class TestStatusName:

    def test_success(self) -> None:
        assert status_name(0x00) == "Success"

    def test_timeout(self) -> None:
        assert status_name(0x10) == "Timeout"

    def test_entry_not_found(self) -> None:
        assert status_name(0xFF) == "EntryNotFound"

    def test_unknown_code(self) -> None:
        assert status_name(0xAB) == "Unknown(0xAB)"


# ---------------------------------------------------------------------------
# to_hex
# ---------------------------------------------------------------------------

class TestToHex:

    def test_none(self) -> None:
        assert to_hex(None) == "null"

    def test_empty(self) -> None:
        assert to_hex(b"") == ""

    def test_bytes(self) -> None:
        assert to_hex(b"\x02\xDE\x17") == "02 DE 17"


# ---------------------------------------------------------------------------
# parse_response_status
# ---------------------------------------------------------------------------

class TestParseResponseStatus:

    def test_none_frame(self) -> None:
        assert parse_response_status(None) == -1

    def test_short_frame(self) -> None:
        assert parse_response_status(b"\x02\xDF\x18\x00\x00") == -1

    def test_wrong_op_group(self) -> None:
        # opGroup=0xDE (request, not confirm) at index 1
        frame = b"\x02\xDE\x18\x01\x00\x04\x00\x01\x00\x00\xFF\xFF"
        assert parse_response_status(frame) == -1

    def test_zero_payload_length(self) -> None:
        # opGroup=0xDF, payloadLen=0 at bytes [7:9]
        frame = b"\x02\xDF\x18\x01\x00\x04\x00\x00\x00\x00\xFF\xFF"
        assert parse_response_status(frame) == -1

    def test_success_response(self) -> None:
        # Minimal valid SET confirm: opGroup=0xDF, payloadLen=3, status=0x00
        frame = bytearray(12)
        frame[0] = 0x02      # STX
        frame[1] = 0xDF      # opGroup confirm
        frame[2] = 0x18      # opCode SET
        frame[7] = 0x03      # payloadLen low byte
        frame[8] = 0x00      # payloadLen high byte
        frame[9] = 0x00      # status = Success
        assert parse_response_status(bytes(frame)) == STATUS_SUCCESS

    def test_failed_response(self) -> None:
        frame = bytearray(12)
        frame[0] = 0x02
        frame[1] = 0xDF
        frame[2] = 0x18
        frame[7] = 0x03
        frame[8] = 0x00
        frame[9] = 0x01      # status = Failed
        assert parse_response_status(bytes(frame)) == 0x01


# ---------------------------------------------------------------------------
# FsciCodec
# ---------------------------------------------------------------------------

class TestFsciCodec:

    def test_handshake_packet_structure(self) -> None:
        codec = FsciCodec()
        pkt = codec.build_handshake_packet()

        assert pkt[0] == 0x02                          # STX
        assert pkt[1] == 0xDE                          # opGroup request
        assert pkt[2] == 0x17                          # opCode GET
        assert pkt[5] == 0x00 and pkt[6] == 0x00       # reserved GET

        payload_len = pkt[7] | (pkt[8] << 8)
        assert payload_len == 4                         # attrId(2) + index(1) + size(1)

        # CRC is last 2 bytes, covers bytes [1:-2]
        inner = pkt[1:-2]
        expected_crc = crc16(inner)
        actual_crc = struct.unpack_from('<H', pkt, len(pkt) - 2)[0]
        assert actual_crc == expected_crc

    def test_feed_mode_packet_structure(self) -> None:
        codec = FsciCodec()
        pkt = codec.build_feed_mode_packet()

        assert pkt[0] == 0x02                          # STX
        assert pkt[1] == 0xDE                          # opGroup request
        assert pkt[2] == 0x18                          # opCode SET
        assert pkt[5] == 0x04 and pkt[6] == 0x00       # reserved SET

        payload_len = pkt[7] | (pkt[8] << 8)
        assert payload_len == 15                        # PumpOverride(5) + CurrentScene(10)

        inner = pkt[1:-2]
        expected_crc = crc16(inner)
        actual_crc = struct.unpack_from('<H', pkt, len(pkt) - 2)[0]
        assert actual_crc == expected_crc

    def test_msg_id_increments(self) -> None:
        codec = FsciCodec()
        pkt1 = codec.build_handshake_packet()
        pkt2 = codec.build_handshake_packet()

        msg_id_1 = struct.unpack_from('<H', pkt1, 3)[0]
        msg_id_2 = struct.unpack_from('<H', pkt2, 3)[0]
        assert msg_id_2 == msg_id_1 + 1

    def test_msg_id_wraps(self) -> None:
        codec = FsciCodec()
        codec._msg_id = 19999
        pkt = codec.build_handshake_packet()
        msg_id = struct.unpack_from('<H', pkt, 3)[0]
        assert msg_id == 0

    def test_feed_mode_crc_matches_known_capture(self) -> None:
        """Verify CRC for feed mode packet with msg_id=9 matches known BLE capture."""
        codec = FsciCodec()
        codec._msg_id = 8  # next call will produce msg_id=9
        pkt = codec.build_feed_mode_packet()

        inner = pkt[1:-2]
        expected_hex = "DE18090004000F00C10200010100910100010401000000"
        assert inner.hex().upper() == expected_hex.upper()

        actual_crc = struct.unpack_from('<H', pkt, len(pkt) - 2)[0]
        assert actual_crc == 0xB13A

    def test_get_scenes_packet_structure(self) -> None:
        codec = FsciCodec()
        pkt = codec.build_get_scenes_packet()

        assert pkt[0] == 0x02                          # STX
        assert pkt[1] == 0xDE                          # opGroup request
        assert pkt[2] == 0x17                          # opCode GET

        payload_len = pkt[7] | (pkt[8] << 8)
        assert payload_len == 4                         # attrId(2) + instance(1) + count(1)

        # Payload: attr=400 (0x0190), instance=0, count=6
        assert pkt[9] == 0x90 and pkt[10] == 0x01      # attr 400 LE
        assert pkt[11] == 0x00                          # instance
        assert pkt[12] == 0x06                          # count

        inner = pkt[1:-2]
        expected_crc = crc16(inner)
        actual_crc = struct.unpack_from('<H', pkt, len(pkt) - 2)[0]
        assert actual_crc == expected_crc


# ---------------------------------------------------------------------------
# parse_scenes_response
# ---------------------------------------------------------------------------

def _build_scenes_frame(scenes: list) -> bytes:
    """Build a fake GET ConfiguredScenes confirm frame for testing.

    scenes: list of (scene_id, timeout_sec, pump_mode, speed_raw) tuples.
    Each scene entry is 33 bytes: id(2) + timeout(2) + name(16) + primitiveData(13).
    """
    # Build scene entries
    entries = b''
    for scene_id, timeout, mode, speed in scenes:
        entry = struct.pack('<HH', scene_id, timeout)
        entry += b'\x00' * 16           # name (16 bytes padding)
        entry += struct.pack('B', mode)  # pump_mode
        entry += struct.pack('<H', speed)  # MaxSpeed
        entry += b'\x00' * 10           # padding
        entries += entry

    count = len(scenes)
    # Attribute header: attrId(2) + instance(1) + count(1) + itemLen(1)
    attr_header = struct.pack('<HBBB', 400, 0, count, 33)
    payload = bytes([STATUS_SUCCESS]) + attr_header + entries
    payload_len = len(payload)

    frame = bytearray(9 + payload_len + 2)
    frame[0] = 0x02       # STX
    frame[1] = 0xDF       # opGroup confirm
    frame[2] = 0x17       # opCode GET
    frame[7] = payload_len & 0xFF
    frame[8] = (payload_len >> 8) & 0xFF
    frame[9:9 + payload_len] = payload
    crc = crc16(bytes(frame[1:-2]))
    struct.pack_into('<H', frame, len(frame) - 2, crc)
    return bytes(frame)


class TestParseScenesResponse:

    def test_none_frame(self) -> None:
        assert parse_scenes_response(None) == []

    def test_short_frame(self) -> None:
        assert parse_scenes_response(b'\x02\xDF\x17') == []

    def test_wrong_op_group(self) -> None:
        frame = bytearray(20)
        frame[1] = 0xDE  # request, not confirm
        assert parse_scenes_response(bytes(frame)) == []

    def test_error_status(self) -> None:
        frame = bytearray(20)
        frame[1] = 0xDF
        frame[7] = 10
        frame[9] = 0x01  # Failed status
        assert parse_scenes_response(bytes(frame)) == []

    def test_single_scene(self) -> None:
        frame = _build_scenes_frame([(1, 360, 0, 1000)])
        scenes = parse_scenes_response(frame)
        assert len(scenes) == 1
        scene_id, timeout, mode, speed = scenes[0]
        assert scene_id == 1
        assert timeout == 360
        assert mode == 0
        assert speed == 1000

    def test_multiple_scenes(self) -> None:
        frame = _build_scenes_frame([
            (1, 360, 0, 1000),
            (2, 600, 1, 500),
            (3, 120, 0, 750),
        ])
        scenes = parse_scenes_response(frame)
        assert len(scenes) == 3
        assert scenes[0] == (1, 360, 0, 1000)
        assert scenes[1] == (2, 600, 1, 500)
        assert scenes[2] == (3, 120, 0, 750)

    def test_scene_id_zero_skipped(self) -> None:
        """scene_id=0 means unused slot, should be skipped."""
        frame = _build_scenes_frame([(0, 0, 0, 0), (1, 360, 0, 1000)])
        scenes = parse_scenes_response(frame)
        assert len(scenes) == 1
        assert scenes[0][0] == 1

    def test_feed_mode_scene(self) -> None:
        """Feed Mode is always scene_id=1."""
        frame = _build_scenes_frame([(1, 360, 0, 1000)])
        scenes = parse_scenes_response(frame)
        assert scenes[0][0] == 1
        # speed 1000 = 100.0%
        assert scenes[0][3] == 1000

    def test_truncated_frame(self) -> None:
        """Frame shorter than payload_len claims — should not crash."""
        frame = _build_scenes_frame([(1, 360, 0, 1000)])
        truncated = frame[:len(frame) - 10]
        # Should not raise, may return partial or empty
        result = parse_scenes_response(truncated)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# parse_mesh_addresses
# ---------------------------------------------------------------------------

def _build_mesh_frame(addresses: list) -> bytes:
    """Build a fake MeshLocalAddresses confirm frame.

    addresses: list of 16-byte IPv6 addresses.
    """
    entries = b''.join(addresses)
    count = len(addresses)
    attr_header = struct.pack('<HBBB', 1005, 0, count, 16)
    payload = bytes([STATUS_SUCCESS]) + attr_header + entries
    payload_len = len(payload)

    frame = bytearray(9 + payload_len + 2)
    frame[0] = 0x02
    frame[1] = 0xDF
    frame[2] = 0x17
    frame[7] = payload_len & 0xFF
    frame[8] = (payload_len >> 8) & 0xFF
    frame[9:9 + payload_len] = payload
    crc = crc16(bytes(frame[1:-2]))
    struct.pack_into('<H', frame, len(frame) - 2, crc)
    return bytes(frame)


class TestParseMeshAddresses:

    def test_none_frame(self) -> None:
        assert parse_mesh_addresses(None) == []

    def test_short_frame(self) -> None:
        assert parse_mesh_addresses(b'\x02\xDF') == []

    def test_error_status(self) -> None:
        frame = bytearray(20)
        frame[1] = 0xDF
        frame[7] = 10
        frame[9] = 0x01
        assert parse_mesh_addresses(bytes(frame)) == []

    def test_single_address(self) -> None:
        addr = bytes(range(16))
        frame = _build_mesh_frame([addr])
        result = parse_mesh_addresses(frame)
        assert len(result) == 1
        assert result[0] == addr

    def test_multiple_addresses(self) -> None:
        addr1 = bytes([0xFD, 0xB5] + [0] * 14)
        addr2 = bytes([0xFD, 0xB5] + [1] * 14)
        frame = _build_mesh_frame([addr1, addr2])
        result = parse_mesh_addresses(frame)
        assert len(result) == 2
        assert result[0] == addr1
        assert result[1] == addr2

    def test_real_handshake_response(self) -> None:
        """Parse actual handshake response captured from AI pump."""
        frame = bytes.fromhex(
            "02DF17010000001600"
            "00ED03000110"
            "FDB5EA180468BAEC518D24E9BC0E756B"
            "6655"
        )
        result = parse_mesh_addresses(frame)
        assert len(result) == 1
        assert result[0][:2] == b'\xFD\xB5'
