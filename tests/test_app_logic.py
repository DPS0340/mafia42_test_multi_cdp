"""app.py 채널 선택 로직 단위 테스트 (네트워크 불필요)."""
from megaphone.app import parse_selected_channels, decide_selected_channels
from megaphone.config import CORE_CHANNEL


def test_parse_no_flag_returns_none():
    assert parse_selected_channels(["megaphone_server.py"]) is None


def test_parse_single_channel():
    assert parse_selected_channels(["x", "--channels", "1"]) == {1}


def test_parse_multiple_channels():
    assert parse_selected_channels(["x", "--channels", "0,1,2"]) == {0, 1, 2}


def test_parse_flag_without_value_returns_none():
    assert parse_selected_channels(["x", "--channels"]) is None


def test_decide_explicit_selection_passthrough():
    assert decide_selected_channels({2, 3}, [0, 1, 2, 3], {}) == {2, 3}


def test_decide_single_account_default_is_core_channel():
    assert decide_selected_channels(None, [0, 1, 2], {}) == {CORE_CHANNEL}


def test_decide_multi_account_default_is_override_channels():
    assert decide_selected_channels(None, [0, 1, 2], {0: "a", 2: "b"}) == {0, 2}


def test_decide_falls_back_to_first_when_core_absent():
    assert decide_selected_channels(None, [5, 7], {}) == {5}
