"""채널별 WebSocket 연결 스레드.

각 채널 서버에 접속해 핸드셰이크/인증/초기화를 수행하고,
확성기 패킷을 수신해 공유 스토어에 적재한다. 연결이 끊기면 재시도한다.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import websocket

from .config import (
    HEARTBEAT_INTERVAL,
    RECONNECT_MAX,
    RECONNECT_MIN,
    WS_PORT,
    channel_name,
)
from .protocol import (
    MSG_AUTH,
    MSG_AUTH_DENIED,
    MSG_AUTH_OK,
    MSG_HEARTBEAT,
    MSG_INIT_DATA,
    MSG_INIT_REPLY,
    MSG_MEGAPHONE,
    MSG_PING_REPLY,
    MSG_SERVER_PING,
    make_packet,
    parse_packet,
    parse_megaphone,
)
from .store import store

logger = logging.getLogger("megaphone")

# Game's post-auth initialization sequence (captured from traffic, meaning unknown).
INIT_SEQUENCE: list[tuple[int, bytes]] = [
    (454, b""),
    (579, b"DAILY_AD"),
    (579, b"DAILY_AD"),
    (19, b""),
    (166, b""),
    (336, b""),
    (457, b""),
    (40, b""),
    (516, b"0"),
    (148, b""),
    (421, b""),
    (24, b""),
    (445, b""),
    (421, b"202513"),
    (24, b""),
    (19, b""),
    (7, b"1"),
    (181, b""),
    (558, b"202513"),
]


class ChannelConnection(threading.Thread):
    """Per-channel WebSocket connection with auto-reconnect."""

    def __init__(self, channel_id: int, host: str, auth_token: str) -> None:
        super().__init__(daemon=True)
        self.channel_id = channel_id
        self.host = host
        self.auth_token = auth_token
        self.ws: Optional[websocket.WebSocket] = None
        self.running: bool = True
        self.name_str = channel_name(channel_id)
        # Diagnostics
        self.connected_at: Optional[float] = None
        self.last_recv_type: Optional[int] = None
        self.unhandled_types: set[int] = set()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def send_pkt(self, msg_type: int, payload: bytes = b"") -> None:
        """Send a binary packet through the WebSocket."""
        if self.ws is None:
            raise RuntimeError("WebSocket not connected")
        self.ws.send(
            make_packet(msg_type, payload), opcode=websocket.ABNF.OPCODE_BINARY
        )

    def recv_until(
        self, target_type: int, max_tries: int = 30
    ) -> tuple[Optional[int], Optional[bytes]]:
        """Receive frames until target_type (or auth denied) arrives.

        Returns (type, payload) or (None, None) on timeout.
        """
        for _ in range(max_tries):
            try:
                data = self.ws.recv()  # type: ignore[union-attr]
                if isinstance(data, str):
                    data = data.encode("latin-1")
                _, t, p = parse_packet(data)
                if t == target_type or t in MSG_AUTH_DENIED:
                    return t, p
            except websocket.WebSocketTimeoutException:
                continue
        return None, None

    def run(self) -> None:
        """Main loop: connect, authenticate, listen, reconnect on failure."""
        backoff = RECONNECT_MIN
        while self.running:
            self.connected_at = None
            try:
                self._connect_and_listen()
                backoff = RECONNECT_MIN  # Reset backoff after successful connect.
            except Exception as exc:
                self._log_disconnect(exc)
            # If running is False, this was an intentional stop (e.g. denied).
            if not self.running:
                break
            store.set_status(self.channel_id, "disconnected")
            logger.info("[%s] %ds until reconnect...", self.name_str, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log_disconnect(self, err: Exception) -> None:
        """Log disconnect diagnostics (hold time is key for diagnosis)."""
        if self.connected_at is not None:
            held = time.time() - self.connected_at
            unhandled = sorted(self.unhandled_types)
            logger.warning(
                "[%s] Disconnect: %s  (held %.1fs, last recv type=%s, "
                "unhandled types=%s)",
                self.name_str,
                err,
                held,
                self.last_recv_type,
                unhandled,
            )
        else:
            logger.warning("[%s] Handshake error: %s", self.name_str, err)

    def _connect_and_listen(self) -> None:
        """Full connection lifecycle: handshake → auth → init → listen."""
        url = f"ws://{self.host}:{WS_PORT}/"
        store.set_status(self.channel_id, "connecting")

        self.ws = websocket.create_connection(
            url, timeout=15,
            header={"Origin": f"http://{self.host}:{WS_PORT}"},
        )
        assert self.ws is not None  # Pyright: narrow Optional.
        self.ws.settimeout(10)

        # Phase 1+2: Hello + Auth burst (game server expects tight packet group).
        logger.debug("[%s] Sending Hello + Auth burst...", self.name_str)
        self.send_pkt(8)
        self.send_pkt(578)
        self.send_pkt(MSG_AUTH, self.auth_token.encode("ascii"))

        # Drain until we see AUTH_OK (or AUTH_DENIED). Hello ack (type 4)
        # and other packets arrive first but we skip them.
        t, _ = self.recv_until(MSG_AUTH_OK)
        if t in MSG_AUTH_DENIED:
            logger.warning("[%s] Connection denied (level restriction?)", self.name_str)
            store.set_status(self.channel_id, "denied")
            assert self.ws is not None  # Pyright: narrow Optional.
            self.ws.close()
            self.running = False
            return
        if t is None:
            raise ConnectionError("No auth response")

        # Phase 3: Post-auth.
        logger.debug("[%s] Post-auth handshake...", self.name_str)
        self.send_pkt(1)
        self.send_pkt(1)
        self.recv_until(MSG_INIT_DATA, max_tries=15)

        # Phase 4: Init sequence playback.
        logger.debug("[%s] Playing init sequence...", self.name_str)
        for msg_type, payload in INIT_SEQUENCE:
            self.send_pkt(msg_type, payload)

        store.set_status(self.channel_id, "connected")
        self.connected_at = time.time()
        logger.info("[%s] Connected!", self.name_str)

        # Phase 5: Listen for megaphone messages.
        self._listen()

    def _send_heartbeat(self) -> None:
        """Send a heartbeat packet with current timestamp."""
        self.send_pkt(
            MSG_HEARTBEAT, str(int(time.time() * 1000)).encode("ascii")
        )

    def _listen(self) -> None:
        """Listen loop: dispatch packets, send heartbeats, track unhandled types."""
        self.ws.settimeout(1)  # Allow periodic heartbeat check.
        self._send_heartbeat()  # Immediate first heartbeat (prevent keepalive timeout).
        last_hb = time.time()

        while self.running:
            try:
                data = self.ws.recv()
                if isinstance(data, str):
                    data = data.encode("latin-1")
                _, msg_type, payload = parse_packet(data)
                if msg_type is None:
                    continue
                self.last_recv_type = msg_type

                if msg_type == MSG_MEGAPHONE:
                    msg = parse_megaphone(payload)
                    if msg and store.add(self.channel_id, msg):
                        logger.info(
                            "[%s] %s: %s",
                            self.name_str,
                            msg["sender"],
                            msg["message"][:50],
                        )
                elif msg_type == MSG_SERVER_PING:
                    self.send_pkt(MSG_PING_REPLY)
                elif msg_type == MSG_INIT_DATA:
                    self.send_pkt(MSG_INIT_REPLY)
                else:
                    self.unhandled_types.add(msg_type)
                    logger.debug(
                        "[%s] Unhandled type %d (total: %d)",
                        self.name_str,
                        msg_type,
                        len(self.unhandled_types),
                    )

            except websocket.WebSocketTimeoutException:
                pass  # Heartbeat check interval.

            # Periodic heartbeat.
            if time.time() - last_hb > HEARTBEAT_INTERVAL:
                self._send_heartbeat()
                last_hb = time.time()
