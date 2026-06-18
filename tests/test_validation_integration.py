"""Integration validation tests using Mafia42 channel-server test doubles."""
from __future__ import annotations

import http.client
import json
import socket
from typing import Optional

import pytest

from conftest import build_megaphone_packet
import megaphone.channel as channel_module
import megaphone.webserver as webserver_module
from megaphone.channel import ChannelConnection
from megaphone.protocol import (
    MSG_HEARTBEAT,
    MSG_INIT_DATA,
    MSG_INIT_REPLY,
    MSG_PING_REPLY,
    MSG_SERVER_PING,
    make_packet,
    parse_packet,
)
from megaphone.store import MessageStore
from megaphone.webserver import start_web_server


class FakeMafia42Socket:
    """Small WebSocket test double that replays server frames to ChannelConnection."""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.owner: Optional[ChannelConnection] = None
        self.sent_frames: list[bytes] = []
        self.timeouts: list[float] = []
        self.closed = False

    def recv(self) -> bytes:
        """Return next server frame, then stop owner loop via timeout."""
        if self._frames:
            return self._frames.pop(0)
        if self.owner is not None:
            self.owner.running = False
        raise channel_module.websocket.WebSocketTimeoutException()

    def send(self, data: bytes, opcode: object = None) -> None:  # noqa: ARG002
        """Record outbound client frame."""
        self.sent_frames.append(data)

    def settimeout(self, timeout: float) -> None:
        """Record timeout changes made by ChannelConnection."""
        self.timeouts.append(timeout)

    def close(self) -> None:
        """Record close call."""
        self.closed = True


def _sent_types(fake_socket: FakeMafia42Socket) -> list[int]:
    """Return parsed outbound message types from fake socket."""
    return [msg_type for _, msg_type, _ in (parse_packet(frame) for frame in fake_socket.sent_frames) if msg_type is not None]


def _run_listen_once(
    *,
    store: MessageStore,
    monkeypatch: pytest.MonkeyPatch,
    channel_id: int,
    frames: list[bytes],
) -> FakeMafia42Socket:
    """Run ChannelConnection listen loop against fake frames until exhausted."""
    monkeypatch.setattr(channel_module, "store", store)
    fake_socket = FakeMafia42Socket(frames)
    conn = ChannelConnection(channel_id, "fake-mafia42.local", "fake-token")
    conn.ws = fake_socket
    fake_socket.owner = conn

    conn._listen()

    assert conn.running is False
    return fake_socket


def _find_free_port() -> int:
    """Return currently free localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def _get_json(port: int, path: str) -> list[dict]:
    """Fetch JSON list from local validation server."""
    conn = http.client.HTTPConnection("localhost", port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read().decode("utf-8")
    finally:
        conn.close()
    assert response.status == 200
    assert "json" in response.getheader("Content-Type", "")
    data = json.loads(body)
    assert isinstance(data, list)
    return data


class TestChannelValidationIntegration:
    """Validate channel ingestion behavior with test-doubled Mafia42 socket."""

    @pytest.mark.integration
    def test_channel_stream_validates_message_ingest_and_control_replies(self, monkeypatch):
        """ChannelConnection parses messages and replies to server control packets."""
        store = MessageStore()
        fake_socket = _run_listen_once(
            store=store,
            monkeypatch=monkeypatch,
            channel_id=0,
            frames=[
                build_megaphone_packet("검증자", "통합 메시지", msg_id=1001),
                make_packet(MSG_SERVER_PING, b""),
                make_packet(MSG_INIT_DATA, b""),
                b"\x00",  # malformed frame: ignored, no crash
            ],
        )

        messages = store.get_all_messages()
        assert len(messages) == 1
        assert messages[0]["sender"] == "검증자"
        assert messages[0]["message"] == "통합 메시지"
        assert messages[0]["msg_id"] == 1001
        assert messages[0]["channel_id"] == 0
        assert messages[0]["scope"] == "server"

        sent_types = _sent_types(fake_socket)
        assert sent_types[0] == MSG_HEARTBEAT
        assert MSG_PING_REPLY in sent_types
        assert MSG_INIT_REPLY in sent_types

    @pytest.mark.integration
    def test_validation_pipeline_promotes_global_message_visible_through_http_api(
        self, monkeypatch
    ):
        """Two channel streams promote same megaphone text and expose result via API."""
        store = MessageStore()
        monkeypatch.setattr(webserver_module, "store", store)
        port = _find_free_port()
        server = start_web_server(port)
        try:
            for channel_id, msg_id in ((0, 2001), (1, 2002)):
                _run_listen_once(
                    store=store,
                    monkeypatch=monkeypatch,
                    channel_id=channel_id,
                    frames=[build_megaphone_packet("공지", "검증 공지", msg_id=msg_id)],
                )

            data = _get_json(port, "/api/messages/all")
            assert len(data) == 1
            assert data[0]["sender"] == "공지"
            assert data[0]["message"] == "검증 공지"
            assert data[0]["scope"] == "global"

            exported = _get_json(port, "/api/export?format=json")
            assert exported == data
        finally:
            server.shutdown()
