"""
Configuration management — load/save config.json.

Single source of truth for all configuration I/O.
All modules MUST use this module for config access (CLAUDE.md requirement).
"""

import json


TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Optional

_FILE = 'config.json'


def load() -> dict:
    """Load configuration from file.

    Returns dict with 'wifi', 'mqtt', 'pump' sections.
    Returns empty sections on missing file or parse error.
    """
    try:
        with open(_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'wifi': {}, 'mqtt': {}, 'pump': {}}


def save(cfg: dict) -> None:
    """Save configuration dict to file."""
    with open(_FILE, 'w') as f:
        json.dump(cfg, f)


def save_feed_mode_duration(scenes_json: str,
                            mesh_ipv6: Optional[bytes]) -> None:
    """Extract FeedMode duration from scenes and persist to config."""
    if mesh_ipv6 is None:
        return
    try:
        scenes = json.loads(scenes_json)
    except (ValueError, TypeError):
        return
    for scene in scenes:
        if scene.get('id') == 1:
            cfg = load()
            fm = cfg.setdefault('feed_mode', {})
            fm[mesh_ipv6.hex()] = scene['timeout']
            save(cfg)
            print("Config: saved feed_mode duration %ds for %s"
                  % (scene['timeout'], mesh_ipv6.hex()[:16]))
            return


def get_max_feed_duration(mesh_prefix_hex: str) -> int:
    """Max Feed Mode duration across all pumps matching a mesh prefix.

    Keys in feed_mode are full 32-char mesh IPv6 hex strings.
    Prefix is the first 16 chars. Returns 0 if no data found.
    """
    cfg = load()
    fm = cfg.get('feed_mode', {})
    max_dur = 0
    for key, dur in fm.items():
        if key.startswith(mesh_prefix_hex) and dur > max_dur:
            max_dur = dur
    return max_dur
