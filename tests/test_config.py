"""Tests for config loading and validation."""

from pathlib import Path

import pytest

from claude_ledger.config import (
    Config,
    expand_path,
    generate_default_config,
    load_config,
)


class TestExpandPath:
    def test_expands_tilde(self):
        result = expand_path("~/Code")
        assert str(result).startswith("/")
        assert "~" not in str(result)

    def test_returns_path_object(self):
        result = expand_path("/some/path")
        assert isinstance(result, Path)

    def test_expands_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_DIR", "/custom/path")
        result = expand_path("$TEST_DIR/projects")
        assert str(result) == "/custom/path/projects"


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, tmp_path):
        config = load_config(tmp_path / "nonexistent")
        assert isinstance(config, Config)
        assert config.stale_days == 7
        assert config.scan_dirs == []
        assert config.github_user is None

    def test_loads_from_yaml(self, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text(
            "version: 1\nscan_dirs:\n  - ~/Code\nstale_days: 14\ngithub_user: testuser\n"
        )
        config = load_config(ledger_dir)
        assert config.stale_days == 14
        assert config.github_user == "testuser"
        assert len(config.scan_dirs) == 1

    def test_rejects_wrong_version(self, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text("version: 99\n")
        with pytest.raises(ValueError, match="Unsupported"):
            load_config(ledger_dir)

    def test_handles_malformed_yaml(self, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text(": invalid: yaml: [[[")
        config = load_config(ledger_dir)
        assert isinstance(config, Config)

    def test_invalid_stale_days_defaults_to_7(self, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text("version: 1\nstale_days: -1\n")
        config = load_config(ledger_dir)
        assert config.stale_days == 7

    def test_parses_workstreams(self, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text(
            "version: 1\nworkstreams:\n  backend:\n    display_name: Backend\n"
            "    members:\n      - api\n      - worker\n"
        )
        config = load_config(ledger_dir)
        assert "backend" in config.workstreams
        assert config.workstreams["backend"].display_name == "Backend"
        assert config.workstreams["backend"].members == ["api", "worker"]

    def test_config_paths(self, tmp_path):
        config = Config(ledger_dir=tmp_path)
        assert config.config_path == tmp_path / "ledger.yaml"
        assert config.state_dir == tmp_path / ".state" / "sessions"
        assert config.locks_dir == tmp_path / ".locks"
        assert config.portfolio_path == tmp_path / "_portfolio.md"
        assert config.workstreams_path == tmp_path / "_workstreams.md"


class TestGenerateDefaultConfig:
    def test_generates_yaml_string(self):
        result = generate_default_config()
        assert "version: 1" in result
        assert "scan_dirs:" in result
        assert "stale_days: 7" in result

    def test_includes_custom_scan_dirs(self):
        result = generate_default_config(scan_dirs=["/my/projects"])
        assert "/my/projects" in result

    def test_includes_github_user(self):
        result = generate_default_config(github_user="testuser")
        assert "testuser" in result
