"""Opero CLI — the main user interface for Opero Core."""

from __future__ import annotations

import argparse
import json
import os
import sys

from opero.core.engine import OperoEngine
from opero.core.models import Task, TaskType, TaskStatus, Feature, FeatureStatus
from opero.core.memory import MemoryEntry, MemoryType


def get_engine() -> OperoEngine:
    return OperoEngine(os.getcwd())


def cmd_init(args):
    """Initialize Opero in the current directory."""
    engine = get_engine()
    if engine.is_initialized():
        project = engine.projects.get_by_path()
        if project:
            print("✦ Opero already initialized in this directory.")
            print(f"  Project: {project.name} ({project.id})")
            return
        # .opero exists but no project — re-initialize

    name = args.name or ""
    desc = args.description or ""
    project = engine.initialize(name=name, description=desc)
    print(f"✦ Opero initialized: {project.name}")
    print(f"  Project ID: {project.id}")
    print(f"  Path: {project.path}")
    print()
    print(f"  Database:   .opero/opero.db")
    print(f"  Agents:     {len(engine.agents.list_agents())} registered")
    tasks = engine.tasks.list_tasks(project_id=project.id)
    print(f"  Tasks:      {len(tasks)} initial tasks created")
    print(f"  CLAUDE.md:  generated")
    print(f"  Hooks:      installed (.claude/settings.json)")
    print(f"  MCP:        configured (.claude/settings.json)")
    print()
    print("  Ready. Open Claude Code in this directory — it will")
    print("  read CLAUDE.md and connect to Opero via MCP automatically.")
    print()
    print("  Quick start:")
    print("    opero status          # see system state")
    print("    opero tasks           # see tasks")
    print("    opero tasks next      # get next task")
    print("    opero memory search   # search project memory")


def cmd_start(args):
    """Start the Opero daemon."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized. Run 'opero init' first.")
        return

    print("✦ Starting Opero daemon...")
    print("  Watching file system, git, and task state.")
    print("  Press Ctrl+C to stop.")
    print()

    from opero.daemon.watcher import run_daemon
    run_daemon(os.getcwd())


def cmd_status(args):
    """Show full system status."""
    engine = get_engine()
    status = engine.status()

    if not status.get("initialized"):
        print("✦ Opero not initialized. Run 'opero init' to start.")
        return

    project = status["project"]
    tasks = status["tasks"]
    git = status["git"]

    print(f"✦ Opero Status")
    print(f"  Project: {project['name']} ({project['id']})")
    print(f"  Path: {project['path']}")
    print()
    print(f"  Tasks:")
    print(f"    Total:       {tasks['total']}")
    print(f"    Todo:        {tasks['todo']}")
    print(f"    In Progress: {tasks['in_progress']}")
    print(f"    Done:        {tasks['done']}")
    print(f"    Blocked:     {tasks['blocked']}")
    print()
    print(f"  Git:")
    print(f"    Branch: {git['branch']}")
    print(f"    Uncommitted changes: {'yes' if git['has_changes'] else 'no'}")
    print()
    print(f"  Agents: {', '.join(status['agents'])}")


def cmd_tasks(args):
    """List and manage tasks."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized.")
        return

    project = engine.projects.get_by_path()
    if not project:
        print("✦ No project found.")
        return

    if args.task_action == "list" or args.task_action is None:
        status_filter = TaskStatus(args.status) if args.status else None
        tasks = engine.tasks.list_tasks(project_id=project.id, status=status_filter)
        if not tasks:
            print("✦ No tasks found.")
            return

        print(f"✦ Tasks ({len(tasks)}):")
        print()
        for t in tasks:
            agent_str = f" [{t.assigned_agent}]" if t.assigned_agent else ""
            priority_str = "!" * t.priority
            print(f"  {t.id}  P{t.priority} {priority_str:<5}  [{t.status.value:<11}]  {t.title}{agent_str}")
            if t.description and args.verbose:
                print(f"           {t.description}")

    elif args.task_action == "add":
        if not args.title:
            print("✦ Task title required: opero tasks add --title 'My task'")
            return
        task = Task(
            project_id=project.id,
            title=args.title,
            description=args.desc or "",
            type=TaskType(args.type) if args.type else TaskType.FEATURE,
            priority=args.priority or 3,
        )
        created = engine.tasks.create(task)
        print(f"✦ Task created: {created.id} — {created.title}")

    elif args.task_action == "update":
        if not args.id:
            print("✦ Task ID required: opero tasks update --id <task_id>")
            return
        updates = {}
        if args.status:
            updates["status"] = args.status
        if args.title:
            updates["title"] = args.title
        if args.priority:
            updates["priority"] = args.priority
        if args.agent:
            updates["assigned_agent"] = args.agent

        if not updates:
            print("✦ No updates specified.")
            return

        task = engine.tasks.update(args.id, **updates)
        if task:
            print(f"✦ Task updated: {task.id} — [{task.status.value}] {task.title}")
        else:
            print(f"✦ Task not found: {args.id}")

    elif args.task_action == "run":
        if not args.id:
            print("✦ Task ID required: opero tasks run --id <task_id>")
            return
        task = engine.tasks.get(args.id)
        if not task:
            print(f"✦ Task not found: {args.id}")
            return
        execution = engine.agents.run_task(task)
        print(f"✦ Execution started: {execution.id}")
        print(f"  Agent: {execution.agent_name}")
        print(f"  Task: {task.title}")

    elif args.task_action == "next":
        next_task = engine.tasks.get_next_task(project.id)
        if next_task:
            print(f"✦ Next task: {next_task.id} — P{next_task.priority} — {next_task.title}")
            print(f"  Type: {next_task.type.value}")
            if next_task.description:
                print(f"  Description: {next_task.description}")
            if next_task.success_criteria:
                print(f"  Success criteria: {next_task.success_criteria}")
        else:
            print("✦ No tasks ready to execute.")


def cmd_features(args):
    """Manage features/epics."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized.")
        return

    project = engine.projects.get_by_path()
    if not project:
        print("✦ No project found.")
        return

    action = args.feature_action

    if action == "list" or action is None:
        features = engine.features.list_features(project.id)
        if not features:
            print("✦ No features. Create one: opero features add -t 'Auth System'")
            return
        print(f"✦ Features ({len(features)}):")
        print()
        for f in features:
            progress = engine.features.get_progress(f.id)
            bar = _progress_bar(progress["percent"])
            print(f"  {f.id}  P{f.priority}  [{f.status.value:<8}]  {f.title}  {bar} {progress['done']}/{progress['total']}")
            if f.description and args.verbose:
                print(f"           {f.description}")

    elif action == "add":
        if not args.title:
            print("✦ Title required: opero features add -t 'Auth System'")
            return
        feature = Feature(
            project_id=project.id,
            title=args.title,
            description=args.desc or "",
            priority=args.priority or 3,
        )
        created = engine.features.create(feature)
        print(f"✦ Feature created: {created.id} — {created.title}")

    elif action == "view":
        if not args.id:
            print("✦ Feature ID required: opero features view --id <id>")
            return
        feature = engine.features.get(args.id)
        if not feature:
            print(f"✦ Feature not found: {args.id}")
            return
        tasks = engine.features.get_tasks(args.id)
        progress = engine.features.get_progress(args.id)
        print(f"✦ Feature: {feature.title}")
        print(f"  ID: {feature.id}")
        print(f"  Status: {feature.status.value}")
        print(f"  Progress: {_progress_bar(progress['percent'])} {progress['done']}/{progress['total']} ({progress['percent']}%)")
        if feature.description:
            print(f"  Description: {feature.description}")
        if tasks:
            print()
            print("  Tasks:")
            for t in tasks:
                agent_str = f" [{t.assigned_agent}]" if t.assigned_agent else ""
                print(f"    {t.id}  [{t.status.value:<11}]  {t.title}{agent_str}")

    elif action == "update":
        if not args.id:
            print("✦ Feature ID required.")
            return
        updates = {}
        if args.status:
            updates["status"] = args.status
        if args.title:
            updates["title"] = args.title
        if args.priority:
            updates["priority"] = args.priority
        if not updates:
            print("✦ No updates specified.")
            return
        feature = engine.features.update(args.id, **updates)
        if feature:
            print(f"✦ Feature updated: {feature.id} — [{feature.status.value}] {feature.title}")
        else:
            print(f"✦ Feature not found: {args.id}")

    elif action == "board":
        board = engine.features.get_full_view(project.id)
        if not board:
            print("✦ No features yet.")
            return
        for item in board:
            f = item["feature"]
            p = item["progress"]
            tasks = item["tasks"]
            print(f"  ┌─ {f['title']}  [{f['status']}]  {_progress_bar(p['percent'])} {p['done']}/{p['total']}")
            for t in tasks:
                status_icon = "✓" if t["status"] == "done" else ">" if t["status"] == "in_progress" else "·" if t["status"] == "blocked" else " "
                print(f"  │  {status_icon} {t['id']}  {t['title']}")
            print(f"  └─")
            print()


def _progress_bar(percent: int, width: int = 10) -> str:
    filled = round(width * percent / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def cmd_sync(args):
    """Sync git state with Opero."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized.")
        return

    result = engine.sync()
    print(f"✦ Git sync complete")
    print(f"  Commits synced: {result.get('commits_synced', 0)}")
    print(f"  Branch: {result.get('branch', 'unknown')}")


def cmd_serve(args):
    """Start the MCP server with dashboard."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized. Run 'opero init' first.")
        return

    port = args.port or 7437
    print(f"✦ Starting Opero dashboard on http://localhost:{port}")
    print(f"  API docs: http://localhost:{port}/docs")
    print()

    # Open browser
    if not args.no_open:
        import webbrowser
        import threading
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    import uvicorn
    os.environ["OPERO_PROJECT_PATH"] = os.getcwd()
    uvicorn.run("opero.mcp.server:app", host="0.0.0.0", port=port, reload=False)


def cmd_memory(args):
    """Manage project memory with vector search."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized.")
        return

    project = engine.projects.get_by_path()
    if not project:
        print("✦ No project found.")
        return

    action = args.memory_action

    if action == "store" or action == "add":
        if not args.title:
            print("✦ Title required: opero memory store --title 'Decision title' --content 'Details...'")
            return
        entry = MemoryEntry(
            project_id=project.id,
            type=MemoryType(args.type) if args.type else MemoryType.CONTEXT,
            title=args.title,
            content=args.content or "",
            tags=args.tags.split(",") if args.tags else [],
            source=args.source or "user",
            source_ref=args.ref or "",
            importance=args.importance or 3,
        )
        stored = engine.memory.store(entry)
        print(f"✦ Memory stored: {stored.id}")
        print(f"  Type: {stored.type.value}")
        print(f"  Title: {stored.title}")
        if stored.tags:
            print(f"  Tags: {', '.join(stored.tags)}")

    elif action == "search":
        if not args.query:
            print("✦ Query required: opero memory search --query 'what architecture decisions'")
            return
        results = engine.memory.search(project.id, args.query, top_k=args.top_k or 10)
        if not results:
            print("✦ No matching memories found.")
            return
        print(f"✦ Search results ({len(results)}):")
        print()
        for entry, score in results:
            print(f"  {entry.id}  [{entry.type.value:<12}]  score={score:.3f}")
            print(f"    {entry.title}")
            if args.verbose and entry.content:
                # Truncate long content
                content = entry.content[:200] + "..." if len(entry.content) > 200 else entry.content
                print(f"    {content}")
            print()

    elif action == "list" or action is None:
        mem_type = MemoryType(args.type) if args.type else None
        memories = engine.memory.list_memories(
            project.id,
            memory_type=mem_type,
            source=args.source,
        )
        if not memories:
            print("✦ No memories found.")
            return
        print(f"✦ Memories ({len(memories)}):")
        print()
        for m in memories:
            tags_str = f" [{', '.join(m.tags)}]" if m.tags else ""
            print(f"  {m.id}  P{m.importance}  [{m.type.value:<12}]  {m.title}{tags_str}")
            if args.verbose and m.content:
                content = m.content[:150] + "..." if len(m.content) > 150 else m.content
                print(f"           {content}")

    elif action == "context":
        ctx = engine.memory.build_context(
            project_id=project.id,
            query=args.query,
            task_id=args.task_id,
            tool=args.tool or "cli",
        )
        print(json.dumps(ctx, indent=2))

    elif action == "get":
        if not args.id:
            print("✦ Memory ID required: opero memory get --id <memory_id>")
            return
        entry = engine.memory.get(args.id)
        if not entry:
            print(f"✦ Memory not found: {args.id}")
            return
        print(f"✦ Memory: {entry.id}")
        print(f"  Type: {entry.type.value}")
        print(f"  Title: {entry.title}")
        print(f"  Content: {entry.content}")
        print(f"  Source: {entry.source}")
        print(f"  Importance: {entry.importance}")
        if entry.tags:
            print(f"  Tags: {', '.join(entry.tags)}")
        if entry.source_ref:
            print(f"  Ref: {entry.source_ref}")
        print(f"  Created: {entry.created_at}")
        print(f"  Updated: {entry.updated_at}")

    elif action == "link":
        if not args.id or not args.link_type or not args.link_id:
            print("✦ Usage: opero memory link --id <mem_id> --link-type task --link-id <task_id>")
            return
        link = engine.memory.link(args.id, args.link_type, args.link_id, args.relationship or "related")
        print(f"✦ Linked memory {args.id} -> {args.link_type}:{args.link_id} ({link.relationship})")

    elif action == "reindex":
        count = engine.memory.reindex(project.id)
        print(f"✦ Reindexed {count} memory entries.")


def cmd_claude(args):
    """Claude Code integration."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized.")
        return

    from opero.integrations.claude_code import ClaudeCodeIntegration
    integration = ClaudeCodeIntegration(os.getcwd())

    action = args.claude_action

    if action == "sync" or action is None:
        path = integration.write_claude_md()
        print(f"✦ CLAUDE.md updated: {path}")
        project = engine.projects.get_by_path()
        if project:
            memories = engine.memory.list_memories(project.id)
            tasks = engine.tasks.list_tasks(project_id=project.id)
            print(f"  Memories: {len(memories)}")
            print(f"  Tasks: {len(tasks)}")
            print(f"  Claude Code will read this on next conversation start.")

    elif action == "hooks":
        path = integration.install_hooks()
        print(f"✦ Hooks installed: {path}")
        print("  PostToolUse: syncs CLAUDE.md after Bash/Edit/Write")
        print("  PreToolUse: ensures CLAUDE.md exists before Bash")
        print("  Stop: syncs git and refreshes CLAUDE.md")

    elif action == "mcp":
        path = integration.install_mcp()
        print(f"✦ MCP server configured: {path}")
        print("  Claude Code will connect to Opero via MCP tools.")
        print("  Available tools: opero_status, opero_tasks_*, opero_memory_*, opero_context")
        print()
        print("  Restart Claude Code for changes to take effect.")

    elif action == "setup":
        # Full setup: CLAUDE.md + hooks + MCP
        md_path = integration.write_claude_md()
        hooks_path = integration.install_hooks()
        mcp_path = integration.install_mcp()
        print(f"✦ Full Claude Code integration installed:")
        print(f"  CLAUDE.md: {md_path}")
        print(f"  Hooks: {hooks_path}")
        print(f"  MCP: {mcp_path}")
        print()
        print("  Restart Claude Code for MCP changes to take effect.")

    elif action == "show":
        content = integration.generate_claude_md()
        print(content)


def cmd_loop(args):
    """Run the autonomous development loop."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized. Run 'opero init' first.")
        return

    project = engine.projects.get_by_path()
    if not project:
        print("✦ No project found.")
        return

    action = args.loop_action

    if action == "status":
        from opero.orchestrator.loop import OrchestratorLoop
        status = OrchestratorLoop.get_status(os.getcwd(), project.id)
        if not status:
            print("✦ No orchestrator runs found.")
            return
        print(f"✦ Orchestrator Run: {status['id']}")
        print(f"  Status: {status['status']}")
        print(f"  Phase: {status['phase']}")
        print(f"  Iteration: {status['iteration']}")
        print(f"  Started: {status['started_at']}")
        if status['completed_at']:
            print(f"  Completed: {status['completed_at']}")
        return

    if action == "pause":
        from opero.orchestrator.loop import OrchestratorLoop
        status = OrchestratorLoop.get_status(os.getcwd(), project.id)
        if status and status['status'] == 'running':
            OrchestratorLoop.pause(os.getcwd(), status['id'])
            print(f"✦ Orchestrator paused: {status['id']}")
        else:
            print("✦ No running orchestrator to pause.")
        return

    if action == "stop":
        from opero.orchestrator.loop import OrchestratorLoop
        status = OrchestratorLoop.get_status(os.getcwd(), project.id)
        if status and status['status'] in ('running', 'paused'):
            OrchestratorLoop.stop(os.getcwd(), status['id'])
            print(f"✦ Orchestrator stopped: {status['id']}")
        else:
            print("✦ No active orchestrator to stop.")
        return

    # Default: run the loop
    spec_text = None
    if args.spec_file:
        from pathlib import Path
        spec_path = Path(args.spec_file)
        if not spec_path.exists():
            print(f"✦ Spec file not found: {args.spec_file}")
            return
        spec_text = spec_path.read_text()
    elif args.spec:
        spec_text = args.spec

    if not spec_text and action != "resume":
        # Check if there are existing TODO tasks to work on
        tasks = engine.tasks.list_tasks(project_id=project.id, status=TaskStatus.TODO)
        if not tasks:
            print("✦ No spec provided and no pending tasks.")
            print("  Usage: opero loop --spec-file spec.md")
            print("         opero loop --spec 'Build a todo app'")
            return
        print(f"✦ No spec — working on {len(tasks)} existing TODO tasks")

    from opero.orchestrator.loop import OrchestratorLoop
    loop = OrchestratorLoop(
        project_path=os.getcwd(),
        project_id=project.id,
        spec_text=spec_text,
        max_iterations=args.max_iterations or 3,
        parallel_agents=args.parallel or 1,
        skip_testing=args.skip_testing or False,
    )
    loop.run()


def cmd_go(args):
    """Launch interactive PM session."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized. Run 'opero init' first.")
        return
    project = engine.projects.get_by_path()
    if not project:
        print("✦ No project found.")
        return
    from opero.orchestrator.interactive import launch_interactive
    launch_interactive(
        os.getcwd(), project.id,
        parallel=args.parallel or 1,
        open_dashboard=not args.no_dashboard,
    )


def cmd_agents(args):
    """List registered agents."""
    engine = get_engine()
    if not engine.is_initialized():
        print("✦ Project not initialized.")
        return

    agents = engine.agents.list_agents()
    print(f"✦ Agents ({len(agents)}):")
    print()
    for a in agents:
        caps = ", ".join(a.capabilities)
        tools = ", ".join(a.tools)
        print(f"  {a.name}")
        print(f"    {a.description}")
        print(f"    Capabilities: {caps}")
        print(f"    Tools: {tools}")
        print()


def main():
    parser = argparse.ArgumentParser(
        prog="opero",
        description="Opero Core — The operating system for AI-driven development",
    )
    subparsers = parser.add_subparsers(dest="command")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize Opero in current directory")
    init_parser.add_argument("--name", "-n", help="Project name")
    init_parser.add_argument("--description", "-d", help="Project description")

    # start
    subparsers.add_parser("start", help="Start the Opero daemon")

    # status
    subparsers.add_parser("status", help="Show system status")

    # tasks
    tasks_parser = subparsers.add_parser("tasks", help="Manage tasks")
    tasks_parser.add_argument("task_action", nargs="?", choices=["list", "add", "update", "run", "next"])
    tasks_parser.add_argument("--id", help="Task ID")
    tasks_parser.add_argument("--title", "-t", help="Task title")
    tasks_parser.add_argument("--desc", help="Task description")
    tasks_parser.add_argument("--type", choices=["feature", "bug", "research", "agent_task", "setup"])
    tasks_parser.add_argument("--status", "-s", choices=["todo", "in_progress", "done", "blocked"])
    tasks_parser.add_argument("--priority", "-p", type=int, choices=[1, 2, 3, 4, 5])
    tasks_parser.add_argument("--agent", help="Agent name")
    tasks_parser.add_argument("--verbose", "-v", action="store_true")

    # features
    feat_parser = subparsers.add_parser("features", help="Manage features/epics")
    feat_parser.add_argument("feature_action", nargs="?",
                             choices=["list", "add", "view", "update", "board"])
    feat_parser.add_argument("--id", help="Feature ID")
    feat_parser.add_argument("--title", "-t", help="Feature title")
    feat_parser.add_argument("--desc", help="Feature description")
    feat_parser.add_argument("--status", "-s", choices=["planning", "active", "done", "paused"])
    feat_parser.add_argument("--priority", "-p", type=int, choices=[1, 2, 3, 4, 5])
    feat_parser.add_argument("--verbose", "-v", action="store_true")

    # sync
    subparsers.add_parser("sync", help="Sync git state with Opero")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start dashboard + API server")
    serve_parser.add_argument("--port", type=int, default=7437)
    serve_parser.add_argument("--no-open", action="store_true", help="Don't open browser")

    # memory
    mem_parser = subparsers.add_parser("memory", help="Manage project memory (vector-backed)")
    mem_parser.add_argument("memory_action", nargs="?",
                            choices=["list", "store", "add", "search", "context", "get", "link", "reindex"])
    mem_parser.add_argument("--id", help="Memory entry ID")
    mem_parser.add_argument("--title", "-t", help="Memory title")
    mem_parser.add_argument("--content", "-c", help="Memory content")
    mem_parser.add_argument("--type", choices=["decision", "architecture", "learning", "context",
                                                "preference", "convention", "issue", "plan"])
    mem_parser.add_argument("--tags", help="Comma-separated tags")
    mem_parser.add_argument("--source", help="Source: user, claude, cursor, system, git")
    mem_parser.add_argument("--ref", help="Source reference (task id, commit sha, file path)")
    mem_parser.add_argument("--importance", "-i", type=int, choices=[1, 2, 3, 4, 5])
    mem_parser.add_argument("--query", "-q", help="Search query")
    mem_parser.add_argument("--top-k", type=int, default=10, help="Number of search results")
    mem_parser.add_argument("--task-id", help="Task ID for context building")
    mem_parser.add_argument("--tool", help="Tool name for context snapshots")
    mem_parser.add_argument("--link-type", choices=["task", "commit", "memory", "file"])
    mem_parser.add_argument("--link-id", help="Linked entity ID")
    mem_parser.add_argument("--relationship", default="related", help="Link relationship type")
    mem_parser.add_argument("--verbose", "-v", action="store_true")

    # claude
    claude_parser = subparsers.add_parser("claude", help="Claude Code integration")
    claude_parser.add_argument("claude_action", nargs="?",
                               choices=["sync", "hooks", "mcp", "setup", "show"],
                               help="sync=update CLAUDE.md, hooks=install hooks, mcp=configure MCP, setup=all")

    # go (interactive PM)
    go_parser = subparsers.add_parser("go", help="Launch interactive PM session (conversational)")
    go_parser.add_argument("--parallel", "-p", type=int, default=1, help="Parallel agents (default 1)")
    go_parser.add_argument("--no-dashboard", action="store_true", help="Don't auto-start dashboard")

    # agents
    subparsers.add_parser("agents", help="List registered agents")

    # loop (orchestrator)
    loop_parser = subparsers.add_parser("loop", help="Run autonomous development loop")
    loop_parser.add_argument("loop_action", nargs="?", choices=["status", "pause", "stop"],
                             help="status=show state, pause=pause loop, stop=stop loop")
    loop_parser.add_argument("--spec-file", "-f", help="Path to spec/PRD file")
    loop_parser.add_argument("--spec", "-s", help="Spec text (inline)")
    loop_parser.add_argument("--max-iterations", "-n", type=int, default=3, help="Max loop iterations (default 3)")
    loop_parser.add_argument("--parallel", "-p", type=int, default=1, help="Parallel agents (default 1)")
    loop_parser.add_argument("--skip-testing", action="store_true", help="Skip Playwright testing phase")

    args = parser.parse_args()

    if args.command is None:
        # Running bare 'opero' — launch interactive PM session
        engine = get_engine()
        project = None
        if engine.is_initialized():
            project = engine.projects.get_by_path()

        if not project:
            # Not fully initialized — bootstrap first
            args.name = None
            args.description = None
            cmd_init(args)
            project = engine.projects.get_by_path()

        if project:
            # Check if claude CLI is available
            import subprocess as _sp
            try:
                _sp.run(["claude", "--version"], capture_output=True, check=True, timeout=5)
            except (FileNotFoundError, _sp.CalledProcessError, _sp.TimeoutExpired):
                # No claude CLI — fall back to status display
                print("✦ Claude Code CLI not found. Showing status instead.")
                print("  Install Claude Code for the interactive experience.")
                print()
                cmd_status(args)
                return

            from opero.orchestrator.interactive import launch_interactive
            launch_interactive(os.getcwd(), project.id)
        return

    commands = {
        "init": cmd_init,
        "start": cmd_start,
        "status": cmd_status,
        "tasks": cmd_tasks,
        "features": cmd_features,
        "memory": cmd_memory,
        "claude": cmd_claude,
        "sync": cmd_sync,
        "serve": cmd_serve,
        "agents": cmd_agents,
        "go": cmd_go,
        "loop": cmd_loop,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
