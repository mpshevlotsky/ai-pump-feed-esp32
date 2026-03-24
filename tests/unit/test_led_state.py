"""Unit tests for core.models.led_state module."""

from core.models.led_state import (
    LedState,
    resolve_state,
    STATE_MAP,
    STATE_BOOT,
    STATE_AP_MODE,
    STATE_WIFI_CONNECTING,
    STATE_WIFI_ONLY,
    STATE_CONNECTED,
    STATE_BLE_ACTIVE,
    STATE_ERROR,
    COLOR_WHITE,
    COLOR_GREEN,
    COLOR_YELLOW,
    COLOR_BLUE,
    COLOR_RED,
    COLOR_PURPLE,
    PATTERN_SOLID,
    PATTERN_BLINK_FAST,
    PATTERN_BLINK_SLOW,
    PATTERN_PULSE,
    PATTERN_DOUBLE,
)


# ---------------------------------------------------------------------------
# LedState data object
# ---------------------------------------------------------------------------

class TestLedState:

    def test_attributes(self) -> None:
        state = LedState((0, 40, 0), 'solid', 4)
        assert state.color == (0, 40, 0)
        assert state.pattern == 'solid'
        assert state.priority == 4

    def test_uses_slots(self) -> None:
        state = LedState((0, 0, 0), 'solid', 0)
        assert not hasattr(state, '__dict__')


# ---------------------------------------------------------------------------
# STATE_MAP completeness and ordering
# ---------------------------------------------------------------------------

class TestStateMap:

    def test_all_states_present(self) -> None:
        expected = [
            STATE_BOOT, STATE_AP_MODE, STATE_WIFI_CONNECTING,
            STATE_WIFI_ONLY, STATE_CONNECTED, STATE_BLE_ACTIVE,
            STATE_ERROR,
        ]
        for name in expected:
            assert name in STATE_MAP

    def test_priorities_strictly_ascending(self) -> None:
        order = [
            STATE_BOOT, STATE_AP_MODE, STATE_WIFI_CONNECTING,
            STATE_WIFI_ONLY, STATE_CONNECTED, STATE_BLE_ACTIVE,
            STATE_ERROR,
        ]
        for i in range(len(order) - 1):
            assert STATE_MAP[order[i]].priority < STATE_MAP[order[i + 1]].priority

    def test_boot_is_white_blink_fast(self) -> None:
        s = STATE_MAP[STATE_BOOT]
        assert s.color == COLOR_WHITE
        assert s.pattern == PATTERN_BLINK_FAST

    def test_connected_is_green_solid(self) -> None:
        s = STATE_MAP[STATE_CONNECTED]
        assert s.color == COLOR_GREEN
        assert s.pattern == PATTERN_SOLID

    def test_error_is_red_double(self) -> None:
        s = STATE_MAP[STATE_ERROR]
        assert s.color == COLOR_RED
        assert s.pattern == PATTERN_DOUBLE

    def test_ap_mode_is_purple_pulse(self) -> None:
        s = STATE_MAP[STATE_AP_MODE]
        assert s.color == COLOR_PURPLE
        assert s.pattern == PATTERN_PULSE

    def test_ble_active_is_blue(self) -> None:
        s = STATE_MAP[STATE_BLE_ACTIVE]
        assert s.color == COLOR_BLUE
        assert s.pattern == PATTERN_BLINK_FAST

    def test_wifi_connecting_is_yellow_blink(self) -> None:
        s = STATE_MAP[STATE_WIFI_CONNECTING]
        assert s.color == COLOR_YELLOW
        assert s.pattern == PATTERN_BLINK_SLOW

    def test_wifi_only_is_yellow_solid(self) -> None:
        s = STATE_MAP[STATE_WIFI_ONLY]
        assert s.color == COLOR_YELLOW
        assert s.pattern == PATTERN_SOLID


# ---------------------------------------------------------------------------
# resolve_state
# ---------------------------------------------------------------------------

class TestResolveState:

    def test_empty_returns_boot(self) -> None:
        result = resolve_state([])
        assert result.color == COLOR_WHITE
        assert result.pattern == PATTERN_BLINK_FAST

    def test_single_state(self) -> None:
        result = resolve_state([STATE_CONNECTED])
        assert result.color == COLOR_GREEN
        assert result.pattern == PATTERN_SOLID

    def test_highest_priority_wins(self) -> None:
        result = resolve_state([STATE_CONNECTED, STATE_BLE_ACTIVE])
        assert result.color == COLOR_BLUE

    def test_error_overrides_all(self) -> None:
        result = resolve_state(
            [STATE_CONNECTED, STATE_BLE_ACTIVE, STATE_ERROR])
        assert result.color == COLOR_RED

    def test_unknown_state_ignored(self) -> None:
        result = resolve_state(['nonexistent', STATE_CONNECTED])
        assert result.color == COLOR_GREEN

    def test_all_unknown_returns_boot(self) -> None:
        result = resolve_state(['foo', 'bar'])
        assert result.color == COLOR_WHITE

    def test_wifi_only_vs_connected(self) -> None:
        result = resolve_state([STATE_WIFI_ONLY, STATE_CONNECTED])
        assert result.color == COLOR_GREEN

    def test_ap_mode_over_boot(self) -> None:
        result = resolve_state([STATE_BOOT, STATE_AP_MODE])
        assert result.color == COLOR_PURPLE

    def test_duplicate_states(self) -> None:
        result = resolve_state([STATE_ERROR, STATE_ERROR])
        assert result.color == COLOR_RED
