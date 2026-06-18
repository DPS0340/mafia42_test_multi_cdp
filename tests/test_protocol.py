"""Protocol module tests — property-based with Hypothesis.

Core invariants tested:
  - make_packet → parse_packet roundtrip preserves msg_type + payload
  - make_packet length always = 4 + len(payload)
  - parse_packet returns None for data < 8 bytes
  - parse_megaphone correctly extracts sender/message from valid text
  - Korean/Unicode text survives encode → decode roundtrip
  - Malformed packets never crash (return None gracefully)
"""
from __future__ import annotations

import struct

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from megaphone.protocol import (
    MSG_HEARTBEAT,
    make_packet,
    parse_megaphone,
    parse_packet,
)

from conftest import (
    PACKET_TYPE_LIST,
    megaphone_payload,
    packet_type,
    shortened_packet,
    valid_packet,
)


# ===========================================================================
# make_packet invariants
# ===========================================================================


class TestMakePacket:
    """Property-based tests for packet encoding (make_packet)."""

    @given(mt=packet_type(), pl=st.binary(max_size=100))
    @settings(max_examples=200)
    def test_length_invariant(self, mt: int, pl: bytes):
        """INVARIANT: packet length field always = 4 + len(payload)."""
        pkt = make_packet(mt, pl)
        assert len(pkt) >= 8
        (length,) = struct.unpack(">I", pkt[0:4])
        assert length == 4 + len(pl)

    @given(mt=packet_type(), pl=st.binary(max_size=100))
    @settings(max_examples=200)
    def test_msg_type_preserved(self, mt: int, pl: bytes):
        """INVARIANT: msg_type in packet matches input."""
        pkt = make_packet(mt, pl)
        (_, msg_type) = struct.unpack(">II", pkt[0:8])
        assert msg_type == mt

    @given(mt=packet_type(), pl=st.binary(max_size=100))
    @settings(max_examples=200)
    def test_payload_contiguous(self, mt: int, pl: bytes):
        """INVARIANT: payload follows header contiguously."""
        pkt = make_packet(mt, pl)
        assert pkt[8:] == pl

    @given(mt=packet_type(), pl=st.binary(max_size=100))
    @settings(max_examples=200)
    def test_make_parse_roundtrip(self, mt: int, pl: bytes):
        """ROUNDTRIP: make → parse recovers original type and payload."""
        pkt = make_packet(mt, pl)
        length, msg_type, payload = parse_packet(pkt)
        assert msg_type == mt
        assert payload == pl

    @given(mt=packet_type())
    @settings(max_examples=50)
    def test_empty_payload(self, mt: int):
        """EDGE: empty payload produces length = 4."""
        pkt = make_packet(mt)
        (length,) = struct.unpack(">I", pkt[0:4])
        assert length == 4

    def test_binary_all_values(self):
        """BOILERPLATE: ensure all 256 byte values survive roundtrip."""
        for pl in [bytes(range(256)), b"\x00\xff\x80\x7f", bytes(256)]:
            length, msg_type, payload = parse_packet(make_packet(MSG_HEARTBEAT, pl))
            assert payload == pl


# ===========================================================================
# parse_packet invariants
# ===========================================================================


class TestParsePacket:
    """Property-based tests for packet decoding (parse_packet)."""

    @given(data=shortened_packet())
    @settings(max_examples=100)
    def test_short_data_returns_none(self, data: bytes):
        """INVARIANT: data < 8 bytes → (None, None, b'')."""
        length, msg_type, payload = parse_packet(data)
        assert length is None
        assert msg_type is None
        assert payload == b""

    @given(pkt=valid_packet())
    @settings(max_examples=100)
    def test_valid_packet_parses(self, pkt: tuple):
        """INVARIANT: valid packets always parse."""
        full_pkt, expected_len, expected_mt, expected_pl = pkt
        length, msg_type, payload = parse_packet(full_pkt)
        assert msg_type == expected_mt
        assert payload == expected_pl

    @given(data=st.binary(min_size=0, max_size=7))
    @settings(max_examples=50)
    def test_too_short_returns_none(self, data: bytes):
        """BOILERPLATE: data shorter than 8 bytes returns None."""
        length, msg_type, payload = parse_packet(data)
        assert length is None

    @given(pl=st.binary(min_size=0, max_size=500))
    @settings(max_examples=100)
    def test_roundtrip_all_types(self, pl: bytes):
        """BOILERPLATE: roundtrip for every packet type."""
        for mt in PACKET_TYPE_LIST:
            length, msg_type, payload = parse_packet(make_packet(mt, pl))
            assert msg_type == mt
            assert payload == pl


# ===========================================================================
# parse_megaphone invariants
# ===========================================================================


class TestParseMegaphone:
    """Property-based tests for megaphone message parsing."""

    @given(pl=megaphone_payload())
    @settings(max_examples=200)
    def test_valid_text_parses(self, pl: bytes):
        """INVARIANT: valid sender:message text extracts sender + message."""
        result = parse_megaphone(pl)
        assert result is not None
        assert "sender" in result
        assert "message" in result
        assert "msg_id" in result
        assert "metadata" in result
        assert len(result["sender"]) > 0 or len(result["message"]) > 0
        # The sender cannot contain the separator " : "
        assert " : " not in result["sender"]

    @given(mid=st.integers(min_value=0, max_value=2**32 - 1))
    @settings(max_examples=50)
    def test_msg_id_preserved(self, mid: int):
        """INVARIANT: msg_id roundtrips through parse_megaphone."""
        text = "sender : hello"
        text_bytes = text.encode("utf-8")
        # Layout: [8B junk][4B msg_id][4B meta][4B text_len][text]
        payload = b"\x00" * 8 + struct.pack(">III", mid, 0, len(text_bytes)) + text_bytes
        result = parse_megaphone(payload)
        assert result is not None
        assert result["msg_id"] == mid

    def test_korean_text_preserved(self):
        """EDGE: full Korean message roundtrip preserves encoding."""
        text = "초보 유저 : 축하합니다 모두 수고하셨습니다"
        text_bytes = text.encode("utf-8")
        payload = b"\x00" * 16 + struct.pack(">I", len(text_bytes)) + text_bytes
        result = parse_megaphone(payload)
        assert result is not None
        assert result["sender"] == "초보 유저"
        assert result["message"] == "축하합니다 모두 수고하셨습니다"

    def test_korean_with_special_chars(self):
        """EDGE: Korean + numbers + special chars."""
        text = "익명의용자99 : 문의는 DM 주세요~! 확인 부탁합니다."
        text_bytes = text.encode("utf-8")
        payload = b"\x00" * 16 + struct.pack(">I", len(text_bytes)) + text_bytes
        result = parse_megaphone(payload)
        assert result is not None
        assert result["sender"] == "익명의용자99"
        assert "DM" in result["message"]

    @given(text=st.text(min_size=1, max_size=50))
    @settings(max_examples=100)
    def test_no_separator_returns_none(self, text: str):
        """INVARIANT: text without ' : ' separator returns None."""
        assume(" : " not in text and len(text) >= 1)
        text_bytes = text.encode("utf-8")
        payload = b"\x00" * 16 + struct.pack(">I", len(text_bytes)) + text_bytes
        result = parse_megaphone(payload)
        assert result is None

    @given(data=st.binary(min_size=0, max_size=19))
    @settings(max_examples=50)
    def test_too_short_returns_none(self, data: bytes):
        """INVARIANT: payload < 20 bytes returns None."""
        result = parse_megaphone(data)
        assert result is None


# ===========================================================================
# Edge cases and malformed data — should never crash
# ===========================================================================


class TestProtocolRobustness:
    """Hypothesis-driven robustness: malformed data should never throw."""

    @given(data=st.binary(min_size=0, max_size=100))
    @settings(max_examples=200)
    def test_parse_packet_never_crashes(self, data: bytes):
        """ROBUSTNESS: parse_packet handles any binary data without exception."""
        try:
            parse_packet(data)
        except Exception as e:
            pytest.fail(f"parse_packet crashed with: {e}")

    @given(data=st.binary(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_parse_megaphone_never_crashes(self, data: bytes):
        """ROBUSTNESS: parse_megaphone handles any binary data without exception."""
        try:
            parse_megaphone(data)
        except Exception as e:
            pytest.fail(f"parse_megaphone crashed with: {e}")

    @given(mt=st.integers(min_value=0, max_value=2**32 - 1), pl=st.binary(max_size=100))
    @settings(max_examples=200)
    def test_make_packet_never_crashes(self, mt: int, pl: bytes):
        """ROBUSTNESS: make_packet handles any msg_type + payload."""
        try:
            make_packet(mt, pl)
        except Exception as e:
            pytest.fail(f"make_packet crashed with: {e}")
