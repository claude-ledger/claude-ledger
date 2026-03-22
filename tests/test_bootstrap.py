"""Tests for bootstrap and heuristic inference."""

from pathlib import Path

import frontmatter
import pytest

from claude_ledger.bootstrap import (
    bootstrap_from_scan,
    build_activity_log,
    create_ledger_file,
    infer_name,
    infer_priority,
    infer_status,
    infer_vision,
)
from claude_ledger.config import Config


class TestInferStatus:
    def test_active_with_recent_commits(self):
        assert infer_status({"commit_count_30d": 5, "last_commit_date": "2026-03-20T00:00:00Z"}) == "active"

    def test_active_with_one_commit(self):
        assert infer_status({"commit_count_30d": 1, "last_commit_date": "2026-03-20T00:00:00Z"}) == "active"

    def test_paused_no_recent_commits_but_recent(self):
        assert infer_status({"commit_count_30d": 0, "last_commit_date": "2026-02-01T00:00:00Z"}) == "paused"

    def test_dormant_old_project(self):
        assert infer_status({"commit_count_30d": 0, "last_commit_date": "2025-10-01T00:00:00Z"}) == "dormant"

    def test_archived_very_old(self):
        assert infer_status({"commit_count_30d": 0, "last_commit_date": "2020-01-01T00:00:00Z"}) == "archived"

    def test_archived_no_commits(self):
        assert infer_status({"commit_count_30d": 0, "last_commit_date": None}) == "archived"


class TestInferPriority:
    def test_p1_high_activity_with_claude_md(self):
        data = {"commit_count_30d": 15, "has_claude_md": True, "mcp_json_exists": False}
        assert infer_priority(data, "active") == "P1"

    def test_p2_moderate_activity(self):
        data = {"commit_count_30d": 5, "has_claude_md": False, "mcp_json_exists": False}
        assert infer_priority(data, "active") == "P2"

    def test_p2_has_mcp(self):
        data = {"commit_count_30d": 1, "has_claude_md": False, "mcp_json_exists": True}
        assert infer_priority(data, "active") == "P2"

    def test_p3_low_activity(self):
        data = {"commit_count_30d": 1, "has_claude_md": False, "mcp_json_exists": False}
        assert infer_priority(data, "active") == "P3"

    def test_p3_when_not_active(self):
        data = {"commit_count_30d": 20, "has_claude_md": True, "mcp_json_exists": True}
        assert infer_priority(data, "paused") == "P3"


class TestInferName:
    def test_from_readme(self):
        data = {"slug": "my-proj", "readme_description": "A great tool for testing"}
        assert infer_name(data) == "A great tool for testing"

    def test_from_claude_md(self):
        data = {"slug": "my-proj", "claude_md_what_is_this": "Dashboard for monitoring.\nMore details."}
        assert infer_name(data) == "Dashboard for monitoring"

    def test_from_package_description(self):
        data = {"slug": "my-proj", "package_description": "CLI for portfolio tracking"}
        assert infer_name(data) == "CLI for portfolio tracking"

    def test_fallback_to_slug(self):
        data = {"slug": "my-cool-project"}
        assert infer_name(data) == "My Cool Project"

    def test_ignores_very_long_readme(self):
        data = {"slug": "proj", "readme_description": "x" * 100}
        # Too long, falls through to slug
        assert infer_name(data) == "Proj"

    def test_ignores_very_short_readme(self):
        data = {"slug": "proj", "readme_description": "Hi"}
        assert infer_name(data) == "Proj"


class TestInferVision:
    def test_from_claude_md(self):
        data = {"claude_md_what_is_this": "A portfolio tracker.\nWith features."}
        assert infer_vision(data) == "A portfolio tracker."

    def test_from_readme(self):
        data = {"readme_description": "Description from README"}
        assert infer_vision(data) == "Description from README"

    def test_empty_when_nothing(self):
        assert infer_vision({}) == ""


class TestBuildActivityLog:
    def test_with_commits(self):
        data = {
            "recent_commits": [
                {"sha": "abc123", "subject": "Fix bug", "date": "2026-03-20T10:00:00Z"},
                {"sha": "def456", "subject": "Add feature", "date": "2026-03-19T10:00:00Z"},
            ]
        }
        log = build_activity_log(data)
        assert "Fix bug (abc123)" in log
        assert "Add feature (def456)" in log
        assert "### 20 March 2026" in log

    def test_no_commits(self):
        log = build_activity_log({"recent_commits": []})
        assert "No commits yet" in log


class TestCreateLedgerFile:
    def test_creates_file(self, tmp_ledger):
        config = Config(ledger_dir=tmp_ledger)
        scan_data = {
            "slug": "new-project",
            "local_directory": "/tmp/new-project",
            "commit_count_30d": 5,
            "last_commit_date": "2026-03-20T00:00:00Z",
            "has_claude_md": True,
            "mcp_json_exists": False,
            "recent_commits": [],
            "tech_stack": ["python"],
            "external_systems": [],
        }
        slug, reason = create_ledger_file(scan_data, config)
        assert slug == "new-project"
        assert reason == "created"
        assert (tmp_ledger / "new-project.md").exists()

        # Verify frontmatter
        post = frontmatter.load(str(tmp_ledger / "new-project.md"))
        assert post.metadata["status"] == "active"
        assert post.metadata["priority"] == "P2"

    def test_skips_existing(self, tmp_ledger, sample_ledger_file):
        config = Config(ledger_dir=tmp_ledger)
        scan_data = {"slug": "test-project", "commit_count_30d": 0, "last_commit_date": None}
        slug, reason = create_ledger_file(scan_data, config)
        assert slug is None
        assert "already exists" in reason

    def test_skips_skip_slugs(self, tmp_ledger):
        config = Config(ledger_dir=tmp_ledger, skip_slugs=["ignored-project"])
        scan_data = {"slug": "ignored-project"}
        slug, reason = create_ledger_file(scan_data, config)
        assert slug is None
        assert "skip_slugs" in reason

    def test_skips_no_track(self, tmp_ledger):
        config = Config(ledger_dir=tmp_ledger, no_track=["internal-tool"])
        scan_data = {"slug": "internal-tool"}
        slug, reason = create_ledger_file(scan_data, config)
        assert slug is None
        assert "no_track" in reason


class TestBootstrapFromScan:
    def test_bootstrap_from_scan_results(self, tmp_ledger):
        config = Config(ledger_dir=tmp_ledger)
        # Create scan results
        import json
        scan_data = {
            "projects": [
                {
                    "slug": "proj-a",
                    "scan_status": "ok",
                    "local_directory": "/tmp/proj-a",
                    "commit_count_30d": 10,
                    "last_commit_date": "2026-03-20T00:00:00Z",
                    "has_claude_md": True,
                    "mcp_json_exists": False,
                    "recent_commits": [],
                    "tech_stack": ["python"],
                    "external_systems": [],
                },
                {
                    "slug": "proj-b",
                    "scan_status": "ok",
                    "local_directory": "/tmp/proj-b",
                    "commit_count_30d": 0,
                    "last_commit_date": None,
                    "has_claude_md": False,
                    "mcp_json_exists": False,
                    "recent_commits": [],
                    "tech_stack": [],
                    "external_systems": [],
                },
            ],
            "github_only": [],
        }
        scan_path = config.scan_results_path
        scan_path.parent.mkdir(parents=True, exist_ok=True)
        with open(scan_path, "w") as f:
            json.dump(scan_data, f)

        counts = bootstrap_from_scan(config)
        assert counts["created"] == 2
        assert (tmp_ledger / "proj-a.md").exists()
        assert (tmp_ledger / "proj-b.md").exists()

    def test_dry_run_creates_nothing(self, tmp_ledger):
        config = Config(ledger_dir=tmp_ledger)
        import json
        scan_data = {
            "projects": [{"slug": "proj-c", "scan_status": "ok", "local_directory": "/tmp/c",
                           "commit_count_30d": 1, "last_commit_date": "2026-03-20T00:00:00Z",
                           "has_claude_md": False, "mcp_json_exists": False,
                           "recent_commits": [], "tech_stack": [], "external_systems": []}],
            "github_only": [],
        }
        with open(config.scan_results_path, "w") as f:
            json.dump(scan_data, f)

        counts = bootstrap_from_scan(config, dry_run=True)
        assert counts["created"] == 1  # Counted as "would create"
        assert not (tmp_ledger / "proj-c.md").exists()  # But not actually created
