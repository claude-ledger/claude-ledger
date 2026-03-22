# claude-ledger

Portfolio-level project tracking for [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

## The Problem

Claude Code gives you per-project memory (CLAUDE.md, todos, memory files). But if you work across 10, 20, 50+ projects, there's no way to:

- **See which projects are active vs stale vs abandoned** across your entire portfolio
- **Track what you did** across all projects in a session — not just one
- **Get a portfolio briefing** at the start of each session with staleness detection
- **Map workstreams** — see which projects cluster together and where changes cascade

## How It Works

claude-ledger is a set of Claude Code hooks + a CLI that automatically:

1. **Tracks activity** — every file edit and git commit is recorded to a per-project ledger file
2. **Generates briefings** — on session start, you get a portfolio summary with priority tiers and staleness detection
3. **Maps workstreams** — see which projects relate and where changes might cascade

All tracking happens via Claude Code hooks — zero manual maintenance.

## Quick Start

```bash
pip install claude-ledger
claude-ledger init --scan-dirs ~/Code --github-user your-username
claude-ledger scan
claude-ledger bootstrap
```

That's it. Every Claude Code session now starts with a portfolio briefing, and activity is tracked automatically.

## What Gets Created

```
~/.claude/ledger/
├── ledger.yaml              # Your configuration
├── _portfolio.md            # Auto-generated briefing (read by Claude at session start)
├── _workstreams.md          # Cross-project dependency map
├── my-project.md            # One ledger file per project
└── another-project.md       #   (YAML frontmatter + activity log)
```

## Commands

| Command | What it does |
|---------|-------------|
| `claude-ledger init` | Set up ledger directory and install Claude Code hooks |
| `claude-ledger scan` | Discover projects in your configured directories |
| `claude-ledger bootstrap` | Create ledger files from scan results |
| `claude-ledger briefing` | Generate portfolio + workstream briefings |
| `claude-ledger status` | Quick portfolio summary |
| `claude-ledger uninstall` | Remove hooks (optionally delete ledger data) |

## Configuration

After running `init`, edit `~/.claude/ledger/ledger.yaml`:

```yaml
version: 1

# Where to scan for projects
scan_dirs:
  - ~/Code
  - ~/Projects

# GitHub username for remote repo discovery (optional)
github_user: your-username

# Directories to scan for stray project files
stray_scan_dirs:
  - ~/Downloads

# Days of inactivity before a project is "stale"
stale_days: 7

# Projects to skip during scan/bootstrap
skip_slugs: []
no_track: []

# Group related projects into workstreams (optional)
workstreams:
  backend:
    display_name: "Backend Services"
    members:
      - api-server
      - auth-service
      - worker-queue
  frontend:
    display_name: "Frontend Apps"
    members:
      - web-app
      - mobile-app
```

## Ledger File Format

Each project gets a markdown file with YAML frontmatter:

```yaml
---
name: My Project
slug: my-project
directory: /Users/you/Code/my-project
repo_url: https://github.com/you/my-project.git
status: active        # active, paused, dormant, archived, completed
priority: P1          # P1, P2, P3 (inferred from activity)
vision: A short description of what this project does
current_phase: building
last_session: '2026-03-20T15:03:39Z'
last_activity: Add user authentication
systems: []
tags: [next.js, typescript, tailwind]
workstreams: [frontend]
---

## Activity Log

### 20 March 2026
- Add user authentication (a1b2c3d)
- Fix login redirect loop (e4f5g6h)
```

## How Hooks Work

claude-ledger installs five hooks into your Claude Code settings:

| Hook | Event | What it does |
|------|-------|-------------|
| **PostToolUse** (Edit/Write) | File edited | Records which project was touched |
| **PostToolUse** (Bash) | Command run | Captures git commit metadata |
| **Stop** | Response complete | Stores session summary |
| **SessionEnd** | Session closes | Finalises activity, commits ledger repo |
| **SessionStart** | Session opens | Generates portfolio briefing |

All hooks have tight timeouts (2-5 seconds) and fail silently — they never block your work.

## Heuristic Inference

When you run `bootstrap`, claude-ledger infers project metadata from what it finds:

| Signal | Inference |
|--------|-----------|
| >=10 commits/30d + CLAUDE.md | P1 (high priority) |
| >=3 commits/30d or has .mcp.json | P2 (medium) |
| Everything else | P3 (low) |
| >=1 commit in 30 days | Active |
| No commits but <90 days old | Paused |
| <365 days old | Dormant |
| Older | Archived |

You can override any of these in the ledger files — they're just markdown.

## Requirements

- Python >= 3.10
- Claude Code (for the hooks integration)
- `gh` CLI (optional, for GitHub repo discovery)

## License

MIT
