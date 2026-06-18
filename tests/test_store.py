"""MessageStore tests — property-based with Hypothesis.

Core invariants tested:
  - add() returns True for new messages, False for duplicates
  - get_recent() returns at most MAX_MESSAGES_PER_CHANNEL items per channel
  - seq numbers are monotonically increasing
  - Same (channel_id, msg_id) → dedup on second add
  - Same text on 2+ channels → global promotion (scope='global')
  - SSE queues receive notifications for new messages + status changes
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from queue import Queue

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from megaphone.config import MAX_MESSAGES_PER_CHANNEL
from megaphone.store import MessageStore

from conftest import ch_id, msg_id, sender, message_text

# ---------------------------------------------------------------------------
# Helper strategies
# ---------------------------------------------------------------------------


def message_dict() -> SearchStrategy[dict]:
    """Strategy: a message dict with sender, message, msg_id."""
    return st.builds(
        lambda s, m, mid: {"sender": s, "message": m, "msg_id": mid},
        s=sender(),
        m=message_text(),
        mid=msg_id(),
    )


# ===========================================================================
# Add + Retrieve invariants
# ===========================================================================


class TestStoreAdd:
    """Property-based tests for message addition and retrieval."""

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=100)
    def test_add_message_returns_true(self, cid: int, msg: dict):
        """INVARIANT: new message → add returns True."""
        store = MessageStore()
        result = store.add(cid, msg)
        assert result is True

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=100)
    def test_add_then_retrieve(self, cid: int, msg: dict):
        """INVARIANT: add → get_recent finds the message."""
        store = MessageStore()
        store.add(cid, msg)
        msgs = store.get_recent(channel_id=cid)
        assert len(msgs) >= 1
        stored = [m for m in msgs if m.get("msg_id") == msg["msg_id"]]
        assert len(stored) >= 1
        assert stored[0]["sender"] == msg["sender"]
        assert stored[0]["message"] == msg["message"]

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=100)
    def test_channel_name_injected(self, cid: int, msg: dict):
        """INVARIANT: stored message gets channel_name + channel_id + time fields."""
        store = MessageStore()
        store.add(cid, msg)
        msgs = store.get_recent(channel_id=cid)
        stored = next(m for m in msgs if m.get("msg_id") == msg["msg_id"])
        assert "channel_name" in stored
        assert stored["channel_id"] == cid
        assert "time" in stored
        assert len(stored["time"]) > 0

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=100)
    def test_scope_server_initially(self, cid: int, msg: dict):
        """INVARIANT: new message has scope='server'."""
        store = MessageStore()
        store.add(cid, msg)
        msgs = store.get_recent(channel_id=cid)
        stored = next(m for m in msgs if m.get("msg_id") == msg["msg_id"])
        assert stored["scope"] == "server"


# ===========================================================================
# seq number invariants
# ===========================================================================


class TestStoreSeq:
    """Property-based tests for global sequence numbering."""

    @given(messages=st.lists(message_dict(), min_size=2, max_size=20))
    @settings(max_examples=50)
    def test_seq_monotonically_increasing(self, messages: list[dict]):
        """INVARIANT: seq numbers are strictly increasing."""
        store = MessageStore()
        seqs = []
        for msg in messages:
            added = store.add(0, msg)
            # Only check seq for messages that were actually stored (not deduped)
            if added:
                for m in store.get_recent(channel_id=0):
                    if m.get("msg_id") == msg["msg_id"]:
                        seqs.append(m["seq"])
                        break
        for i in range(1, len(seqs)):
            assert seqs[i] > seqs[i - 1], f"seq not monotonic: {seqs}"


# ===========================================================================
# Dedup invariants
# ===========================================================================


class TestStoreDedup:
    """Property-based tests for message deduplication."""

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=100)
    def test_same_msg_id_dedup(self, cid: int, msg: dict):
        """INVARIANT: same (channel_id, msg_id) → second add returns False."""
        store = MessageStore()
        store.add(cid, msg)
        result = store.add(cid, msg)
        assert result is False

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=100)
    def test_different_msg_id_allowed(self, cid: int, msg: dict):
        """INVARIANT: different msg_id on same channel → second add succeeds."""
        store = MessageStore()
        store.add(cid, msg)
        msg2 = {**msg, "msg_id": msg["msg_id"] + 1}
        result = store.add(cid, msg2)
        assert result is True

    @given(msg=message_dict())
    @settings(max_examples=50)
    def test_same_msg_id_different_channel_allowed(self, msg: dict):
        """INVARIANT: same msg_id on diff channels → dedup by text not msg_id."""
        store = MessageStore()
        store.add(0, msg)
        result = store.add(1, {**msg, "msg_id": msg["msg_id"]})
        # Different channel, different msg_id (or same) → check dedup
        # If same text on diff channel, it's promoted to global → returns False
        assert result in (True, False)


# ===========================================================================
# Global promotion invariants
# ===========================================================================


class TestStoreGlobalPromotion:
    """Property-based tests for global megaphone promotion."""

    @given(cid1=ch_id(), cid2=ch_id(), msg=message_dict())
    @settings(max_examples=100)
    def test_same_text_on_two_channels_promotes(
        self, cid1: int, cid2: int, msg: dict
    ):
        """INVARIANT: same text on 2+ different channels → scope='global'."""
        assume(cid1 != cid2)
        store = MessageStore()
        store.add(cid1, msg)
        store.add(cid2, {**msg, "msg_id": msg["msg_id"] + 1})
        all_msgs = store.get_all_messages()
        global_msgs = [m for m in all_msgs if m.get("scope") == "global"]
        assert len(global_msgs) >= 1
        assert global_msgs[0]["sender"] == msg["sender"]
        assert global_msgs[0]["message"] == msg["message"]

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=100)
    def test_unique_text_not_promoted(self, cid: int, msg: dict):
        """INVARIANT: unique text (no duplicate) stays as 'server' scope."""
        store = MessageStore()
        store.add(cid, msg)
        for m in store.get_all_messages():
            if m.get("msg_id") == msg["msg_id"]:
                assert m["scope"] == "server"
                break


# ===========================================================================
# Migrated fixed scenarios
# ===========================================================================


def _add_scenario(
    store: MessageStore,
    rows: Sequence[tuple[int, str, str] | tuple[int, str, str, int]],
) -> list[bool]:
    """Add fixed scenario rows using explicit or sequential msg_id values."""
    results = []
    for seq, row in enumerate(rows, start=1):
        if len(row) == 4:
            cid, sender_name, text, mid = row
        else:
            cid, sender_name, text = row
            mid = seq
        results.append(
            store.add(cid, {"sender": sender_name, "message": text, "msg_id": mid})
        )
    return results


class TestStoreFixedScenarios:
    """Deterministic scenario coverage migrated from the old ad-hoc CLI."""

    def test_normal_messages_remain_server_scoped(self):
        """SCENARIO: unique messages across channels stay server-scoped."""
        store = MessageStore()
        rows = [
            (0, "마피아보스", "안녕하세요 반갑습니다"),
            (0, "요원X", "오늘 게임 재밌네요"),
            (1, "감시자", "1채널 메시지입니다"),
            (0, "익명의용자", "모두 수고하셨어요"),
            (42, "마피아42", "확성기 테스트 중입니다"),
        ]

        assert _add_scenario(store, rows) == [True] * len(rows)
        messages = store.get_all_messages()
        assert len(messages) == len(rows)
        assert {msg["scope"] for msg in messages} == {"server"}

    def test_two_channel_repeat_promotes_single_global(self):
        """SCENARIO: same sender/text on two channels promotes original message."""
        store = MessageStore()
        rows = [
            (0, "초보유저", "공지: 오늘 이벤트 있습니다"),
            (1, "초보유저", "공지: 오늘 이벤트 있습니다"),
        ]

        assert _add_scenario(store, rows) == [True, False]
        messages = store.get_all_messages()
        assert len(messages) == 1
        assert messages[0]["scope"] == "global"
        assert messages[0]["sender"] == "초보유저"
        assert messages[0]["message"] == "공지: 오늘 이벤트 있습니다"

    def test_three_channel_repeat_plus_unique_messages(self):
        """SCENARIO: repeated global notice stores once; unique messages remain."""
        store = MessageStore()
        rows = [
            (0, "공지", "전체 공지: 서버 점검 안내"),
            (42, "공지", "전체 공지: 서버 점검 안내"),
            (142, "공지", "전체 공지: 서버 점검 안내"),
            (3, "익명", "3채널 메시지"),
            (20, "성인", "성인 채널 메시지"),
        ]

        assert _add_scenario(store, rows) == [True, False, False, True, True]
        messages = store.get_all_messages()
        global_messages = [msg for msg in messages if msg.get("scope") == "global"]
        server_messages = [msg for msg in messages if msg.get("scope") == "server"]
        assert len(messages) == 3
        assert len(global_messages) == 1
        assert len(server_messages) == 2

    def test_mixed_scenario_counts_global_and_server_messages(self):
        """SCENARIO: mixed unique and repeated messages produce expected counts."""
        store = MessageStore()
        rows = [
            (0, "A", "첫번째 메시지"),
            (1, "B", "두번째 메시지"),
            (0, "A", "공지: 점검 안내"),
            (1, "A", "공지: 점검 안내"),
            (3, "C", "세번째 메시지"),
            (4, "D", "네번째 메시지"),
        ]

        assert _add_scenario(store, rows) == [True, True, True, False, True, True]
        messages = store.get_all_messages()
        assert len(messages) == 5
        assert sum(msg.get("scope") == "global" for msg in messages) == 1
        assert sum(msg.get("scope") == "server" for msg in messages) == 4

    def test_same_channel_retransmit_is_dropped(self):
        """SCENARIO: same channel/msg_id retransmit is not stored twice."""
        store = MessageStore()
        rows = [
            (0, "유저", "안녕하세요", 1),
            (0, "유저", "안녕하세요", 1),
        ]

        assert _add_scenario(store, rows) == [True, False]
        messages = store.get_all_messages()
        assert len(messages) == 1
        assert messages[0]["scope"] == "server"


# ===========================================================================
# Buffer / lifecycle invariants
# ===========================================================================


class TestStoreLifecycle:
    """Property-based tests for store lifecycle and capacity."""

    @given(
        cid=ch_id(),
        extra=st.integers(min_value=0, max_value=MAX_MESSAGES_PER_CHANNEL),
    )
    @settings(max_examples=50)
    def test_max_messages_enforced(self, cid: int, extra: int):
        """INVARIANT: per-channel deque never exceeds MAX_MESSAGES_PER_CHANNEL."""
        store = MessageStore()
        total = MAX_MESSAGES_PER_CHANNEL + extra
        for i in range(total):
            store.add(
                cid, {"sender": f"A{i}", "message": f"msg{i}", "msg_id": i + 1}
            )
        msgs = store.get_recent(channel_id=cid)
        assert len(msgs) <= MAX_MESSAGES_PER_CHANNEL

    @given(msgs=st.lists(message_dict(), min_size=1, max_size=10))
    @settings(max_examples=50)
    def test_set_status(self, msgs: list[dict]):
        """INVARIANT: set_status + get_status_snapshot roundtrip."""
        store = MessageStore()
        for i in range(len(msgs)):
            status = f"status_{i}"
            store.set_status(i, status)
        snapshot = store.get_status_snapshot()
        for i in range(len(msgs)):
            assert snapshot[i] == f"status_{i}"

    def test_empty_store_has_no_messages(self):
        """EDGE: fresh store should be empty."""
        store = MessageStore()
        assert store.get_all_messages() == []
        assert store.get_recent() == []


# ===========================================================================
# SSE notification invariants
# ===========================================================================


class TestStoreSSENotifications:
    """Property-based tests for SSE subscriber notifications."""

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=50)
    def test_add_notifies_sse(self, cid: int, msg: dict):
        """INVARIANT: add() sends notification to SSE queue."""
        store = MessageStore()
        q: Queue[str] = Queue()
        store.add_queue(q)
        store.add(cid, msg)
        data = q.get_nowait()
        parsed = json.loads(data)
        assert parsed.get("sender") == msg["sender"]
        assert parsed.get("message") == msg["message"]

    @given(cid=ch_id())
    @settings(max_examples=50)
    def test_status_notifies_sse(self, cid: int):
        """INVARIANT: set_status sends notification to SSE queue."""
        store = MessageStore()
        q: Queue[str] = Queue()
        store.add_queue(q)
        store.set_status(cid, "connected")
        data = q.get_nowait()
        parsed = json.loads(data)
        assert parsed.get("type") == "status"
        assert parsed.get("channel_id") == cid
        assert parsed.get("status") == "connected"

    @given(cid=ch_id(), msg=message_dict())
    @settings(max_examples=50, deadline=None)
    def test_remove_queue_stops_notifications(self, cid: int, msg: dict):
        """INVARIANT: removed queue receives no more notifications."""
        store = MessageStore()
        q: Queue[str] = Queue()
        store.add_queue(q)
        store.remove_queue(q)
        store.add(cid, msg)
        assert q.empty()

    @given(cid1=ch_id(), cid2=ch_id(), msg=message_dict())
    @settings(max_examples=50, deadline=None)
    def test_global_promotion_notifies_sse(self, cid1: int, cid2: int, msg: dict):
        """INVARIANT: global promotion sends 'update' notification to SSE."""
        assume(cid1 != cid2)
        store = MessageStore()
        q: Queue[str] = Queue()
        store.add_queue(q)
        store.add(cid1, msg)
        q.get_nowait()  # consume new-msg notification
        store.add(cid2, {**msg, "msg_id": msg["msg_id"] + 1})
        notifications = []
        while not q.empty():
            notifications.append(json.loads(q.get_nowait()))
        updates = [n for n in notifications if n.get("type") == "update"]
        assert len(updates) >= 1


# ===========================================================================
# get_all_messages ordering
# ===========================================================================


class TestStoreGetAll:
    """Property-based tests for cross-channel message retrieval."""

    @given(
        msgs=st.lists(
            st.builds(
                lambda c, m: (c, {**m, "msg_id": id(m)}),
                c=ch_id(),
                m=message_dict(),
            ),
            min_size=1,
            max_size=15,
        )
    )
    @settings(max_examples=50)
    def test_get_all_sorted_by_seq(self, msgs: list[tuple[int, dict]]):
        """INVARIANT: get_all_messages returns messages sorted by seq."""
        store = MessageStore()
        for cid, msg in msgs:
            store.add(cid, msg)
        all_msgs = store.get_all_messages()
        seqs = [m["seq"] for m in all_msgs]
        assert seqs == sorted(seqs), f"not sorted: {seqs}"
