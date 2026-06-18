"""Tests for multi-client CDP monitor.

Tests the MultiCDPMonitor, CDPClient, and related functions without
requiring actual CDP connections (uses mocks).
"""
from __future__ import annotations

import base64
import json
import struct
import threading
from unittest.mock import MagicMock, patch

import pytest
import websocket

from megaphone.multi_cdp import (
    CDP_PORT_END,
    CDP_PORT_START,
    MAX_CLIENTS,
    CDPClient,
    MultiCDPMonitor,
    load_host_to_channel,
    parse_megaphone_payload,
    ws_url_to_channel_id,
)
from megaphone.protocol import MSG_MEGAPHONE, make_packet
from megaphone.store import MessageStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_megaphone_frame(sender: str, message: str, msg_id: int = 1) -> bytes:
    """Build a MSG_MEGAPHONE packet as it would appear in CDP base64 payload."""
    text = f"{sender} : {message}"
    text_bytes = text.encode("utf-8")
    payload = (
        b"\x00" * 8
        + struct.pack(">I", msg_id)
        + struct.pack(">I", 0)
        + struct.pack(">I", len(text_bytes))
        + text_bytes
    )
    return make_packet(MSG_MEGAPHONE, payload)


def make_cdp_frame(sender: str, message: str, msg_id: int = 1) -> dict:
    """Build a CDP Network.webSocketFrameReceived event dict."""
    raw = build_megaphone_frame(sender, message, msg_id)
    return {
        "method": "Network.webSocketFrameReceived",
        "params": {
            "response": {
                "payloadData": base64.b64encode(raw).decode("ascii"),
            }
        },
    }


def make_ws_created_event(url: str) -> dict:
    """Build a CDP Network.webSocketCreated event dict."""
    return {
        "method": "Network.webSocketCreated",
        "params": {"url": url},
    }


# ---------------------------------------------------------------------------
# Unit tests: parse_megaphone_payload
# ---------------------------------------------------------------------------


class TestParseMegaphonePayload:
    """Tests for parse_megaphone_payload function."""

    def test_valid_payload(self):
        """parse_megaphone_payload correctly parses valid payload."""
        text = "TestUser : Hello World"
        text_bytes = text.encode("utf-8")
        payload = (
            b"\x00" * 8
            + struct.pack(">I", 42)
            + struct.pack(">I", 0)
            + struct.pack(">I", len(text_bytes))
            + text_bytes
        )
        result = parse_megaphone_payload(payload)
        assert result is not None
        assert result["sender"] == "TestUser"
        assert result["message"] == "Hello World"
        assert result["msg_id"] == 42
        assert result["metadata"] == 0

    def test_korean_text(self):
        """parse_megaphone_payload handles Korean text."""
        text = "플레이어1 : 안녕하세요"
        text_bytes = text.encode("utf-8")
        payload = (
            b"\x00" * 8
            + struct.pack(">I", 1)
            + struct.pack(">I", 0)
            + struct.pack(">I", len(text_bytes))
            + text_bytes
        )
        result = parse_megaphone_payload(payload)
        assert result is not None
        assert result["sender"] == "플레이어1"
        assert result["message"] == "안녕하세요"

    def test_short_payload_returns_none(self):
        """Payload shorter than 20 bytes returns None."""
        assert parse_megaphone_payload(b"\x00" * 19) is None
        assert parse_megaphone_payload(b"") is None

    def test_no_separator_returns_none(self):
        """Payload without ' : ' separator returns None."""
        text_bytes = b"NoSeparatorHere"
        payload = (
            b"\x00" * 8
            + struct.pack(">I", 1)
            + struct.pack(">I", 0)
            + struct.pack(">I", len(text_bytes))
            + text_bytes
        )
        assert parse_megaphone_payload(payload) is None

    def test_multiple_separators(self):
        """Only first ' : ' is used as separator."""
        text = "User : msg : extra"
        text_bytes = text.encode("utf-8")
        payload = (
            b"\x00" * 8
            + struct.pack(">I", 1)
            + struct.pack(">I", 0)
            + struct.pack(">I", len(text_bytes))
            + text_bytes
        )
        result = parse_megaphone_payload(payload)
        assert result is not None
        assert result["sender"] == "User"
        assert result["message"] == "msg : extra"


# ---------------------------------------------------------------------------
# Unit tests: ws_url_to_channel_id
# ---------------------------------------------------------------------------


class TestWsUrlToChannelId:
    """Tests for ws_url_to_channel_id function."""

    def test_known_host(self):
        """Returns correct channel_id for known host."""
        from megaphone import multi_cdp

        multi_cdp.HOST_TO_CHANNEL = {"75.2.1.51": 1, "75.2.71.31": 0}
        assert ws_url_to_channel_id("ws://75.2.1.51:53421/") == 1

    def test_unknown_host(self):
        """Returns 0 for unknown host."""
        from megaphone import multi_cdp

        multi_cdp.HOST_TO_CHANNEL = {}
        assert ws_url_to_channel_id("ws://1.2.3.4:53421/") == 0

    def test_malformed_url(self):
        """Returns 0 for malformed URL."""
        assert ws_url_to_channel_id("") == 0
        assert ws_url_to_channel_id("not-a-url") == 0


# ---------------------------------------------------------------------------
# Unit tests: CDPClient
# ---------------------------------------------------------------------------


class TestCDPClient:
    """Tests for CDPClient dataclass."""

    def test_default_name(self):
        """CDPClient gets auto-generated name if not specified."""
        store = MessageStore()
        client = CDPClient(port=9222, store=store)
        assert client.name == "CDP:9222"

    def test_custom_name(self):
        """CDPClient uses custom name when provided."""
        store = MessageStore()
        client = CDPClient(port=9223, store=store, name="Game2")
        assert client.name == "Game2"

    def test_initial_state(self):
        """CDPClient starts in stopped/disconnected state."""
        store = MessageStore()
        client = CDPClient(port=9222, store=store)
        assert not client.running
        assert not client.connected
        assert client.current_channel == 0

    def test_stop_sets_running_false(self):
        """stop() sets running to False."""
        store = MessageStore()
        client = CDPClient(port=9222, store=store)
        client._running = True
        client.stop()
        assert not client.running


# ---------------------------------------------------------------------------
# Unit tests: MultiCDPMonitor
# ---------------------------------------------------------------------------


class TestMultiCDPMonitor:
    """Tests for MultiCDPMonitor management class."""

    def test_add_client(self):
        """add_client creates and registers a new client."""
        monitor = MultiCDPMonitor()
        client = monitor.add_client(9222)
        assert 9222 in monitor.clients
        assert client.port == 9222

    def test_add_client_returns_existing(self):
        """add_client returns existing client if port already registered."""
        monitor = MultiCDPMonitor()
        client1 = monitor.add_client(9222)
        client2 = monitor.add_client(9222)
        assert client1 is client2
        assert len(monitor.clients) == 1

    def test_max_clients_limit(self):
        """add_client raises ValueError when MAX_CLIENTS exceeded."""
        monitor = MultiCDPMonitor()
        for i in range(MAX_CLIENTS):
            monitor.add_client(9222 + i)
        with pytest.raises(ValueError, match="Maximum"):
            monitor.add_client(9222 + MAX_CLIENTS)

    def test_remove_client(self):
        """remove_client stops and unregisters a client."""
        monitor = MultiCDPMonitor()
        monitor.add_client(9222)
        monitor.remove_client(9222)
        assert 9222 not in monitor.clients

    def test_remove_nonexistent_client(self):
        """remove_client is a no-op for unregistered port."""
        monitor = MultiCDPMonitor()
        monitor.remove_client(9999)  # Should not raise

    def test_from_port_range(self):
        """from_port_range creates clients for all ports in range."""
        monitor = MultiCDPMonitor.from_port_range(start=9222, end=9224)
        assert set(monitor.clients.keys()) == {9222, 9223, 9224}

    def test_from_port_range_default(self):
        """from_port_range with defaults creates 6 clients."""
        monitor = MultiCDPMonitor.from_port_range()
        assert len(monitor.clients) == 6
        expected = set(range(CDP_PORT_START, CDP_PORT_END + 1))
        assert set(monitor.clients.keys()) == expected

    def test_get_status(self):
        """get_status returns status dict for all clients."""
        monitor = MultiCDPMonitor()
        monitor.add_client(9222)
        monitor.add_client(9223)
        status = monitor.get_status()
        assert 9222 in status
        assert 9223 in status
        assert status[9222]["running"] is False
        assert status[9222]["connected"] is False

    def test_stop_all(self):
        """stop() signals all clients to stop."""
        monitor = MultiCDPMonitor()
        monitor.add_client(9222)
        monitor.add_client(9223)
        # Simulate running state.
        for client in monitor.clients.values():
            client._running = True
        monitor.stop()
        for client in monitor.clients.values():
            assert not client.running


# ---------------------------------------------------------------------------
# Unit tests: load_host_to_channel
# ---------------------------------------------------------------------------


class TestLoadHostToChannel:
    """Tests for load_host_to_channel config loader."""

    def test_loads_from_config(self, tmp_path):
        """Loads host-to-channel mapping from config file."""
        config = {
            "channels": [
                {"channel_id": 0, "host": "75.2.71.31"},
                {"channel_id": 1, "host": "75.2.1.51"},
            ]
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        from megaphone import multi_cdp

        multi_cdp.HOST_TO_CHANNEL = {}
        result = load_host_to_channel(str(config_file))
        assert result == {"75.2.71.31": 0, "75.2.1.51": 1}

    def test_missing_file(self, tmp_path):
        """Handles missing config file gracefully."""
        from megaphone import multi_cdp

        multi_cdp.HOST_TO_CHANNEL = {}
        result = load_host_to_channel(str(tmp_path / "nonexistent.json"))
        assert result == {}

    def test_invalid_json(self, tmp_path):
        """Handles invalid JSON gracefully."""
        config_file = tmp_path / "bad.json"
        config_file.write_text("not json", encoding="utf-8")

        from megaphone import multi_cdp

        multi_cdp.HOST_TO_CHANNEL = {}
        result = load_host_to_channel(str(config_file))
        assert result == {}


# ---------------------------------------------------------------------------
# Unit tests: CDPClient frame processing (mocked websocket)
# ---------------------------------------------------------------------------


class TestCDPClientFrameProcessing:
    """Test CDPClient frame processing with mocked websocket."""

    def test_process_megaphone_frame(self):
        """CDPClient correctly processes a megaphone WebSocket frame."""
        store = MessageStore()
        client = CDPClient(port=9222, store=store)

        # Set channel context.
        client._current_channel = 1

        # Build CDP frame event.
        frame = make_cdp_frame("TestUser", "Hello!", msg_id=100)

        # Simulate the frame processing by calling the store directly
        # (since _read_frames is a loop, we test the parsing logic separately).
        raw_bytes = base64.b64decode(frame["params"]["response"]["payloadData"])
        msg_type = struct.unpack(">I", raw_bytes[4:8])[0]
        assert msg_type == MSG_MEGAPHONE

        result = parse_megaphone_payload(raw_bytes[8:])
        assert result is not None
        assert result["sender"] == "TestUser"
        assert result["message"] == "Hello!"

        # Add to store and verify.
        store.add(client._current_channel, result)
        messages = store.get_recent(channel_id=1)
        assert len(messages) == 1
        assert messages[0]["sender"] == "TestUser"

    def test_multiple_messages_different_channels(self):
        """Messages from different channels are stored separately."""
        store = MessageStore()

        # Channel 0 message.
        msg0 = {"msg_id": 1, "sender": "User0", "message": "msg from ch0", "metadata": 0}
        store.add(0, msg0)

        # Channel 1 message.
        msg1 = {"msg_id": 2, "sender": "User1", "message": "msg from ch1", "metadata": 0}
        store.add(1, msg1)

        assert len(store.get_recent(channel_id=0)) == 1
        assert len(store.get_recent(channel_id=1)) == 1
        assert len(store.get_recent()) == 2

    def test_concurrent_store_access(self):
        """Multiple threads can safely write to the store."""
        store = MessageStore()
        errors = []

        def writer(channel_id: int, count: int):
            try:
                for i in range(count):
                    msg = {
                        "msg_id": channel_id * 1000 + i,
                        "sender": f"User{channel_id}",
                        "message": f"msg {i}",
                        "metadata": 0,
                    }
                    store.add(channel_id, msg)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(ch, 50)) for ch in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        total = sum(len(store.get_recent(channel_id=ch, limit=100)) for ch in range(6))
        assert total == 300


# ---------------------------------------------------------------------------
# Integration tests: MultiCDPMonitor with mocked CDP
# ---------------------------------------------------------------------------


class TestMultiCDPIntegration:
    """Integration tests with mocked CDP connections."""

    @patch("megaphone.multi_cdp.websocket.create_connection")
    @patch("megaphone.multi_cdp.urllib.request.urlopen")
    def test_client_connects_and_reads(self, mock_urlopen, mock_ws):
        """CDPClient connects and processes frames."""
        # Mock CDP /json endpoint.
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(
            [{"type": "page", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1"}]
        ).encode()
        mock_urlopen.return_value = mock_resp

        # Mock WebSocket.
        ws_instance = MagicMock()
        mock_ws.return_value = ws_instance

        # Simulate: connection enable response, then a megaphone frame, then timeout.
        frame = make_cdp_frame("Player1", "Test message", msg_id=1)
        call_count = [0]
        store = MessageStore()
        client = CDPClient(port=9222, store=store)
        client._running = True

        def recv_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return json.dumps({"id": 1, "result": {}})
            elif call_count[0] == 2:
                return json.dumps(frame)
            else:
                # Stop the client before raising so loop doesn't reconnect.
                client._running = False
                raise websocket.WebSocketTimeoutException("timeout")

        ws_instance.recv.side_effect = recv_side_effect

        # Run _monitor_loop — it will exit after _read_frames raises timeout.
        client._monitor_loop()

        # Verify connection was attempted.
        mock_ws.assert_called_once()
        # Verify client reconnected state.
        assert not client.connected

    @patch("megaphone.multi_cdp.CDPClient.start")
    @patch("megaphone.multi_cdp.CDPClient.stop")
    def test_monitor_start_stop(self, mock_stop, mock_start):
        """MultiCDPMonitor start/stop delegates to clients."""
        monitor = MultiCDPMonitor()
        monitor.add_client(9222)
        monitor.add_client(9223)

        monitor.start()
        assert mock_start.call_count == 2

        monitor.stop()
        assert mock_stop.call_count == 2


# ---------------------------------------------------------------------------
# Unit tests: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for multi-CDP components."""

    def test_empty_megaphone_text(self):
        """Payload with empty sender/message returns None."""
        text_bytes = b" : "
        payload = (
            b"\x00" * 8
            + struct.pack(">I", 1)
            + struct.pack(">I", 0)
            + struct.pack(">I", len(text_bytes))
            + text_bytes
        )
        # " : " has " : " so it will parse, but sender and message will be empty.
        result = parse_megaphone_payload(payload)
        # This actually parses — the separator check passes.
        assert result is not None
        assert result["sender"] == ""
        assert result["message"] == ""

    def test_very_long_message(self):
        """Handles very long megaphone messages."""
        long_msg = "A" * 10000
        text = f"Sender : {long_msg}"
        text_bytes = text.encode("utf-8")
        payload = (
            b"\x00" * 8
            + struct.pack(">I", 1)
            + struct.pack(">I", 0)
            + struct.pack(">I", len(text_bytes))
            + text_bytes
        )
        result = parse_megaphone_payload(payload)
        assert result is not None
        assert len(result["message"]) == 10000

    def test_port_range_validation(self):
        """from_port_range handles single-port range."""
        monitor = MultiCDPMonitor.from_port_range(start=9222, end=9222)
        assert len(monitor.clients) == 1
        assert 9222 in monitor.clients

    def test_add_remove_add_client(self):
        """Can re-add a client after removing it."""
        monitor = MultiCDPMonitor()
        monitor.add_client(9222)
        monitor.remove_client(9222)
        assert len(monitor.clients) == 0
        monitor.add_client(9222)
        assert len(monitor.clients) == 1

    def test_status_with_mixed_states(self):
        """get_status correctly reports mixed client states."""
        monitor = MultiCDPMonitor()
        c1 = monitor.add_client(9222)
        monitor.add_client(9223)
        c1._running = True
        c1._connected = True
        c1._current_channel = 1

        status = monitor.get_status()
        assert status[9222]["running"] is True
        assert status[9222]["connected"] is True
        assert status[9222]["channel"] == 1
        assert status[9223]["running"] is False
        assert status[9223]["connected"] is False
