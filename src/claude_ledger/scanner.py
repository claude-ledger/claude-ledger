"""Portfolio scanner — discovers projects across local directories and GitHub."""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_ledger.config import Config


@dataclass
class ScanResults:
    """Results from a portfolio scan."""

    scan_date: str = ""
    scanner_version: str = "1.0"
    projects: list[dict[str, Any]] = field(default_factory=list)
    github_only: list[dict[str, Any]] = field(default_factory=list)
    stray_directories: list[dict[str, Any]] = field(default_factory=list)
    stray_files: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_cmd(cmd: list[str] | str, cwd: str | None = None, timeout: int = 30) -> str | None:
    """Run a command, return stdout or None on failure.

    Accepts a list (preferred, no shell) or a string (uses shell=True for
    backwards compatibility with callers like scan_github_repos).
    """
    use_shell = isinstance(cmd, str)
    try:
        result = subprocess.run(
            cmd, shell=use_shell, capture_output=True, text=True,
            cwd=cwd, timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def scan_git_metadata(project_dir: Path) -> dict[str, Any]:
    """Extract git metadata from a local project directory.

    Batches git queries into two subprocess calls (down from six) for speed.
    """
    d = str(project_dir)
    empty = {
        "has_git": False,
        "remote_url": None,
        "default_branch": None,
        "last_commit_date": None,
        "last_commit_subject": None,
        "commit_count_30d": 0,
        "branch_count": 0,
        "recent_commits": [],
    }
    if not (project_dir / ".git").exists():
        return empty

    # --- Batch 1: metadata that doesn't need log formatting ---
    # Four queries joined by a NUL separator so we can split reliably.
    batch1_script = (
        'remote=$(git remote get-url origin 2>/dev/null || echo ""); '
        'branch=$(git branch --show-current 2>/dev/null || git rev-parse --abbrev-ref HEAD 2>/dev/null || echo ""); '
        'count=$(git rev-list --count --since="30 days ago" HEAD 2>/dev/null || echo "0"); '
        'branches=$(git branch -a 2>/dev/null | wc -l | tr -d " "); '
        'printf "%s\\0%s\\0%s\\0%s" "$remote" "$branch" "$count" "$branches"'
    )
    batch1_raw = _run_cmd(["sh", "-c", batch1_script], cwd=d)
    if batch1_raw is None:
        return empty

    parts1 = batch1_raw.split("\0")
    remote = parts1[0] if len(parts1) > 0 and parts1[0] else None
    branch = parts1[1] if len(parts1) > 1 and parts1[1] else None
    count_str = parts1[2] if len(parts1) > 2 else "0"
    branch_count_str = parts1[3] if len(parts1) > 3 else "0"
    commit_count_30d = int(count_str) if count_str.isdigit() else 0
    branch_count = int(branch_count_str) if branch_count_str.isdigit() else 0

    # --- Batch 2: recent commits (includes the -1 data we need) ---
    # Use NUL (%x00) as field delimiter — commit subjects can contain pipes.
    recent_raw = _run_cmd(
        ["git", "log", "-10", "--format=%h%x00%s%x00%cI"],
        cwd=d,
    )
    last_date = None
    last_subject = None
    recent_commits: list[dict[str, str]] = []
    if recent_raw:
        for line in recent_raw.splitlines():
            line_parts = line.split("\0", 2)
            if len(line_parts) >= 3:
                recent_commits.append({
                    "sha": line_parts[0],
                    "subject": line_parts[1],
                    "date": line_parts[2],
                })
        if recent_commits:
            last_date = recent_commits[0]["date"]
            last_subject = recent_commits[0]["subject"]

    return {
        "has_git": True,
        "remote_url": remote,
        "default_branch": branch,
        "last_commit_date": last_date,
        "last_commit_subject": last_subject,
        "commit_count_30d": commit_count_30d,
        "branch_count": branch_count,
        "recent_commits": recent_commits,
    }


def extract_claude_md(project_dir: Path) -> dict[str, Any]:
    """Extract key sections from CLAUDE.md."""
    claude_path = project_dir / "CLAUDE.md"
    if not claude_path.exists():
        return {"has_claude_md": False, "what_is_this": None, "status": None}

    try:
        content = claude_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"has_claude_md": True, "what_is_this": None, "status": None}

    what_is_this = None
    status = None

    match = re.search(r"##\s*What This Is\s*\n+(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if match:
        what_is_this = match.group(1).strip()[:500]

    match = re.search(r"##\s*(?:Current Status|Status)\s*\n+(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if match:
        status = match.group(1).strip()[:500]

    return {"has_claude_md": True, "what_is_this": what_is_this, "status": status}


def _extract_readme_title(lines: list[str]) -> str | None:
    """Extract the H1 title from README lines."""
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            # Skip if it's just the slug repeated or a badge
            if title and not title.startswith("[") and not title.startswith("!"):
                return title
    return None


def _is_boilerplate_line(line: str) -> bool:
    """Check if a README line is boilerplate/non-descriptive."""
    stripped = line.strip()
    if not stripped:
        return True
    # Markdown badges
    if stripped.startswith("[![") or stripped.startswith("!["):
        return True
    # HTML tags
    if stripped.startswith("<") and not stripped.startswith("<http"):
        return True
    # Code blocks
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return True
    # Bullet points with commands
    if stripped.startswith("- ") or stripped.startswith("* "):
        inner = stripped[2:].strip()
        if inner.startswith("`") or inner.startswith("npm ") or inner.startswith("yarn "):
            return True
    # Common boilerplate phrases
    boilerplate = [
        "getting started", "quick start", "installation", "usage",
        "yarn dev", "npm run", "pnpm dev", "bun dev", "npx ",
        "this is a [next.js]", "bootstrapped with",
    ]
    lower = stripped.lower()
    if any(lower.startswith(phrase) for phrase in boilerplate):
        return True
    return False


def _read_readme_lines(project_dir: Path) -> list[str] | None:
    """Read README.md and return lines, or None if missing/unreadable."""
    readme_path = project_dir / "README.md"
    if not readme_path.exists():
        return None
    try:
        return readme_path.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return None


def extract_readme(project_dir: Path, lines: list[str] | None = None) -> str | None:
    """Extract description from README.md.

    Returns the first meaningful paragraph after the title, filtering out
    badges, code blocks, boilerplate, and HTML tags.

    If *lines* is provided, uses them directly instead of re-reading the file.
    """
    if lines is None:
        lines = _read_readme_lines(project_dir)
    if lines is None:
        return None

    desc_lines: list[str] = []
    past_title = False
    in_code_block = False

    for line in lines:
        # Track code blocks
        if line.strip().startswith("```") or line.strip().startswith("~~~"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if line.startswith("# "):
            past_title = True
            continue

        if past_title:
            if line.strip() == "":
                if desc_lines:
                    break
                continue
            if line.startswith("#"):
                break
            if _is_boilerplate_line(line):
                if desc_lines:
                    break
                continue
            desc_lines.append(line.strip())

    return " ".join(desc_lines)[:500] if desc_lines else None


def scan_tech_stack(project_dir: Path) -> tuple[list[str], str | None, bool]:
    """Detect tech stack from config files. Returns (stack, description, has_package_json)."""
    stack: list[str] = []

    if (project_dir / "package.json").exists():
        try:
            pkg = json.loads((project_dir / "package.json").read_text())
            deps = list((pkg.get("dependencies") or {}).keys())
            deps += list((pkg.get("devDependencies") or {}).keys())

            for name, label in [
                ("next", "next.js"), ("react", "react"), ("vue", "vue"),
                ("tailwindcss", "tailwind"), ("express", "express"),
                ("typescript", "typescript"),
            ]:
                if name in deps:
                    stack.append(label)

            if not stack:
                stack.append("node")
            return stack, pkg.get("description"), True
        except (json.JSONDecodeError, OSError):
            return ["node"], None, True

    if (project_dir / "requirements.txt").exists():
        return ["python"], None, False
    if (project_dir / "pyproject.toml").exists():
        return ["python"], None, False

    html_files = list(project_dir.glob("*.html"))
    if html_files:
        return ["html"], None, False

    return ["unknown"], None, False


def scan_structure(project_dir: Path) -> dict[str, Any]:
    """Analyse file structure: file count, docs, tests, MCP config, external systems."""
    has_docs = (project_dir / "docs").exists()
    has_tests = any(
        (project_dir / d).exists() for d in ("tests", "test", "__tests__")
    )
    has_mcp_json = (project_dir / ".mcp.json").exists()

    file_count = 0
    skip = {".git", "node_modules", "__pycache__", ".next", "dist", "build", ".claude"}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        file_count += len(files)
        if file_count > 1000:
            break

    external_systems: list[str] = []
    if has_mcp_json:
        try:
            mcp = json.loads((project_dir / ".mcp.json").read_text())
            external_systems = list(mcp.get("mcpServers", {}).keys())
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "file_count": min(file_count, 1000),
        "has_docs": has_docs,
        "has_tests": has_tests,
        "has_mcp_json": has_mcp_json,
        "external_systems": external_systems,
    }


def scan_local_directory(project_dir: Path, github_user: str | None = None) -> dict[str, Any]:
    """Full scan of a single local project directory."""
    slug = project_dir.name
    git_meta = scan_git_metadata(project_dir)
    claude_md = extract_claude_md(project_dir)
    tech_stack, pkg_desc, has_pkg = scan_tech_stack(project_dir)
    structure = scan_structure(project_dir)

    # Read README once, pass lines to both title and description extractors
    readme_lines = _read_readme_lines(project_dir)
    readme_desc = extract_readme(project_dir, lines=readme_lines)
    readme_title = _extract_readme_title(readme_lines) if readme_lines else None

    remote_url = git_meta.get("remote_url")
    third_party = bool(remote_url and github_user and github_user not in remote_url)

    github_name = None
    if remote_url:
        match = re.search(r"github\.com[/:][\w-]+/([\w.-]+?)(?:\.git)?$", remote_url)
        if match:
            github_name = match.group(1)

    name_mismatch = None
    if github_name and github_name != slug:
        name_mismatch = {"github": github_name, "local": slug}

    return {
        "slug": slug,
        "scan_status": "ok",
        "local_directory": str(project_dir),
        "github_url": remote_url,
        "github_name": github_name,
        "has_git": git_meta["has_git"],
        "default_branch": git_meta["default_branch"],
        "last_commit_date": git_meta["last_commit_date"],
        "last_commit_subject": git_meta["last_commit_subject"],
        "commit_count_30d": git_meta["commit_count_30d"],
        "branch_count": git_meta["branch_count"],
        "recent_commits": git_meta["recent_commits"],
        "has_claude_md": claude_md["has_claude_md"],
        "claude_md_what_is_this": claude_md["what_is_this"],
        "claude_md_status": claude_md["status"],
        "readme_title": readme_title,
        "readme_description": readme_desc,
        "package_description": pkg_desc,
        "tech_stack": tech_stack,
        "package_json_exists": has_pkg,
        "mcp_json_exists": structure["has_mcp_json"],
        "external_systems": structure["external_systems"],
        "file_count": structure["file_count"],
        "has_docs": structure["has_docs"],
        "has_tests": structure["has_tests"],
        "is_third_party": third_party,
        "name_mismatch": name_mismatch,
    }


_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def scan_github_repos(github_user: str) -> list[dict[str, Any]]:
    """List all repos on GitHub for the user. Requires gh CLI."""
    if not _SAFE_IDENTIFIER_RE.match(github_user):
        return []
    raw = _run_cmd(
        [
            "gh", "repo", "list", github_user, "--limit", "100",
            "--json", "name,description,pushedAt,isArchived,isEmpty,url,defaultBranchRef",
        ],
        timeout=60,
    )
    if not raw:
        return []

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def find_stray_files(
    project_slugs: list[str], stray_dirs: list[Path]
) -> list[dict[str, str]]:
    """Find project-related files in stray locations (e.g. ~/Downloads)."""
    strays: list[dict[str, str]] = []
    for scan_dir in stray_dirs:
        if not scan_dir.exists():
            continue
        try:
            for f in scan_dir.iterdir():
                if not f.is_file():
                    continue
                name_lower = f.name.lower()
                for slug in project_slugs:
                    first_word = slug.lower().split("-")[0]
                    if re.search(r"(?:^|[-_])" + re.escape(first_word), name_lower):
                        strays.append({
                            "path": str(f),
                            "probable_project": slug,
                            "filename": f.name,
                        })
                        break
        except PermissionError:
            continue
    return strays


def scan_portfolio(config: Config, log_fn: Any = None) -> ScanResults:
    """Run a full portfolio scan using the provided configuration.

    Args:
        config: Loaded configuration.
        log_fn: Optional callable(msg: str) for progress logging.

    Returns:
        ScanResults with all discovered projects.
    """
    log = log_fn or (lambda msg: None)
    results = ScanResults(scan_date=datetime.now(timezone.utc).isoformat())

    all_projects: list[dict[str, Any]] = []
    all_slugs: list[str] = []

    # Scan local directories (parallel within each scan_dir)
    for scan_dir in config.scan_dirs:
        if not scan_dir.exists():
            log(f"Skipping {scan_dir} (does not exist)")
            continue

        log(f"Scanning {scan_dir}...")
        subdirs = sorted([
            d for d in scan_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])
        log(f"  Found {len(subdirs)} directories")

        # Use threads to parallelise I/O-bound git subprocess calls
        max_workers = min(8, len(subdirs)) if subdirs else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_dir = {
                executor.submit(scan_local_directory, d, config.github_user): d
                for d in subdirs
            }
            for future in as_completed(future_to_dir):
                project_dir = future_to_dir[future]
                try:
                    result = future.result()
                    all_projects.append(result)
                    all_slugs.append(result["slug"])
                except Exception as e:
                    log(f"  ERROR scanning {project_dir.name}: {e}")
                    all_projects.append({
                        "slug": project_dir.name,
                        "scan_status": "failed",
                        "local_directory": str(project_dir),
                        "error": str(e),
                    })

    # Deduplicate by slug (keep more recently active)
    seen: dict[str, dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []
    for p in all_projects:
        slug = p["slug"]
        if slug in seen:
            existing = seen[slug]
            existing_date = existing.get("last_commit_date") or ""
            new_date = p.get("last_commit_date") or ""
            if new_date > existing_date:
                existing.update(p)
            log(f"  DUPLICATE: {slug} found in multiple locations")
        else:
            seen[slug] = p
            deduped.append(p)

    results.projects = deduped

    # Scan GitHub repos (if configured)
    if config.github_user:
        log(f"Scanning GitHub repos for {config.github_user}...")
        repos = scan_github_repos(config.github_user)
        log(f"  Found {len(repos)} GitHub repos")

        local_urls = {p.get("github_url") for p in deduped if p.get("github_url")}
        local_names = {p.get("github_name") for p in deduped if p.get("github_name")}
        local_slugs = set(all_slugs)

        for repo in repos:
            name = repo.get("name", "")
            url = repo.get("url", "")
            if url + ".git" in local_urls or url in local_urls:
                continue
            if name in local_names or name in local_slugs:
                continue

            results.github_only.append({
                "slug": name,
                "github_url": url if url.endswith(".git") else url + ".git",
                "scan_status": "github_only",
                "description": repo.get("description"),
                "last_push": repo.get("pushedAt"),
                "is_archived": repo.get("isArchived", False),
                "is_empty": repo.get("isEmpty", False),
                "default_branch": (repo.get("defaultBranchRef") or {}).get("name"),
            })
            all_slugs.append(name)

    # Find stray files
    if config.stray_scan_dirs:
        log("Scanning for stray files...")
        results.stray_files = find_stray_files(all_slugs, config.stray_scan_dirs)
        log(f"  Found {len(results.stray_files)} stray files")

    # Summary
    results.summary = {
        "total_local_dirs": len(deduped),
        "total_github_repos": len(results.github_only) + len(deduped),
        "github_only_count": len(results.github_only),
        "with_git": sum(1 for p in deduped if p.get("has_git")),
        "with_claude_md": sum(1 for p in deduped if p.get("has_claude_md")),
        "with_remote": sum(1 for p in deduped if p.get("github_url")),
        "third_party": sum(1 for p in deduped if p.get("is_third_party")),
        "name_mismatches": sum(1 for p in deduped if p.get("name_mismatch")),
        "stray_files_count": len(results.stray_files),
    }

    return results


def save_scan_results(results: ScanResults, path: Path) -> None:
    """Write scan results to JSON file (atomic)."""
    from claude_ledger.utils import atomic_write_json

    atomic_write_json(path, results.to_dict())
