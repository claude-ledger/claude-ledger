"""Configuration loading and validation for claude-ledger."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_LEDGER_DIR = Path.home() / ".claude" / "ledger"
CONFIG_FILENAME = "ledger.yaml"
CONFIG_VERSION = 1

# Default directories to scan for projects
DEFAULT_SCAN_DIRS: list[str] = []

# Directories inside scan_dirs to always skip
DEFAULT_IGNORE_DIRS = ["node_modules", ".git", "__pycache__", ".next", "dist", "build"]


@dataclass
class WorkstreamConfig:
    """A named group of related projects."""

    display_name: str
    members: list[str] = field(default_factory=list)


@dataclass
class Config:
    """claude-ledger configuration."""

    ledger_dir: Path = field(default_factory=lambda: DEFAULT_LEDGER_DIR)
    scan_dirs: list[Path] = field(default_factory=list)
    github_user: str | None = None
    stray_scan_dirs: list[Path] = field(default_factory=list)
    stale_days: int = 7
    skip_slugs: list[str] = field(default_factory=list)
    no_track: list[str] = field(default_factory=list)
    ignore_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_DIRS))
    workstreams: dict[str, WorkstreamConfig] = field(default_factory=dict)

    @property
    def config_path(self) -> Path:
        return self.ledger_dir / CONFIG_FILENAME

    @property
    def state_dir(self) -> Path:
        return self.ledger_dir / ".state" / "sessions"

    @property
    def locks_dir(self) -> Path:
        return self.ledger_dir / ".locks"

    @property
    def errors_log(self) -> Path:
        return self.ledger_dir / "_errors.log"

    @property
    def scan_results_path(self) -> Path:
        return self.ledger_dir / "_scan-results.json"

    @property
    def portfolio_path(self) -> Path:
        return self.ledger_dir / "_portfolio.md"

    @property
    def workstreams_path(self) -> Path:
        return self.ledger_dir / "_workstreams.md"

    @property
    def directory_index_path(self) -> Path:
        return self.ledger_dir / "_directory_index.json"


def expand_path(p: str | Path) -> Path:
    """Expand ~ and environment variables in a path."""
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


def _parse_workstreams(raw: dict[str, Any]) -> dict[str, WorkstreamConfig]:
    """Parse workstream definitions from YAML."""
    result = {}
    for ws_id, ws_data in raw.items():
        if isinstance(ws_data, dict):
            result[ws_id] = WorkstreamConfig(
                display_name=ws_data.get("display_name", ws_id),
                members=ws_data.get("members", []),
            )
    return result


def load_config(ledger_dir: Path | None = None) -> Config:
    """Load configuration from ledger.yaml, falling back to defaults.

    Args:
        ledger_dir: Override the ledger directory. Defaults to ~/.claude/ledger/.

    Returns:
        Populated Config instance.
    """
    ledger_dir = expand_path(ledger_dir) if ledger_dir else DEFAULT_LEDGER_DIR
    config_path = ledger_dir / CONFIG_FILENAME

    if not config_path.exists():
        return Config(ledger_dir=ledger_dir)

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return Config(ledger_dir=ledger_dir)

    # Validate version
    version = raw.get("version", 1)
    if version != CONFIG_VERSION:
        raise ValueError(
            f"Unsupported ledger.yaml version {version} (expected {CONFIG_VERSION}). "
            f"Please upgrade claude-ledger."
        )

    # Parse scan_dirs
    scan_dirs = [expand_path(d) for d in raw.get("scan_dirs", [])]
    stray_scan_dirs = [expand_path(d) for d in raw.get("stray_scan_dirs", [])]

    # Parse stale_days
    stale_days = raw.get("stale_days", 7)
    if not isinstance(stale_days, int) or stale_days < 1:
        stale_days = 7

    # Parse workstreams
    workstreams = _parse_workstreams(raw.get("workstreams", {}))

    return Config(
        ledger_dir=ledger_dir,
        scan_dirs=scan_dirs,
        github_user=raw.get("github_user"),
        stray_scan_dirs=stray_scan_dirs,
        stale_days=stale_days,
        skip_slugs=raw.get("skip_slugs", []),
        no_track=raw.get("no_track", []),
        ignore_dirs=raw.get("ignore_dirs", list(DEFAULT_IGNORE_DIRS)),
        workstreams=workstreams,
    )


def generate_default_config(
    scan_dirs: list[str] | None = None,
    github_user: str | None = None,
) -> str:
    """Generate a default ledger.yaml content string.

    Args:
        scan_dirs: Directories to scan for projects.
        github_user: GitHub username for remote repo discovery.

    Returns:
        YAML string ready to write to ledger.yaml.
    """
    dirs = scan_dirs or ["~/Code", "~/Projects"]
    config = {
        "version": CONFIG_VERSION,
        "scan_dirs": dirs,
        "github_user": github_user,
        "stray_scan_dirs": ["~/Downloads"],
        "stale_days": 7,
        "skip_slugs": [],
        "no_track": [],
        "ignore_dirs": list(DEFAULT_IGNORE_DIRS),
        "workstreams": {},
    }
    return yaml.dump(config, default_flow_style=False, sort_keys=False)
