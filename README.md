# Opero Core

The operating system for AI-driven software development.

Local-first development brain that tracks tasks, stores decisions, and gives Claude Code persistent memory across sessions. No cloud, no API keys — everything runs in SQLite.

## Install

```bash
git clone https://github.com/juancruzgui/opero-core.git
cd opero-core
pip install -e .
```

## Use it on any project

```bash
cd ~/my-project
opero
```

That's it. One command. It:

1. Initializes a git repo (if needed)
2. Creates the Opero database (`.opero/opero.db`)
3. Registers 5 default agents
4. Creates bootstrap tasks
5. Generates `CLAUDE.md` for Claude Code
6. Installs Claude Code hooks (`.claude/settings.json`)
7. Configures MCP server so Claude gets Opero tools directly

Then open Claude Code:

```bash
claude
```

Claude will read `CLAUDE.md`, connect to Opero via MCP, and follow the rules: create tasks before working, store decisions, search memory before deciding.

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
opero memory list         # All stored memories
opero agents             # List registered agents
opero sync               # Sync git commits to task system
opero claude sync        # Regenerate CLAUDE.md from current state
opero serve              # Start HTTP MCP server (port 7437)
```

## How it works with Claude Code

Three integration paths, all automatic after `opero`:

**CLAUDE.md** — Generated from Opero's database. Contains project decisions, conventions, active tasks, git state. Claude Code reads this on every conversation start.

**MCP tools** — Claude gets 11 tools via stdio MCP: `opero_status`, `opero_task_create`, `opero_memory_search`, `opero_context`, etc. No HTTP — direct stdin/stdout.

**Hooks** — After every Bash/Edit/Write, hooks refresh CLAUDE.md. When Claude stops, hooks sync git state. Next session picks up where the last left off.

## What gets created in your project

```
my-project/
  CLAUDE.md              # Auto-generated, gitignored
  .claude/
    settings.json        # Hooks + MCP config
  .opero/
    opero.db             # All state, gitignored
    .gitignore
```

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

## Tech stack

- Python
- FastAPI (MCP HTTP server)
- SQLite with WAL mode
- Git CLI
- Zero external services
