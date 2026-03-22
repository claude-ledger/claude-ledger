"""Tests for the activity capture system."""

import json
from datetime import datetime, timezone

import frontmatter

from claude_ledger.capture import (
    _append_activity,
    _auto_discover_project,
    _get_session_state,
    _insert_bullet_into_content,
    _is_safe_path_component,
    _resolve_project_from_cwd,
    _resolve_project_from_path,
    _save_session_state,
    _touch_project,
    _update_directory_index,
    handle_commit,
    handle_session_end,
    handle_stop_note,
    handle_touch,
    rebuild_directory_index,
)
from claude_ledger.config import Config, SubProjectConfig, load_config


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


class TestAutoDiscoverProject:
    def test_discovers_new_project_in_scan_dir(self, tmp_path):
        """Auto-creates ledger file when editing a file in an untracked project."""
        scan_dir = tmp_path / "code"
        scan_dir.mkdir()
        project_dir = scan_dir / "new-project"
        project_dir.mkdir()
        (project_dir / "main.py").write_text("print('hello')")

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()

        config = Config(ledger_dir=ledger_dir, scan_dirs=[scan_dir])

        slug, directory = _auto_discover_project(
            str(project_dir / "main.py"), config, ledger_dir,
        )

        assert slug == "new-project"
        assert directory == str(project_dir)
        assert (ledger_dir / "new-project.md").exists()

        # Verify frontmatter
        import frontmatter as fm
        post = fm.load(str(ledger_dir / "new-project.md"))
        assert post.metadata["slug"] == "new-project"
        assert post.metadata["status"] == "active"
        assert post.metadata["current_phase"] == "discovered"

    def test_skips_file_outside_scan_dirs(self, tmp_path):
        """Does not discover projects outside configured scan dirs."""
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        config = Config(ledger_dir=ledger_dir, scan_dirs=[tmp_path / "code"])

        slug, directory = _auto_discover_project(
            "/some/other/path/file.py", config, ledger_dir,
        )
        assert slug is None

    def test_skips_configured_exclusions(self, tmp_path):
        """Respects skip_slugs config."""
        scan_dir = tmp_path / "code"
        project_dir = scan_dir / "excluded-project"
        project_dir.mkdir(parents=True)
        (project_dir / "file.py").write_text("")

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()

        config = Config(
            ledger_dir=ledger_dir,
            scan_dirs=[scan_dir],
            skip_slugs=["excluded-project"],
        )

        slug, _ = _auto_discover_project(
            str(project_dir / "file.py"), config, ledger_dir,
        )
        assert slug is None

    def test_skips_hidden_dirs(self, tmp_path):
        """Does not discover dotfile directories."""
        scan_dir = tmp_path / "code"
        hidden_dir = scan_dir / ".hidden"
        hidden_dir.mkdir(parents=True)
        (hidden_dir / "file.py").write_text("")

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        config = Config(ledger_dir=ledger_dir, scan_dirs=[scan_dir])

        slug, _ = _auto_discover_project(
            str(hidden_dir / "file.py"), config, ledger_dir,
        )
        assert slug is None

    def test_updates_directory_index(self, tmp_path):
        """Auto-discovery updates the directory index."""
        scan_dir = tmp_path / "code"
        project_dir = scan_dir / "indexed-proj"
        project_dir.mkdir(parents=True)
        (project_dir / "app.py").write_text("")

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        config = Config(ledger_dir=ledger_dir, scan_dirs=[scan_dir])

        _auto_discover_project(str(project_dir / "app.py"), config, ledger_dir)

        index_path = ledger_dir / "_directory_index.json"
        assert index_path.exists()
        with open(index_path) as f:
            index = json.load(f)
        assert index[str(project_dir)] == "indexed-proj"

    def test_returns_existing_if_ledger_file_exists(self, tmp_path):
        """If ledger file already exists (stale index), returns it without recreating."""
        scan_dir = tmp_path / "code"
        project_dir = scan_dir / "existing-proj"
        project_dir.mkdir(parents=True)

        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()

        # Pre-create ledger file
        import frontmatter as fm
        post = fm.Post("## Activity Log\n")
        post.metadata = {"slug": "existing-proj", "directory": str(project_dir), "name": "Custom Name"}
        with open(ledger_dir / "existing-proj.md", "w") as f:
            f.write(fm.dumps(post))

        config = Config(ledger_dir=ledger_dir, scan_dirs=[scan_dir])
        slug, directory = _auto_discover_project(
            str(project_dir / "file.py"), config, ledger_dir,
        )
        assert slug == "existing-proj"


class TestUpdateDirectoryIndex:
    def test_creates_index_if_missing(self, tmp_path):
        _update_directory_index(tmp_path, "my-proj", "/code/my-proj")
        index_path = tmp_path / "_directory_index.json"
        assert index_path.exists()
        with open(index_path) as f:
            data = json.load(f)
        assert data["/code/my-proj"] == "my-proj"

    def test_appends_to_existing_index(self, tmp_path):
        # Pre-populate
        with open(tmp_path / "_directory_index.json", "w") as f:
            json.dump({"/code/old": "old-proj"}, f)

        _update_directory_index(tmp_path, "new-proj", "/code/new")

        with open(tmp_path / "_directory_index.json") as f:
            data = json.load(f)
        assert data["/code/old"] == "old-proj"
        assert data["/code/new"] == "new-proj"

    def test_noop_if_already_correct(self, tmp_path):
        with open(tmp_path / "_directory_index.json", "w") as f:
            json.dump({"/code/proj": "proj"}, f)

        mtime_before = (tmp_path / "_directory_index.json").stat().st_mtime
        import time
        time.sleep(0.01)
        _update_directory_index(tmp_path, "proj", "/code/proj")
        mtime_after = (tmp_path / "_directory_index.json").stat().st_mtime

        # Should not rewrite if already correct
        assert mtime_before == mtime_after


class TestSubProjectResolution:
    def _setup_parent(self, tmp_path):
        """Create a parent project with directory index."""
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        parent_dir = tmp_path / "code" / "mouve-engine"
        parent_dir.mkdir(parents=True)
        (parent_dir / "docs" / "hr").mkdir(parents=True)
        (parent_dir / "docs" / "curriculum").mkdir(parents=True)
        (parent_dir / "src").mkdir()

        # Write directory index
        index = {str(parent_dir): "mouve-engine"}
        with open(ledger_dir / "_directory_index.json", "w") as f:
            json.dump(index, f)

        return ledger_dir, parent_dir

    def test_matches_sub_project_path(self, tmp_path):
        ledger_dir, parent_dir = self._setup_parent(tmp_path)
        config = Config(
            ledger_dir=ledger_dir,
            sub_projects={
                "studio-manager": SubProjectConfig(
                    parent="mouve-engine",
                    paths=["docs/hr/*"],
                ),
            },
        )
        slug, directory = _resolve_project_from_path(
            str(parent_dir / "docs" / "hr" / "jd.md"), ledger_dir, config,
        )
        assert slug == "studio-manager"

    def test_falls_through_to_parent_for_non_matching_path(self, tmp_path):
        ledger_dir, parent_dir = self._setup_parent(tmp_path)
        config = Config(
            ledger_dir=ledger_dir,
            sub_projects={
                "studio-manager": SubProjectConfig(
                    parent="mouve-engine",
                    paths=["docs/hr/*"],
                ),
            },
        )
        slug, directory = _resolve_project_from_path(
            str(parent_dir / "src" / "main.py"), ledger_dir, config,
        )
        assert slug == "mouve-engine"

    def test_multiple_sub_projects_in_same_parent(self, tmp_path):
        ledger_dir, parent_dir = self._setup_parent(tmp_path)
        config = Config(
            ledger_dir=ledger_dir,
            sub_projects={
                "studio-manager": SubProjectConfig(
                    parent="mouve-engine",
                    paths=["docs/hr/*"],
                ),
                "curriculum": SubProjectConfig(
                    parent="mouve-engine",
                    paths=["docs/curriculum/*"],
                ),
            },
        )

        slug1, _ = _resolve_project_from_path(
            str(parent_dir / "docs" / "hr" / "jd.md"), ledger_dir, config,
        )
        slug2, _ = _resolve_project_from_path(
            str(parent_dir / "docs" / "curriculum" / "ballet.md"), ledger_dir, config,
        )
        assert slug1 == "studio-manager"
        assert slug2 == "curriculum"

    def test_glob_pattern_with_double_star(self, tmp_path):
        ledger_dir, parent_dir = self._setup_parent(tmp_path)
        (parent_dir / "docs" / "hr" / "templates").mkdir()

        config = Config(
            ledger_dir=ledger_dir,
            sub_projects={
                "studio-manager": SubProjectConfig(
                    parent="mouve-engine",
                    paths=["docs/hr/**"],
                ),
            },
        )
        slug, _ = _resolve_project_from_path(
            str(parent_dir / "docs" / "hr" / "templates" / "offer.md"), ledger_dir, config,
        )
        assert slug == "studio-manager"

    def test_no_sub_projects_configured(self, tmp_path):
        ledger_dir, parent_dir = self._setup_parent(tmp_path)
        config = Config(ledger_dir=ledger_dir)

        slug, _ = _resolve_project_from_path(
            str(parent_dir / "docs" / "hr" / "jd.md"), ledger_dir, config,
        )
        assert slug == "mouve-engine"  # falls through to parent

    def test_backwards_compatible_without_config(self, tmp_path):
        """Calling without config still works (no sub-project matching)."""
        ledger_dir, parent_dir = self._setup_parent(tmp_path)

        slug, _ = _resolve_project_from_path(
            str(parent_dir / "docs" / "hr" / "jd.md"), ledger_dir,
        )
        assert slug == "mouve-engine"
