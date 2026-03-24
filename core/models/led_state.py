"""LED state definitions — colors, patterns, and priority resolution.

Hardware-independent. All state-to-visual mapping logic lives here.
CPython-compatible for unit testing.
"""

try:
    from typing import Optional, Tuple
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Colors (R, G, B) — dimmed to avoid blinding at close range
# ---------------------------------------------------------------------------

COLOR_OFF = (0, 0, 0)
COLOR_WHITE = (40, 40, 40)
COLOR_GREEN = (0, 40, 0)
COLOR_YELLOW = (40, 30, 0)
COLOR_RED = (40, 0, 0)
COLOR_BLUE = (0, 0, 40)
COLOR_PURPLE = (20, 0, 30)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

PATTERN_SOLID = 'solid'
PATTERN_BLINK_SLOW = 'blink_slow'
PATTERN_BLINK_FAST = 'blink_fast'
PATTERN_PULSE = 'pulse'
PATTERN_DOUBLE = 'double'


# ---------------------------------------------------------------------------
# State names
# ---------------------------------------------------------------------------

STATE_BOOT = 'boot'
STATE_AP_MODE = 'ap_mode'
STATE_WIFI_CONNECTING = 'wifi_connecting'
STATE_WIFI_ONLY = 'wifi_only'
STATE_CONNECTED = 'connected'
STATE_BLE_ACTIVE = 'ble_active'
STATE_ERROR = 'error'


# ---------------------------------------------------------------------------
# LedState data object
# ---------------------------------------------------------------------------

class LedState:
    """LED visual state: color + blink pattern + priority."""
    __slots__ = ('color', 'pattern', 'priority')

    def __init__(self, color: Tuple[int, int, int], pattern: str, priority: int) -> None:
        self.color: Tuple[int, int, int] = color
        self.pattern: str = pattern
        self.priority: int = priority


# ---------------------------------------------------------------------------
# State map — state name to visual representation
# ---------------------------------------------------------------------------

STATE_MAP = {
    STATE_BOOT:            LedState(COLOR_WHITE,  PATTERN_BLINK_FAST, 0),
    STATE_AP_MODE:         LedState(COLOR_PURPLE, PATTERN_PULSE,      1),
    STATE_WIFI_CONNECTING: LedState(COLOR_YELLOW, PATTERN_BLINK_SLOW, 2),
    STATE_WIFI_ONLY:       LedState(COLOR_YELLOW, PATTERN_SOLID,      3),
    STATE_CONNECTED:       LedState(COLOR_GREEN,  PATTERN_SOLID,      4),
    STATE_BLE_ACTIVE:      LedState(COLOR_BLUE,   PATTERN_BLINK_FAST, 5),
    STATE_ERROR:           LedState(COLOR_RED,    PATTERN_DOUBLE,     6),
}

_DEFAULT_STATE = STATE_MAP[STATE_BOOT]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_state(state_names: list) -> LedState:
    """Select highest-priority LedState from a list of active state names.

    Unknown state names are ignored.
    Returns boot state if list is empty or all names unknown.
    """
    best = _DEFAULT_STATE
    for name in state_names:
        state = STATE_MAP.get(name)
        if state is not None and state.priority > best.priority:
            best = state
    return best
