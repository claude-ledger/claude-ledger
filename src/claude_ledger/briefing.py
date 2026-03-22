"""Portfolio briefing and workstream map generator."""

from __future__ import annotations

__all__ = [
    "load_ledger_files",
    "generate_portfolio",
    "generate_workstreams",
    "generate_status_line",
    "generate_briefing",
]

from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from claude_ledger.config import Config
from claude_ledger.utils import days_since, format_date_short


SKIP_FILES = {"_portfolio.md", "_workstreams.md", "_errors.log", "_scan-results.json",
              "_analysis.json", "_scan-log.txt", "_reorg-proposal.md", "_directory_index.json"}


def load_ledger_files(ledger_dir: Path) -> list[dict]:
    """Load all project ledger files as metadata dicts."""
    projects = []
    for f in sorted(ledger_dir.glob("*.md")):
        if f.name.startswith("_") or f.name in SKIP_FILES or f.name.endswith("-archive.md"):
            continue
        try:
            post = frontmatter.load(f)
            meta = dict(post.metadata)
            meta["_filename"] = f.name
            projects.append(meta)
        except Exception:
            continue
    return projects


def generate_portfolio(projects: list[dict], stale_days: int = 7) -> str:
    """Generate _portfolio.md content — projects grouped by priority and staleness."""
    now = datetime.now(timezone.utc)
    date_str = f"{now.day} {now.strftime('%B %Y')}"

    p1_active: list[str] = []
    p2_active: list[str] = []
    p3_active: list[str] = []
    stale: list[tuple[int, str]] = []
    completed: list[str] = []
    paused: list[str] = []
    needs_review: list[str] = []

    for p in projects:
        status = p.get("status", "unknown")
        priority = p.get("priority", "P3")
        name = p.get("name", p.get("slug", "?"))
        phase = p.get("current_phase", "")
        last = format_date_short(p.get("last_session"))
        activity = p.get("last_activity", "")
        age = days_since(p.get("last_session"))

        line = f"- **{name}** — {phase}. Last: {last}"
        if activity:
            line += f" ({activity[:80]})"

        if status == "completed":
            completed.append(line)
        elif status == "archived":
            continue
        elif status == "unknown":
            needs_review.append(line)
        elif status in ("paused", "dormant"):
            paused.append(line)
        elif status == "active" and age > stale_days:
            stale.append((age, line))
        elif priority == "P1":
            p1_active.append(line)
        elif priority == "P2":
            p2_active.append(line)
        else:
            p3_active.append(line)

    stale.sort(key=lambda x: -x[0])
    stale_lines = [s[1] for s in stale]

    sections = [f"# Portfolio Briefing — {date_str}", ""]

    for label, items in [
        ("Active (P1)", p1_active),
        ("Active (P2)", p2_active),
        ("Active (P3)", p3_active),
        (f"Stale (>{stale_days} days)", stale_lines),
        ("Paused", paused),
        ("Needs Review", needs_review),
        ("Completed", completed),
    ]:
        if items:
            sections.append(f"## {label}")
            sections.extend(items)
            sections.append("")

    return "\n".join(sections)


def generate_workstreams(projects: list[dict], config: Config) -> str:
    """Generate _workstreams.md content — cross-project workstream map.

    Includes both explicit workstreams (from config) and implicit workstreams
    from sub-projects that share a parent repo.
    """
    now = datetime.now(timezone.utc)
    date_str = f"{now.day} {now.strftime('%B %Y')}"

    workstream_members: dict[str, list[dict]] = {}
    unassigned: list[str] = []

    # Build a slug lookup for sub-project parent grouping
    sub_project_slugs = set(config.sub_projects.keys()) if config.sub_projects else set()
    parent_to_children: dict[str, list[str]] = {}
    for slug, sp in (config.sub_projects or {}).items():
        parent_to_children.setdefault(sp.parent, []).append(slug)

    for p in projects:
        if p.get("status") == "archived":
            continue

        ws_list = p.get("workstreams", [])
        name = p.get("name", p.get("slug", "?"))
        priority = p.get("priority", "P3")
        status = p.get("status", "?")
        phase = p.get("current_phase", "")

        if not ws_list:
            unassigned.append(f"- **{name}** ({priority}, {status}) — {phase}")
        else:
            for ws in ws_list:
                workstream_members.setdefault(ws, []).append(p)

    # Build display name lookup from config
    ws_display = {
        ws_id: ws_config.display_name
        for ws_id, ws_config in config.workstreams.items()
    }

    sections = [f"# Workstream Map — {date_str}", ""]

    for ws_id, members in sorted(workstream_members.items()):
        ws_name = ws_display.get(ws_id, ws_id.replace("-", " ").replace("_", " ").title())
        sections.append(f"## {ws_name} [{ws_id}]")

        members.sort(key=lambda p: (
            {"P1": 0, "P2": 1, "P3": 2}.get(p.get("priority", "P3"), 2),
            -(days_since(p.get("last_session")) or 0),
        ))

        for p in members:
            name = p.get("name", p.get("slug", "?"))
            priority = p.get("priority", "P3")
            status = p.get("status", "?")
            phase = p.get("current_phase", "")
            sections.append(f"- **{name}** ({priority}, {status}) — {phase}")

        if len(members) >= 3:
            hub = max(members, key=lambda p: len(p.get("workstreams", [])))
            hub_name = hub.get("name", hub.get("slug", "?"))
            sections.append(
                f"> CASCADE: changes to {hub_name} may affect "
                f"{len(members) - 1} other projects in this workstream"
            )

        sections.append("")

    # Auto-generate sub-project groups (implicit workstreams from shared parents)
    project_by_slug = {p.get("slug", ""): p for p in projects}
    for parent_slug, children in sorted(parent_to_children.items()):
        # Collect parent + children as group members
        group_members = []
        parent = project_by_slug.get(parent_slug)
        if parent and parent.get("status") != "archived":
            group_members.append(parent)
        for child_slug in children:
            child = project_by_slug.get(child_slug)
            if child and child.get("status") != "archived":
                group_members.append(child)

        if len(group_members) < 2:
            continue

        parent_name = parent.get("name", parent_slug) if parent else parent_slug
        sections.append(f"## {parent_name} (sub-projects) [{parent_slug}]")

        for p in group_members:
            name = p.get("name", p.get("slug", "?"))
            priority = p.get("priority", "P3")
            status = p.get("status", "?")
            phase = p.get("current_phase", "")
            is_parent = p.get("slug") == parent_slug
            marker = " [parent]" if is_parent else ""
            sections.append(f"- **{name}** ({priority}, {status}) — {phase}{marker}")

        # Sub-projects always get a cascade warning — they share a repo
        sections.append(
            f"> CASCADE: these {len(group_members)} projects share the {parent_name} "
            f"repo — changes to shared code affect all sub-projects"
        )

        sections.append("")

        # Remove sub-project entries from unassigned (they're now grouped)
        grouped_names = {p.get("name", p.get("slug", "?")) for p in group_members}
        unassigned = [
            line for line in unassigned
            if not any(f"**{gn}**" in line for gn in grouped_names)
        ]

    if unassigned:
        sections.append("## Unassigned")
        sections.extend(unassigned)
        sections.append("")

    return "\n".join(sections)


def generate_status_line(projects: list[dict], stale_days: int = 7) -> str:
    """Generate a one-line status summary for CLI output."""
    total = len(projects)
    p1_count = sum(
        1 for p in projects
        if p.get("priority") == "P1" and p.get("status") == "active"
    )
    stale_count = sum(
        1 for p in projects
        if p.get("status") == "active" and days_since(p.get("last_session")) > stale_days
    )
    return (
        f"Portfolio briefing ready — {total} projects tracked, "
        f"{p1_count} P1 active, {stale_count} stale."
    )


def generate_briefing(config: Config) -> str:
    """Generate portfolio + workstream briefings and return status line.

    Writes _portfolio.md and _workstreams.md to the ledger directory.
    Returns the status line for hook output.
    """
    projects = load_ledger_files(config.ledger_dir)

    if not projects:
        return "No ledger files found."

    portfolio = generate_portfolio(projects, config.stale_days)
    config.portfolio_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config.portfolio_path, "w") as f:
        f.write(portfolio)

    workstreams = generate_workstreams(projects, config)
    with open(config.workstreams_path, "w") as f:
        f.write(workstreams)

    status = generate_status_line(projects, config.stale_days)
    return status
