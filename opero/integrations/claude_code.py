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
        sections.append("### MANDATORY Workflow")
        sections.append("Work is organized as: **Project → Features → Tasks**")
        sections.append("")
        sections.append("You have an MCP server called `opero` with tools. Use them as MCP tools, NOT as bash commands.")
        sections.append("")
        sections.append("**BEFORE writing any code, you MUST use the MCP tool `opero_start_work`.**")
        sections.append("This is an MCP tool (not a CLI command). Call it via the MCP server `opero`.")
        sections.append("It searches for existing tasks, creates one, stores the user's intent, and returns relevant memories.")
        sections.append("")
        sections.append("**AFTER finishing work, you MUST use the MCP tool `opero_complete_work`.**")
        sections.append("This stores what you built, what you learned, and any decisions made.")
        sections.append("")
        sections.append("1. User asks for something → use MCP tool `opero_start_work` with their request, your intent, and thought process")
        sections.append("2. Review the returned existing tasks and memories — don't duplicate work")
        sections.append("3. Write code (Opero auto-commits every change)")
        sections.append("4. When done → use MCP tool `opero_complete_work` with outcome, learnings, and decisions")
        sections.append("5. **DO NOT commit manually** — Opero handles git automatically")
        sections.append("6. **DO NOT skip opero_start_work** — every piece of work must be tracked")
        sections.append("7. **These are MCP tools, NOT CLI commands** — do not run them in Bash")
        sections.append("")

        # Commands reference
        sections.append("### Available Commands")
        sections.append("```")
        sections.append("opero status                           # Full system state")
        sections.append("opero features                         # List features with progress")
        sections.append("opero features add -t 'Feature name'   # Create feature")
        sections.append("opero features view --id X             # View feature + tasks")
        sections.append("opero features board                   # Full board view")
        sections.append("opero tasks                            # List all tasks")
        sections.append("opero tasks next                       # Get next priority task")
        sections.append("opero tasks add --title '...'          # Create task")
        sections.append("opero tasks update --id X --status Y   # Update task")
        sections.append("opero memory search --query '...'      # Search memory")
        sections.append("opero memory store --type T --title X  # Store memory")
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

        # Features and tasks
        board = self.engine.features.get_full_view(project.id)
        if board:
            sections.append("## Features")
            for item in board:
                f = item["feature"]
                p = item["progress"]
                if f["status"] in ("planning", "active"):
                    sections.append(f"### {f['title']} [{f['status']}] — {p['done']}/{p['total']} tasks done")
                    if f.get("description"):
                        sections.append(f"{f['description']}")
                    for t in item["tasks"]:
                        if t["status"] in ("todo", "in_progress"):
                            icon = ">" if t["status"] == "in_progress" else " "
                            sections.append(f"- [{icon}] `{t['id']}` [{t['type']}] {t['title']}")
                    sections.append("")

        # Unassigned tasks (not in any feature)
        all_tasks = self.engine.tasks.list_tasks(project_id=project.id)
        orphan_tasks = [t for t in all_tasks if not t.feature_id and t.status.value in ("todo", "in_progress")]
        if orphan_tasks:
            sections.append("## Unassigned Tasks (need a feature)")
            for t in orphan_tasks:
                status_icon = ">" if t.status.value == "in_progress" else " "
                sections.append(f"- [{status_icon}] `{t.id}` P{t.priority} [{t.type.value}] {t.title}")
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
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{py} -m opero.integrations.claude_code --hook user-prompt"
                            }
                        ]
                    }
                ],
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
        """Configure Opero MCP server in Claude Code settings.local.json."""
        settings_dir = Path(self.project_path) / ".claude"
        settings_dir.mkdir(exist_ok=True)

        # MCP servers go in settings.local.json (Claude Code reads MCP from here)
        local_path = settings_dir / "settings.local.json"
        existing = {}
        if local_path.exists():
            try:
                existing = json.loads(local_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        mcp = self.get_mcp_config()
        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"].update(mcp.get("mcpServers", {}))

        local_path.write_text(json.dumps(existing, indent=2) + "\n")

        # Also remove mcpServers from settings.json if present (cleanup)
        settings_path = settings_dir / "settings.json"
        if settings_path.exists():
            try:
                shared = json.loads(settings_path.read_text())
                if "mcpServers" in shared:
                    del shared["mcpServers"]
                    settings_path.write_text(json.dumps(shared, indent=2) + "\n")
            except (json.JSONDecodeError, OSError):
                pass

        return local_path

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
# Session + activity tracking
# ---------------------------------------------------------------------------

def _get_session_id() -> str:
    """Get or create a session ID for this Claude Code session.

    Uses an env var so all hook calls within the same Claude session
    share one ID. Falls back to a file-based session.
    """
    sid = os.environ.get("OPERO_SESSION_ID")
    if sid:
        return sid

    # Check for a session file (created on first hook call)
    session_file = Path(os.getcwd()) / ".opero" / ".session"
    if session_file.exists():
        content = session_file.read_text().strip()
        # Session is stale if file is older than 2 hours
        age = datetime.utcnow().timestamp() - session_file.stat().st_mtime
        if age < 7200 and content:
            return content

    # New session
    import uuid
    sid = uuid.uuid4().hex[:12]
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(sid)
    return sid


def _log_activity(tool_name: str, action: str = "", file_path: str = "", detail: str = ""):
    """Log Claude Code tool activity to the database."""
    try:
        from opero.db.schema import get_connection
        cwd = os.getcwd()
        conn = get_connection(cwd)

        # Get project ID
        row = conn.execute("SELECT id FROM projects WHERE path = ?", (cwd,)).fetchone()
        project_id = row["id"] if row else None

        session_id = _get_session_id()

        # Get current task (if any in_progress)
        task_id = None
        if project_id:
            task_row = conn.execute(
                "SELECT id FROM tasks WHERE project_id = ? AND status = 'in_progress' LIMIT 1",
                (project_id,),
            ).fetchone()
            task_id = task_row["id"] if task_row else None

        conn.execute(
            "INSERT INTO claude_activity (project_id, session_id, tool_name, action, file_path, task_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, session_id, tool_name, action, file_path, task_id, detail),
        )

        # Upsert session
        conn.execute(
            """INSERT INTO claude_sessions (id, project_id, status, current_task_id, last_heartbeat, started_at)
               VALUES (?, ?, 'active', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET last_heartbeat = CURRENT_TIMESTAMP, current_task_id = ?, status = 'active'""",
            (session_id, project_id, task_id, task_id),
        )

        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hook handlers — called by Claude Code hooks via stdin JSON
# ---------------------------------------------------------------------------

def _parse_hook_input() -> dict:
    """Parse the JSON that Claude Code sends to hook stdin."""
    import sys
    try:
        data = sys.stdin.read()
        if data:
            return json.loads(data)
    except Exception:
        pass
    return {}


def _auto_commit_and_remember(file_path: str, action: str, task_id: str | None, cwd: str):
    """Auto-commit a file change and store a memory of what was done.

    Acts like a real developer: every meaningful change gets committed
    immediately with a descriptive message linked to the active task.
    """
    import subprocess

    try:
        # Check if the file is inside the project (not in .opero, .claude, etc.)
        rel = os.path.relpath(file_path, cwd)
        if rel.startswith(".opero") or rel.startswith(".claude") or rel == "CLAUDE.md":
            return

        # Check if file actually has changes
        status = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain", "--", file_path],
            capture_output=True, text=True, check=False,
        )
        if not status.stdout.strip():
            return  # No changes to commit

        # Stage the file
        subprocess.run(
            ["git", "-C", cwd, "add", "--", file_path],
            capture_output=True, check=False,
        )

        # Build commit message
        fname = Path(file_path).name
        verb = "Update" if action == "edit" else "Add"
        task_prefix = f"[{task_id}] " if task_id else ""
        commit_msg = f"{task_prefix}{verb} {fname}"

        # Commit
        result = subprocess.run(
            ["git", "-C", cwd, "commit", "-m", commit_msg],
            capture_output=True, text=True, check=False,
        )

        if result.returncode != 0:
            return

        # Get the commit SHA
        sha_result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        sha = sha_result.stdout.strip()[:12] if sha_result.returncode == 0 else ""

        # Store a memory of this change
        from opero.core.memory import MemoryEntry, MemoryType
        from opero.db.schema import get_connection

        conn = get_connection(cwd)
        row = conn.execute("SELECT id FROM projects WHERE path = ?", (cwd,)).fetchone()
        conn.close()

        if row:
            project_id = row["id"]
            engine = OperoEngine(cwd)
            engine.memory.store(MemoryEntry(
                project_id=project_id,
                type=MemoryType.CONTEXT,
                title=commit_msg,
                content=f"File: {rel}\nAction: {action}\nCommit: {sha}",
                tags=["auto-commit", action, Path(file_path).suffix.lstrip(".")],
                source="claude",
                source_ref=sha,
                importance=4,
            ))

            # Sync commit to opero's git tracking
            engine.git.sync_commits(project_id)

        _log_activity("git", "commit", file_path, f"Committed: {commit_msg}")

    except Exception:
        pass


def handle_post_tool(hook_input: dict = None):
    """After a tool call: log activity, auto-commit code changes, store memory."""
    try:
        if hook_input is None:
            hook_input = _parse_hook_input()

        tool_name = hook_input.get("tool_name", "unknown")
        tool_input = hook_input.get("tool_input", {})

        # Extract useful info from the tool call
        action = ""
        file_path = ""
        detail = ""

        if tool_name == "Edit":
            file_path = tool_input.get("file_path", "")
            action = "edit"
            detail = f"Edited {Path(file_path).name}" if file_path else ""
        elif tool_name == "Write":
            file_path = tool_input.get("file_path", "")
            action = "write"
            detail = f"Wrote {Path(file_path).name}" if file_path else ""
        elif tool_name == "Bash":
            cmd = tool_input.get("command", "")
            action = "bash"
            detail = cmd[:100] if cmd else ""
        elif tool_name == "Read":
            file_path = tool_input.get("file_path", "")
            action = "read"
            detail = f"Read {Path(file_path).name}" if file_path else ""
        else:
            action = tool_name.lower()

        # Log the activity
        _log_activity(tool_name, action, file_path, detail)

        # Auto-commit on Edit/Write (act like a real developer)
        cwd = os.getcwd()
        if tool_name in ("Edit", "Write") and file_path:
            from opero.db.schema import get_connection
            conn = get_connection(cwd)
            row = conn.execute("SELECT id FROM projects WHERE path = ?", (cwd,)).fetchone()
            project_id = row["id"] if row else None
            task_id = None
            if project_id:
                task_row = conn.execute(
                    "SELECT id FROM tasks WHERE project_id = ? AND status = 'in_progress' LIMIT 1",
                    (project_id,),
                ).fetchone()
                task_id = task_row["id"] if task_row else None
            conn.close()
            _auto_commit_and_remember(file_path, action, task_id, cwd)

        # Refresh CLAUDE.md if stale (every 60s)
        engine = OperoEngine(cwd)
        if not engine.is_initialized():
            return

        claude_md = Path(cwd) / "CLAUDE.md"
        if claude_md.exists():
            age = datetime.utcnow().timestamp() - claude_md.stat().st_mtime
            if age < 60:
                return
        integration = ClaudeCodeIntegration(cwd)
        integration.write_claude_md()
    except Exception:
        pass


def handle_user_prompt(hook_input: dict = None):
    """When user submits a prompt, inject Opero context as a reminder.

    This hook fires BEFORE Claude processes the message. Outputs JSON
    with additionalContext that gets injected into Claude's context.
    """
    try:
        if hook_input is None:
            hook_input = _parse_hook_input()

        cwd = os.getcwd()
        engine = OperoEngine(cwd)
        if not engine.is_initialized():
            return

        project = engine.projects.get_by_path()
        if not project:
            return

        # Build context for Claude
        all_tasks = engine.tasks.list_tasks(project_id=project.id)
        in_progress = [t for t in all_tasks if t.status.value == "in_progress"]
        features = engine.features.list_features(project.id)
        active_features = [f for f in features if f.status.value == "active"]

        lines = []
        lines.append("[OPERO] MANDATORY: Use the MCP tool opero_start_work (from the 'opero' MCP server) BEFORE writing any code. Use opero_complete_work when done. These are MCP tools, NOT bash commands.")

        if in_progress:
            task_list = ", ".join(f"{t.title} ({t.id})" for t in in_progress[:3])
            lines.append(f"[OPERO] In progress: {task_list}")

        if active_features:
            feat_list = ", ".join(f"{f.title} ({f.id})" for f in active_features[:3])
            lines.append(f"[OPERO] Active features: {feat_list}")

        if not in_progress and not active_features:
            lines.append("[OPERO] No active work. Use opero_start_work to create a feature and task first.")

        context = "\n".join(lines)

        # Output structured JSON to stdout — Claude Code injects additionalContext
        import sys
        output = json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        })
        sys.stdout.write(output)
        sys.stdout.flush()

        # Log the prompt as activity
        user_msg = hook_input.get("prompt", "")
        _log_activity("user", "prompt", detail=user_msg[:200] if user_msg else "")

    except Exception:
        pass


def handle_pre_tool(hook_input: dict = None):
    """Before a tool call, ensure CLAUDE.md exists and log session start."""
    try:
        if hook_input is None:
            hook_input = _parse_hook_input()

        # Ensure session is tracked
        _log_activity("session", "heartbeat")

        claude_md = Path(os.getcwd()) / "CLAUDE.md"
        if not claude_md.exists():
            engine = OperoEngine(os.getcwd())
            if engine.is_initialized():
                integration = ClaudeCodeIntegration(os.getcwd())
                integration.write_claude_md()
    except Exception:
        pass


def handle_on_stop(hook_input: dict = None):
    """When Claude stops, mark session ended, sync git, refresh CLAUDE.md."""
    try:
        # Mark session as stopped
        from opero.db.schema import get_connection
        cwd = os.getcwd()
        session_id = _get_session_id()
        conn = get_connection(cwd)
        conn.execute(
            "UPDATE claude_sessions SET status = 'stopped', stopped_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        conn.commit()
        conn.close()

        _log_activity("session", "stop", detail="Claude session ended")

        # Clean up session file
        session_file = Path(cwd) / ".opero" / ".session"
        if session_file.exists():
            session_file.unlink()

        engine = OperoEngine(cwd)
        if not engine.is_initialized():
            return
        engine.sync()
        integration = ClaudeCodeIntegration(cwd)
        integration.write_claude_md()
    except Exception:
        pass


if __name__ == "__main__":
    import sys

    hook_input = _parse_hook_input()

    args = sys.argv[1:]
    if "--hook" in args:
        idx = args.index("--hook")
        hook_type = args[idx + 1] if idx + 1 < len(args) else ""
        if hook_type == "user-prompt":
            handle_user_prompt(hook_input)
        elif hook_type == "post-tool":
            handle_post_tool(hook_input)
        elif hook_type == "pre-tool":
            handle_pre_tool(hook_input)
        elif hook_type == "on-stop":
            handle_on_stop(hook_input)
