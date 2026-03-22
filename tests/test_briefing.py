"""Tests for portfolio briefing and workstream generation."""

from datetime import datetime, timezone, timedelta

import frontmatter
import pytest

from claude_ledger.briefing import (
    generate_briefing,
    generate_portfolio,
    generate_status_line,
    generate_workstreams,
    load_ledger_files,
)
from claude_ledger.config import Config, WorkstreamConfig


def _make_project(name, slug, status="active", priority="P1", phase="building",
                  last_session=None, workstreams=None):
    """Helper to create a project metadata dict."""
    if last_session is None:
        last_session = datetime.now(timezone.utc).isoformat()
    return {
        "name": name,
        "slug": slug,
        "status": status,
        "priority": priority,
        "current_phase": phase,
        "last_session": last_session,
        "last_activity": f"Latest work on {name}",
        "workstreams": workstreams or [],
    }


class TestLoadLedgerFiles:
    def test_loads_ledger_files(self, tmp_ledger, sample_ledger_file):
        projects = load_ledger_files(tmp_ledger)
        assert len(projects) == 1
        assert projects[0]["name"] == "Test Project"

    def test_skips_underscore_files(self, tmp_ledger):
        (tmp_ledger / "_portfolio.md").write_text("# Not a project")
        projects = load_ledger_files(tmp_ledger)
        assert len(projects) == 0

    def test_skips_archive_files(self, tmp_ledger):
        post = frontmatter.Post("Old stuff")
        post.metadata = {"name": "Old", "slug": "old"}
        with open(tmp_ledger / "old-archive.md", "w") as f:
            f.write(frontmatter.dumps(post))
        projects = load_ledger_files(tmp_ledger)
        assert len(projects) == 0


class TestGeneratePortfolio:
    def test_groups_by_priority(self):
        projects = [
            _make_project("Alpha", "alpha", priority="P1"),
            _make_project("Beta", "beta", priority="P2"),
            _make_project("Gamma", "gamma", priority="P3"),
        ]
        output = generate_portfolio(projects)
        assert "## Active (P1)" in output
        assert "## Active (P2)" in output
        assert "## Active (P3)" in output
        assert "**Alpha**" in output

    def test_detects_stale(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        projects = [
            _make_project("Stale One", "stale", last_session=old_date),
        ]
        output = generate_portfolio(projects, stale_days=7)
        assert "## Stale (>7 days)" in output
        assert "**Stale One**" in output

    def test_groups_paused(self):
        projects = [_make_project("Paused", "paused", status="paused")]
        output = generate_portfolio(projects)
        assert "## Paused" in output

    def test_groups_completed(self):
        projects = [_make_project("Done", "done", status="completed")]
        output = generate_portfolio(projects)
        assert "## Completed" in output

    def test_skips_archived(self):
        projects = [_make_project("Old", "old", status="archived")]
        output = generate_portfolio(projects)
        assert "Old" not in output

    def test_custom_stale_days(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        projects = [_make_project("Semi", "semi", last_session=recent)]

        # With default 7 days: not stale
        output7 = generate_portfolio(projects, stale_days=7)
        assert "Stale" not in output7

        # With 3 days: stale
        output3 = generate_portfolio(projects, stale_days=3)
        assert "Stale" in output3


class TestGenerateWorkstreams:
    def test_groups_by_workstream(self):
        projects = [
            _make_project("API", "api", workstreams=["backend"]),
            _make_project("Worker", "worker", workstreams=["backend"]),
            _make_project("Web", "web", workstreams=["frontend"]),
        ]
        config = Config(workstreams={
            "backend": WorkstreamConfig(display_name="Backend Services", members=["api", "worker"]),
            "frontend": WorkstreamConfig(display_name="Frontend Apps", members=["web"]),
        })
        output = generate_workstreams(projects, config)
        assert "## Backend Services [backend]" in output
        assert "## Frontend Apps [frontend]" in output
        assert "**API**" in output

    def test_cascade_warning(self):
        projects = [
            _make_project("A", "a", workstreams=["big"]),
            _make_project("B", "b", workstreams=["big"]),
            _make_project("C", "c", workstreams=["big"]),
        ]
        config = Config(workstreams={
            "big": WorkstreamConfig(display_name="Big Team", members=["a", "b", "c"]),
        })
        output = generate_workstreams(projects, config)
        assert "CASCADE" in output

    def test_unassigned(self):
        projects = [_make_project("Lone", "lone")]
        config = Config()
        output = generate_workstreams(projects, config)
        assert "## Unassigned" in output
        assert "**Lone**" in output

    def test_fallback_display_name(self):
        projects = [_make_project("X", "x", workstreams=["my-team"])]
        config = Config()  # No workstream config
        output = generate_workstreams(projects, config)
        assert "My Team" in output  # Titlecased from slug


class TestGenerateStatusLine:
    def test_status_line(self):
        projects = [
            _make_project("A", "a", priority="P1"),
            _make_project("B", "b", priority="P2"),
            _make_project("C", "c", priority="P3", status="paused"),
        ]
        line = generate_status_line(projects)
        assert "3 projects tracked" in line
        assert "1 P1 active" in line

    def test_stale_count(self):
        old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        projects = [
            _make_project("Active", "active"),
            _make_project("Stale", "stale", last_session=old),
        ]
        line = generate_status_line(projects, stale_days=7)
        assert "1 stale" in line


class TestGenerateBriefingIntegration:
    def test_full_briefing(self, tmp_ledger, sample_ledger_file):
        config = Config(ledger_dir=tmp_ledger)
        status = generate_briefing(config)
        assert "1 projects tracked" in status or "1 P1 active" in status
        assert config.portfolio_path.exists()
        assert config.workstreams_path.exists()

    def test_empty_ledger(self, tmp_ledger):
        config = Config(ledger_dir=tmp_ledger)
        status = generate_briefing(config)
        assert "No ledger files found" in status
