"""CLI entry point for claude-ledger."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from claude_ledger import __version__
from claude_ledger.config import (
    CONFIG_FILENAME,
    DEFAULT_LEDGER_DIR,
    generate_default_config,
    load_config,
)


@click.group()
@click.version_option(version=__version__, prog_name="claude-ledger")
@click.option(
    "--ledger-dir",
    type=click.Path(path_type=Path),
    default=None,
    envvar="CLAUDE_LEDGER_DIR",
    help="Override ledger directory (default: ~/.claude/ledger/)",
)
@click.pass_context
def cli(ctx: click.Context, ledger_dir: Path | None) -> None:
    """Portfolio-level project tracking for Claude Code."""
    ctx.ensure_object(dict)
    ctx.obj["ledger_dir"] = ledger_dir


def _load_config(ctx: click.Context):
    return load_config(ctx.obj.get("ledger_dir"))


# --- init ---


HOOKS_SPEC = {
    "PostToolUse": [
        {
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [{
                "type": "command",
                "command": "claude-ledger capture --touch",
                "timeout": 2,
            }],
        },
        {
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": "claude-ledger capture --commit",
                "timeout": 3,
            }],
        },
    ],
    "Stop": [
        {
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": "claude-ledger capture --stop-note",
                "timeout": 2,
            }],
        },
    ],
    "SessionEnd": [
        {
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": "claude-ledger capture --session-end",
                "timeout": 5,
            }],
        },
    ],
    "SessionStart": [
        {
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": "claude-ledger briefing",
                "timeout": 5,
            }],
        },
    ],
}

HOOK_MARKER = "claude-ledger"


def _merge_hooks(settings_path: Path) -> list[str]:
    """Safely merge ledger hooks into existing settings.json.

    Returns list of hook types that were added.
    """
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            click.echo(f"  WARNING: Could not parse {settings_path}. Skipping hook installation.")
            return []
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    added: list[str] = []

    for event_type, new_entries in HOOKS_SPEC.items():
        existing = hooks.get(event_type, [])

        for new_entry in new_entries:
            # Check if our hook already exists (by command string)
            already_installed = False
            for existing_entry in existing:
                for h in existing_entry.get("hooks", []):
                    if HOOK_MARKER in h.get("command", ""):
                        # Check same matcher
                        if existing_entry.get("matcher", "") == new_entry.get("matcher", ""):
                            already_installed = True
                            break
                if already_installed:
                    break

            if not already_installed:
                existing.append(new_entry)
                added.append(f"{event_type} ({new_entry.get('matcher', '*')})")

        hooks[event_type] = existing

    settings["hooks"] = hooks

    # Write atomically
    tmp_path = settings_path.parent / f".{settings_path.name}.tmp"
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(settings, f, indent=2)
        import os
        os.replace(str(tmp_path), str(settings_path))
    except Exception as e:
        click.echo(f"  ERROR writing settings: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    return added


def _remove_hooks(settings_path: Path) -> list[str]:
    """Remove all claude-ledger hooks from settings.json.

    Returns list of hook types that were removed.
    """
    if not settings_path.exists():
        return []

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    hooks = settings.get("hooks", {})
    removed: list[str] = []

    for event_type in list(hooks.keys()):
        entries = hooks[event_type]
        filtered = []
        for entry in entries:
            has_marker = any(
                HOOK_MARKER in h.get("command", "")
                for h in entry.get("hooks", [])
            )
            if has_marker:
                removed.append(f"{event_type} ({entry.get('matcher', '*')})")
            else:
                filtered.append(entry)
        hooks[event_type] = filtered

        # Remove empty event types
        if not hooks[event_type]:
            del hooks[event_type]

    settings["hooks"] = hooks

    import os
    tmp_path = settings_path.parent / f".{settings_path.name}.tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(str(tmp_path), str(settings_path))
    except Exception as e:
        click.echo(f"  ERROR writing settings: {e}")

    return removed


@cli.command()
@click.option("--scan-dirs", multiple=True, help="Directories to scan for projects")
@click.option("--github-user", default=None, help="GitHub username for repo discovery")
@click.pass_context
def init(ctx: click.Context, scan_dirs: tuple[str, ...], github_user: str | None) -> None:
    """Set up the ledger directory and install Claude Code hooks."""
    ledger_dir = ctx.obj.get("ledger_dir") or DEFAULT_LEDGER_DIR

    click.echo(f"Initialising claude-ledger at {ledger_dir}")

    # 1. Create directory
    ledger_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"  Created {ledger_dir}")

    # 2. Create .gitignore
    gitignore_path = ledger_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(
            ".state/\n.locks/\n__pycache__/\n*.tmp\n*.tmp.*\n_scan-results.json\n_scan-log.txt\n"
        )
        click.echo("  Created .gitignore")

    # 3. Initialise git repo
    if not (ledger_dir / ".git").exists():
        subprocess.run(
            ["git", "init"], capture_output=True, cwd=str(ledger_dir), timeout=10,
        )
        click.echo("  Initialised git repository")

    # 4. Generate config
    config_path = ledger_dir / CONFIG_FILENAME
    if not config_path.exists():
        dirs = list(scan_dirs) if scan_dirs else None
        config_content = generate_default_config(scan_dirs=dirs, github_user=github_user)
        config_path.write_text(config_content)
        click.echo(f"  Created {CONFIG_FILENAME}")
    else:
        click.echo(f"  {CONFIG_FILENAME} already exists (skipped)")

    # 5. Install hooks
    settings_path = Path.home() / ".claude" / "settings.json"
    added = _merge_hooks(settings_path)
    if added:
        click.echo(f"  Installed {len(added)} hooks:")
        for h in added:
            click.echo(f"    + {h}")
    else:
        click.echo("  Hooks already installed (skipped)")

    click.echo("\nDone! Next steps:")
    click.echo("  1. Edit ledger.yaml to configure your scan directories")
    click.echo("  2. Run: claude-ledger scan")
    click.echo("  3. Run: claude-ledger bootstrap")


# --- scan ---


@cli.command()
@click.option("--output", type=click.Path(path_type=Path), default=None, help="Output file path")
@click.pass_context
def scan(ctx: click.Context, output: Path | None) -> None:
    """Discover projects in configured directories."""
    config = _load_config(ctx)

    if not config.scan_dirs:
        click.echo("No scan_dirs configured in ledger.yaml. Add directories to scan.")
        click.echo(f"  Edit: {config.config_path}")
        sys.exit(1)

    from claude_ledger.scanner import save_scan_results, scan_portfolio

    results = scan_portfolio(config, log_fn=click.echo)
    out_path = output or config.scan_results_path
    save_scan_results(results, out_path)

    click.echo(f"\nResults written to {out_path}")
    for k, v in results.summary.items():
        click.echo(f"  {k}: {v}")


# --- bootstrap ---


@cli.command()
@click.option("--dry-run", is_flag=True, help="Show what would be created without writing files")
@click.pass_context
def bootstrap(ctx: click.Context, dry_run: bool) -> None:
    """Create ledger files from scan results."""
    config = _load_config(ctx)

    if not config.scan_results_path.exists():
        click.echo("No scan results found. Run 'claude-ledger scan' first.")
        sys.exit(1)

    from claude_ledger.bootstrap import bootstrap_from_scan

    if dry_run:
        click.echo("Dry run — showing what would be created:\n")

    counts = bootstrap_from_scan(config, dry_run=dry_run, log_fn=click.echo)

    click.echo(f"\n{'Dry run complete' if dry_run else 'Bootstrap complete'}:")
    click.echo(f"  {'Would create' if dry_run else 'Created'}: {counts['created']}")
    click.echo(f"  Skipped: {counts['skipped']}")
    click.echo(f"  Errors: {counts['errors']}")

    if not dry_run and counts["created"] > 0:
        # Rebuild directory index
        from claude_ledger.capture import rebuild_directory_index
        rebuild_directory_index(config.ledger_dir)
        click.echo("  Directory index rebuilt")


# --- briefing ---


@cli.command()
@click.pass_context
def briefing(ctx: click.Context) -> None:
    """Generate portfolio and workstream briefings."""
    config = _load_config(ctx)

    from claude_ledger.briefing import generate_briefing

    status = generate_briefing(config)
    click.echo(status)
    click.echo(
        f"Read {config.portfolio_path} and {config.workstreams_path} for full context."
    )


# --- status ---


@cli.command()
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def status(ctx: click.Context, as_json: bool) -> None:
    """Show portfolio summary."""
    config = _load_config(ctx)

    from claude_ledger.briefing import generate_status_line, load_ledger_files
    from claude_ledger.utils import days_since

    projects = load_ledger_files(config.ledger_dir)

    if not projects:
        click.echo("No ledger files found. Run 'claude-ledger scan' then 'claude-ledger bootstrap'.")
        return

    if as_json:
        summary = {
            "total": len(projects),
            "active": sum(1 for p in projects if p.get("status") == "active"),
            "p1": sum(
                1 for p in projects
                if p.get("priority") == "P1" and p.get("status") == "active"
            ),
            "p2": sum(
                1 for p in projects
                if p.get("priority") == "P2" and p.get("status") == "active"
            ),
            "stale": sum(
                1 for p in projects
                if p.get("status") == "active"
                and days_since(p.get("last_session")) > config.stale_days
            ),
            "paused": sum(1 for p in projects if p.get("status") in ("paused", "dormant")),
            "completed": sum(1 for p in projects if p.get("status") == "completed"),
        }
        click.echo(json.dumps(summary, indent=2))
    else:
        click.echo(generate_status_line(projects, config.stale_days))

        # Show active projects table
        active = [p for p in projects if p.get("status") == "active"]
        active.sort(key=lambda p: (
            {"P1": 0, "P2": 1, "P3": 2}.get(p.get("priority", "P3"), 2),
        ))

        if active:
            click.echo(f"\nActive projects ({len(active)}):")
            for p in active:
                name = p.get("name", p.get("slug", "?"))
                priority = p.get("priority", "P3")
                phase = p.get("current_phase", "")
                age = days_since(p.get("last_session"))
                stale_marker = " [STALE]" if age > config.stale_days else ""
                click.echo(f"  {priority} {name} — {phase}{stale_marker}")


# --- capture (hook entry point) ---


@cli.command(hidden=True)
@click.argument("mode")
@click.pass_context
def capture(ctx: click.Context, mode: str) -> None:
    """Hook entry point for activity capture (internal use)."""
    from claude_ledger.capture import (
        handle_commit,
        handle_session_end,
        handle_stop_note,
        handle_touch,
        _read_stdin,
        _get_ledger_dir,
    )

    hook_data = _read_stdin()
    ledger_dir = ctx.obj.get("ledger_dir") or _get_ledger_dir()

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
        click.echo(f"Unknown capture mode: {mode}")
        sys.exit(1)


# --- uninstall ---


@cli.command()
@click.option("--delete", is_flag=True, help="Also delete the ledger directory")
@click.pass_context
def uninstall(ctx: click.Context, delete: bool) -> None:
    """Remove claude-ledger hooks from Claude Code settings."""
    settings_path = Path.home() / ".claude" / "settings.json"
    removed = _remove_hooks(settings_path)

    if removed:
        click.echo(f"Removed {len(removed)} hooks:")
        for h in removed:
            click.echo(f"  - {h}")
    else:
        click.echo("No claude-ledger hooks found in settings.")

    if delete:
        config = _load_config(ctx)
        import shutil
        if config.ledger_dir.exists():
            if click.confirm(f"Delete {config.ledger_dir} and all ledger files?"):
                shutil.rmtree(config.ledger_dir)
                click.echo(f"Deleted {config.ledger_dir}")
            else:
                click.echo("Cancelled.")


if __name__ == "__main__":
    cli()
