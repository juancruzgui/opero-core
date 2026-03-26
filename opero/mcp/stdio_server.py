"""MCP stdio server for Claude Code integration using the official MCP SDK.

Usage in .mcp.json:
{
  "mcpServers": {
    "opero": {
      "command": "python",
      "args": ["-m", "opero.mcp.stdio_server"],
      "env": { "OPERO_PROJECT_PATH": "/path/to/project" }
    }
  }
}
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from opero.core.engine import OperoEngine
from opero.core.memory import MemoryEntry, MemoryType
from opero.core.models import Task, TaskType, TaskStatus, Feature, FeatureStatus


def get_engine() -> OperoEngine:
    project_path = os.environ.get("OPERO_PROJECT_PATH", os.getcwd())
    return OperoEngine(project_path)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    ("opero_status", "Get full Opero project status: tasks, agents, git state", {}),
    ("opero_tasks_list", "List all tasks. Filter by status or type.", {
        "status": {"type": "string", "enum": ["todo", "in_progress", "done", "blocked"]},
        "type": {"type": "string", "enum": ["feature", "bug", "research", "agent_task", "setup"]},
    }),
    ("opero_tasks_next", "Get the next highest-priority task ready to work on", {}),
    ("opero_task_create", "Create a new task", {
        "title": {"type": "string"}, "description": {"type": "string"},
        "type": {"type": "string", "enum": ["feature", "bug", "research", "agent_task", "setup"], "default": "feature"},
        "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "success_criteria": {"type": "string"},
    }),
    ("opero_task_update", "Update a task's status, title, or priority", {
        "task_id": {"type": "string"}, "status": {"type": "string", "enum": ["todo", "in_progress", "done", "blocked"]},
        "title": {"type": "string"}, "priority": {"type": "integer"}, "outputs": {"type": "string"},
    }),
    ("opero_memory_store", "Store a memory entry indexed for vector search", {
        "type": {"type": "string", "enum": ["decision", "architecture", "learning", "context", "preference", "convention", "issue", "plan"]},
        "title": {"type": "string"}, "content": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
    }),
    ("opero_memory_search", "Semantic search over project memory", {
        "query": {"type": "string"}, "top_k": {"type": "integer", "default": 5},
    }),
    ("opero_memory_list", "List memory entries by type or source", {
        "type": {"type": "string", "enum": ["decision", "architecture", "learning", "context", "preference", "convention", "issue", "plan"]},
        "source": {"type": "string"},
    }),
    ("opero_context", "Get full project context: decisions, conventions, architecture, relevant memories", {
        "query": {"type": "string"}, "task_id": {"type": "string"},
    }),
    ("opero_git_sync", "Sync git commits into Opero tracking", {}),
    ("opero_feature_create", "Create a feature/epic to group related tasks", {
        "title": {"type": "string"}, "description": {"type": "string"},
        "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
    }),
    ("opero_feature_list", "List all features with progress", {
        "status": {"type": "string", "enum": ["planning", "active", "done", "paused"]},
    }),
    ("opero_feature_task", "Add a task under a feature", {
        "feature_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
        "type": {"type": "string", "enum": ["feature", "bug", "research", "agent_task", "setup"], "default": "feature"},
        "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "success_criteria": {"type": "string"},
    }),
    ("opero_feature_get", "Get a feature with tasks and progress", {"feature_id": {"type": "string"}}),
    ("opero_feature_update", "Update feature status", {
        "feature_id": {"type": "string"}, "status": {"type": "string", "enum": ["planning", "active", "done", "paused"]},
    }),
    ("opero_start_work", "CALL THIS FIRST when user asks to do something. Searches existing tasks, creates one under a feature, stores intent as memory.", {
        "user_request": {"type": "string"}, "intent": {"type": "string"},
        "feature_id": {"type": "string"}, "feature_title": {"type": "string"},
        "task_title": {"type": "string"}, "task_description": {"type": "string"},
        "thought_process": {"type": "string"},
    }),
    ("opero_complete_work", "CALL THIS when done. Stores outcome, learnings, decisions. Marks task done.", {
        "task_id": {"type": "string"}, "outcome": {"type": "string"},
        "learnings": {"type": "string"}, "decisions": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
    }),
    ("opero_memory_link", "Link a memory to a task, commit, file, or another memory", {
        "memory_id": {"type": "string"}, "linked_type": {"type": "string", "enum": ["task", "commit", "memory", "file"]},
        "linked_id": {"type": "string"}, "relationship": {"type": "string", "default": "related"},
    }),
    ("opero_verify_task", "Tester tool: mark a task as verified (passed) or failed with test results", {
        "task_id": {"type": "string"},
        "verified": {"type": "boolean"},
        "test_results": {"type": "string"},
        "failure_reason": {"type": "string"},
    }),
    ("opero_orchestrator_status", "Get current orchestrator loop status: phase, iteration, active agents, progress", {}),
    ("opero_agent_status", "Agent reports its current status/heartbeat so dashboard can show real-time state", {
        "agent_name": {"type": "string"},
        "task_id": {"type": "string"},
        "status_message": {"type": "string"},
    }),
]

REQUIRED_FIELDS = {
    "opero_task_create": ["title"],
    "opero_task_update": ["task_id"],
    "opero_memory_store": ["type", "title", "content"],
    "opero_memory_search": ["query"],
    "opero_feature_create": ["title"],
    "opero_feature_task": ["feature_id", "title"],
    "opero_feature_get": ["feature_id"],
    "opero_feature_update": ["feature_id"],
    "opero_start_work": ["user_request", "intent", "task_title"],
    "opero_complete_work": ["task_id", "outcome"],
    "opero_memory_link": ["memory_id", "linked_type", "linked_id"],
    "opero_verify_task": ["task_id", "verified"],
    "opero_agent_status": ["agent_name"],
}


# ---------------------------------------------------------------------------
# Start/Complete work handlers
# ---------------------------------------------------------------------------

def _handle_start_work(engine, pid, args):
    user_request = args["user_request"]
    intent = args["intent"]
    task_title = args["task_title"]
    feature_id = args.get("feature_id")
    feature_title = args.get("feature_title")
    thought_process = args.get("thought_process", "")

    result = {"existing_tasks": [], "relevant_memories": [], "task": None, "feature": None, "memories_stored": []}

    for t in engine.tasks.list_tasks(project_id=pid):
        request_words = set(user_request.lower().split()) - {"a", "the", "to", "for", "and", "or", "in", "on", "is", "it"}
        if len(request_words & set(t.title.lower().split())) >= 2:
            result["existing_tasks"].append({"id": t.id, "title": t.title, "status": t.status.value})

    for m, s in engine.memory.search(pid, user_request, top_k=5):
        result["relevant_memories"].append({"title": m.title, "content": m.content[:200], "type": m.type.value, "score": round(s, 3)})

    if feature_id:
        # Use existing feature
        f = engine.features.get(feature_id)
        if f:
            result["feature"] = {"id": f.id, "title": f.title}
            if f.status == FeatureStatus.PLANNING:
                engine.features.update(feature_id, status="active")
    else:
        # Create a feature — use provided title or derive from task title
        ft = feature_title or task_title
        # Check if there's already an active feature we should add to
        active_features = engine.features.list_features(pid, status=FeatureStatus.ACTIVE)
        if active_features:
            # Use the most recent active feature
            f = active_features[-1]
            feature_id = f.id
            result["feature"] = {"id": f.id, "title": f.title, "reused": True}
        else:
            f = engine.features.create(Feature(project_id=pid, title=ft, status=FeatureStatus.ACTIVE, priority=2))
            feature_id = f.id
            result["feature"] = {"id": f.id, "title": f.title, "created": True}

    task = engine.tasks.create(Task(project_id=pid, feature_id=feature_id, title=task_title, description=args.get("task_description", ""), type=TaskType.FEATURE, status=TaskStatus.IN_PROGRESS, priority=2))
    result["task"] = {"id": task.id, "title": task.title, "status": "in_progress"}

    mem = engine.memory.store(MemoryEntry(project_id=pid, type=MemoryType.CONTEXT, title=f"Intent: {task_title}", content=f"User: {user_request}\nIntent: {intent}\nApproach: {thought_process}", tags=["intent"], source="claude", source_ref=task.id, importance=2))
    engine.memory.link(mem.id, "task", task.id, "intent")
    result["memories_stored"].append({"type": "intent"})

    if thought_process:
        pm = engine.memory.store(MemoryEntry(project_id=pid, type=MemoryType.PLAN, title=f"Approach: {task_title}", content=thought_process, tags=["plan"], source="claude", source_ref=task.id, importance=3))
        engine.memory.link(pm.id, "task", task.id, "plan")
        result["memories_stored"].append({"type": "plan"})

    return result


def _handle_complete_work(engine, pid, args):
    task_id = args["task_id"]
    task = engine.tasks.get(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    result = {"task_id": task_id, "memories_stored": []}
    outcome = args["outcome"]
    files = args.get("files_changed", [])
    content = f"{outcome}\n\nFiles: {', '.join(files)}" if files else outcome

    om = engine.memory.store(MemoryEntry(project_id=pid, type=MemoryType.CONTEXT, title=f"Outcome: {task.title}", content=content, tags=["outcome"], source="claude", source_ref=task_id, importance=2))
    engine.memory.link(om.id, "task", task_id, "outcome")
    result["memories_stored"].append({"type": "outcome"})

    if args.get("learnings"):
        lm = engine.memory.store(MemoryEntry(project_id=pid, type=MemoryType.LEARNING, title=f"Learning: {task.title}", content=args["learnings"], tags=["learning"], source="claude", source_ref=task_id, importance=2))
        engine.memory.link(lm.id, "task", task_id, "learning")
        result["memories_stored"].append({"type": "learning"})

    if args.get("decisions"):
        dm = engine.memory.store(MemoryEntry(project_id=pid, type=MemoryType.DECISION, title=f"Decision: {task.title}", content=args["decisions"], tags=["decision"], source="claude", source_ref=task_id, importance=1))
        engine.memory.link(dm.id, "task", task_id, "decision")
        result["memories_stored"].append({"type": "decision"})

    engine.tasks.update(task_id, status="done", outputs=outcome)
    result["task_status"] = "done"

    if task.feature_id:
        done = engine.features.check_completion(task.feature_id)
        f = engine.features.get(task.feature_id)
        if f:
            result["feature"] = {"id": f.id, "title": f.title, "status": f.status.value, "progress": engine.features.get_progress(f.id), "completed": done}

    return result


# ---------------------------------------------------------------------------
# Verify / Orchestrator / Agent Status handlers
# ---------------------------------------------------------------------------

def _handle_verify_task(engine, pid, args):
    task_id = args["task_id"]
    verified = args["verified"]
    test_results = args.get("test_results", "")
    failure_reason = args.get("failure_reason", "")

    task = engine.tasks.get(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    from opero.db.schema import get_connection
    conn = get_connection(engine.project_path)

    if verified:
        conn.execute("UPDATE tasks SET verification_status = 'passed' WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        # Store test results as memory
        engine.memory.store(MemoryEntry(
            project_id=pid, type=MemoryType.LEARNING,
            title=f"Test passed: {task.title}",
            content=test_results, tags=["test", "passed"],
            source="claude", source_ref=task_id, importance=3,
        ))
        return {"task_id": task_id, "verification_status": "passed", "message": "Task verified successfully"}
    else:
        conn.execute("UPDATE tasks SET verification_status = 'failed', status = 'todo' WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        # Create a fix subtask under the same feature
        fix_task = engine.tasks.create(Task(
            project_id=pid,
            feature_id=task.feature_id,
            title=f"Fix: {task.title} — {failure_reason[:80]}",
            description=f"Verification failed for task {task_id}.\n\nFailure: {failure_reason}\n\nTest results:\n{test_results}",
            type=TaskType.BUG,
            priority=2,
            parent_task_id=task_id,
            success_criteria=task.success_criteria,
        ))
        return {
            "task_id": task_id, "verification_status": "failed",
            "fix_task_id": fix_task.id, "failure_reason": failure_reason,
        }


def _handle_orchestrator_status(engine, pid):
    from opero.db.schema import get_connection
    conn = get_connection(engine.project_path)
    run = conn.execute(
        "SELECT * FROM orchestrator_runs WHERE project_id = ? ORDER BY started_at DESC LIMIT 1",
        (pid,)
    ).fetchone()
    if not run:
        conn.close()
        return {"status": "no_runs", "message": "No orchestrator runs found"}

    # Task progress
    tasks = engine.tasks.list_tasks(project_id=pid)
    total = len(tasks)
    by_status = {}
    verified = 0
    for t in tasks:
        by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
    # Count verified via raw query
    verified = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE project_id = ? AND verification_status = 'passed'", (pid,)
    ).fetchone()[0]

    # Active executions
    active = conn.execute(
        "SELECT agent_name, task_id FROM task_executions WHERE status = 'running'"
    ).fetchall()
    conn.close()

    return {
        "run": dict(run),
        "progress": {
            "total": total,
            "todo": by_status.get("todo", 0),
            "in_progress": by_status.get("in_progress", 0),
            "done": by_status.get("done", 0),
            "blocked": by_status.get("blocked", 0),
            "verified": verified,
        },
        "active_agents": [{"agent": r["agent_name"], "task_id": r["task_id"]} for r in active],
    }


def _handle_agent_status(engine, pid, args):
    agent_name = args["agent_name"]
    task_id = args.get("task_id")
    status_message = args.get("status_message", "")
    from opero.db.schema import get_connection
    conn = get_connection(engine.project_path)
    # Use agent_name as session id for agent-launched instances
    session_id = f"agent-{agent_name}"
    conn.execute(
        """INSERT INTO claude_sessions (id, project_id, status, current_task_id, last_heartbeat, started_at)
           VALUES (?, ?, 'active', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET last_heartbeat = CURRENT_TIMESTAMP, current_task_id = ?, status = 'active'""",
        (session_id, pid, task_id, task_id),
    )
    conn.execute(
        "INSERT INTO claude_activity (project_id, session_id, tool_name, action, task_id, detail) VALUES (?, ?, ?, ?, ?, ?)",
        (pid, session_id, "agent", "heartbeat", task_id, f"[{agent_name}] {status_message}"),
    )
    conn.commit()
    conn.close()
    return {"agent_name": agent_name, "session_id": session_id, "status": "active"}


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def handle_tool(name: str, arguments: dict) -> Any:
    engine = get_engine()
    project = engine.projects.get_by_path()
    if not project and name != "opero_status":
        return {"error": "Project not initialized"}
    pid = project.id if project else ""

    if name == "opero_status": return engine.status()
    elif name == "opero_tasks_list":
        s = TaskStatus(arguments["status"]) if arguments.get("status") else None
        tt = TaskType(arguments["type"]) if arguments.get("type") else None
        return {"tasks": [{"id": t.id, "title": t.title, "status": t.status.value, "type": t.type.value, "priority": t.priority} for t in engine.tasks.list_tasks(project_id=pid, status=s, task_type=tt)]}
    elif name == "opero_tasks_next":
        t = engine.tasks.get_next_task(pid)
        return {"id": t.id, "title": t.title, "priority": t.priority, "description": t.description} if t else {"message": "No tasks ready"}
    elif name == "opero_task_create":
        t = engine.tasks.create(Task(project_id=pid, title=arguments["title"], description=arguments.get("description", ""), type=TaskType(arguments.get("type", "feature")), priority=arguments.get("priority", 3)))
        return {"id": t.id, "title": t.title}
    elif name == "opero_task_update":
        tid = arguments.pop("task_id"); t = engine.tasks.update(tid, **{k: v for k, v in arguments.items() if v is not None})
        return {"id": t.id, "status": t.status.value} if t else {"error": "Not found"}
    elif name == "opero_memory_store":
        m = engine.memory.store(MemoryEntry(project_id=pid, type=MemoryType(arguments["type"]), title=arguments["title"], content=arguments["content"], tags=arguments.get("tags", []), source="claude", importance=arguments.get("importance", 3)))
        return {"id": m.id, "title": m.title}
    elif name == "opero_memory_search":
        return {"results": [{"title": m.title, "content": m.content, "type": m.type.value, "score": round(s, 4)} for m, s in engine.memory.search(pid, arguments["query"], top_k=arguments.get("top_k", 5))]}
    elif name == "opero_memory_list":
        mt = MemoryType(arguments["type"]) if arguments.get("type") else None
        return {"memories": [{"id": m.id, "title": m.title, "type": m.type.value, "content": m.content[:200]} for m in engine.memory.list_memories(pid, memory_type=mt, source=arguments.get("source"))]}
    elif name == "opero_context":
        return engine.memory.build_context(project_id=pid, query=arguments.get("query"), task_id=arguments.get("task_id"), tool="claude")
    elif name == "opero_git_sync": return engine.sync()
    elif name == "opero_feature_create":
        f = engine.features.create(Feature(project_id=pid, title=arguments["title"], description=arguments.get("description", ""), priority=arguments.get("priority", 3)))
        return {"id": f.id, "title": f.title}
    elif name == "opero_feature_list":
        st = FeatureStatus(arguments["status"]) if arguments.get("status") else None
        return {"features": [{"id": f.id, "title": f.title, "status": f.status.value, "progress": engine.features.get_progress(f.id)} for f in engine.features.list_features(pid, status=st)]}
    elif name == "opero_feature_task":
        t = engine.features.add_task(arguments["feature_id"], Task(project_id=pid, feature_id=arguments["feature_id"], title=arguments["title"], description=arguments.get("description", ""), type=TaskType(arguments.get("type", "feature")), priority=arguments.get("priority", 3)))
        return {"id": t.id, "title": t.title}
    elif name == "opero_feature_get":
        f = engine.features.get(arguments["feature_id"])
        if not f: return {"error": "Not found"}
        return {"feature": {"id": f.id, "title": f.title, "status": f.status.value}, "tasks": [{"id": t.id, "title": t.title, "status": t.status.value} for t in engine.features.get_tasks(f.id)], "progress": engine.features.get_progress(f.id)}
    elif name == "opero_feature_update":
        fid = arguments["feature_id"]; f = engine.features.update(fid, **{k: v for k, v in arguments.items() if k != "feature_id" and v is not None})
        return {"id": f.id, "status": f.status.value} if f else {"error": "Not found"}
    elif name == "opero_start_work": return _handle_start_work(engine, pid, arguments)
    elif name == "opero_complete_work": return _handle_complete_work(engine, pid, arguments)
    elif name == "opero_memory_link":
        l = engine.memory.link(arguments["memory_id"], arguments["linked_type"], arguments["linked_id"], arguments.get("relationship", "related"))
        return {"id": l.id}
    elif name == "opero_verify_task":
        return _handle_verify_task(engine, pid, arguments)
    elif name == "opero_orchestrator_status":
        return _handle_orchestrator_status(engine, pid)
    elif name == "opero_agent_status":
        return _handle_agent_status(engine, pid, arguments)
    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# MCP Server using official SDK
# ---------------------------------------------------------------------------

app = Server("opero")


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools = []
    for name, desc, props in TOOL_DEFS:
        required = REQUIRED_FIELDS.get(name, [])
        tools.append(Tool(
            name=name,
            description=desc,
            inputSchema={"type": "object", "properties": props, "required": required},
        ))
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = handle_tool(name, arguments or {})
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    main()
