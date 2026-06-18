"""Store prune and expiry edge-case tests."""
from __future__ import annotations

import time

from hypothesis import given, settings
from hypothesis import strategies as st

from megaphone.config import MSG_ID_TTL, GLOBAL_DEDUP_WINDOW
from megaphone.store import MessageStore


class TestPruneExpiry:
    """Test _prune removes expired entries correctly."""

    def test_prune_removes_expired_seen_ids(self):
        """INVARIANT: expired (channel_id, msg_id) entries are removed."""
        store = MessageStore()
        # Manually add an expired entry
        store._seen_ids[(0, 1)] = time.time() - MSG_ID_TTL - 10
        store._seen_ids[(1, 2)] = time.time() + MSG_ID_TTL  # not expired

        store._prune(time.time())

        assert (0, 1) not in store._seen_ids
        assert (1, 2) in store._seen_ids

    def test_prune_removes_expired_dedup(self):
        """INVARIANT: expired dedup entries are removed."""
        store = MessageStore()
        now = time.time()
        store._dedup[("sender1", "msg1")] = {
            "msg": {"seq": 1},
            "channels": {0},
            "expire": now - GLOBAL_DEDUP_WINDOW - 10,  # expired
            "is_global": False,
        }
        store._dedup[("sender2", "msg2")] = {
            "msg": {"seq": 2},
            "channels": {1},
            "expire": now + GLOBAL_DEDUP_WINDOW,  # not expired
            "is_global": False,
        }

        store._prune(now)

        assert ("sender1", "msg1") not in store._dedup
        assert ("sender2", "msg2") in store._dedup

    def test_prune_keeps_non_expired(self):
        """INVARIANT: non-expired entries survive prune."""
        store = MessageStore()
        now = time.time()
        store._seen_ids[(0, 1)] = now + 100
        store._dedup[("a", "b")] = {
            "msg": {},
            "channels": {0},
            "expire": now + 100,
            "is_global": False,
        }

        store._prune(now)

        assert (0, 1) in store._seen_ids
        assert ("a", "b") in store._dedup

    def test_prune_on_empty_store_is_noop(self):
        """INVARIANT: prune on empty store doesn't crash."""
        store = MessageStore()
        store._prune(time.time())  # no crash


class TestDedupBoundaryConditions:
    """Edge cases around dedup timing boundaries."""

    def test_seen_id_expires_exactly_at_ttl(self):
        """INVARIANT: entry expires when now >= expire time."""
        store = MessageStore()
        now = time.time()
        store._seen_ids[(0, 1)] = now  # exactly at boundary
        store._prune(now)
        assert (0, 1) not in store._seen_ids

    def test_dedup_expires_at_global_window_boundary(self):
        """INVARIANT: dedup entry removed when expire <= now."""
        store = MessageStore()
        now = time.time()
        store._dedup[("a", "b")] = {
            "msg": {"seq": 1},
            "channels": {0},
            "expire": now,
            "is_global": False,
        }
        store._prune(now)
        assert ("a", "b") not in store._dedup

    def test_add_after_dedup_expires_allows_new(self):
        """INVARIANT: message can be re-added after dedup expires."""
        store = MessageStore()
        # Add a message
        store.add(0, {"sender": "A", "message": "hello", "msg_id": 1})
        # Advance time past GLOBAL_DEDUP_WINDOW
        store._dedup[("A", "hello")]["expire"] = time.time() - GLOBAL_DEDUP_WINDOW - 1
        # Re-add from different channel — should succeed now
        result = store.add(1, {"sender": "A", "message": "hello", "msg_id": 2})
        assert result is True  # not a duplicate anymore

    def test_add_after_seen_id_expires_allows_new(self):
        """INVARIANT: retransmit can be re-added after seen_id expires."""
        store = MessageStore()
        store.add(0, {"sender": "A", "message": "hello", "msg_id": 1})
        # Advance time past MSG_ID_TTL
        store._seen_ids[(0, 1)] = time.time() - MSG_ID_TTL - 1
        # Same channel, same msg_id — should be accepted now
        result = store.add(0, {"sender": "A", "message": "hello", "msg_id": 1})
        assert result is True  # expired, so not a duplicate


class TestConcurrentPruneAndAdd:
    """Prune running during add operations."""

    @given(
        n_adds=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=20, deadline=500)
    def test_prune_during_rapid_adds(self, n_adds: int):
        """INVARIANT: prune during rapid adds doesn't corrupt state."""
        import threading
        store = MessageStore()
        errors = []

        def add_messages():
            try:
                for i in range(n_adds):
                    store.add(0, {"sender": f"user{i}", "message": f"msg{i}", "msg_id": i + 1})
            except Exception as e:
                errors.append(e)

        def prune_loop():
            try:
                for _ in range(10):
                    store._prune(time.time())
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=add_messages)
        t2 = threading.Thread(target=prune_loop)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Errors during concurrent prune+add: {errors}"
        # Store should have valid state
        msgs = store.get_all_messages()
        assert len(msgs) <= n_adds  # some may be deduped