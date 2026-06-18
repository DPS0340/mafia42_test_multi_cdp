"""Shared pytest fixtures and Hypothesis strategies for all test modules.

Strategies:
  - ch_id(): valid channel IDs (0, 1, 2, 3, 19, 20, 42, 142 + random)
  - sender(): valid Korean/ASCII usernames (1~12 chars)
  - message_text(): valid megaphone message text (1~200 chars)
  - megaphone_text(): "sender : message" format string
  - packet_type(): valid MSG_* protocol constants
  - packet_payload(): raw payload bytes with correct layout
  - valid_packet(): complete binary packet
  - shortened_packet(): malformed packets (truncated, etc.)
"""
from __future__ import annotations

import logging
import struct
from queue import Queue
from typing import Generator, Tuple
from unittest.mock import MagicMock

import pytest
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from megaphone.protocol import (
    MSG_AUTH,
    MSG_AUTH_OK,
    MSG_HEARTBEAT,
    MSG_INIT_DATA,
    MSG_INIT_REPLY,
    MSG_MEGAPHONE,
    MSG_PING_REPLY,
    MSG_SERVER_PING,
    make_packet,
)
from megaphone.store import MessageStore

logging.disable(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Store isolation: redirect PERSIST_FILE to a per-test temp directory so that
# MessageStore does not load stale data from a previous test's persisted file.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_persist_file(tmp_path, monkeypatch):
    """Prevent MessageStore from cross-test contamination via the persist file."""
    monkeypatch.setattr("megaphone.store.PERSIST_FILE", str(tmp_path / "test_persist.json"))
    # Also disable persistence to prevent hypothesis example bleed
    monkeypatch.setattr(MessageStore, "_load_persisted", lambda self: None)
    monkeypatch.setattr(MessageStore, "_save_persisted", lambda self: None)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CHANNEL_IDS = [0, 1, 2, 3, 19, 20, 42, 142]
PACKET_TYPE_LIST = [
    MSG_MEGAPHONE,
    MSG_AUTH,
    MSG_AUTH_OK,
    MSG_HEARTBEAT,
    MSG_INIT_DATA,
    MSG_INIT_REPLY,
    MSG_SERVER_PING,
    MSG_PING_REPLY,
]
KOREAN_CHARS = (
    "가나다라마바사아자차카타파하"
    "거너더러머버서어저처커터퍼허"
    "고노도로모보소오조초코토포호"
    "구누두루무부수우주추쿠투푸후"
    "그느드르므브스으즈츠크트프흐"
    "기니디리미비시이지치키티피히"
    "깨께빠싸짜"
    "안녕하세요반갑습니다"
    "공지수고감사축하축하확성기채널모니터"
    "마피아이벤트서버오픈업데이트점검"
    "0123456789"
    ".,!?~_ "
)

# ---------------------------------------------------------------------------
# Hypothesis Strategies
# ---------------------------------------------------------------------------


def ch_id() -> SearchStrategy[int]:
    """Strategy: valid channel ID (from config or random)."""
    return st.sampled_from(VALID_CHANNEL_IDS) | st.integers(min_value=0, max_value=999)


def sender() -> SearchStrategy[str]:
    """Strategy: realistic Korean/ASCII sender name (1~12 chars)."""
    return st.text(
        alphabet=st.characters(whitelist_categories=["L", "N", "P"]),
        min_size=1,
        max_size=12,
    ).filter(lambda s: s.strip() != "" and ":" not in s)


def message_text() -> SearchStrategy[str]:
    """Strategy: realistic megaphone message text (1~200 chars)."""
    return st.text(
        alphabet=st.sampled_from(KOREAN_CHARS),
        min_size=1,
        max_size=200,
    ).filter(lambda s: s.strip() != "")


def megaphone_text() -> SearchStrategy[str]:
    """Strategy: "sender : message" format text (megaphone-compatible)."""
    return st.builds(lambda s, m: f"{s} : {m}", s=sender(), m=message_text())


def packet_type() -> SearchStrategy[int]:
    """Strategy: valid protocol packet type constant."""
    return st.sampled_from(PACKET_TYPE_LIST)


def packet_payload() -> SearchStrategy[bytes]:
    """Strategy: raw payload bytes (up to 4 KB)."""
    return st.binary(min_size=0, max_size=4096)


def valid_packet() -> SearchStrategy[Tuple[int, int, bytes, bytes]]:
    """Strategy: (full_packet_bytes, length, msg_type, payload).

    Generates a valid protocol packet and its parsed components.
    """
    return st.builds(
        lambda mt, pl: (
            make_packet(mt, pl),
            struct.unpack(">I", make_packet(mt, pl)[0:4])[0],
            mt,
            pl,
        ),
        mt=packet_type(),
        pl=packet_payload(),
    )


def shortened_packet() -> SearchStrategy[bytes]:
    """Strategy: malformed packets shorter than 8 bytes."""
    return st.binary(min_size=1, max_size=7)


def megaphone_payload() -> SearchStrategy[bytes]:
    """Strategy: valid megaphone payload (header + text bytes).

    Layout: [8B junk][4B msg_id][4B metadata][4B text_len][UTF-8 text]
    """
    return st.builds(
        lambda mid, meta, text: (
            b"\x00" * 8
            + struct.pack(">I", mid)
            + struct.pack(">I", meta)
            + struct.pack(">I", len(text.encode("utf-8")))
            + text.encode("utf-8")
        ),
        mid=st.integers(min_value=0, max_value=2**32 - 1),
        meta=st.integers(min_value=0, max_value=2**32 - 1),
        text=megaphone_text(),
    )


def msg_id() -> SearchStrategy[int]:
    """Strategy: message ID (same range as server)."""
    return st.integers(min_value=0, max_value=2**32 - 1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_store() -> Generator[MessageStore, None, None]:
    """Return a fresh, isolated MessageStore."""
    yield MessageStore()


@pytest.fixture
def mock_sse_queue() -> Queue[str]:
    """Return a Queue that behaves like an SSE subscriber."""
    return Queue()


@pytest.fixture
def mock_cdp_server() -> MagicMock:
    """Return a MagicMock that simulates a CDP WebSocket connection."""
    mock = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Helpers: fixed-data packet builders (for non-hypothesis tests)
# ---------------------------------------------------------------------------


def build_megaphone_packet(
    sender: str, message: str, msg_id: int = 1, metadata: int = 0
) -> bytes:
    """Build a realistic MSG_MEGAPHONE packet."""
    text = f"{sender} : {message}"
    text_bytes = text.encode("utf-8")
    payload = (
        b"\x00" * 8
        + struct.pack(">I", msg_id)
        + struct.pack(">I", metadata)
        + struct.pack(">I", len(text_bytes))
        + text_bytes
    )
    return make_packet(MSG_MEGAPHONE, payload)


def build_heartbeat_packet() -> bytes:
    return make_packet(MSG_HEARTBEAT, b"\x00")


def build_auth_ok_packet() -> bytes:
    return make_packet(MSG_AUTH_OK, b"\x01")


def build_server_ping_packet() -> bytes:
    return make_packet(MSG_SERVER_PING, b"\x00\x00\x00\x00")
