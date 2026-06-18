"""Constants and channel metadata.

Also loads runtime config from an optional JSON file (megaphone_config.json)
that overrides defaults for token, selected channels, and ports.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("megaphone")

# ---------------------------------------------------------------------------
# Game constants (network capture based, may change with game updates)
# ---------------------------------------------------------------------------

# The fixed WebSocket gateway port the game client always uses.
# The 'port' field in channel_ko.php responses (e.g. 1992) is internal only.
WS_PORT: int = 53421

# Remote debugging (CDP) port for the Mafia42 Electron client.
CDP_PORT: int = 9222

# Local web interface port.
WEB_PORT: int = 8080

# CDP capture timeout (seconds) — give up if no channel switch occurs.
CAPTURE_TIMEOUT: int = 120

# Heartbeat interval (seconds) — keepalive timeout margin.
HEARTBEAT_INTERVAL: int = 3

# Reconnect backoff (seconds) — start / max values.
RECONNECT_MIN: int = 5
RECONNECT_MAX: int = 60

# Message dedup TTL (seconds) — same msg_id within this window = retransmit.
MSG_ID_TTL: float = 300.0

# Global dedup window (seconds) — same (sender, message) across channels.
GLOBAL_DEDUP_WINDOW: float = 8.0

# Maximum messages kept per channel in memory.
MAX_MESSAGES_PER_CHANNEL: int = 200

# Persisted message file (JSON).  Survives server restarts.
PERSIST_FILE: str = "megaphone_messages.json"

# Maximum messages persisted to disk.  0 = unlimited (infinity).
MAX_PERSISTED: int = 0

# Minimum seconds between disk writes of the persist file.  The store throttles
# saves to this interval instead of rewriting the whole file on every message.
PERSIST_INTERVAL: float = 5.0

# Maximum messages buffered in the browser (front-end constant, exported
# so the HTML can stay in sync).
MAX_BUFFER: int = 500

# Single-account default channel when no --channels flag or config selection is set.
# Global megaphones are visible from any channel, so one core channel is enough.
CORE_CHANNEL: int = 1

# Channels excluded from monitoring entirely: never connected, population hidden.
# 19 = "비밀" — appears to be a Mafia42 admin/staff channel, so it is intentionally
# left out of the monitor (risk avoidance).
EXCLUDED_CHANNELS: frozenset[int] = frozenset({19})

# ---------------------------------------------------------------------------
# Channel name mapping
# ---------------------------------------------------------------------------

CHANNEL_NAMES: dict[int, str] = {
    0: "초보",
    1: "1채널",
    2: "2채널",
    3: "3채널",
    19: "20세이상",
    20: "랭크",
    42: "마피아42",
    142: "랭크",
}


def channel_name(channel_id: int) -> str:
    """Return display name for a channel ID. Unknown IDs become 'ch<n>'."""
    return CHANNEL_NAMES.get(channel_id, f"ch{channel_id}")


# ---------------------------------------------------------------------------
# Config file support
# ---------------------------------------------------------------------------

CONFIG_FILE = "megaphone_config.json"
CONFIG_TEMPLATE_FILE = "megaphone_config.json.template"


def _normalize_port(value: Any, default: int) -> int:
    """Return a valid TCP port or the provided default."""
    if isinstance(value, int) and 1 <= value <= 65535:
        return value
    return default


def _normalize_positive_int(value: Any, default: int) -> int:
    """Return a positive integer or the provided default."""
    if isinstance(value, int) and value > 0:
        return value
    return default


def _config_path() -> Path:
    """Return current config file path."""
    return Path(CONFIG_FILE)


def _template_path() -> Path:
    """Return current template file path."""
    return Path(CONFIG_TEMPLATE_FILE)


def load_config() -> dict[str, Any]:
    """Load optional config file from the working directory.

    Returns {} when no config file exists. When a config file exists, returns the
    file values merged over validated defaults.
    """
    config_path = _config_path()
    if not config_path.exists():
        return {}
    try:
        with config_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        logger.info("Loaded config from %s", config_path)
        merged = apply_config_defaults(cfg)
        if "selected_channels" not in cfg:
            merged.pop("selected_channels", None)
        return merged
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load %s: %s", config_path, exc)
        return {}


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config dict to the working directory."""
    config_path = _config_path()
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    logger.info("Saved config to %s", config_path)


def apply_config_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge config file values over module-level defaults.

    Returns a fresh dict with all settings.
    """
    merged = {
        "ws_port": WS_PORT,
        "cdp_port": CDP_PORT,
        "web_port": WEB_PORT,
        "capture_timeout": CAPTURE_TIMEOUT,
        "heartbeat_interval": HEARTBEAT_INTERVAL,
        "reconnect_min": RECONNECT_MIN,
        "reconnect_max": RECONNECT_MAX,
        "selected_channels": None,  # None means apply runtime default logic.
        "persist_file": PERSIST_FILE,
        "max_persisted": MAX_PERSISTED,
    }
    merged.update(cfg)
    merged["ws_port"] = _normalize_port(merged.get("ws_port"), WS_PORT)
    merged["cdp_port"] = _normalize_port(merged.get("cdp_port"), CDP_PORT)
    merged["web_port"] = _normalize_port(merged.get("web_port"), WEB_PORT)
    merged["capture_timeout"] = _normalize_positive_int(
        merged.get("capture_timeout"), CAPTURE_TIMEOUT
    )
    merged["heartbeat_interval"] = _normalize_positive_int(
        merged.get("heartbeat_interval"), HEARTBEAT_INTERVAL
    )
    merged["reconnect_min"] = _normalize_positive_int(
        merged.get("reconnect_min"), RECONNECT_MIN
    )
    merged["reconnect_max"] = _normalize_positive_int(
        merged.get("reconnect_max"), RECONNECT_MAX
    )
    return merged
