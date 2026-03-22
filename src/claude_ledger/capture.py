"""Activity capture — the hook workhorse.

Called by Claude Code hooks to track file edits, git commits,
session summaries, and session end finalisation.

Modes:
  --touch        Record project activity from file edits (PostToolUse on Edit/Write/MultiEdit)
  --commit       Capture git commit metadata (PostToolUse on Bash)
  --stop-note    Store latest assistant message as session summary (Stop hook)
  --session-end  Finalise all touched projects, commit ledger repo (SessionEnd hook)

Reads hook JSON from stdin.
"""

from __future__ import annotations

__all__ = [
    "handle_touch",
    "handle_commit",
    "handle_stop_note",
    "handle_session_end",
    "rebuild_directory_index",
]

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

from claude_ledger.config import DEFAULT_LEDGER_DIR, load_config

# Session IDs and slugs used in file paths must match this pattern.
_SAFE_PATH_COMPONENT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def _is_safe_path_component(value: str) -> bool:
    """Validate that a value is safe to use in file path construction."""
    return bool(value and _SAFE_PATH_COMPONENT_RE.match(value) and ".." not in value)
from claude_ledger.utils import (
    acquire_lock,
    atomic_write_frontmatter,
    atomic_write_json,
    format_date_heading,
    log_error,
    release_lock,
)


def _read_stdin() -> dict[str, Any]:
    """Read hook JSON from stdin."""
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.read()
            if data.strip():
                return json.loads(data)
    except Exception:
        pass
    return {}


def _get_ledger_dir() -> Path:
    """Get ledger directory from environment or default."""
    env = os.environ.get("CLAUDE_LEDGER_DIR")
    if env:
        return Path(env).expanduser()
    return DEFAULT_LEDGER_DIR


def _resolve_project_from_path(file_path: str, ledger_dir: Path) -> tuple[str | None, str | None]:
    """Resolve a file path to a project slug by matching against ledger files."""
    if not file_path:
        return None, None

    file_path = os.path.abspath(file_path)

    # Try directory index cache first (fast path)
    index_path = ledger_dir / "_directory_index.json"
    if index_path.exists():
        try:
            with open(index_path) as f:
                index = json.load(f)
            for proj_dir, slug in index.items():
                if file_path.startswith(proj_dir + "/"):
                    return slug, proj_dir
        except (json.JSONDecodeError, OSError):
            pass

    # Fall back to scanning ledger files
    for f in ledger_dir.glob("*.md"):
        if f.name.startswith("_") or f.name.endswith("-archive.md"):
            continue
        try:
            post = frontmatter.load(f)
            proj_dir = post.metadata.get("directory", "")
            if proj_dir and file_path.startswith(proj_dir + "/"):
                return post.metadata.get("slug", f.stem), proj_dir
        except Exception:
            continue

    return None, None


def _resolve_project_from_cwd(cwd: str, ledger_dir: Path) -> tuple[str | None, str | None]:
    """Resolve cwd to a project slug.

    Checks the directory index first (fast path, ~0.02ms) before falling
    back to ``git rev-parse --show-toplevel`` (~8.5ms subprocess overhead).
    """
    if not cwd:
        return None, None

    ignore = {str(ledger_dir), str(Path.home() / ".claude"), str(Path.home())}
    if cwd in ignore:
        return None, None

    # Fast path: try directory index before spawning git
    slug, proj_dir = _resolve_project_from_path(cwd + "/dummy", ledger_dir)
    if slug:
        return slug, proj_dir

    # Slow path: ask git for the repo root (cwd may be a subdirectory)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if result.returncode == 0:
            repo_root = result.stdout.strip()
            slug, proj_dir = _resolve_project_from_path(repo_root + "/dummy", ledger_dir)
            if slug:
                return slug, proj_dir
    except (subprocess.TimeoutExpired, OSError):
        pass

    return None, None


def _get_session_state(session_id: str, state_dir: Path) -> dict[str, Any]:
    """Load session state file."""
    if not _is_safe_path_component(session_id):
        return {"session_id": session_id, "started_at": "", "updated_at": "", "projects": {}}
    state_path = state_dir / f"{session_id}.json"
    if state_path.exists():
        try:
            with open(state_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "session_id": session_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "projects": {},
    }


def _save_session_state(
    session_id: str, state: dict[str, Any], state_dir: Path, locks_dir: Path
) -> None:
    """Save session state file."""
    if not _is_safe_path_component(session_id):
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_path = state_dir / f"{session_id}.json"

    fd = acquire_lock(f"session-{session_id}", locks_dir, timeout_secs=1)
    if fd is None:
        return
    try:
        atomic_write_json(state_path, state)
    finally:
        release_lock(fd)


def _touch_project(
    session_id: str, slug: str, directory: str | None,
    state_dir: Path, locks_dir: Path,
) -> None:
    """Mark a project as touched in session state."""
    state = _get_session_state(session_id, state_dir)
    now = datetime.now(timezone.utc).isoformat()

    if slug not in state["projects"]:
        state["projects"][slug] = {
            "directory": directory,
            "touched": True,
            "last_touched_at": now,
            "latest_stop_summary": None,
            "commits": [],
        }
    else:
        state["projects"][slug]["last_touched_at"] = now
        state["projects"][slug]["touched"] = True

    _save_session_state(session_id, state, state_dir, locks_dir)


def _append_activity(slug: str, bullet: str, ledger_dir: Path, locks_dir: Path) -> bool:
    """Append an activity bullet to a project's ledger file."""
    ledger_path = ledger_dir / f"{slug}.md"
    if not ledger_path.exists():
        return False

    fd = acquire_lock(slug, locks_dir, timeout_secs=2)
    if fd is None:
        return False

    try:
        post = frontmatter.load(ledger_path)
        heading = f"### {format_date_heading()}"
        post.content = _insert_bullet_into_content(post.content or "", heading, bullet)

        post.metadata["last_session"] = datetime.now(timezone.utc).isoformat()
        post.metadata["last_activity"] = bullet.lstrip("- ").strip()[:120]

        atomic_write_frontmatter(ledger_path, post)
        return True
    except Exception:
        return False
    finally:
        release_lock(fd)


def _insert_bullet_into_content(content: str, heading: str, bullet: str) -> str:
    """Insert a bullet under a date heading using line-based logic.

    If the heading exists, appends the bullet after existing entries under it.
    If not, creates the heading under ``## Activity Log`` (or prepends it).
    """
    if heading in content:
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == heading:
                insert_idx = i + 1
                while insert_idx < len(lines):
                    next_line = lines[insert_idx].strip()
                    if next_line.startswith("### ") or (
                        next_line == ""
                        and insert_idx + 1 < len(lines)
                        and lines[insert_idx + 1].strip().startswith("### ")
                    ):
                        break
                    insert_idx += 1
                lines.insert(insert_idx, bullet)
                return "\n".join(lines)
    elif "## Activity Log" in content:
        return content.replace(
            "## Activity Log\n",
            f"## Activity Log\n\n{heading}\n{bullet}\n",
        )
    return f"## Activity Log\n\n{heading}\n{bullet}\n" + content


# === MODE HANDLERS ===


def handle_touch(hook_data: dict[str, Any], ledger_dir: Path) -> None:
    """Record project from edited file paths."""
    session_id = hook_data.get("session_id")
    if not session_id:
        return

    file_path = hook_data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    config = load_config(ledger_dir)
    slug, directory = _resolve_project_from_path(file_path, ledger_dir)
    if slug:
        _touch_project(session_id, slug, directory, config.state_dir, config.locks_dir)


def handle_commit(hook_data: dict[str, Any], ledger_dir: Path) -> None:
    """Capture git commit metadata."""
    session_id = hook_data.get("session_id")
    cwd = hook_data.get("cwd", "")
    if not session_id:
        return

    config = load_config(ledger_dir)
    slug, directory = _resolve_project_from_cwd(cwd, ledger_dir)
    if slug:
        _touch_project(session_id, slug, directory, config.state_dir, config.locks_dir)

    # Check if the Bash command was a git commit
    command = hook_data.get("tool_input", {}).get("command", "")
    if not re.search(r"^\s*git\s+commit\b", command):
        return

    if not slug:
        return

    # Check if commit succeeded
    stdout = str(hook_data.get("tool_response", {}).get("stdout", ""))
    if not re.search(r"\[.+\s+[a-f0-9]+\]", stdout):
        return

    # Capture commit metadata (NUL delimiter — subjects can contain pipes)
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h%x00%s%x00%cI"],
            capture_output=True, text=True,
            cwd=directory or cwd, timeout=5,
        )
        if result.returncode != 0:
            return
        parts = result.stdout.strip().split("\0", 2)
        if len(parts) < 3:
            return
        sha, subject, commit_time = parts
    except (subprocess.TimeoutExpired, OSError):
        return

    # Update session state
    state = _get_session_state(session_id, config.state_dir)
    proj_state = state.get("projects", {}).get(slug, {})
    commits = proj_state.get("commits", [])

    if any(c.get("sha") == sha for c in commits):
        return

    commits.append({
        "sha": sha,
        "subject": subject,
        "committed_at": commit_time,
        "captured_to_ledger": False,
    })

    if slug not in state["projects"]:
        state["projects"][slug] = {
            "directory": directory,
            "touched": True,
            "last_touched_at": datetime.now(timezone.utc).isoformat(),
            "latest_stop_summary": None,
            "commits": commits,
        }
    else:
        state["projects"][slug]["commits"] = commits

    _save_session_state(session_id, state, config.state_dir, config.locks_dir)

    # Write to ledger immediately
    bullet = f"- {subject} ({sha})"
    success = _append_activity(slug, bullet, ledger_dir, config.locks_dir)

    if success:
        state = _get_session_state(session_id, config.state_dir)
        for c in state.get("projects", {}).get(slug, {}).get("commits", []):
            if c.get("sha") == sha:
                c["captured_to_ledger"] = True
        _save_session_state(session_id, state, config.state_dir, config.locks_dir)


def handle_stop_note(hook_data: dict[str, Any], ledger_dir: Path) -> None:
    """Store latest assistant message as session summary."""
    session_id = hook_data.get("session_id")
    if not session_id:
        return

    last_msg = hook_data.get("last_assistant_message", "")
    if not last_msg:
        return

    summary = last_msg[:200].replace("\n", " ").strip()
    config = load_config(ledger_dir)
    state = _get_session_state(session_id, config.state_dir)
    cwd = hook_data.get("cwd", "")
    slug, directory = _resolve_project_from_cwd(cwd, ledger_dir)

    if slug and slug in state.get("projects", {}):
        state["projects"][slug]["latest_stop_summary"] = summary
    elif slug:
        _touch_project(session_id, slug, directory, config.state_dir, config.locks_dir)
        state = _get_session_state(session_id, config.state_dir)
        if slug in state.get("projects", {}):
            state["projects"][slug]["latest_stop_summary"] = summary

    _save_session_state(session_id, state, config.state_dir, config.locks_dir)


def handle_session_end(hook_data: dict[str, Any], ledger_dir: Path) -> None:
    """Finalise all touched projects and commit ledger repo."""
    session_id = hook_data.get("session_id")
    if not session_id:
        return

    config = load_config(ledger_dir)
    state = _get_session_state(session_id, config.state_dir)
    projects = state.get("projects", {})

    if not projects:
        cwd = hook_data.get("cwd", "")
        slug, directory = _resolve_project_from_cwd(cwd, ledger_dir)
        if slug:
            ledger_path = ledger_dir / f"{slug}.md"
            if ledger_path.exists():
                fd = acquire_lock(slug, config.locks_dir, timeout_secs=4)
                if fd:
                    try:
                        post = frontmatter.load(ledger_path)
                        post.metadata["last_session"] = datetime.now(timezone.utc).isoformat()
                        atomic_write_frontmatter(ledger_path, post)
                    finally:
                        release_lock(fd)
        _commit_ledger_repo(ledger_dir)
        _cleanup_session(session_id, config.state_dir)
        return

    for slug, proj_data in sorted(projects.items()):
        if not proj_data.get("touched"):
            continue

        ledger_path = ledger_dir / f"{slug}.md"
        if not ledger_path.exists():
            continue

        fd = acquire_lock(slug, config.locks_dir, timeout_secs=4)
        if fd is None:
            continue

        try:
            post = frontmatter.load(ledger_path)

            heading = f"### {format_date_heading()}"

            # Replay uncaptured commits
            for c in proj_data.get("commits", []):
                if not c.get("captured_to_ledger"):
                    bullet = f"- {c['subject']} ({c['sha']})"
                    post.content = _insert_bullet_into_content(
                        post.content or "", heading, bullet,
                    )

            # Write session summary for commit-free projects
            commits = proj_data.get("commits", [])
            summary = proj_data.get("latest_stop_summary")
            if not commits and summary:
                bullet = f"- [Session] {summary[:120]}"
                post.content = _insert_bullet_into_content(
                    post.content or "", heading, bullet,
                )

            post.metadata["last_session"] = datetime.now(timezone.utc).isoformat()
            if summary:
                post.metadata["last_activity"] = summary[:120]

            atomic_write_frontmatter(ledger_path, post)
        except Exception as e:
            log_error(config.errors_log, f"session-end failed for {slug}: {e}")
        finally:
            release_lock(fd)

    _commit_ledger_repo(ledger_dir)
    _cleanup_session(session_id, config.state_dir)


def _commit_ledger_repo(ledger_dir: Path) -> None:
    """Commit any changes to the ledger git repo."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(ledger_dir), timeout=5,
        )
        if not result.stdout.strip():
            return

        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True, cwd=str(ledger_dir), timeout=5,
        )
        subprocess.run(
            ["git", "commit", "-m", "Ledger update"],
            capture_output=True, cwd=str(ledger_dir), timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def _cleanup_session(session_id: str, state_dir: Path) -> None:
    """Delete session state file after successful finalisation."""
    if not _is_safe_path_component(session_id):
        return
    state_path = state_dir / f"{session_id}.json"
    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        pass


def rebuild_directory_index(ledger_dir: Path) -> None:
    """Rebuild the directory→slug index cache for fast path resolution."""
    index: dict[str, str] = {}
    for f in ledger_dir.glob("*.md"):
        if f.name.startswith("_"):
            continue
        try:
            post = frontmatter.load(f)
            directory = post.metadata.get("directory", "")
            slug = post.metadata.get("slug", f.stem)
            if directory:
                index[directory] = slug
        except Exception:
            continue

    index_path = ledger_dir / "_directory_index.json"
    atomic_write_json(index_path, index)


def main() -> None:
    """Legacy entry point for hook invocation.

    Prefer the CLI subcommand: ``claude-ledger capture --touch|--commit|...``
    This is kept for backwards compatibility with hooks that call
    ``python -m claude_ledger.capture --touch`` directly.
    """
    hook_data = _read_stdin()

    if len(sys.argv) < 2:
        print("Usage: claude-ledger capture --touch|--commit|--stop-note|--session-end")
        sys.exit(1)

    mode = sys.argv[1]
    ledger_dir = _get_ledger_dir()

    handlers = {
        "--touch": handle_touch,
        "--commit": handle_commit,
        "--stop-note": handle_stop_note,
        "--session-end": handle_session_end,
    }

    handler = handlers.get(mode)
    if handler:
        handler(hook_data, ledger_dir)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
