"""Claude Code integration for Opero Core.

Generates CLAUDE.md from Opero state so Claude Code uses Opero as its
single source of truth. Also configures hooks and MCP server settings.

How it works:
1. `opero claude sync` — regenerates CLAUDE.md from current project state
   (tasks, memory, decisions, conventions, git status)
2. `opero claude hooks` — installs Claude Code hooks that auto-sync
   Opero after tool calls (Bash, Edit, Write)
3. `opero claude mcp` — configures Claude Code to connect to Opero's
   MCP server for direct tool access

Claude Code reads CLAUDE.md on every conversation start. By keeping it
in sync with Opero's database, Claude always has fresh context without
any API calls.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from opero.core.engine import OperoEngine
from opero.core.memory import MemoryType


class ClaudeCodeIntegration:
    """Manages the bridge between Opero Core and Claude Code."""

    def __init__(self, project_path: str):
        self.project_path = project_path
        self.engine = OperoEngine(project_path)

    def generate_claude_md(self) -> str:
        """Generate CLAUDE.md content from current Opero state."""
        project = self.engine.projects.get_by_path()
        if not project:
            return "# Project not initialized\nRun `opero init` to set up this project.\n"

        sections = []

        # Header
        sections.append(f"# {project.name}")
        if project.description:
            sections.append(f"\n{project.description}")
        sections.append("")

        # Opero integration instructions
        sections.append("## Opero Core Integration")
        sections.append("")
        sections.append("This project is managed by Opero Core. You MUST follow these rules:")
        sections.append("")
        sections.append("### Rules")
        sections.append("1. **Check tasks before working**: Run `opero tasks` to see current tasks")
        sections.append("2. **No work without a task**: Before writing code, ensure a task exists. Create one with `opero tasks add --title '...'`")
        sections.append("3. **Update task status**: When starting work, run `opero tasks update --id <id> --status in_progress`")
        sections.append("4. **Complete tasks**: When done, run `opero tasks update --id <id> --status done`")
        sections.append("5. **Store decisions**: When making architectural decisions, run:")
        sections.append("   `opero memory store --type decision --title '...' --content '...' --source claude`")
        sections.append("6. **Store learnings**: When discovering something important, run:")
        sections.append("   `opero memory store --type learning --title '...' --content '...' --source claude`")
        sections.append("7. **Search memory before deciding**: Run `opero memory search --query '...'` to check for prior decisions")
        sections.append("8. **Commit with task refs**: Use `[task_id]` prefix in commit messages")
        sections.append("9. **Sync after commits**: Run `opero sync` after committing")
        sections.append("")

        # Commands reference
        sections.append("### Available Commands")
        sections.append("```")
        sections.append("opero status                           # Full system state")
        sections.append("opero tasks                            # List all tasks")
        sections.append("opero tasks next                       # Get next priority task")
        sections.append("opero tasks add --title '...'          # Create task")
        sections.append("opero tasks update --id X --status Y   # Update task")
        sections.append("opero memory search --query '...'      # Search memory")
        sections.append("opero memory store --type T --title X  # Store memory")
        sections.append("opero memory list                      # List all memories")
        sections.append("opero memory context --query '...'     # Get full context")
        sections.append("opero sync                             # Sync git state")
        sections.append("```")
        sections.append("")

        # Tech stack
        if project.tech_stack:
            sections.append("## Tech Stack")
            sections.append(project.tech_stack)
            sections.append("")

        # Architecture
        if project.architecture_notes:
            sections.append("## Architecture")
            sections.append(project.architecture_notes)
            sections.append("")

        # Architecture memories
        arch_memories = self.engine.memory.list_memories(
            project.id, memory_type=MemoryType.ARCHITECTURE
        )
        if arch_memories:
            sections.append("## Architecture Notes")
            for m in arch_memories:
                sections.append(f"### {m.title}")
                sections.append(m.content)
                sections.append("")

        # Active decisions
        decisions = self.engine.memory.list_memories(
            project.id, memory_type=MemoryType.DECISION
        )
        if decisions:
            sections.append("## Decisions")
            for d in decisions:
                imp_marker = " [CRITICAL]" if d.importance == 1 else ""
                sections.append(f"- **{d.title}**{imp_marker}: {d.content}")
            sections.append("")

        # Conventions
        conventions = self.engine.memory.list_memories(
            project.id, memory_type=MemoryType.CONVENTION
        )
        if conventions:
            sections.append("## Conventions")
            for c in conventions:
                sections.append(f"- **{c.title}**: {c.content}")
            sections.append("")

        # Preferences
        preferences = self.engine.memory.list_memories(
            project.id, memory_type=MemoryType.PREFERENCE
        )
        if preferences:
            sections.append("## Preferences")
            for p in preferences:
                sections.append(f"- {p.content}")
            sections.append("")

        # Known issues
        issues = self.engine.memory.list_memories(
            project.id, memory_type=MemoryType.ISSUE
        )
        if issues:
            sections.append("## Known Issues")
            for i in issues:
                sections.append(f"- **{i.title}**: {i.content}")
            sections.append("")

        # Current tasks
        all_tasks = self.engine.tasks.list_tasks(project_id=project.id)
        active_tasks = [t for t in all_tasks if t.status.value in ("todo", "in_progress")]
        if active_tasks:
            sections.append("## Active Tasks")
            for t in active_tasks:
                status_icon = ">" if t.status.value == "in_progress" else " "
                sections.append(f"- [{status_icon}] `{t.id}` P{t.priority} [{t.type.value}] {t.title}")
                if t.description:
                    sections.append(f"  {t.description}")
            sections.append("")

        # Git state
        sections.append("## Git")
        sections.append(f"- Branch: `{self.engine.git.current_branch()}`")
        sections.append(f"- Uncommitted changes: {'yes' if self.engine.git.has_changes() else 'no'}")
        recent = self.engine.git.get_log(5)
        if recent:
            sections.append("- Recent commits:")
            for c in recent:
                sections.append(f"  - `{c['sha'][:8]}` {c['message']}")
        sections.append("")

        # Footer
        sections.append("---")
        sections.append(f"*Auto-generated by Opero Core at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*")
        sections.append(f"*Run `opero claude sync` to refresh*")

        return "\n".join(sections)

    def write_claude_md(self) -> Path:
        """Write CLAUDE.md to the project root."""
        content = self.generate_claude_md()
        path = Path(self.project_path) / "CLAUDE.md"
        path.write_text(content)
        return path

    def get_hooks_config(self) -> dict:
        """Generate Claude Code hooks config for .claude/settings.json."""
        py = self._find_python()
        return {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash|Edit|Write",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{py} -m opero.integrations.claude_code --hook post-tool"
                            }
                        ]
                    }
                ],
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{py} -m opero.integrations.claude_code --hook pre-tool"
                            }
                        ]
                    }
                ],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{py} -m opero.integrations.claude_code --hook on-stop"
                            }
                        ]
                    }
                ]
            }
        }

    def get_mcp_config(self) -> dict:
        """Generate MCP server config for Claude Code settings."""
        py = self._find_python()
        return {
            "mcpServers": {
                "opero": {
                    "command": py,
                    "args": ["-m", "opero.mcp.stdio_server"],
                    "env": {
                        "OPERO_PROJECT_PATH": self.project_path
                    }
                }
            }
        }

    def install_hooks(self) -> Path:
        """Install Claude Code hooks into project .claude/settings.json."""
        settings_dir = Path(self.project_path) / ".claude"
        settings_dir.mkdir(exist_ok=True)
        settings_path = settings_dir / "settings.json"

        existing = {}
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        hooks = self.get_hooks_config()
        existing.update(hooks)

        settings_path.write_text(json.dumps(existing, indent=2) + "\n")
        return settings_path

    def install_mcp(self) -> Path:
        """Configure Opero MCP server in Claude Code settings."""
        settings_dir = Path(self.project_path) / ".claude"
        settings_dir.mkdir(exist_ok=True)
        settings_path = settings_dir / "settings.json"

        existing = {}
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        mcp = self.get_mcp_config()
        existing.update(mcp)

        settings_path.write_text(json.dumps(existing, indent=2) + "\n")
        return settings_path

    def _find_python(self) -> str:
        """Find the Python interpreter that has opero installed.

        Prefers the opero venv if it exists (.opero/venv/bin/python),
        otherwise falls back to the current interpreter.
        """
        venv_python = Path(self.project_path) / ".opero" / "venv" / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)
        import sys
        return sys.executable


# ---------------------------------------------------------------------------
# Hook handlers — called by Claude Code hooks
# ---------------------------------------------------------------------------

def handle_post_tool():
    """After a tool call, sync CLAUDE.md if memory or tasks changed."""
    try:
        engine = OperoEngine(os.getcwd())
        if not engine.is_initialized():
            return

        # Check if CLAUDE.md is stale (older than 60 seconds)
        claude_md = Path(os.getcwd()) / "CLAUDE.md"
        if claude_md.exists():
            age = datetime.utcnow().timestamp() - claude_md.stat().st_mtime
            if age < 60:
                return  # Recent enough, skip

        integration = ClaudeCodeIntegration(os.getcwd())
        integration.write_claude_md()
    except Exception:
        pass  # Hooks must not fail


def handle_pre_tool():
    """Before a Bash tool call, ensure CLAUDE.md exists."""
    try:
        claude_md = Path(os.getcwd()) / "CLAUDE.md"
        if not claude_md.exists():
            engine = OperoEngine(os.getcwd())
            if engine.is_initialized():
                integration = ClaudeCodeIntegration(os.getcwd())
                integration.write_claude_md()
    except Exception:
        pass


def handle_on_stop():
    """When Claude stops, sync git and refresh CLAUDE.md."""
    try:
        engine = OperoEngine(os.getcwd())
        if not engine.is_initialized():
            return
        engine.sync()
        integration = ClaudeCodeIntegration(os.getcwd())
        integration.write_claude_md()
    except Exception:
        pass


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--hook" in args:
        idx = args.index("--hook")
        hook_type = args[idx + 1] if idx + 1 < len(args) else ""
        if hook_type == "post-tool":
            handle_post_tool()
        elif hook_type == "pre-tool":
            handle_pre_tool()
        elif hook_type == "on-stop":
            handle_on_stop()
