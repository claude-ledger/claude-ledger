# claude-ledger

[![PyPI version](https://img.shields.io/pypi/v/claude-ledger)](https://pypi.org/project/claude-ledger/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/claude-ledger)](https://pypi.org/project/claude-ledger/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Portfolio-level project tracking for [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

## Why This Exists

Claude Code is excellent at per-project memory. Each project gets its own `CLAUDE.md`, auto-memory files, and todos. Within a single project, context persists well.

But if you work across many projects — 10, 20, 50+ — there's a fundamental gap: **Claude starts every session ignorant of what happened in your other projects, even on the same machine.** There's no portfolio layer.

This is the problem I hit running 30+ projects with Claude Code as my primary development tool. The symptoms:

- **Constant re-explaining.** "Where were we?" became the opening line of every session. Not just for the current project — for the *portfolio*. Which projects are active? What did I ship yesterday? What's been neglected?
- **Invisible progress.** I built a session tracking system but never wired the hooks. Six days of work on a client project went completely untracked. I only discovered this during an audit.
- **Lost cross-project awareness.** Pausing one project without realising it blocks three others. Changing shared infrastructure without knowing what depends on it. No cascade warnings.
- **Session amnesia after compaction.** Mid-session context compression would wipe portfolio awareness entirely. The project I was *currently* working on survived, but everything else vanished.

Per-project memory is solved. **Portfolio-level intelligence is not.** That's what claude-ledger addresses.

## How It Fits Into Claude Code's Memory System

Claude Code already has a layered memory system. claude-ledger adds the missing layer:

```
Layer 1: CLAUDE.md          Per-project instructions, vocabulary, protocols
Layer 2: Auto-memory        Per-project facts, preferences, feedback
Layer 3: Todos/Tasks        Per-project work tracking
─────────────────────────────────────────────────────────
Layer 4: claude-ledger      Cross-project portfolio awareness  ← NEW
```

Layers 1-3 answer: "What do I need to know about *this* project?"

Layer 4 answers: "What's happening across *all* my projects? What's active, what's stale, what depends on what, and what did I do yesterday?"

claude-ledger sits *on top of* the existing system — it doesn't replace anything. Your CLAUDE.md files, memory, and todos keep working exactly as they do now. The ledger adds the portfolio view that was missing.

## How It Works

claude-ledger is a set of Claude Code hooks + a CLI that automatically:

1. **Tracks activity** — every file edit and git commit is recorded to a per-project ledger file, without you doing anything
2. **Generates briefings** — on session start, Claude gets a portfolio summary with priority tiers and staleness detection
3. **Maps workstreams** — groups related projects together and warns when changes might cascade

The key design principle: **capture must be fully automatic.** Manual triggers have already proven unreliable — if it requires a human to remember to run something, it won't happen. Hooks solve this.

### What Claude Sees at Session Start

When you start a Claude Code session, the SessionStart hook generates a briefing like:

```
Portfolio briefing ready — 32 projects tracked, 7 P1 active, 3 stale.
Read ~/.claude/ledger/_portfolio.md and ~/.claude/ledger/_workstreams.md for full context.
```

Claude can then read the full briefing — grouped by priority, with staleness warnings and workstream cascade alerts — before you even type your first message.

### What Happens During a Session

As you work, hooks fire silently in the background:

- **Edit a file** → the ledger records which project was touched
- **Make a git commit** → the commit message and SHA are captured to the project's activity log
- **Claude responds** → the session summary is stored
- **Session ends** → everything is finalised and the ledger repo is committed (giving you time-travel via `git log`)

You never interact with the ledger directly. It just accumulates.

### New Projects Are Discovered Automatically

When you start working on a project that hasn't been scanned yet, the hooks detect it. If the file you're editing is inside one of your configured `scan_dirs`, claude-ledger auto-creates a minimal ledger file on the spot — no re-scan needed.

```
You create ~/Code/new-project/ → start editing files →
hook fires → "I don't know this project" →
checks: inside a scan_dir? → yes →
creates new-project.md (P3, active, discovered) →
updates directory index → tracks from now on
```

The portfolio grows organically as you work. The initial `scan` + `bootstrap` gives you a snapshot of everything that exists today, but the ledger stays current without manual intervention.

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
├── _portfolio.md            # Auto-generated briefing (Claude reads this at session start)
├── _workstreams.md          # Cross-project dependency map with cascade warnings
├── .git/                    # Time-travel — git log shows portfolio state over time
├── my-project.md            # One ledger file per project
└── another-project.md       #   (YAML frontmatter + activity log)
```

The whole thing is plain markdown files in `~/.claude/`. No database, no daemon, no external service. Git-backed for time-travel. Human-readable. Works offline.

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

# Sub-projects inside a parent repo (optional)
# When you have multiple logical projects in one repo, map paths to slugs.
# Each gets its own ledger file + cascade warnings in the workstream map.
sub_projects:
  studio-manager:
    parent: my-monorepo
    paths:
      - "docs/hr/*"
      - "docs/projects/STUDIO-MANAGER*"
  curriculum:
    parent: my-monorepo
    paths:
      - "docs/curriculum/*"

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

### 18 March 2026
- Initial project scaffold (1234567)
```

These are just markdown files. Edit them freely — change priorities, update the vision, add notes. The hooks only ever *append* to the activity log and update timestamps.

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

### Concurrent Sessions

If you run multiple Claude Code sessions at once (different terminals, different projects), the ledger handles it safely. Each project's ledger file has its own lock file, and session state is isolated per session ID. Two sessions touching the same project won't corrupt each other's data.

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

## Design Decisions

**Why plain files, not a database?** Markdown files are human-readable, git-compatible, and always available. No MCP dependency, no daemon, no external service. Each file is independently lockable for thread-safe concurrent writes.

**Why YAML frontmatter?** Structured enough for machines to parse (priority sorting, staleness detection), readable enough for humans to edit. The format is forward-compatible — a future daemon or dashboard could ingest these files directly.

**Why hooks, not manual tracking?** Because manual triggers don't work. I built a session tracking script and forgot to wire the hooks for 6 days. If it requires a human to remember, it won't happen.

**Why the workstreams layer?** Because pausing one project can silently block three others. Cascade warnings surface cross-project risks *before* they cause problems.

## Requirements

- Python >= 3.10
- Claude Code (for the hooks integration)
- `gh` CLI (optional, for GitHub repo discovery)

## License

MIT
