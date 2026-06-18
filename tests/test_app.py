"""app.py orchestration tests — channel parsing, config merging."""
from __future__ import annotations

from types import SimpleNamespace


import megaphone.app as app_module
from megaphone.app import parse_selected_channels


class TestParseSelectedChannels:
    """--channels argument parsing."""

    def test_no_channels_flag_returns_none(self):
        """INVARIANT: absent --channels → None (all channels)."""
        assert parse_selected_channels(["server.py"]) is None
        assert parse_selected_channels(["server.py", "--verbose"]) is None

    def test_single_channel(self):
        """INVARIANT: --channels 0 → {0}."""
        result = parse_selected_channels(["server.py", "--channels", "0"])
        assert result == {0}

    def test_multiple_channels(self):
        """INVARIANT: --channels 0,1,42 → {0, 1, 42}."""
        result = parse_selected_channels(["server.py", "--channels", "0,1,42"])
        assert result == {0, 1, 42}

    def test_single_value_no_comma(self):
        """INVARIANT: --channels 42 → {42}."""
        result = parse_selected_channels(["server.py", "--channels", "42"])
        assert result == {42}

    def test_out_of_order_channels(self):
        """INVARIANT: --channels 142,0,1 preserves set semantics."""
        result = parse_selected_channels(["server.py", "--channels", "142,0,1"])
        assert result == {0, 1, 142}

    def test_invalid_value_ignored(self):
        """INVARIANT: non-numeric values are silently skipped."""
        result = parse_selected_channels(["server.py", "--channels", "0,abc,1"])
        assert result == {0, 1}

    def test_empty_string_value(self):
        """INVARIANT: --channels '' → None (no filtering)."""
        result = parse_selected_channels(["server.py", "--channels", ""])
        assert result is None

    def test_channels_flag_missing_value(self):
        """INVARIANT: --channels at end of args → None."""
        result = parse_selected_channels(["server.py", "--channels"])
        assert result is None

    def test_negative_channel_id(self):
        """INVARIANT: negative numbers are ignored (not valid channel IDs)."""
        result = parse_selected_channels(["server.py", "--channels", "0,-1,1"])
        assert result == {0, 1}


class TestMainSelectedChannels:
    """main() honors channel selection from CLI/config."""

    def test_main_uses_selected_channels_from_config_when_cli_missing(self, monkeypatch):
        """INVARIANT: saved selected_channels filters connections without CLI override."""
        started_channels: list[int] = []
        shutdown_called = {"value": False}

        class FakeConnection:
            def __init__(self, channel_id: int, host: str, auth_token: str) -> None:
                self.channel_id = channel_id
                self.host = host
                self.auth_token = auth_token
                self.running = True

            def start(self) -> None:
                started_channels.append(self.channel_id)

        def fake_sleep(seconds: float) -> None:
            if seconds == 1:
                raise KeyboardInterrupt

        def fake_shutdown() -> None:
            shutdown_called["value"] = True

        monkeypatch.setattr(app_module, "load_config", lambda: {"selected_channels": [1, 42]})
        monkeypatch.setattr(
            app_module,
            "extract_token_and_channels_cdp",
            lambda: (
                "TEST_TOKEN_PLACEHOLDER",
                [
                    {"channel_id": 0, "host": "ch0.local", "user_count": 10},
                    {"channel_id": 1, "host": "ch1.local", "user_count": 20},
                    {"channel_id": 42, "host": "ch42.local", "user_count": 30},
                ],
                None,  # channel_api
            ),
        )
        monkeypatch.setattr(app_module, "load_token_overrides", lambda: {})
        monkeypatch.setattr(
            app_module,
            "resolve_channel_tokens",
            lambda ch_ids, token, overrides: {cid: token for cid in ch_ids},
        )
        monkeypatch.setattr(
            app_module,
            "populations_from_list",
            lambda channels: {},
        )
        class FakePoller:
            def __init__(self, url, headers=None):
                pass
            def start(self):
                pass

        monkeypatch.setattr(app_module, "PopulationPoller", FakePoller)
        monkeypatch.setattr(
            app_module,
            "start_web_server",
            lambda _port: SimpleNamespace(shutdown=fake_shutdown),
        )
        monkeypatch.setattr(app_module, "ChannelConnection", FakeConnection)
        monkeypatch.setattr(app_module.time, "sleep", fake_sleep)
        monkeypatch.setattr(app_module.signal, "signal", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(app_module.sys, "argv", ["megaphone_server.py"])

        app_module.main()

        assert started_channels == [1, 42]
        assert shutdown_called["value"] is True

    def test_main_excludes_secret_channel_19(self, monkeypatch):
        """INVARIANT: 비밀 채널(19)은 선택에 포함돼도 접속 대상에서 제외된다."""
        started_channels: list[int] = []

        class FakeConnection:
            def __init__(self, channel_id: int, host: str, auth_token: str) -> None:
                self.channel_id = channel_id
                self.running = True

            def start(self) -> None:
                started_channels.append(self.channel_id)

        def fake_sleep(seconds: float) -> None:
            if seconds == 1:
                raise KeyboardInterrupt

        monkeypatch.setattr(app_module, "load_config", lambda: {"selected_channels": [1, 19]})
        monkeypatch.setattr(
            app_module,
            "extract_token_and_channels_cdp",
            lambda: (
                "TEST_TOKEN_PLACEHOLDER",
                [
                    {"channel_id": 1, "host": "ch1.local", "user_count": 20},
                    {"channel_id": 19, "host": "ch19.local", "user_count": 3},
                    {"channel_id": 42, "host": "ch42.local", "user_count": 30},
                ],
                None,
            ),
        )
        monkeypatch.setattr(app_module, "load_token_overrides", lambda: {})
        monkeypatch.setattr(
            app_module,
            "resolve_channel_tokens",
            lambda ch_ids, token, overrides: {cid: token for cid in ch_ids},
        )
        monkeypatch.setattr(app_module, "populations_from_list", lambda channels: {})

        class FakePoller:
            def __init__(self, url, headers=None):
                pass

            def start(self):
                pass

        monkeypatch.setattr(app_module, "PopulationPoller", FakePoller)
        monkeypatch.setattr(
            app_module, "start_web_server", lambda _port: SimpleNamespace(shutdown=lambda: None)
        )
        monkeypatch.setattr(app_module, "ChannelConnection", FakeConnection)
        monkeypatch.setattr(app_module.time, "sleep", fake_sleep)
        monkeypatch.setattr(app_module.signal, "signal", lambda *_a, **_k: None)
        monkeypatch.setattr(app_module.sys, "argv", ["megaphone_server.py"])

        app_module.main()

        assert 19 not in started_channels
        assert started_channels == [1]
