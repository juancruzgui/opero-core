"""Interactive orchestrator — launches Claude Code as a conversational PM.

Instead of CLI flags, the user just runs `opero` and talks naturally.
Claude acts as the PM: helps build specs, creates features/tasks, then
dispatches dev agents and monitors their progress.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _ensure_mcp_config(project_path: str) -> str:
    """Ensure .mcp.json exists for MCP tool access."""
    mcp_path = Path(project_path).resolve() / ".mcp.json"
    abs_project_path = str(Path(project_path).resolve())

    import opero
    opero_root = str(Path(opero.__file__).resolve().parent.parent)

    py = sys.executable
    env = {"OPERO_PROJECT_PATH": abs_project_path}
    try:
        from importlib.metadata import distribution
        distribution("opero")
    except Exception:
        env["PYTHONPATH"] = opero_root

    config = {
        "mcpServers": {
            "opero": {
                "command": py,
                "args": ["-m", "opero.mcp.stdio_server"],
                "env": env,
            }
        }
    }
    mcp_path.write_text(json.dumps(config, indent=2))
    return str(mcp_path)


def _build_system_prompt(project_path: str, project_id: str) -> str:
    """Build the system prompt for the interactive PM session."""
    from opero.core.engine import OperoEngine
    engine = OperoEngine(project_path)

    # Gather current state
    project = engine.projects.get_by_path()
    project_name = project.name if project else "Unknown"
    tasks = engine.tasks.list_tasks(project_id=project_id)
    features = engine.features.list_features(project_id)

    todo = sum(1 for t in tasks if t.status.value == "todo")
    in_progress = sum(1 for t in tasks if t.status.value == "in_progress")
    done = sum(1 for t in tasks if t.status.value == "done")
    blocked = sum(1 for t in tasks if t.status.value == "blocked")

    feature_summary = ""
    if features:
        lines = []
        for f in features:
            prog = engine.features.get_progress(f.id)
            lines.append(f"  - {f.title} [{f.status.value}] — {prog['done']}/{prog['total']} tasks done")
        feature_summary = "Current features:\n" + "\n".join(lines)

    return f"""You are Opero — an AI project manager and development orchestrator.
You help the user build software by managing the full development lifecycle:
planning specs, breaking them into features and tasks, dispatching AI dev agents,
testing, and reviewing.

## Project: {project_name}
Tasks: {todo} todo, {in_progress} in progress, {done} done, {blocked} blocked
{feature_summary}

## Your Capabilities

You have access to the opero MCP tools:
- `opero_feature_create` / `opero_feature_list` / `opero_feature_get` / `opero_feature_update` — manage features
- `opero_feature_task` — create tasks under features
- `opero_task_create` / `opero_tasks_list` / `opero_task_update` — manage tasks
- `opero_memory_store` / `opero_memory_search` / `opero_context` — project memory
- `opero_start_work` / `opero_complete_work` — track your own work
- `opero_verify_task` — mark tasks as verified/failed
- `opero_orchestrator_status` — check loop status

You can also Edit files, run Bash commands, and do everything Claude Code can do.

## How to Work

### When the user describes what they want to build:
1. Ask clarifying questions to understand the full scope
2. Propose a feature breakdown — show them the plan
3. When they approve, create features and tasks using the MCP tools
4. Each task MUST have `success_criteria` — specific, testable conditions

### When the user says to start building (e.g. "go", "build it", "start"):
1. Run the orchestrator loop by executing:
   ```bash
   python -m opero.orchestrator.run_loop --project-path "{project_path}" --project-id "{project_id}"
   ```
   This launches dev agents in the background.
2. Tell the user the loop is running and they can watch at http://localhost:7437

### When the user asks about progress:
1. Call `opero_tasks_list` and `opero_feature_list` to check status
2. Summarize what's done, in progress, and blocked

### When the user wants to change scope:
1. Create/update features and tasks as needed
2. If agents are running, new tasks will be picked up automatically

## Tone
- Be conversational and collaborative, not robotic
- Ask questions when the spec is vague
- Suggest best practices (tech stack, architecture) when relevant
- Proactively flag risks or missing pieces
- Keep the user informed about what's happening without being verbose
"""


def _build_resume_prompt(project_path: str, project_id: str) -> str:
    """Build a prompt that catches up on existing project state."""
    from opero.core.engine import OperoEngine
    engine = OperoEngine(project_path)

    tasks = engine.tasks.list_tasks(project_id=project_id)
    features = engine.features.list_features(project_id)

    if not features and not tasks:
        return ""

    lines = ["Here's where the project stands:\n"]
    for f in features:
        prog = engine.features.get_progress(f.id)
        lines.append(f"**{f.title}** [{f.status.value}] — {prog['done']}/{prog['total']} tasks")
        ftasks = engine.features.get_tasks(f.id)
        for t in ftasks:
            agent = f" [{t.assigned_agent}]" if t.assigned_agent else ""
            lines.append(f"  - [{t.status.value}] {t.title}{agent}")

    return "\n".join(lines)


def launch_interactive(project_path: str, project_id: str,
                       parallel: int = 1, open_dashboard: bool = True):
    """Launch an interactive Claude Code session as the PM orchestrator."""
    mcp_config = _ensure_mcp_config(project_path)
    system_prompt = _build_system_prompt(project_path, project_id)

    # Start dashboard in background
    if open_dashboard:
        _start_dashboard_background(project_path)

    print("✦ Opero — AI Project Manager")
    print("  Describe what you want to build. I'll plan it, break it into tasks,")
    print("  and dispatch agents to build it.")
    print()
    if open_dashboard:
        print("  Dashboard: http://localhost:7437")
        print()

    cmd = [
        "claude",
        "--mcp-config", mcp_config,
        "--system-prompt", system_prompt,
    ]

    # If there's existing work, add a resume context as the initial prompt
    resume = _build_resume_prompt(project_path, project_id)
    if resume:
        cmd.extend(["--append-system-prompt", f"\n\n## Current Project State\n{resume}"])

    os.execvp("claude", cmd)


def _start_dashboard_background(project_path: str, port: int = 7437):
    """Start the dashboard server in the background and open in browser."""
    import time
    import webbrowser
    from urllib.request import urlopen
    from urllib.error import URLError

    url = f"http://localhost:{port}"

    # Check if already running
    try:
        urlopen(f"{url}/health", timeout=1)
        # Already running — just open browser
        webbrowser.open(url)
        return
    except (URLError, OSError):
        pass

    # Start server
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "opero.mcp.server:app",
         "--host", "0.0.0.0", "--port", str(port), "--log-level", "error"],
        cwd=project_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "OPERO_PROJECT_PATH": str(Path(project_path).resolve())},
    )

    # Wait for server to be ready (up to 5s), then open browser
    for _ in range(25):
        time.sleep(0.2)
        try:
            urlopen(f"{url}/health", timeout=1)
            webbrowser.open(url)
            return
        except (URLError, OSError):
            continue

    # Server didn't start in time — open anyway, browser will retry
    webbrowser.open(url)
