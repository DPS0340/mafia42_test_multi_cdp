"""ChannelConnection unit tests — handshake, auth, listen, reconnection."""
from __future__ import annotations

import struct
from typing import Optional

import pytest

import megaphone.channel as channel_module
from megaphone.protocol import (
    MSG_AUTH_DENIED,
    MSG_AUTH_OK,
    MSG_HELLO_ACK,
    MSG_INIT_DATA,
    MSG_INIT_REPLY,
    MSG_MEGAPHONE,
    MSG_PING_REPLY,
    MSG_SERVER_PING,
    make_packet,
    parse_packet,
)
from megaphone.store import MessageStore


# ===========================================================================
# Test doubles
# ===========================================================================


class FakeSocket:
    """Minimal WebSocket test double for ChannelConnection.

    Args:
        frames: pre-loaded frames for recv()
        stop_on_empty: if True, raise ConnectionError when frames exhausted
            (use when FakeSocket has no owner and the caller can't set
            running=False — e.g. when returned from monkeypatched
            create_connection)
    """

    def __init__(self, frames: list[bytes], stop_on_empty: bool = False) -> None:
        self._frames = list(frames)
        self._stop_on_empty = stop_on_empty
        self.owner: Optional[channel_module.ChannelConnection] = None
        self.sent: list[tuple[Optional[int], bytes]] = []
        self.timeout: float = 10.0
        self.closed = False

    def recv(self) -> bytes:
        if self._frames:
            return self._frames.pop(0)
        # No frames left — try to stop the connection.
        if self.owner is not None:
            self.owner.running = False
        if self._stop_on_empty:
            raise ConnectionError("FakeSocket: frames exhausted")
        raise channel_module.websocket.WebSocketTimeoutException()

    def send(self, data: bytes, opcode: object = None) -> None:  # noqa: ARG002
        _, msg_type, payload = parse_packet(data)
        self.sent.append((msg_type, payload))

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def close(self) -> None:
        self.closed = True


# ===========================================================================
# Helpers
# ===========================================================================


def _hello_ack() -> bytes:
    return make_packet(MSG_HELLO_ACK, b"")


def _auth_ok() -> bytes:
    return make_packet(MSG_AUTH_OK, b"")


def _auth_denied() -> bytes:
    return make_packet(MSG_AUTH_DENIED[0], b"")


def _init_data() -> bytes:
    return make_packet(MSG_INIT_DATA, b"")


def _megaphone(sender: str, message: str, msg_id: int = 1) -> bytes:
    text = f"{sender} : {message}".encode("utf-8")
    payload = b"\x00" * 8 + struct.pack(">III", msg_id, 0, len(text)) + text
    return make_packet(MSG_MEGAPHONE, payload)


def _server_ping() -> bytes:
    return make_packet(MSG_SERVER_PING, b"")


def _unknown_type(type_id: int) -> bytes:
    return make_packet(type_id, b"unknown")


def _handshake_frames() -> list[bytes]:
    """Frames for a full successful handshake (hello → auth → init)."""
    return [_hello_ack(), _auth_ok(), _init_data()]


def _extract_sent_types(fake: FakeSocket) -> list[int]:
    """Return sent message types (filtering out None)."""
    return [t for t, _ in fake.sent if t is not None]


# ===========================================================================
# send_pkt
# ===========================================================================


class TestSendPkt:
    """send_pkt behavior."""

    def test_raises_when_ws_none(self):
        """INVARIANT: send_pkt() raises RuntimeError when ws is None."""
        conn = channel_module.ChannelConnection(0, "localhost", "token")
        with pytest.raises(RuntimeError, match="WebSocket not connected"):
            conn.send_pkt(1)


# ===========================================================================
# recv_until
# ===========================================================================


class TestRecvUntil:
    """recv_until behavior."""

    def test_returns_target_on_match(self):
        """INVARIANT: recv_until returns (type, payload) when target arrives."""
        conn = channel_module.ChannelConnection(0, "localhost", "token")
        conn.ws = FakeSocket([_hello_ack()])  # type: ignore[assignment]

        result_type, _ = conn.recv_until(MSG_HELLO_ACK, max_tries=5)
        assert result_type == MSG_HELLO_ACK

    def test_returns_none_on_timeout(self):
        """INVARIANT: returns (None, None) when target never arrives."""
        conn = channel_module.ChannelConnection(0, "localhost", "token")
        conn.ws = FakeSocket([])  # type: ignore[assignment]

        result_type, result_payload = conn.recv_until(9999, max_tries=3)
        assert result_type is None
        assert result_payload is None

    def test_stops_on_auth_denied(self):
        """INVARIANT: stops early on any MSG_AUTH_DENIED type."""
        conn = channel_module.ChannelConnection(0, "localhost", "token")
        conn.ws = FakeSocket([_auth_denied()])  # type: ignore[assignment]

        result_type, _ = conn.recv_until(MSG_AUTH_OK, max_tries=5)
        assert result_type in MSG_AUTH_DENIED


# ===========================================================================
# _connect_and_listen (lifecycle)
# ===========================================================================


class TestConnectAndListen:
    """Full connection lifecycle: handshake → auth → init → listen.

    All tests monkeypatch websocket.create_connection so no real network
    calls are made.  stop_on_empty=True ensures _listen exits cleanly
    when the FakeSocket has no more frames.
    """

    def _install(self, monkeypatch: pytest.MonkeyPatch, fake: FakeSocket) -> None:
        """Patch create_connection to return *fake*."""

        def _create(url: str, timeout: float = 15, **kwargs) -> FakeSocket:  # noqa: ARG001
            return fake

        monkeypatch.setattr(channel_module.websocket, "create_connection", _create)

    def test_successful_handshake_connects(self, monkeypatch):
        """INVARIANT: valid handshake establishes connection before listen."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket(_handshake_frames(), stop_on_empty=True)
        self._install(monkeypatch, fake)

        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        with pytest.raises(ConnectionError, match="FakeSocket"):
            conn._connect_and_listen()

        # These are set BEFORE _listen() is called.
        assert fake.closed is False
        assert conn.connected_at is not None
        assert store.get_status_snapshot().get(0) == "connected"

    def test_no_hello_response_raises(self, monkeypatch):
        """INVARIANT: missing hello response raises ConnectionError."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket([])  # timeout → recv_until returns (None, None)
        self._install(monkeypatch, fake)

        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        with pytest.raises(ConnectionError, match="No auth response"):
            conn._connect_and_listen()

    def test_auth_denied_stops_connection(self, monkeypatch):
        """INVARIANT: auth denial sets status='denied' and closes socket."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket([_hello_ack(), _auth_denied()])
        self._install(monkeypatch, fake)

        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        conn._connect_and_listen()

        assert fake.closed is True
        assert store.get_status_snapshot().get(0) == "denied"
        assert conn.running is False

    def test_no_auth_response_raises(self, monkeypatch):
        """INVARIANT: no auth response raises ConnectionError."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket([_hello_ack()])  # timeout on auth → (None, None)
        self._install(monkeypatch, fake)

        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        with pytest.raises(ConnectionError, match="No auth response"):
            conn._connect_and_listen()


# ===========================================================================
# _listen loop
# ===========================================================================


class TestListenLoop:
    """Listen loop: message dispatch, heartbeats, server responses.

    Each test assigns fake.owner = conn so the FakeSocket sets
    conn.running = False when frames are exhausted.
    """

    def test_dispatches_megaphone_to_store(self, monkeypatch):
        """INVARIANT: megaphone packets are stored in MessageStore."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket([_megaphone("유저", "안녕하세요", 1)])
        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        conn.ws = fake  # type: ignore[assignment]
        fake.owner = conn

        conn._listen()

        msgs = store.get_all_messages()
        assert len(msgs) == 1
        assert msgs[0]["sender"] == "유저"
        assert msgs[0]["message"] == "안녕하세요"

    def test_responds_to_server_ping(self, monkeypatch):
        """INVARIANT: server ping triggers MSG_PING_REPLY."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket([_server_ping()])
        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        conn.ws = fake  # type: ignore[assignment]
        fake.owner = conn

        conn._listen()

        sent_types = _extract_sent_types(fake)
        assert MSG_PING_REPLY in sent_types

    def test_responds_to_init_data(self, monkeypatch):
        """INVARIANT: INIT_DATA triggers MSG_INIT_REPLY."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket([_init_data()])
        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        conn.ws = fake  # type: ignore[assignment]
        fake.owner = conn

        conn._listen()

        sent_types = _extract_sent_types(fake)
        assert MSG_INIT_REPLY in sent_types

    def test_tracks_unhandled_types(self, monkeypatch):
        """INVARIANT: unknown packet types are tracked."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket([_unknown_type(9999)])
        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        conn.ws = fake  # type: ignore[assignment]
        fake.owner = conn

        conn._listen()

        assert 9999 in conn.unhandled_types

    def test_ignores_malformed_frames(self, monkeypatch):
        """INVARIANT: malformed frames (< 8 bytes) are silently skipped."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        fake = FakeSocket([b"\x00\x00\x00\x01"])  # 4 bytes → parse returns None
        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        conn.ws = fake  # type: ignore[assignment]
        fake.owner = conn

        conn._listen()  # should not crash

        msgs = store.get_all_messages()
        assert len(msgs) == 0


# ===========================================================================
# Reconnection
# ===========================================================================


class TestReconnection:
    """Reconnection logic with exponential backoff."""

    def test_reconnects_after_failure(self, monkeypatch):
        """INVARIANT: failed connection triggers reconnection attempt."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        connect_calls = [0]
        conn = channel_module.ChannelConnection(0, "ch.local", "token")

        def fake_create(url: str, timeout: float = 15, **kwargs) -> FakeSocket:  # noqa: ARG001
            connect_calls[0] += 1
            if connect_calls[0] <= 2:
                raise ConnectionError(f"fail #{connect_calls[0]}")
            # 3rd+ call: stop the loop
            conn.running = False
            return FakeSocket([])

        monkeypatch.setattr(
            channel_module.websocket, "create_connection", fake_create
        )

        conn.start()
        conn.join(timeout=10)

        assert connect_calls[0] >= 2

    def test_stops_on_deny(self, monkeypatch):
        """INVARIANT: auth denial stops reconnection loop."""
        store = MessageStore()
        monkeypatch.setattr(channel_module, "store", store)

        def fake_create(url: str, timeout: float = 15, **kwargs) -> FakeSocket:  # noqa: ARG001
            return FakeSocket([_hello_ack(), _auth_denied()])

        monkeypatch.setattr(
            channel_module.websocket, "create_connection", fake_create
        )

        conn = channel_module.ChannelConnection(0, "ch.local", "token")
        conn.start()
        conn.join(timeout=10)

        assert conn.running is False
        assert store.get_status_snapshot().get(0) == "denied"


# ===========================================================================
# Attributes
# ===========================================================================


class TestChannelConnectionAttributes:
    """Basic attribute verification."""

    def test_attributes_set_correctly(self):
        """INVARIANT: constructor sets expected attributes."""
        conn = channel_module.ChannelConnection(42, "ch.local", "token")
        assert conn.channel_id == 42
        assert conn.host == "ch.local"
        assert conn.auth_token == "token"
        assert conn.name_str == "마피아42"
        assert conn.running is True
        assert conn.ws is None
        assert conn.connected_at is None
        assert conn.unhandled_types == set()
