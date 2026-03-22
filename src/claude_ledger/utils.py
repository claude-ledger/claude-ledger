"""Shared utilities: atomic writes, file locking, date formatting, frontmatter helpers."""

from __future__ import annotations

__all__ = [
    "acquire_lock",
    "release_lock",
    "atomic_write_json",
    "atomic_write_frontmatter",
    "format_date_heading",
    "format_date_short",
    "days_since",
    "load_ledger_file",
    "save_ledger_file",
    "log_error",
]

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter


# --- File Locking ---
# Cross-platform: fcntl on Unix, msvcrt on Windows, no-op fallback


def acquire_lock(name: str, locks_dir: Path, timeout_secs: float = 2.0) -> int | None:
    """Acquire a file lock with timeout.

    Args:
        name: Lock name (used as filename).
        locks_dir: Directory to store lock files.
        timeout_secs: Maximum seconds to wait.

    Returns:
        File descriptor on success, None on timeout/error.
    """
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / f"{name}.lock"

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    except OSError:
        return None

    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        try:
            _lock_fd(fd)
            return fd
        except (BlockingIOError, OSError):
            time.sleep(0.05)

    # Timeout
    os.close(fd)
    return None


def release_lock(fd: int | None) -> None:
    """Release a file lock."""
    if fd is None:
        return
    try:
        _unlock_fd(fd)
        os.close(fd)
    except OSError:
        pass


if sys.platform == "win32":
    import msvcrt

    def _lock_fd(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


# --- Atomic File Operations ---


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically via temp file + rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_frontmatter(path: Path, post: frontmatter.Post) -> None:
    """Write a frontmatter file atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w") as f:
            f.write(frontmatter.dumps(post))
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# --- Date Utilities ---


def format_date_heading(dt: datetime | None = None) -> str:
    """Format date as 'D Month YYYY' for activity log headings.

    Uses f-string day formatting for cross-platform compatibility
    (avoiding %-d which is macOS/Linux only).
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    return f"{dt.day} {dt.strftime('%B %Y')}"


def format_date_short(iso_str: str | None) -> str:
    """Format ISO date to 'D Month' for briefing output."""
    if not iso_str:
        return "?"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return f"{dt.day} {dt.strftime('%B')}"
    except (ValueError, AttributeError):
        return "?"


def days_since(iso_str: str | None) -> int:
    """Calculate days since an ISO date string. Returns 999 on parse failure."""
    if not iso_str:
        return 999
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.days
    except (ValueError, AttributeError):
        return 999


# --- Frontmatter Helpers ---


def load_ledger_file(path: Path) -> frontmatter.Post:
    """Load a ledger markdown file with YAML frontmatter."""
    return frontmatter.load(str(path))


def save_ledger_file(path: Path, post: frontmatter.Post) -> None:
    """Save a ledger markdown file atomically."""
    atomic_write_frontmatter(path, post)


# --- Logging ---


def log_error(errors_log: Path, msg: str) -> None:
    """Append an error message to the errors log."""
    try:
        errors_log.parent.mkdir(parents=True, exist_ok=True)
        with open(errors_log, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except OSError:
        pass
