"""Bootstrap ledger files from scan results using heuristic inference."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

from claude_ledger.config import Config
from claude_ledger.utils import days_since, format_date_heading


def infer_status(scan_data: dict[str, Any]) -> str:
    """Infer project status from git activity.

    - >=1 commit in 30 days → active
    - 0 commits but last commit <90 days ago → paused
    - Last commit <365 days ago → dormant
    - Older or no commits → archived
    """
    commits_30d = scan_data.get("commit_count_30d", 0)
    age = days_since(scan_data.get("last_commit_date"))

    if commits_30d >= 1:
        return "active"
    if age <= 90:
        return "paused"
    if age <= 365:
        return "dormant"
    return "archived"


def infer_priority(scan_data: dict[str, Any], status: str) -> str:
    """Infer priority from activity level and project signals.

    P1: heavily active (>=10 commits/30d) with CLAUDE.md
    P2: moderately active (>=3 commits/30d) or has .mcp.json
    P3: everything else
    """
    if status != "active":
        return "P3"

    commits_30d = scan_data.get("commit_count_30d", 0)
    has_claude_md = scan_data.get("has_claude_md", False)
    has_mcp = scan_data.get("mcp_json_exists", False)

    if commits_30d >= 10 and has_claude_md:
        return "P1"
    if commits_30d >= 3 or has_mcp:
        return "P2"
    return "P3"


def infer_name(scan_data: dict[str, Any]) -> str:
    """Infer a display name from available metadata.

    Priority: README first line → CLAUDE.md 'What This Is' first sentence → slug titlecased.
    """
    # Try README description — often the project title is the first heading
    readme = scan_data.get("readme_description")
    if readme:
        # Take first sentence, max 60 chars
        first_sentence = readme.split(".")[0].strip()
        if 3 <= len(first_sentence) <= 60:
            return first_sentence

    # Try CLAUDE.md "What This Is"
    what_is = scan_data.get("claude_md_what_is_this")
    if what_is:
        first_line = what_is.split("\n")[0].strip().rstrip(".")
        if 3 <= len(first_line) <= 60:
            return first_line

    # Try package.json description
    pkg_desc = scan_data.get("package_description")
    if pkg_desc and 3 <= len(pkg_desc) <= 60:
        return pkg_desc

    # Fall back to slug titlecased
    slug = scan_data.get("slug", "unknown")
    return slug.replace("-", " ").replace("_", " ").title()


def infer_vision(scan_data: dict[str, Any]) -> str:
    """Infer a one-line project vision from available metadata."""
    what_is = scan_data.get("claude_md_what_is_this")
    if what_is:
        return what_is.split("\n")[0].strip()[:200]

    readme = scan_data.get("readme_description")
    if readme:
        return readme[:200]

    pkg_desc = scan_data.get("package_description")
    if pkg_desc:
        return pkg_desc[:200]

    return ""


def get_workstreams_for_slug(slug: str, config: Config) -> list[str]:
    """Get workstream IDs that include this project slug."""
    return [
        ws_id
        for ws_id, ws_config in config.workstreams.items()
        if slug in ws_config.members
    ]


def build_activity_log(scan_data: dict[str, Any]) -> str:
    """Build initial activity log from recent commits."""
    commits = scan_data.get("recent_commits", [])
    if not commits:
        return "## Activity Log\n\nNo commits yet."

    lines = ["## Activity Log", ""]
    current_date = None

    for c in commits[:5]:
        date_str = c.get("date")
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                heading = format_date_heading(dt)
            except (ValueError, AttributeError):
                heading = None

            if heading and heading != current_date:
                current_date = heading
                lines.append(f"### {current_date}")

        sha = c.get("sha", "?")
        subject = c.get("subject", "Unknown")
        lines.append(f"- {subject} ({sha})")

    return "\n".join(lines)


def create_ledger_file(
    scan_data: dict[str, Any],
    config: Config,
) -> tuple[str | None, str]:
    """Create a single ledger markdown file from scan data.

    Returns:
        (slug, reason) — slug if created, None if skipped. Reason explains why.
    """
    slug = scan_data.get("slug", "")
    if not slug:
        return None, "no slug"

    if slug in config.skip_slugs:
        return None, "skipped (in skip_slugs)"

    if slug in config.no_track:
        return None, "skipped (in no_track)"

    # Idempotent: don't overwrite existing ledger files
    ledger_path = config.ledger_dir / f"{slug}.md"
    if ledger_path.exists():
        return None, "skipped (ledger file already exists)"

    # Infer metadata
    status = infer_status(scan_data)
    priority = infer_priority(scan_data, status)
    name = infer_name(scan_data)
    vision = infer_vision(scan_data)
    workstreams = get_workstreams_for_slug(slug, config)

    # Build frontmatter
    metadata = {
        "name": name,
        "slug": slug,
        "directory": scan_data.get("local_directory", ""),
        "repo_url": scan_data.get("github_url"),
        "status": status,
        "priority": priority,
        "vision": vision,
        "current_phase": "unknown",
        "last_session": scan_data.get("last_commit_date"),
        "last_activity": scan_data.get("last_commit_subject", ""),
        "systems": scan_data.get("external_systems", []),
        "tags": scan_data.get("tech_stack", []),
        "workstreams": workstreams,
    }

    # Build content
    activity_log = build_activity_log(scan_data)

    post = frontmatter.Post(activity_log)
    post.metadata = metadata

    # Write
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "w") as f:
        f.write(frontmatter.dumps(post))

    return slug, "created"


def bootstrap_from_scan(
    config: Config,
    scan_results_path: Path | None = None,
    dry_run: bool = False,
    log_fn: Any = None,
) -> dict[str, int]:
    """Create ledger files for all projects in scan results.

    Args:
        config: Loaded configuration.
        scan_results_path: Path to _scan-results.json. Defaults to config path.
        dry_run: If True, print what would be created but don't write files.
        log_fn: Optional callable(msg: str) for progress logging.

    Returns:
        Counts dict: {"created": N, "skipped": N, "errors": N}
    """
    log = log_fn or (lambda msg: None)
    path = scan_results_path or config.scan_results_path

    if not path.exists():
        log(f"No scan results found at {path}. Run 'claude-ledger scan' first.")
        return {"created": 0, "skipped": 0, "errors": 0}

    with open(path) as f:
        scan_data = json.load(f)

    created = 0
    skipped = 0
    errors = 0

    # Process local projects
    for entry in scan_data.get("projects", []):
        if entry.get("scan_status") == "failed":
            skipped += 1
            continue

        try:
            if dry_run:
                status = infer_status(entry)
                priority = infer_priority(entry, status)
                name = infer_name(entry)
                slug = entry.get("slug", "?")

                if slug in config.skip_slugs or slug in config.no_track:
                    log(f"  - {slug}: would skip")
                    skipped += 1
                elif (config.ledger_dir / f"{slug}.md").exists():
                    log(f"  - {slug}: already exists")
                    skipped += 1
                else:
                    log(f"  + {slug}: {name} ({priority}, {status})")
                    created += 1
            else:
                result, reason = create_ledger_file(entry, config)
                if result:
                    log(f"  + {result}")
                    created += 1
                else:
                    log(f"  - {entry.get('slug', '?')}: {reason}")
                    skipped += 1
        except Exception as e:
            log(f"  ! {entry.get('slug', '?')}: ERROR — {e}")
            errors += 1

    # Process GitHub-only repos
    for entry in scan_data.get("github_only", []):
        slug = entry.get("slug", "")
        if entry.get("is_archived"):
            skipped += 1
            continue

        gh_scan = {
            "slug": slug,
            "github_url": entry.get("github_url"),
            "last_commit_date": entry.get("last_push"),
            "commit_count_30d": 0,
            "has_claude_md": False,
            "mcp_json_exists": False,
            "recent_commits": [],
            "tech_stack": [],
            "external_systems": [],
        }

        try:
            if dry_run:
                status = infer_status(gh_scan)
                priority = infer_priority(gh_scan, status)
                if slug in config.skip_slugs or slug in config.no_track:
                    log(f"  - {slug}: would skip")
                    skipped += 1
                elif (config.ledger_dir / f"{slug}.md").exists():
                    log(f"  - {slug}: already exists")
                    skipped += 1
                else:
                    log(f"  + {slug}: (GitHub-only, {priority}, {status})")
                    created += 1
            else:
                result, reason = create_ledger_file(gh_scan, config)
                if result:
                    log(f"  + {result} (GitHub-only)")
                    created += 1
                else:
                    log(f"  - {slug}: {reason}")
                    skipped += 1
        except Exception as e:
            log(f"  ! {slug}: ERROR — {e}")
            errors += 1

    return {"created": created, "skipped": skipped, "errors": errors}
