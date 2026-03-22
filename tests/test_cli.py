"""Tests for the CLI commands."""

import json

import pytest
from click.testing import CliRunner

from claude_ledger.cli import _build_hooks_spec, cli


@pytest.fixture
def runner():
    return CliRunner()


class TestVersion:
    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        from claude_ledger import __version__
        assert __version__ in result.output


class TestInit:
    def test_creates_ledger_dir(self, runner, tmp_path):
        ledger_dir = tmp_path / "ledger"
        # Use a fake settings path to avoid touching real settings
        result = runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "init"])
        assert result.exit_code == 0
        assert ledger_dir.exists()
        assert (ledger_dir / "ledger.yaml").exists()
        assert (ledger_dir / ".gitignore").exists()
        assert (ledger_dir / ".git").exists()

    def test_init_is_idempotent(self, runner, tmp_path):
        ledger_dir = tmp_path / "ledger"
        runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "init"])
        result = runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "init"])
        assert result.exit_code == 0
        assert "already exists" in result.output

    def test_init_with_scan_dirs(self, runner, tmp_path):
        ledger_dir = tmp_path / "ledger"
        result = runner.invoke(cli, [
            "--ledger-dir", str(ledger_dir),
            "init", "--scan-dirs", "/tmp/projects",
        ])
        assert result.exit_code == 0
        config_content = (ledger_dir / "ledger.yaml").read_text()
        assert "/tmp/projects" in config_content


class TestScan:
    def test_scan_no_config(self, runner, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text("version: 1\nscan_dirs: []\n")
        result = runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "scan"])
        assert result.exit_code != 0
        assert "No scan_dirs" in result.output

    def test_scan_with_dirs(self, runner, tmp_path, sample_project):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        parent = sample_project.parent
        (ledger_dir / "ledger.yaml").write_text(
            f"version: 1\nscan_dirs:\n  - {parent}\n"
        )
        result = runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "scan"])
        assert result.exit_code == 0
        assert "Results written to" in result.output
        assert (ledger_dir / "_scan-results.json").exists()


class TestBootstrap:
    def test_bootstrap_no_scan(self, runner, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text("version: 1\n")
        result = runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "bootstrap"])
        assert result.exit_code != 0
        assert "scan" in result.output.lower()

    def test_bootstrap_dry_run(self, runner, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text("version: 1\n")
        scan_data = {
            "projects": [{
                "slug": "test-proj", "scan_status": "ok",
                "local_directory": "/tmp/test", "commit_count_30d": 5,
                "last_commit_date": "2026-03-20T00:00:00Z",
                "has_claude_md": False, "mcp_json_exists": False,
                "recent_commits": [], "tech_stack": [], "external_systems": [],
            }],
            "github_only": [],
        }
        with open(ledger_dir / "_scan-results.json", "w") as f:
            json.dump(scan_data, f)

        result = runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "bootstrap", "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert not (ledger_dir / "test-proj.md").exists()


class TestBriefing:
    def test_briefing_empty(self, runner, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text("version: 1\n")
        result = runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "briefing"])
        assert result.exit_code == 0
        assert "No ledger files" in result.output

    def test_briefing_with_projects(self, runner, tmp_ledger, sample_ledger_file):
        result = runner.invoke(cli, ["--ledger-dir", str(tmp_ledger), "briefing"])
        assert result.exit_code == 0
        assert "1 projects tracked" in result.output or "P1 active" in result.output
        assert (tmp_ledger / "_portfolio.md").exists()
        assert (tmp_ledger / "_workstreams.md").exists()


class TestStatus:
    def test_status_empty(self, runner, tmp_path):
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.yaml").write_text("version: 1\n")
        result = runner.invoke(cli, ["--ledger-dir", str(ledger_dir), "status"])
        assert result.exit_code == 0
        assert "No ledger files" in result.output

    def test_status_with_projects(self, runner, tmp_ledger, sample_ledger_file):
        result = runner.invoke(cli, ["--ledger-dir", str(tmp_ledger), "status"])
        assert result.exit_code == 0
        assert "P1" in result.output
        assert "Test Project" in result.output

    def test_status_json(self, runner, tmp_ledger, sample_ledger_file):
        result = runner.invoke(cli, ["--ledger-dir", str(tmp_ledger), "status", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total"] == 1
        assert data["p1"] == 1


class TestUninstall:
    def test_uninstall_no_hooks(self, runner, tmp_path):
        # Create empty settings
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text("{}")

        result = runner.invoke(cli, ["uninstall"], env={"HOME": str(tmp_path)})
        assert result.exit_code == 0
        assert "No claude-ledger hooks" in result.output

    def test_uninstall_removes_hooks(self, runner, tmp_path):
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        settings = {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "claude-ledger briefing"}]},
                    {"matcher": "", "hooks": [{"type": "command", "command": "some-other-tool"}]},
                ]
            }
        }
        with open(settings_dir / "settings.json", "w") as f:
            json.dump(settings, f)

        result = runner.invoke(cli, ["uninstall"], env={"HOME": str(tmp_path)})
        assert result.exit_code == 0
        assert "Removed" in result.output

        # Verify the other hook is preserved
        with open(settings_dir / "settings.json") as f:
            updated = json.load(f)
        session_hooks = updated["hooks"]["SessionStart"]
        assert len(session_hooks) == 1
        assert "some-other-tool" in session_hooks[0]["hooks"][0]["command"]


class TestBuildHooksSpec:
    def test_builds_four_hook_types(self):
        spec = _build_hooks_spec("/usr/local/bin/claude-ledger")
        assert "PostToolUse" in spec
        assert "Stop" in spec
        assert "SessionEnd" in spec
        assert "SessionStart" in spec

    def test_uses_cli_path_in_commands(self):
        spec = _build_hooks_spec("/custom/path/claude-ledger")
        for entries in spec.values():
            for entry in entries:
                for hook in entry["hooks"]:
                    assert hook["command"].startswith("/custom/path/claude-ledger")

    def test_python_m_path_works(self):
        spec = _build_hooks_spec("python3 -m claude_ledger")
        commands = []
        for entries in spec.values():
            for entry in entries:
                for hook in entry["hooks"]:
                    commands.append(hook["command"])
        assert any("capture --touch" in c for c in commands)
        assert any("briefing" in c for c in commands)

    def test_matchers_are_correct(self):
        spec = _build_hooks_spec("claude-ledger")
        post_tool_matchers = [e["matcher"] for e in spec["PostToolUse"]]
        assert "Edit|Write|MultiEdit" in post_tool_matchers
        assert "Bash" in post_tool_matchers
