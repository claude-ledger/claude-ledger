"""Tests for shared utilities."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import frontmatter
import pytest

from claude_ledger.utils import (
    acquire_lock,
    atomic_write_frontmatter,
    atomic_write_json,
    days_since,
    format_date_heading,
    format_date_short,
    log_error,
    release_lock,
)


class TestFormatDateHeading:
    def test_formats_date(self):
        dt = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)
        assert format_date_heading(dt) == "20 March 2026"

    def test_single_digit_day_no_leading_zero(self):
        dt = datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc)
        assert format_date_heading(dt) == "5 January 2026"

    def test_defaults_to_now(self):
        result = format_date_heading()
        assert len(result) > 5
        # Should contain a month name
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        assert any(m in result for m in months)


class TestFormatDateShort:
    def test_formats_iso_date(self):
        assert format_date_short("2026-03-20T10:00:00Z") == "20 March"

    def test_returns_question_mark_for_none(self):
        assert format_date_short(None) == "?"

    def test_returns_question_mark_for_empty(self):
        assert format_date_short("") == "?"

    def test_returns_question_mark_for_invalid(self):
        assert format_date_short("not-a-date") == "?"

    def test_handles_timezone_offset(self):
        result = format_date_short("2026-03-20T10:00:00+01:00")
        assert "20" in result
        assert "March" in result


class TestDaysSince:
    def test_returns_999_for_none(self):
        assert days_since(None) == 999

    def test_returns_999_for_empty(self):
        assert days_since("") == 999

    def test_returns_999_for_invalid(self):
        assert days_since("not-a-date") == 999

    def test_recent_date_returns_small_number(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        result = days_since(yesterday)
        assert 0 <= result <= 2

    def test_old_date_returns_large_number(self):
        old = "2020-01-01T00:00:00Z"
        result = days_since(old)
        assert result > 365

    def test_handles_naive_datetime(self):
        naive = "2026-03-20T10:00:00"
        result = days_since(naive)
        assert isinstance(result, int)


class TestAtomicWriteJson:
    def test_writes_valid_json(self, tmp_path):
        path = tmp_path / "test.json"
        data = {"key": "value", "number": 42}
        atomic_write_json(path, data)

        with open(path) as f:
            loaded = json.load(f)
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "test.json"
        atomic_write_json(path, {"ok": True})
        assert path.exists()

    def test_no_temp_files_left(self, tmp_path):
        path = tmp_path / "test.json"
        atomic_write_json(path, {"ok": True})
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "test.json"


class TestAtomicWriteFrontmatter:
    def test_writes_valid_frontmatter(self, tmp_path):
        path = tmp_path / "test.md"
        post = frontmatter.Post("Some content")
        post.metadata = {"title": "Test", "status": "active"}
        atomic_write_frontmatter(path, post)

        loaded = frontmatter.load(str(path))
        assert loaded.metadata["title"] == "Test"
        assert loaded.content == "Some content"


class TestFileLocking:
    def test_acquire_and_release(self, tmp_path):
        locks_dir = tmp_path / "locks"
        fd = acquire_lock("test", locks_dir, timeout_secs=1)
        assert fd is not None
        release_lock(fd)

    def test_creates_locks_dir(self, tmp_path):
        locks_dir = tmp_path / "nonexistent" / "locks"
        fd = acquire_lock("test", locks_dir, timeout_secs=1)
        assert fd is not None
        assert locks_dir.exists()
        release_lock(fd)

    def test_release_none_is_safe(self):
        release_lock(None)  # Should not raise


class TestLogError:
    def test_appends_to_file(self, tmp_path):
        log_path = tmp_path / "errors.log"
        log_error(log_path, "test error 1")
        log_error(log_path, "test error 2")

        content = log_path.read_text()
        assert "test error 1" in content
        assert "test error 2" in content

    def test_handles_missing_parent_dir(self, tmp_path):
        log_path = tmp_path / "nonexistent" / "errors.log"
        # Should not raise
        log_error(log_path, "test")
