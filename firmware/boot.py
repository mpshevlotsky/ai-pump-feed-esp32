"""
boot.py — runs on every ESP32 reset before main.py.

Attempts early WiFi client connection so the network is available
when main.py starts. WiFiManager in main.py takes over ongoing
WiFi management and adopts this connection if successful.

Uses blocking time.sleep_ms() — acceptable here since boot.py
runs once before the async event loop starts.
"""

import sys
sys.path.extend(['/firmware', '/core'])

import gc
import config


# ---------------------------------------------------------------------------
# Factory reset via BOOT button (GPIO0, active low)
# ---------------------------------------------------------------------------

_RESET_PIN = 0
_RESET_HOLD_MS = 3_000
_RESET_POLL_MS = 50


def _check_factory_reset() -> None:
    """Delete config.json and reboot if BOOT button held for 3 seconds."""
    import machine
    import time

    btn = machine.Pin(_RESET_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    if btn.value() != 0:
        return

    print('boot: BOOT button held, hold 3s for factory reset...')
    start = time.ticks_ms()
    while btn.value() == 0:
        if time.ticks_diff(time.ticks_ms(), start) >= _RESET_HOLD_MS:
            import os
            try:
                os.remove('config.json')
                print('boot: FACTORY RESET — config deleted')
            except OSError:
                print('boot: FACTORY RESET — no config to delete')
            machine.reset()
        time.sleep_ms(_RESET_POLL_MS)
    print('boot: button released, normal boot')


_check_factory_reset()
del _check_factory_reset


# ---------------------------------------------------------------------------
# Early WiFi connection
# ---------------------------------------------------------------------------

_CONNECT_TIMEOUT_MS = 15_000
_POLL_INTERVAL_MS = 100


def _boot() -> None:
    import time
    import network

    cfg = config.load()
    ssid = cfg.get('wifi', {}).get('ssid', '')
    if not ssid:
        print('boot: no WiFi credentials')
        return

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect(ssid, cfg['wifi'].get('password', ''))

    deadline = time.ticks_add(time.ticks_ms(), _CONNECT_TIMEOUT_MS)
    while not sta.isconnected():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            break
        time.sleep_ms(_POLL_INTERVAL_MS)

    if sta.isconnected():
        print('boot: WiFi connected, IP=%s' % sta.ifconfig()[0])
    else:
        sta.active(False)
        print('boot: WiFi connection failed')


_boot()
del _boot
gc.collect()
