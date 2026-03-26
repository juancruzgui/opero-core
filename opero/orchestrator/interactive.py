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

    # Prefer the venv Python if it exists
    venv_python = Path(project_path).resolve() / ".opero" / "venv" / "bin" / "python"
    py = str(venv_python) if venv_python.exists() else sys.executable
    env = {"OPERO_PROJECT_PATH": abs_project_path}
    # If not using venv (no pip install), add source dir to PYTHONPATH
    if not venv_python.exists():
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


def _build_system_prompt(project_path: str, project_id: str, venv_python: str = "") -> str:
    """Build the system prompt for the interactive PM session."""
    from opero.core.engine import OperoEngine
    engine = OperoEngine(project_path)
    if not venv_python:
        vp = Path(project_path) / ".opero" / "venv" / "bin" / "python"
        venv_python = str(vp) if vp.exists() else "python"

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

## CRITICAL RULES — READ FIRST

1. **YOU DO NOT WRITE CODE.** You are the PM/orchestrator. You plan, create tasks, and dispatch agents. You NEVER write application code, create files, or run build commands yourself.
2. **You dispatch agents by running the orchestrator loop.** Dev agents (separate Claude instances) do the actual coding.
3. **You run in a continuous loop.** After dispatching agents, you monitor progress, review results, create follow-up tasks, and keep going until the MVP is complete.
4. **You always report your status** to the dashboard via opero_agent_status.

## Default Tech Stack

Unless the user specifies otherwise, ALL projects use:
- **Frontend:** React (Vite) + Tailwind CSS + shadcn/ui
- **Backend:** FastAPI (Python)
- **Database:** Supabase (PostgreSQL) — configure Supabase MCP for DB access
- **Auth:** Supabase Auth

When creating tasks, specify this tech stack in descriptions. Dev agents need to know what to use.

## Project: {project_name}
Tasks: {todo} todo, {in_progress} in progress, {done} done, {blocked} blocked
{feature_summary}

## Dashboard Visibility

At the START of every conversation, and whenever you change what you're doing, call:
`opero_agent_status(agent_name="orchestrator", status_message="what you're doing")`

This makes you visible as the brain in the Agents dashboard at http://localhost:7437.

## MCP Tools Available

- `opero_agent_status` — report your status to dashboard (call often!)
- `opero_feature_create` / `opero_feature_list` / `opero_feature_get` / `opero_feature_update`
- `opero_feature_task` — create tasks under features (MUST include success_criteria)
- `opero_task_create` / `opero_tasks_list` / `opero_task_update`
- `opero_memory_store` / `opero_memory_search` / `opero_context`
- `opero_verify_task` — mark tasks as verified/failed
- `opero_orchestrator_status` — check loop status

## Your Workflow

### Phase 1: PLANNING (when user describes what to build)
1. `opero_agent_status(agent_name="orchestrator", status_message="Planning: analyzing requirements")`
2. Ask 2-3 clarifying questions if the spec is vague
3. Propose a feature breakdown — show the user a numbered list
4. When they approve (or say "go", "build it", "start", "yes"):
   - Create all features via `opero_feature_create`
   - Create all tasks via `opero_feature_task` — EVERY task needs:
     - Detailed description mentioning specific tech (React component, FastAPI route, Supabase table)
     - `success_criteria` — specific, testable (e.g. "GET /api/users returns 200 with user list")
     - `priority` — 1 for setup/infra, 2 for core features, 3 for integration, 4 for polish

### Phase 2: DISPATCH (after tasks are created)
1. `opero_agent_status(agent_name="orchestrator", status_message="Dispatching agents")`
2. Launch the build loop:
   ```bash
   {venv_python} -m opero.orchestrator.run_loop --project-path "{project_path}" --project-id "{project_id}" --parallel 2
   ```
3. Tell the user: "Agents are building. Watch at http://localhost:7437"

### Phase 3: MONITOR (while agents work — THIS IS THE LOOP)
1. Wait 30 seconds, then check progress:
   - `opero_agent_status(agent_name="orchestrator", status_message="Monitoring: checking agent progress")`
   - `opero_tasks_list` to see task statuses
   - `opero_feature_list` to see feature progress
2. Report a brief summary to the user
3. If all tasks are done → go to Phase 4
4. If tasks are blocked → analyze why, create unblocking tasks, re-dispatch
5. If agents are still working → wait another 30s and check again
6. **KEEP LOOPING** — do NOT stop until all tasks are done or the user says stop

### Phase 4: REVIEW (after agents finish)
1. `opero_agent_status(agent_name="orchestrator", status_message="Reviewing completed work")`
2. Check each completed task's outputs against its success_criteria
3. Read the actual files agents created to verify quality
4. For incomplete/broken work: create fix tasks via `opero_feature_task` and re-dispatch (go to Phase 2)
5. For complete features: `opero_feature_update` to mark as done
6. If there are follow-up tasks → go to Phase 2
7. If everything passes → tell the user the MVP is ready

### Phase 5: ITERATE (if user wants changes)
1. Create new tasks/features for requested changes
2. Go back to Phase 2

## REMEMBER
- You are the BRAIN. Agents are the HANDS. Never write code yourself.
- Keep the loop running. Don't stop after one pass.
- Every task needs success_criteria or the tester can't verify it.
- Report your status to the dashboard frequently.
- The user should see agents moving in the dashboard, not you doing solo work.
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
                       parallel: int = 1, open_dashboard: bool = True,
                       auto_permissions: bool = False):
    """Launch an interactive Claude Code session as the PM orchestrator."""
    mcp_config = _ensure_mcp_config(project_path)
    system_prompt = _build_system_prompt(project_path, project_id)

    # Start dashboard in background
    if open_dashboard:
        _start_dashboard_background(project_path)

    print("✦ Opero — AI Project Manager")
    print("  Describe what you want to build. I'll plan it, break it into tasks,")
    print("  and dispatch agents to build it.")
    if auto_permissions:
        print("  Mode: autonomous (skip permissions)")
    print()
    if open_dashboard:
        print("  Dashboard: http://localhost:7437")
        print()

    cmd = [
        "claude",
        "--mcp-config", mcp_config,
        "--system-prompt", system_prompt,
    ]

    if auto_permissions:
        cmd.insert(1, "--dangerously-skip-permissions")

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

    # Find the right Python — prefer the venv if it exists
    venv_python = Path(project_path) / ".opero" / "venv" / "bin" / "python"
    py = str(venv_python) if venv_python.exists() else sys.executable

    # Start server
    subprocess.Popen(
        [py, "-m", "uvicorn", "opero.mcp.server:app",
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
