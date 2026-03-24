"""WS2812 RGB LED status indicator.

Drives a single NeoPixel on GPIO 48 (ESP32-S3-DevKitC-1 onboard LED).
Renders color and blink patterns based on LedState from core.
"""

import asyncio
import time

from core.models.led_state import LedState, resolve_state, STATE_MAP, STATE_BOOT

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import List, Optional, Tuple
    from asyncio import Task
    from neopixel import NeoPixel


# ---------------------------------------------------------------------------
# Hardware constants
# ---------------------------------------------------------------------------

_LED_PIN = 48
_LED_COUNT = 1
_UPDATE_MS = 50
_BRIGHTNESS_SHIFT = 1  # right-shift bits: 1 = 50%, 2 = 25%

# Pattern timing (ms)
_BLINK_SLOW_PERIOD = 1000
_BLINK_FAST_PERIOD = 300
_PULSE_PERIOD = 2000
_DOUBLE_PERIOD = 1000
_DOUBLE_ON1_END = 100
_DOUBLE_OFF1_END = 200
_DOUBLE_ON2_END = 300


# ---------------------------------------------------------------------------
# LedIndicator
# ---------------------------------------------------------------------------

class LedIndicator:
    """WS2812 RGB LED status indicator on GPIO 48.

    Lifecycle: __init__() -> initialize() -> start() -> ... -> stop()

    set_states() is called by Supervisor each loop iteration.
    The internal render loop updates the LED every 50ms for smooth patterns.
    """

    def __init__(self) -> None:
        self._np: Optional[NeoPixel] = None
        self._task: Optional[Task] = None
        self._state: LedState = STATE_MAP[STATE_BOOT]

    def initialize(self) -> None:
        """No-op — hardware init deferred to start()."""
        pass

    async def start(self) -> None:
        """Initialize NeoPixel hardware and start render loop."""
        import neopixel
        from machine import Pin
        self._np = neopixel.NeoPixel(Pin(_LED_PIN), _LED_COUNT)
        self._task = asyncio.create_task(self._run())
        print("LED: started on GPIO %d" % _LED_PIN)

    async def stop(self) -> None:
        """Stop render loop and turn off LED."""
        if self._task:
            self._task.cancel()
            self._task = None
        if self._np:
            self._np[0] = (0, 0, 0)
            self._np.write()
            self._np = None
        print("LED: stopped")

    def set_states(self, state_names: List[str]) -> None:
        """Update LED state from list of active system states."""
        self._state = resolve_state(state_names)

    # -- Render loop --------------------------------------------------------

    async def _run(self) -> None:
        """Async render loop — updates LED every _UPDATE_MS."""
        while True:
            self._render()
            await asyncio.sleep_ms(_UPDATE_MS)

    def _render(self) -> None:
        """Compute and write LED color based on current state and time."""
        state = self._state
        r, g, b = _apply_pattern(state.color, state.pattern,
                                 time.ticks_ms())
        self._np[0] = (r >> _BRIGHTNESS_SHIFT,
                       g >> _BRIGHTNESS_SHIFT,
                       b >> _BRIGHTNESS_SHIFT)
        self._np.write()


# ---------------------------------------------------------------------------
# Pattern rendering (pure functions, no hardware)
# ---------------------------------------------------------------------------

def _apply_pattern(color: Tuple[int, int, int], pattern: str, ticks: int) -> Tuple[int, int, int]:
    """Apply blink/pulse pattern to a base color.

    Returns (R, G, B) tuple. Uses integer math only.
    """
    if pattern == 'solid':
        return color

    if pattern == 'blink_slow':
        phase = ticks % _BLINK_SLOW_PERIOD
        if phase < _BLINK_SLOW_PERIOD // 2:
            return color
        return (0, 0, 0)

    if pattern == 'blink_fast':
        phase = ticks % _BLINK_FAST_PERIOD
        if phase < _BLINK_FAST_PERIOD // 2:
            return color
        return (0, 0, 0)

    if pattern == 'pulse':
        phase = ticks % _PULSE_PERIOD
        half = _PULSE_PERIOD // 2
        if phase < half:
            brightness = phase * 255 // half
        else:
            brightness = (_PULSE_PERIOD - phase) * 255 // half
        return (
            color[0] * brightness // 255,
            color[1] * brightness // 255,
            color[2] * brightness // 255,
        )

    if pattern == 'double':
        phase = ticks % _DOUBLE_PERIOD
        on = (phase < _DOUBLE_ON1_END
              or _DOUBLE_OFF1_END <= phase < _DOUBLE_ON2_END)
        if on:
            return color
        return (0, 0, 0)

    return color
