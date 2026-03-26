# Opero Core

The operating system for AI-driven software development.

Local-first development brain that tracks tasks, stores decisions, and gives Claude Code persistent memory across sessions. No cloud, no API keys — everything runs in SQLite.

## Setup

From inside your project:

```bash
cd ~/my-project
git clone https://github.com/juancruzgui/opero-core.git .opero-core
.opero-core/install.sh
```

That's it. The install script:

1. Installs `opero` as a CLI command
2. Initializes git (if needed)
3. Creates the database (`.opero/opero.db`)
4. Registers 5 default agents
5. Creates bootstrap tasks
6. Generates `CLAUDE.md` for Claude Code
7. Installs Claude Code hooks + MCP server
8. Adds `.opero-core/`, `.opero/`, `.claude/`, `CLAUDE.md` to your `.gitignore`

Then start Claude Code:

```bash
claude
```

Claude reads `CLAUDE.md`, connects to Opero via MCP, and follows the rules: create tasks before working, store decisions, search memory before deciding.

## What lives in your project

```
my-project/
  .opero-core/             # The opero source (gitignored)
  .opero/                  # Database + state (gitignored)
  .claude/                 # Claude Code hooks + MCP config (gitignored)
  CLAUDE.md                # Auto-generated context (gitignored)
  .gitignore               # Updated automatically
  ... your code ...
```

Everything Opero creates is gitignored. Your repo stays clean.

## Commands

```bash
opero                    # Auto-init + show status
opero status             # Full system state
opero tasks              # List all tasks
opero tasks next         # Get highest priority ready task
opero tasks add -t "X"   # Create a task
opero tasks update --id X --status done
opero memory search -q "why did we choose postgres"
opero memory store --type decision -t "Use Redis" -c "For caching layer"
opero memory list        # All stored memories
opero agents             # List registered agents
opero sync               # Sync git commits to task system
opero claude sync        # Regenerate CLAUDE.md from current state
opero serve              # Start HTTP MCP server (port 7437)
```

## How it works with Claude Code

Three integration paths, all automatic after install:

**CLAUDE.md** — Generated from Opero's database. Contains project decisions, conventions, active tasks, git state. Claude Code reads this on every conversation start.

**MCP tools** — Claude gets 11 tools via stdio MCP: `opero_status`, `opero_task_create`, `opero_memory_search`, `opero_context`, etc. No HTTP — direct stdin/stdout.

**Hooks** — After every Bash/Edit/Write, hooks refresh CLAUDE.md. When Claude stops, hooks sync git state. Next session picks up where the last left off.

## Memory types

| Type | Use for |
|------|---------|
| `decision` | Architectural choices and why |
| `architecture` | System design notes |
| `convention` | Coding standards, workflow rules |
| `preference` | User preferences for AI behavior |
| `learning` | Discovered gotchas, insights |
| `issue` | Known problems, tech debt |
| `plan` | Implementation plans |
| `context` | General context |

Memories are indexed with TF-IDF vectors for semantic search. Pure Python, no external dependencies.

## Updating Opero

```bash
cd ~/my-project/.opero-core
git pull
pip install -e . --quiet
```

Since it's installed in editable mode, pulling new code is all you need.

## Tech stack

- Python
- FastAPI (MCP HTTP server)
- SQLite with WAL mode
- Git CLI
- Zero external services
