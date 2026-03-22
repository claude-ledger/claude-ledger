"""Tests for the activity capture system."""

import json
from datetime import datetime, timezone

import frontmatter

from claude_ledger.capture import (
    _append_activity,
    _get_session_state,
    _insert_bullet_into_content,
    _is_safe_path_component,
    _resolve_project_from_cwd,
    _resolve_project_from_path,
    _save_session_state,
    _touch_project,
    handle_commit,
    handle_session_end,
    handle_stop_note,
    handle_touch,
    rebuild_directory_index,
)
from claude_ledger.config import load_config


class TestResolveProjectFromPath:
    def test_resolves_known_project(self, tmp_ledger, sample_ledger_file):
        # Update the ledger file directory to point to a real-ish path
        post = frontmatter.load(str(sample_ledger_file))
        post.metadata["directory"] = "/tmp/test-project"
        with open(sample_ledger_file, "w") as f:
            f.write(frontmatter.dumps(post))

        slug, directory = _resolve_project_from_path("/tmp/test-project/src/main.py", tmp_ledger)
        assert slug == "test-project"
        assert directory == "/tmp/test-project"

    def test_returns_none_for_unknown(self, tmp_ledger):
        slug, directory = _resolve_project_from_path("/unknown/path/file.py", tmp_ledger)
        assert slug is None

    def test_returns_none_for_empty(self, tmp_ledger):
        slug, directory = _resolve_project_from_path("", tmp_ledger)
        assert slug is None

    def test_uses_directory_index_cache(self, tmp_ledger):
        # Create an index cache
        index = {"/tmp/cached-project": "cached-slug"}
        with open(tmp_ledger / "_directory_index.json", "w") as f:
            json.dump(index, f)

        slug, directory = _resolve_project_from_path("/tmp/cached-project/file.py", tmp_ledger)
        assert slug == "cached-slug"


class TestSessionState:
    def test_get_nonexistent_session(self, tmp_ledger):
        config = load_config(tmp_ledger)
        state = _get_session_state("test-session-123", config.state_dir)
        assert state["session_id"] == "test-session-123"
        assert state["projects"] == {}

    def test_save_and_load(self, tmp_ledger):
        config = load_config(tmp_ledger)
        state = {
            "session_id": "test-session",
            "started_at": "2026-03-22T10:00:00Z",
            "updated_at": "2026-03-22T10:00:00Z",
            "projects": {"my-proj": {"touched": True}},
        }
        _save_session_state("test-session", state, config.state_dir, config.locks_dir)

        loaded = _get_session_state("test-session", config.state_dir)
        assert loaded["projects"]["my-proj"]["touched"] is True


class TestTouchProject:
    def test_touch_new_project(self, tmp_ledger):
        config = load_config(tmp_ledger)
        _touch_project("sess-1", "my-proj", "/tmp/my-proj", config.state_dir, config.locks_dir)

        state = _get_session_state("sess-1", config.state_dir)
        assert "my-proj" in state["projects"]
        assert state["projects"]["my-proj"]["touched"] is True

    def test_touch_existing_updates_timestamp(self, tmp_ledger):
        config = load_config(tmp_ledger)
        _touch_project("sess-1", "my-proj", "/tmp/my-proj", config.state_dir, config.locks_dir)
        first_touch = _get_session_state("sess-1", config.state_dir)["projects"]["my-proj"]["last_touched_at"]

        import time
        time.sleep(0.01)
        _touch_project("sess-1", "my-proj", "/tmp/my-proj", config.state_dir, config.locks_dir)
        second_touch = _get_session_state("sess-1", config.state_dir)["projects"]["my-proj"]["last_touched_at"]

        assert second_touch >= first_touch


class TestAppendActivity:
    def test_appends_to_existing_heading(self, tmp_ledger, sample_ledger_file):
        # Rewrite with a date heading
        post = frontmatter.load(str(sample_ledger_file))
        today = datetime.now(timezone.utc)
        heading = f"{today.day} {today.strftime('%B %Y')}"
        post.content = f"## Activity Log\n\n### {heading}\n- Existing entry\n"
        with open(sample_ledger_file, "w") as f:
            f.write(frontmatter.dumps(post))

        success = _append_activity("test-project", "- New entry (abc123)", tmp_ledger, tmp_ledger / ".locks")
        assert success is True

        loaded = frontmatter.load(str(sample_ledger_file))
        assert "New entry (abc123)" in loaded.content
        assert "Existing entry" in loaded.content

    def test_creates_new_heading(self, tmp_ledger, sample_ledger_file):
        success = _append_activity("test-project", "- First entry today (def456)", tmp_ledger, tmp_ledger / ".locks")
        assert success is True

        loaded = frontmatter.load(str(sample_ledger_file))
        assert "First entry today (def456)" in loaded.content

    def test_returns_false_for_missing_project(self, tmp_ledger):
        success = _append_activity("nonexistent", "- Entry", tmp_ledger, tmp_ledger / ".locks")
        assert success is False

    def test_updates_last_activity_metadata(self, tmp_ledger, sample_ledger_file):
        _append_activity("test-project", "- Updated something (xyz)", tmp_ledger, tmp_ledger / ".locks")
        loaded = frontmatter.load(str(sample_ledger_file))
        assert "Updated something" in loaded.metadata.get("last_activity", "")


class TestHandleTouch:
    def test_touch_with_known_project(self, tmp_ledger, sample_ledger_file):
        # Update directory to match
        post = frontmatter.load(str(sample_ledger_file))
        post.metadata["directory"] = "/tmp/test-project"
        with open(sample_ledger_file, "w") as f:
            f.write(frontmatter.dumps(post))

        hook_data = {
            "session_id": "sess-touch",
            "tool_input": {"file_path": "/tmp/test-project/src/app.py"},
        }
        handle_touch(hook_data, tmp_ledger)

        config = load_config(tmp_ledger)
        state = _get_session_state("sess-touch", config.state_dir)
        assert "test-project" in state["projects"]

    def test_touch_ignores_unknown_path(self, tmp_ledger):
        hook_data = {
            "session_id": "sess-touch",
            "tool_input": {"file_path": "/unknown/random/file.py"},
        }
        handle_touch(hook_data, tmp_ledger)

        config = load_config(tmp_ledger)
        state = _get_session_state("sess-touch", config.state_dir)
        assert state["projects"] == {}

    def test_touch_ignores_missing_session_id(self, tmp_ledger):
        hook_data = {"tool_input": {"file_path": "/some/file.py"}}
        handle_touch(hook_data, tmp_ledger)  # Should not raise


class TestHandleStopNote:
    def test_stores_summary(self, tmp_ledger, sample_ledger_file):
        post = frontmatter.load(str(sample_ledger_file))
        post.metadata["directory"] = "/tmp/test-project"
        with open(sample_ledger_file, "w") as f:
            f.write(frontmatter.dumps(post))

        # First touch the project so it's in session state
        config = load_config(tmp_ledger)
        _touch_project("sess-stop", "test-project", "/tmp/test-project", config.state_dir, config.locks_dir)

        hook_data = {
            "session_id": "sess-stop",
            "cwd": "/tmp/test-project",
            "last_assistant_message": "I fixed the authentication bug and added tests.",
        }
        handle_stop_note(hook_data, tmp_ledger)

        state = _get_session_state("sess-stop", config.state_dir)
        assert state["projects"]["test-project"]["latest_stop_summary"] is not None
        assert "authentication" in state["projects"]["test-project"]["latest_stop_summary"]


class TestHandleSessionEnd:
    def test_finalises_session(self, tmp_ledger, sample_ledger_file):
        post = frontmatter.load(str(sample_ledger_file))
        post.metadata["directory"] = "/tmp/test-project"
        with open(sample_ledger_file, "w") as f:
            f.write(frontmatter.dumps(post))

        config = load_config(tmp_ledger)

        # Simulate a session with a touch and a stop note
        _touch_project("sess-end", "test-project", "/tmp/test-project", config.state_dir, config.locks_dir)
        state = _get_session_state("sess-end", config.state_dir)
        state["projects"]["test-project"]["latest_stop_summary"] = "Wrapped up the feature."
        _save_session_state("sess-end", state, config.state_dir, config.locks_dir)

        # Init git in ledger dir so session-end can commit
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_ledger), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_ledger), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_ledger), capture_output=True)

        hook_data = {"session_id": "sess-end", "cwd": "/tmp/test-project"}
        handle_session_end(hook_data, tmp_ledger)

        # Session state should be cleaned up
        assert not (config.state_dir / "sess-end.json").exists()

        # Ledger file should have session summary
        loaded = frontmatter.load(str(sample_ledger_file))
        assert "Wrapped up the feature" in loaded.content


class TestRebuildDirectoryIndex:
    def test_builds_index(self, tmp_ledger, sample_ledger_file):
        rebuild_directory_index(tmp_ledger)
        index_path = tmp_ledger / "_directory_index.json"
        assert index_path.exists()

        with open(index_path) as f:
            index = json.load(f)
        assert "/tmp/test-project" in index
        assert index["/tmp/test-project"] == "test-project"


class TestSafePathComponent:
    def test_normal_session_id(self):
        assert _is_safe_path_component("abc-123_def.456") is True

    def test_rejects_traversal(self):
        assert _is_safe_path_component("../../etc/passwd") is False

    def test_rejects_empty(self):
        assert _is_safe_path_component("") is False

    def test_rejects_slash(self):
        assert _is_safe_path_component("foo/bar") is False

    def test_rejects_dotdot_in_middle(self):
        assert _is_safe_path_component("foo..bar") is False


class TestInsertBulletIntoContent:
    def test_inserts_under_existing_heading(self):
        content = "## Activity Log\n\n### 22 March 2026\n- Existing entry\n"
        result = _insert_bullet_into_content(content, "### 22 March 2026", "- New entry")
        assert "- New entry" in result
        assert "- Existing entry" in result
        # New entry should come after existing
        assert result.index("- Existing entry") < result.index("- New entry")

    def test_creates_heading_under_activity_log(self):
        content = "## Activity Log\n\nSome old content.\n"
        result = _insert_bullet_into_content(content, "### 22 March 2026", "- First entry")
        assert "### 22 March 2026" in result
        assert "- First entry" in result

    def test_prepends_when_no_activity_log(self):
        content = "Just some text."
        result = _insert_bullet_into_content(content, "### 22 March 2026", "- Entry")
        assert result.startswith("## Activity Log")
        assert "- Entry" in result


class TestResolveProjectFromCwd:
    def test_resolves_known_cwd(self, tmp_ledger, sample_ledger_file):
        # Build a directory index for fast lookup
        rebuild_directory_index(tmp_ledger)
        slug, directory = _resolve_project_from_cwd("/tmp/test-project", tmp_ledger)
        assert slug == "test-project"
        assert directory == "/tmp/test-project"

    def test_returns_none_for_unknown(self, tmp_ledger):
        slug, directory = _resolve_project_from_cwd("/unknown/path", tmp_ledger)
        assert slug is None

    def test_returns_none_for_empty(self, tmp_ledger):
        slug, directory = _resolve_project_from_cwd("", tmp_ledger)
        assert slug is None

    def test_ignores_home_dir(self, tmp_ledger):
        from pathlib import Path
        home = str(Path.home())
        slug, directory = _resolve_project_from_cwd(home, tmp_ledger)
        assert slug is None


class TestHandleCommit:
    def test_ignores_non_commit_commands(self, tmp_ledger, sample_ledger_file):
        hook_data = {
            "session_id": "sess-commit",
            "cwd": "/tmp/test-project",
            "tool_input": {"command": "git status"},
        }
        handle_commit(hook_data, tmp_ledger)
        config = load_config(tmp_ledger)
        state = _get_session_state("sess-commit", config.state_dir)
        # Should not have any commits recorded
        proj = state.get("projects", {}).get("test-project", {})
        assert proj.get("commits", []) == []

    def test_ignores_missing_session_id(self, tmp_ledger):
        hook_data = {
            "cwd": "/tmp/test-project",
            "tool_input": {"command": "git commit -m 'test'"},
        }
        handle_commit(hook_data, tmp_ledger)  # Should not raise

    def test_ignores_failed_commit(self, tmp_ledger, sample_ledger_file):
        # Build index so cwd resolves
        rebuild_directory_index(tmp_ledger)
        hook_data = {
            "session_id": "sess-commit-fail",
            "cwd": "/tmp/test-project",
            "tool_input": {"command": "git commit -m 'test'"},
            "tool_response": {"stdout": "nothing to commit"},
        }
        handle_commit(hook_data, tmp_ledger)
        config = load_config(tmp_ledger)
        state = _get_session_state("sess-commit-fail", config.state_dir)
        proj = state.get("projects", {}).get("test-project", {})
        assert proj.get("commits", []) == []
