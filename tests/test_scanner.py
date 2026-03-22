"""Tests for the portfolio scanner."""

import json


from claude_ledger.config import Config
from claude_ledger.scanner import (
    _extract_readme_title,
    _is_boilerplate_line,
    extract_claude_md,
    extract_readme,
    scan_git_metadata,
    scan_local_directory,
    scan_portfolio,
    scan_structure,
    scan_tech_stack,
    save_scan_results,
)


class TestScanGitMetadata:
    def test_non_git_dir(self, tmp_path):
        result = scan_git_metadata(tmp_path)
        assert result["has_git"] is False
        assert result["commit_count_30d"] == 0

    def test_git_dir(self, sample_project):
        result = scan_git_metadata(sample_project)
        assert result["has_git"] is True
        assert result["commit_count_30d"] >= 1
        assert result["last_commit_subject"] is not None
        assert len(result["recent_commits"]) >= 1


class TestExtractClaudeMd:
    def test_no_claude_md(self, tmp_path):
        result = extract_claude_md(tmp_path)
        assert result["has_claude_md"] is False

    def test_with_claude_md(self, sample_project):
        result = extract_claude_md(sample_project)
        assert result["has_claude_md"] is True
        assert "sample project" in result["what_is_this"].lower()

    def test_with_status_section(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text(
            "## What This Is\n\nMy tool.\n\n## Current Status\n\nIn progress.\n"
        )
        result = extract_claude_md(tmp_path)
        assert result["status"] is not None
        assert "progress" in result["status"].lower()


class TestReadmeTitleExtraction:
    def test_extracts_title(self):
        lines = ["# My Project", "", "Description here."]
        assert _extract_readme_title(lines) == "My Project"

    def test_skips_badge_title(self):
        lines = ["# [![badge](url)", "", "Content"]
        assert _extract_readme_title(lines) is None

    def test_no_title(self):
        lines = ["Just some text", "no heading"]
        assert _extract_readme_title(lines) is None


class TestBoilerplateDetection:
    def test_badge_is_boilerplate(self):
        assert _is_boilerplate_line("[![npm](https://badge.com)]") is True

    def test_code_block_is_boilerplate(self):
        assert _is_boilerplate_line("```bash") is True

    def test_yarn_dev_is_boilerplate(self):
        assert _is_boilerplate_line("yarn dev") is True

    def test_nextjs_scaffold_is_boilerplate(self):
        assert _is_boilerplate_line("This is a [Next.js](https://nextjs.org) project") is True

    def test_normal_text_is_not_boilerplate(self):
        assert _is_boilerplate_line("A portfolio tracking tool for developers.") is False


class TestExtractReadme:
    def test_no_readme(self, tmp_path):
        assert extract_readme(tmp_path) is None

    def test_with_readme(self, sample_project):
        result = extract_readme(sample_project)
        assert result is not None
        assert "test project" in result.lower()

    def test_empty_readme(self, tmp_path):
        (tmp_path / "README.md").write_text("# Title\n")
        assert extract_readme(tmp_path) is None

    def test_filters_badges(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "# My Tool\n\n[![badge](https://img.shields.io/x)](url)\n\nActual description here.\n"
        )
        result = extract_readme(tmp_path)
        assert result == "Actual description here."

    def test_filters_code_blocks(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "# Next App\n\n```bash\nnpm run dev\nyarn dev\npnpm dev\n```\n\nReal description.\n"
        )
        result = extract_readme(tmp_path)
        assert result == "Real description."

    def test_filters_nextjs_boilerplate(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "# My App\n\nThis is a [Next.js](https://nextjs.org/) project bootstrapped with create-next-app.\n"
        )
        result = extract_readme(tmp_path)
        assert result is None


class TestScanTechStack:
    def test_node_project(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"next": "^14", "react": "^18"}, "description": "A Next.js app"}'
        )
        stack, desc, has_pkg = scan_tech_stack(tmp_path)
        assert "next.js" in stack
        assert "react" in stack
        assert desc == "A Next.js app"
        assert has_pkg is True

    def test_python_project(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\nrequests\n")
        stack, desc, has_pkg = scan_tech_stack(tmp_path)
        assert "python" in stack
        assert has_pkg is False

    def test_html_project(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>")
        stack, desc, has_pkg = scan_tech_stack(tmp_path)
        assert "html" in stack

    def test_unknown_project(self, tmp_path):
        stack, desc, has_pkg = scan_tech_stack(tmp_path)
        assert "unknown" in stack


class TestScanStructure:
    def test_basic_structure(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "file.py").write_text("pass")
        result = scan_structure(tmp_path)
        assert result["has_docs"] is True
        assert result["has_tests"] is True
        assert result["file_count"] >= 1

    def test_mcp_json(self, tmp_path):
        (tmp_path / ".mcp.json").write_text('{"mcpServers": {"airtable": {}}}')
        result = scan_structure(tmp_path)
        assert result["has_mcp_json"] is True
        assert "airtable" in result["external_systems"]


class TestScanLocalDirectory:
    def test_scans_project(self, sample_project):
        result = scan_local_directory(sample_project)
        assert result["slug"] == "my-project"
        assert result["scan_status"] == "ok"
        assert result["has_git"] is True
        assert result["has_claude_md"] is True


class TestScanPortfolio:
    def test_scans_directory(self, sample_project, tmp_path):
        # sample_project is at tmp_path/my-project
        parent = sample_project.parent
        config = Config(scan_dirs=[parent])
        results = scan_portfolio(config)
        assert results.summary["total_local_dirs"] >= 1
        found = [p for p in results.projects if p["slug"] == "my-project"]
        assert len(found) == 1

    def test_skips_missing_dirs(self, tmp_path):
        config = Config(scan_dirs=[tmp_path / "nonexistent"])
        results = scan_portfolio(config)
        assert results.summary["total_local_dirs"] == 0

    def test_save_results(self, tmp_path):
        from claude_ledger.scanner import ScanResults
        results = ScanResults(scan_date="2026-03-22", summary={"total": 5})
        path = tmp_path / "results.json"
        save_scan_results(results, path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["summary"]["total"] == 5
