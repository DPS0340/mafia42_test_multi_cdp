"""Mafia42 채널 서버 바이너리 프로토콜 헬퍼.

패킷 구조: [4B length(BE)] [4B msg_type(BE)] [payload...]
  length = 4(msg_type 크기) + len(payload)
"""

from __future__ import annotations

import logging
import struct
from typing import Optional, Tuple

logger = logging.getLogger("megaphone")

# ---------------------------------------------------------------------------
# Observed packet types (from network capture, some meanings are estimates).
# ---------------------------------------------------------------------------

MSG_MEGAPHONE: int = 14            # Megaphone message (receive)
MSG_AUTH: int = 45                 # Auth token send / CDP capture target
MSG_AUTH_OK: int = 1002            # Auth success response
MSG_AUTH_DENIED: Tuple[int, ...] = (100186, 20018)  # Connection denied (level restriction, etc.)
MSG_HELLO_ACK: int = 4             # Hello response
MSG_INIT_DATA: int = 10003         # Initial data (respond with INIT_REPLY on receive)
MSG_INIT_REPLY: int = 454
MSG_SERVER_PING: int = 9           # Server ping (respond with PING_REPLY)
MSG_PING_REPLY: int = 306
MSG_HEARTBEAT: int = 81            # Periodic heartbeat send


def make_packet(msg_type: int, payload: bytes = b"") -> bytes:
    """Encode msg_type + payload into a protocol frame."""
    length = 4 + len(payload)
    return struct.pack(">II", length, msg_type) + payload


def parse_packet(
    data: bytes,
) -> Tuple[Optional[int], Optional[int], bytes]:
    """Decompose a frame into (length, msg_type, payload).

    Returns (None, None, b'') for frames shorter than 8 bytes.
    """
    if not data or len(data) < 8:
        return None, None, b""
    length = struct.unpack(">I", data[0:4])[0]
    msg_type = struct.unpack(">I", data[4:8])[0]
    return length, msg_type, data[8:]


def parse_megaphone(payload: bytes) -> Optional[dict]:
    """Parse a megaphone payload into a message dict, or None if malformed.

    Payload layout: [..8B..][4B msg_id][4B metadata][4B text_len][UTF-8 text]
    """
    if len(payload) < 20:
        return None
    msg_id = struct.unpack(">I", payload[8:12])[0]
    metadata = struct.unpack(">I", payload[12:16])[0]
    text_len = struct.unpack(">I", payload[16:20])[0]
    text = payload[20: 20 + text_len].decode("utf-8", errors="replace")
    if " : " not in text:
        return None
    sender, message = text.split(" : ", 1)
    return {
        "msg_id": msg_id,
        "sender": sender,
        "message": message,
        "metadata": metadata,
    }
