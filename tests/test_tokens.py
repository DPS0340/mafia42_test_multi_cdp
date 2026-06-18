"""tokens.py 채널별 토큰 해석 단위 테스트."""
from megaphone.tokens import load_token_overrides, resolve_channel_tokens


def test_resolve_single_account_uses_captured_token():
    assert resolve_channel_tokens([1], "CAP", {}) == {1: "CAP"}


def test_resolve_override_takes_priority_with_capture_fallback():
    result = resolve_channel_tokens([0, 1], "CAP", {0: "T0"})
    assert result == {0: "T0", 1: "CAP"}


def test_resolve_channel_without_any_token_is_excluded():
    assert resolve_channel_tokens([0], None, {}) == {}


def test_load_overrides_missing_file_returns_empty(tmp_path):
    assert load_token_overrides(tmp_path / "nope.json") == {}


def test_load_overrides_parses_int_keys(tmp_path):
    f = tmp_path / "tokens.json"
    f.write_text('{"0": "a", "1": "b"}', encoding="utf-8")
    assert load_token_overrides(f) == {0: "a", 1: "b"}


def test_load_overrides_skips_empty_and_non_int_keys(tmp_path):
    f = tmp_path / "tokens.json"
    f.write_text('{"0": "a", "1": "", "_설명": "x"}', encoding="utf-8")
    assert load_token_overrides(f) == {0: "a"}


def test_load_overrides_bad_json_returns_empty(tmp_path):
    f = tmp_path / "tokens.json"
    f.write_text("{not valid json", encoding="utf-8")
    assert load_token_overrides(f) == {}
