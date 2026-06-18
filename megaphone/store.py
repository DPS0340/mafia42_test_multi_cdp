"""수신 메시지 보관 + SSE 구독자 관리 (스레드 안전).

메인 모듈에서 로깅과 상수를 config.py에서 import하므로,
여기서는 store만 모듈 레벨 싱글톤으로 노출한다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Optional

from .config import (
    GLOBAL_DEDUP_WINDOW,
    MAX_MESSAGES_PER_CHANNEL,
    MAX_PERSISTED,
    MSG_ID_TTL,
    PERSIST_FILE,
    PERSIST_INTERVAL,
    channel_name,
)

logger = logging.getLogger("megaphone")


class MessageStore:
    """Thread-safe store for received megaphone messages and SSE subscribers."""

    def __init__(self) -> None:
        self.messages: dict[int, deque[dict]] = defaultdict(
            lambda: deque(maxlen=MAX_MESSAGES_PER_CHANNEL)
        )
        self.sse_queues: list[Queue[str]] = []
        self.lock = threading.Lock()
        self.channel_status: dict[int, str] = {}
        self.channel_population: dict[int, int] = {}  # channel_id -> 접속 인원수 (channel_ko.php 폴링)
        self._seq: int = 0  # Global monotonically increasing sequence.
        # (sender, message) -> {'msg': dict, 'channels': set, 'expire': float, 'is_global': bool}
        self._dedup: dict[tuple[str, str], dict] = {}
        # (channel_id, msg_id) -> expire  (same-channel retransmit blocker)
        self._seen_ids: dict[tuple[int, int], float] = {}
        # Persistence file path
        self._persist_path = Path(PERSIST_FILE)
        # Last disk-write timestamp (for save throttling).
        self._last_save: float = 0.0
        # Load persisted messages on startup
        self._load_persisted()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, channel_id: int, msg: dict) -> bool:
        """Add a megaphone message.  Deduplicates retransmits and global repeats.

        Returns True if the message was stored/pushed, False if it was a duplicate.
        """
        now = time.time()
        msg["channel_id"] = channel_id
        msg["channel_name"] = channel_name(channel_id)
        msg["time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        id_key: tuple[int, int] = (channel_id, msg.get("msg_id", 0))
        text_key: tuple[str, str] = (msg.get("sender", ""), msg.get("message", ""))

        notify_new: Optional[dict] = None
        notify_update: Optional[dict] = None

        with self.lock:
            self._prune(now)

            # 1st pass: same-channel retransmit (same msg_id) → drop.
            if id_key in self._seen_ids:
                self._seen_ids[id_key] = now + MSG_ID_TTL
                return False
            self._seen_ids[id_key] = now + MSG_ID_TTL

            # 2nd pass: identical text on a different channel → global.
            rec = self._dedup.get(text_key)
            if rec is not None and channel_id not in rec["channels"]:
                rec["channels"].add(channel_id)
                rec["expire"] = now + GLOBAL_DEDUP_WINDOW
                if not rec["is_global"]:
                    rec["is_global"] = True
                    rec["msg"]["scope"] = "global"
                    notify_update = {
                        "type": "update",
                        "seq": rec["msg"]["seq"],
                        "scope": "global",
                    }
                    logger.debug(
                        "Global promotion: %s on %s (now on %s channels)",
                        rec["msg"].get("sender"),
                        rec["msg"]["channel_name"],
                        len(rec["channels"]),
                    )
            else:
                # New message — store and push.
                self._seq += 1
                msg["seq"] = self._seq
                msg["scope"] = "server"
                self.messages[channel_id].append(msg)
                self._dedup[text_key] = {
                    "msg": msg,
                    "channels": {channel_id},
                    "expire": now + GLOBAL_DEDUP_WINDOW,
                    "is_global": False,
                }
                notify_new = msg

        if notify_new is not None:
            self._notify(notify_new)
            # Throttle disk writes: rewriting the whole file on every message is
            # O(n) per message. Save at most once per PERSIST_INTERVAL; flush()
            # on shutdown persists the tail.
            if now - self._last_save >= PERSIST_INTERVAL:
                self._save_persisted()
                self._last_save = now
            return True
        if notify_update is not None:
            self._notify(notify_update)
        return False

    def set_status(self, channel_id: int, status: str) -> None:
        """Update the connection status for a channel and notify SSE subscribers."""
        with self.lock:
            self.channel_status[channel_id] = status
        self._notify(
            {
                "type": "status",
                "channel_id": channel_id,
                "channel_name": channel_name(channel_id),
                "status": status,
            }
        )

    def get_status_snapshot(self) -> dict[int, str]:
        """Return a copy of the current channel status dict."""
        with self.lock:
            return dict(self.channel_status)

    def set_populations(self, pops: dict[int, int]) -> None:
        """{channel_id: 인원수} 갱신. 값이 바뀐 채널만 SSE로 통지."""
        changed: dict[int, int] = {}
        with self.lock:
            for cid, n in pops.items():
                if self.channel_population.get(cid) != n:
                    self.channel_population[cid] = n
                    changed[cid] = n
        for cid, n in changed.items():
            self._notify({
                "type": "population",
                "channel_id": cid,
                "channel_name": channel_name(cid),
                "population": n,
            })

    def get_population_snapshot(self) -> dict[int, int]:
        """Return a copy of the current channel population dict."""
        with self.lock:
            return dict(self.channel_population)

    def add_queue(self, q: Queue[str]) -> None:
        """Register an SSE subscriber queue."""
        with self.lock:
            self.sse_queues.append(q)

    def remove_queue(self, q: Queue[str]) -> None:
        """Unregister an SSE subscriber queue."""
        with self.lock:
            try:
                self.sse_queues.remove(q)
            except ValueError:
                pass

    def get_recent(
        self, channel_id: Optional[int] = None, limit: int = 50
    ) -> list[dict]:
        """Return the most recent messages, sorted by global sequence.

        If *channel_id* is given, return only messages from that channel.
        """
        with self.lock:
            if channel_id is not None:
                return list(self.messages[channel_id])[-limit:]
            result: list[dict] = []
            for msgs in self.messages.values():
                result.extend(msgs)
            # Sort by global sequence (time strings flip at midnight).
            result.sort(key=lambda m: m.get("seq", 0))
            return result[-limit:]

    def get_all_messages(self) -> list[dict]:
        """Return ALL stored messages sorted by sequence (no limit)."""
        with self.lock:
            result: list[dict] = []
            for msgs in self.messages.values():
                result.extend(msgs)
            result.sort(key=lambda m: m.get("seq", 0))
            return result

    def flush(self) -> None:
        """Force-write any pending messages to disk. Call on shutdown."""
        self._save_persisted()
        self._last_save = time.time()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune(self, now: float) -> None:
        """Remove expired dedup entries."""
        for k in [k for k, r in self._dedup.items() if r["expire"] <= now]:
            del self._dedup[k]
        for k in [k for k, e in self._seen_ids.items() if e <= now]:
            del self._seen_ids[k]

    def _notify(self, data: dict) -> None:
        """Broadcast *data* to all SSE subscriber queues."""
        text = json.dumps(data, ensure_ascii=False)
        with self.lock:
            queues = list(self.sse_queues)
        for q in queues:
            try:
                q.put_nowait(text)
            except Exception:
                pass  # Client disconnected; the queue will be removed.

    # ------------------------------------------------------------------
    # Persistence (JSON file)
    # ------------------------------------------------------------------

    def _load_persisted(self) -> None:
        """Load messages from JSON file on startup."""
        if not self._persist_path.exists():
            logger.info("No persist file found at %s", self._persist_path)
            return

        try:
            with self._persist_path.open(encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict) or "messages" not in data:
                logger.warning("Invalid persist file format, ignoring")
                return

            messages = data.get("messages", [])
            if not isinstance(messages, list):
                logger.warning("Invalid messages format in persist file")
                return

            loaded_count = 0
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                channel_id = msg.get("channel_id")
                if channel_id is None:
                    continue

                # Restore message to store
                self.messages[channel_id].append(msg)
                loaded_count += 1

                # Update sequence counter
                seq = msg.get("seq", 0)
                if seq > self._seq:
                    self._seq = seq

            logger.info("Loaded %d messages from %s", loaded_count, self._persist_path)

        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load persist file: %s", exc)

    def _save_persisted(self) -> None:
        """Save messages to JSON file."""
        try:
            # Collect all messages
            all_messages = []
            for channel_msgs in self.messages.values():
                all_messages.extend(channel_msgs)

            # Sort by sequence
            all_messages.sort(key=lambda m: m.get("seq", 0))

            # Apply max persisted limit
            if MAX_PERSISTED > 0 and len(all_messages) > MAX_PERSISTED:
                all_messages = all_messages[-MAX_PERSISTED:]

            # Write to file
            data = {
                "messages": all_messages,
                "saved_at": datetime.now().isoformat(),
                "count": len(all_messages),
            }

            with self._persist_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.debug("Saved %d messages to %s", len(all_messages), self._persist_path)

        except OSError as exc:
            logger.warning("Failed to save persist file: %s", exc)


# Process-global singleton (shared by channel threads and web server).
store = MessageStore()
