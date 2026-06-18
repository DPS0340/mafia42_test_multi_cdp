"""Multi-client CDP monitor — connect to up to 6 Mafia42 instances simultaneously.

Each game instance runs with its own --remote-debugging-port (9222-9227).
This module manages multiple CDP connections in parallel threads, aggregating
megaphone messages into the shared MessageStore.
"""
from __future__ import annotations

import base64
import json
import logging
import struct
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import websocket

from .config import channel_name
from .protocol import MSG_MEGAPHONE
from .store import MessageStore, store

logger = logging.getLogger("megaphone")

# Default port range for multiple Mafia42 instances.
CDP_PORT_START = 9222
CDP_PORT_END = 9227  # inclusive — 6 instances total
MAX_CLIENTS = 6

# Channel host → channel_id mapping (populated at runtime).
HOST_TO_CHANNEL: dict[str, int] = {}


def load_host_to_channel(config_path: str = "megaphone_config.json") -> dict[str, int]:
    """Build host → channel_id mapping from config file."""
    global HOST_TO_CHANNEL
    try:
        with open(config_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        for ch in cfg.get("channels", []):
            HOST_TO_CHANNEL[ch["host"]] = ch["channel_id"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        logger.warning("Could not load channel config: %s", exc)
    return HOST_TO_CHANNEL


def ws_url_to_channel_id(url: str) -> int:
    """Extract channel_id from WS URL like ws://75.2.1.51:53421/"""
    try:
        host = url.split("//")[1].split(":")[0]
        return HOST_TO_CHANNEL.get(host, 0)
    except (IndexError, AttributeError):
        return 0


@dataclass
class CDPClient:
    """A single CDP connection to one Mafia42 game instance."""

    port: int
    store: MessageStore
    name: str = ""
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _running: bool = field(default=False, init=False)
    _connected: bool = field(default=False, init=False)
    _current_channel: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"CDP:{self.port}"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def current_channel(self) -> int:
        return self._current_channel

    def start(self) -> None:
        """Start monitoring in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name=self.name
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the monitor loop to stop."""
        self._running = False

    def _connect(self) -> Optional[websocket.WebSocket]:
        """Connect to CDP and return the WebSocket, or None on failure."""
        try:
            url = f"http://127.0.0.1:{self.port}/json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                targets = json.loads(resp.read().decode())
            cdp_url = next(
                t["webSocketDebuggerUrl"]
                for t in targets
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
            )
            cdp = websocket.create_connection(cdp_url, timeout=300)
            cdp.send(json.dumps({"id": 1, "method": "Network.enable"}))
            cdp.settimeout(60)
            self._connected = True
            logger.info("[%s] Connected to CDP on port %d", self.name, self.port)
            return cdp
        except Exception as exc:
            self._connected = False
            logger.warning("[%s] CDP connection failed on port %d: %s", self.name, self.port, exc)
            return None

    def _monitor_loop(self) -> None:
        """Main monitoring loop — reconnects on failure."""
        while self._running:
            cdp = self._connect()
            if not cdp:
                time.sleep(5)
                continue

            try:
                self._read_frames(cdp)
            except websocket.WebSocketTimeoutException:
                logger.info("[%s] Timeout, reconnecting...", self.name)
            except Exception as exc:
                logger.warning("[%s] Error: %s, retrying in 5s...", self.name, exc)
                time.sleep(5)
            finally:
                self._connected = False
                try:
                    cdp.close()
                except Exception:
                    pass

    def _read_frames(self, cdp: websocket.WebSocket) -> None:
        """Read and process CDP frames from a connected WebSocket."""
        while self._running:
            raw = cdp.recv()
            msg = json.loads(raw)
            method = msg.get("method", "")
            params = msg.get("params", {})

            # Track channel switches.
            if method == "Network.webSocketCreated":
                url = params.get("url", "")
                if "53421" in url:
                    self._current_channel = ws_url_to_channel_id(url)
                    self.store.set_status(self._current_channel, "connected")
                    logger.info(
                        "[%s] Channel %d connected: %s",
                        self.name, self._current_channel, url[:60],
                    )

            # Process received WebSocket frames.
            if method == "Network.webSocketFrameReceived":
                pd = params.get("response", {}).get("payloadData", "")
                if not pd or len(pd) <= 20:
                    continue
                try:
                    raw_bytes = base64.b64decode(pd)
                    if len(raw_bytes) < 8:
                        continue
                    msg_type = struct.unpack(">I", raw_bytes[4:8])[0]
                    if msg_type == MSG_MEGAPHONE:
                        m = parse_megaphone_payload(raw_bytes[8:])
                        if m:
                            self.store.add(self._current_channel, m)
                            logger.info(
                                "[%s] ch%d %s: %s",
                                self.name, self._current_channel,
                                m["sender"], m["message"][:60],
                            )
                except Exception:
                    pass


def parse_megaphone_payload(payload: bytes) -> Optional[dict]:
    """Parse megaphone payload: [..8B..][4B msg_id][4B metadata][4B text_len][UTF-8 text]"""
    if len(payload) < 20:
        return None
    msg_id = struct.unpack(">I", payload[8:12])[0]
    metadata = struct.unpack(">I", payload[12:16])[0]
    text_len = struct.unpack(">I", payload[16:20])[0]
    text = payload[20 : 20 + text_len].decode("utf-8", errors="replace")
    if " : " not in text:
        return None
    sender, message = text.split(" : ", 1)
    return {
        "msg_id": msg_id,
        "sender": sender,
        "message": message,
        "metadata": metadata,
    }


@dataclass
class MultiCDPMonitor:
    """Manages multiple CDP client connections to Mafia42 game instances.

    Supports up to MAX_CLIENTS (6) simultaneous connections, each on a
    different CDP port. Messages from all clients are aggregated into
    the shared MessageStore.
    """

    store: MessageStore = field(default_factory=lambda: store)
    clients: dict[int, CDPClient] = field(default_factory=dict)
    _started: bool = False

    def add_client(self, port: int, name: str = "") -> CDPClient:
        """Add a CDP client for the given port. Returns existing client if already added."""
        if port in self.clients:
            return self.clients[port]
        if len(self.clients) >= MAX_CLIENTS:
            raise ValueError(
                f"Maximum {MAX_CLIENTS} clients reached. "
                f"Current ports: {sorted(self.clients.keys())}"
            )
        client = CDPClient(port=port, store=self.store, name=name)
        self.clients[port] = client
        return client

    def remove_client(self, port: int) -> None:
        """Stop and remove a CDP client."""
        client = self.clients.pop(port, None)
        if client:
            client.stop()

    def start(self) -> None:
        """Start all registered CDP clients."""
        for client in self.clients.values():
            client.start()
        self._started = True

    def stop(self) -> None:
        """Stop all CDP clients."""
        for client in self.clients.values():
            client.stop()
        self._started = False

    def get_status(self) -> dict[int, dict]:
        """Return status of all clients."""
        return {
            port: {
                "running": client.running,
                "connected": client.connected,
                "channel": client.current_channel,
                "channel_name": channel_name(client.current_channel),
            }
            for port, client in self.clients.items()
        }

    @classmethod
    def from_port_range(
        cls,
        start: int = CDP_PORT_START,
        end: int = CDP_PORT_END,
        msg_store: Optional[MessageStore] = None,
    ) -> "MultiCDPMonitor":
        """Create a monitor with clients for a range of ports [start, end] inclusive."""
        monitor = cls(store=msg_store or store)
        for port in range(start, end + 1):
            monitor.add_client(port, name=f"CDP:{port}")
        return monitor
