"""Config module tests — load, save, defaults, edge cases."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from megaphone import config


class TestLoadConfig:
    """Config file loading behavior."""

    def test_load_config_returns_defaults_when_no_file(self, tmp_path: Path):
        """INVARIANT: missing config file returns all defaults."""
        cfg_dir = tmp_path / "no_config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "megaphone_config.json"

        with mock.patch.object(config, "CONFIG_FILE", str(cfg_file)):
            result = config.load_config()

        assert result == {}

    def test_load_config_returns_saved_values(self, tmp_path: Path):
        """INVARIANT: saved token + channels persist across loads."""
        cfg_dir = tmp_path / "with_config"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "megaphone_config.json"
        cfg_file.write_text(json.dumps({
            "token": "TEST_TOKEN_PLACEHOLDER_123",
            "selected_channels": [0, 1, 42],
            "web_port": 9999,
            "capture_timeout": 60,
        }))

        with mock.patch.object(config, "CONFIG_FILE", str(cfg_file)):
            result = config.load_config()

        assert result["token"] == "TEST_TOKEN_PLACEHOLDER_123"
        assert result["selected_channels"] == [0, 1, 42]
        assert result["web_port"] == 9999
        assert result["capture_timeout"] == 60

    def test_load_config_handles_partial_file(self, tmp_path: Path):
        """INVARIANT: partial config file fills missing keys with defaults."""
        cfg_dir = tmp_path / "partial"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "megaphone_config.json"
        cfg_file.write_text(json.dumps({"token": "TEST_TOKEN_PLACEHOLDER_PARTIAL"}))

        with mock.patch.object(config, "CONFIG_FILE", str(cfg_file)):
            result = config.load_config()

        assert result["token"] == "TEST_TOKEN_PLACEHOLDER_PARTIAL"
        # Defaults should fill in
        assert "selected_channels" not in result
        assert result["web_port"] == config.WEB_PORT
        assert result["capture_timeout"] == config.CAPTURE_TIMEOUT


class TestApplyConfigDefaults:
    """apply_config_defaults merges user config with hardcoded defaults."""

    def test_empty_config_returns_defaults(self):
        """INVARIANT: empty config dict returns all defaults."""
        result = config.apply_config_defaults({})

        assert result["web_port"] == config.WEB_PORT
        assert result["ws_port"] == config.WS_PORT
        assert result["capture_timeout"] == config.CAPTURE_TIMEOUT
        assert result["selected_channels"] is None

    def test_config_overrides_defaults(self):
        """INVARIANT: provided values override defaults."""
        user_cfg = {
            "web_port": 7777,
            "capture_timeout": 300,
            "selected_channels": [0, 142],
        }
        result = config.apply_config_defaults(user_cfg)

        assert result["web_port"] == 7777
        assert result["capture_timeout"] == 300
        assert result["selected_channels"] == [0, 142]
        # Unspecified defaults still present
        assert result["ws_port"] == config.WS_PORT

    def test_invalid_web_port_defaults_back(self):
        """INVARIANT: out-of-range web_port falls back to default."""
        result = config.apply_config_defaults({"web_port": 99999})
        assert result["web_port"] == config.WEB_PORT

    def test_invalid_capture_timeout_defaults_back(self):
        """INVARIANT: zero/negative capture_timeout falls back to default."""
        result = config.apply_config_defaults({"capture_timeout": 0})
        assert result["capture_timeout"] == config.CAPTURE_TIMEOUT


class TestSaveConfig:
    """Config file persistence."""

    def test_save_config_creates_file(self, tmp_path: Path):
        """INVARIANT: save_config writes valid JSON file."""
        cfg_dir = tmp_path / "saved"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "megaphone_config.json"

        with mock.patch.object(config, "CONFIG_FILE", str(cfg_file)):
            config.save_config({"token": "TEST_TOKEN_PLACEHOLDER_NEW", "web_port": 8888})

        assert cfg_file.exists()
        data = json.loads(cfg_file.read_text())
        assert data["token"] == "TEST_TOKEN_PLACEHOLDER_NEW"
        assert data["web_port"] == 8888

    def test_save_config_overwrites_existing(self, tmp_path: Path):
        """INVARIANT: save_config replaces previous content."""
        cfg_dir = tmp_path / "overwrite"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "megaphone_config.json"
        cfg_file.write_text(json.dumps({"token": "old", "count": 1}))

        with mock.patch.object(config, "CONFIG_FILE", str(cfg_file)):
            config.save_config({"token": "new", "count": 2})

        data = json.loads(cfg_file.read_text())
        assert data == {"token": "new", "count": 2}


class TestChannelName:
    """channel_name maps IDs to human-readable names."""

    @pytest.mark.parametrize("cid,name", [
        (0, "초보"),
        (1, "1채널"),
        (2, "2채널"),
        (3, "3채널"),
        (19, "20세이상"),
        (20, "랭크"),
        (42, "마피아42"),
        (142, "랭크"),
    ])
    def test_known_channels(self, cid: int, name: str):
        """INVARIANT: known channel IDs map to correct names."""
        assert config.channel_name(cid) == name

    def test_unknown_channel_returns_number(self):
        """INVARIANT: unknown channel IDs return 'ch<N>' format."""
        assert config.channel_name(999) == "ch999"
        assert config.channel_name(10000) == "ch10000"