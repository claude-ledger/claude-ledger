"""Shared fixtures for claude-ledger tests."""

import subprocess

import frontmatter
import pytest



@pytest.fixture
def tmp_ledger(tmp_path):
    """Create a temporary ledger directory with config."""
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / ".locks").mkdir()
    (ledger_dir / ".state" / "sessions").mkdir(parents=True)

    # Write a minimal config
    config_content = """\
version: 1
scan_dirs: []
github_user: null
stray_scan_dirs: []
stale_days: 7
skip_slugs: []
no_track: []
ignore_dirs: [node_modules, .git, __pycache__]
workstreams: {}
"""
    (ledger_dir / "ledger.yaml").write_text(config_content)
    return ledger_dir


@pytest.fixture
def config(tmp_ledger):
    """Load config from the temporary ledger directory."""
    from claude_ledger.config import load_config
    return load_config(tmp_ledger)


@pytest.fixture
def sample_project(tmp_path):
    """Create a sample git project directory with commits."""
    proj = tmp_path / "my-project"
    proj.mkdir()

    # Init git repo
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(proj), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(proj), capture_output=True,
    )

    # Create a file and commit
    (proj / "README.md").write_text("# My Project\n\nA test project for unit tests.")
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(proj), capture_output=True,
    )

    # Create CLAUDE.md
    (proj / "CLAUDE.md").write_text(
        "## What This Is\n\nA sample project for testing.\n\n## Status\n\nActive\n"
    )
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add CLAUDE.md"],
        cwd=str(proj), capture_output=True,
    )

    return proj


@pytest.fixture
def sample_ledger_file(tmp_ledger):
    """Create a sample ledger .md file and return its path."""
    post = frontmatter.Post("## Activity Log\n\nNo commits yet.")
    post.metadata = {
        "name": "Test Project",
        "slug": "test-project",
        "directory": "/tmp/test-project",
        "repo_url": None,
        "status": "active",
        "priority": "P1",
        "vision": "A test project",
        "current_phase": "building",
        "last_session": "2026-03-20T10:00:00Z",
        "last_activity": "Initial commit",
        "systems": [],
        "tags": ["python"],
        "workstreams": [],
    }
    path = tmp_ledger / "test-project.md"
    with open(path, "w") as f:
        f.write(frontmatter.dumps(post))
    return path
