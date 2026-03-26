"""MCP stdio server for Claude Code integration.

Claude Code communicates with MCP servers over stdin/stdout using
JSON-RPC. This module wraps Opero's tools as MCP tools that Claude
Code can call directly — no HTTP, no API keys.

Usage in .claude/settings.json:
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
import sys
from typing import Any

from opero.core.engine import OperoEngine
from opero.core.memory import MemoryEntry, MemoryType
from opero.core.models import Task, TaskType, TaskStatus, Feature, FeatureStatus


def get_engine() -> OperoEngine:
    project_path = os.environ.get("OPERO_PROJECT_PATH", os.getcwd())
    return OperoEngine(project_path)


# ---------------------------------------------------------------------------
# Tool definitions (MCP format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "opero_status",
        "description": "Get full Opero project status: tasks, agents, git state",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "opero_tasks_list",
        "description": "List all tasks. Optionally filter by status (todo, in_progress, done, blocked) or type (feature, bug, research, agent_task, setup)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["todo", "in_progress", "done", "blocked"]},
                "type": {"type": "string", "enum": ["feature", "bug", "research", "agent_task", "setup"]},
            },
        },
    },
    {
        "name": "opero_tasks_next",
        "description": "Get the next highest-priority task that is ready to work on (all dependencies met)",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "opero_task_create",
        "description": "Create a new task. All work must have an associated task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "description": {"type": "string", "description": "Detailed task description"},
                "type": {"type": "string", "enum": ["feature", "bug", "research", "agent_task", "setup"], "default": "feature"},
                "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                "success_criteria": {"type": "string", "description": "What defines done"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "opero_task_update",
        "description": "Update a task's status, title, priority, or assigned agent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string", "enum": ["todo", "in_progress", "done", "blocked"]},
                "title": {"type": "string"},
                "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                "outputs": {"type": "string", "description": "Task outputs/results"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "opero_memory_store",
        "description": "Store a memory entry (decision, architecture note, learning, convention, preference, issue, plan). Memories are indexed for vector search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["decision", "architecture", "learning", "context", "preference", "convention", "issue", "plan"]},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "importance": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3, "description": "1=critical, 5=low"},
            },
            "required": ["type", "title", "content"],
        },
    },
    {
        "name": "opero_memory_search",
        "description": "Semantic search over project memory using TF-IDF vector similarity. Use this before making decisions to check for prior context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "opero_memory_list",
        "description": "List memory entries, optionally filtered by type or source",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["decision", "architecture", "learning", "context", "preference", "convention", "issue", "plan"]},
                "source": {"type": "string"},
            },
        },
    },
    {
        "name": "opero_context",
        "description": "Get full project context package: decisions, conventions, architecture, relevant memories for a query, task-linked memories. This is the main entry point for understanding project state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you're about to work on"},
                "task_id": {"type": "string", "description": "Current task ID if known"},
            },
        },
    },
    {
        "name": "opero_git_sync",
        "description": "Sync git commits into Opero's tracking system. Links commits to tasks via [task_id] prefix.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "opero_feature_create",
        "description": "Create a feature/epic to group related tasks. E.g. 'Authentication System', 'Task Management UI'. All tasks should belong to a feature.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Feature name, e.g. 'Auth System'"},
                "description": {"type": "string", "description": "What this feature delivers"},
                "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
            },
            "required": ["title"],
        },
    },
    {
        "name": "opero_feature_list",
        "description": "List all features/epics with their progress",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["planning", "active", "done", "paused"]},
            },
        },
    },
    {
        "name": "opero_feature_task",
        "description": "Add a task under a feature. Use this instead of opero_task_create when the task belongs to a feature.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "type": {"type": "string", "enum": ["feature", "bug", "research", "agent_task", "setup"], "default": "feature"},
                "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                "success_criteria": {"type": "string"},
            },
            "required": ["feature_id", "title"],
        },
    },
    {
        "name": "opero_feature_get",
        "description": "Get a feature with all its tasks and progress percentage",
        "inputSchema": {
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
            },
            "required": ["feature_id"],
        },
    },
    {
        "name": "opero_feature_update",
        "description": "Update a feature status (planning, active, done, paused)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "feature_id": {"type": "string"},
                "status": {"type": "string", "enum": ["planning", "active", "done", "paused"]},
            },
            "required": ["feature_id"],
        },
    },
    {
        "name": "opero_start_work",
        "description": "CALL THIS FIRST when the user asks you to do something. It searches for existing related tasks, creates one if needed under a feature, stores the user's intent as memory, and sets the task to in_progress. Returns the task to work on plus any relevant memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_request": {"type": "string", "description": "What the user asked for, in their words"},
                "intent": {"type": "string", "description": "Your understanding of what needs to be done and why"},
                "feature_id": {"type": "string", "description": "Feature this belongs to (if known)"},
                "feature_title": {"type": "string", "description": "Create a new feature with this title if feature_id not provided"},
                "task_title": {"type": "string", "description": "Short task title"},
                "task_description": {"type": "string", "description": "Detailed description of what to build"},
                "thought_process": {"type": "string", "description": "Your approach: what you plan to do, key decisions, trade-offs"},
            },
            "required": ["user_request", "intent", "task_title"],
        },
    },
    {
        "name": "opero_complete_work",
        "description": "CALL THIS when you finish a task. Stores what was built, what was learned, and any decisions made. Marks the task done and checks if the feature is complete.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID being completed"},
                "outcome": {"type": "string", "description": "What was built/changed — files, functions, architecture"},
                "learnings": {"type": "string", "description": "Gotchas, insights, things that surprised you or would help next time"},
                "decisions": {"type": "string", "description": "Architectural or design decisions made and why"},
                "files_changed": {"type": "array", "items": {"type": "string"}, "description": "List of files that were modified"},
            },
            "required": ["task_id", "outcome"],
        },
    },
    {
        "name": "opero_memory_link",
        "description": "Link a memory entry to a task, commit, file, or another memory",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "linked_type": {"type": "string", "enum": ["task", "commit", "memory", "file"]},
                "linked_id": {"type": "string"},
                "relationship": {"type": "string", "default": "related"},
            },
            "required": ["memory_id", "linked_type", "linked_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _handle_start_work(engine: OperoEngine, pid: str, args: dict) -> dict:
    """Search for existing tasks, create if needed, store intent memory, start work.

    This is the main entry point when the user asks Claude to do something.
    """
    from opero.core.memory import MemoryEntry, MemoryType

    user_request = args["user_request"]
    intent = args["intent"]
    task_title = args["task_title"]
    task_description = args.get("task_description", "")
    thought_process = args.get("thought_process", "")
    feature_id = args.get("feature_id")
    feature_title = args.get("feature_title")

    result = {
        "existing_tasks": [],
        "relevant_memories": [],
        "task": None,
        "feature": None,
        "memories_stored": [],
    }

    # 1. Search for similar existing tasks
    all_tasks = engine.tasks.list_tasks(project_id=pid)
    for t in all_tasks:
        title_lower = t.title.lower()
        request_lower = user_request.lower()
        # Simple keyword overlap check
        request_words = set(request_lower.split())
        title_words = set(title_lower.split())
        overlap = request_words & title_words - {"a", "the", "to", "for", "and", "or", "in", "on", "is", "it"}
        if len(overlap) >= 2 or title_lower in request_lower or request_lower in title_lower:
            result["existing_tasks"].append({
                "id": t.id, "title": t.title, "status": t.status.value,
                "feature_id": t.feature_id, "description": t.description,
            })

    # 2. Search memory for prior related work
    memory_results = engine.memory.search(pid, user_request, top_k=5)
    result["relevant_memories"] = [
        {"title": m.title, "content": m.content[:200], "type": m.type.value, "score": round(s, 3)}
        for m, s in memory_results
    ]

    # 3. Create or get feature
    if not feature_id and feature_title:
        feature = Feature(project_id=pid, title=feature_title, status=FeatureStatus.ACTIVE, priority=2)
        feature = engine.features.create(feature)
        feature_id = feature.id
        result["feature"] = {"id": feature.id, "title": feature.title, "status": "active", "created": True}
    elif feature_id:
        feature = engine.features.get(feature_id)
        if feature:
            result["feature"] = {"id": feature.id, "title": feature.title, "status": feature.status.value}
            # Auto-activate if planning
            if feature.status == FeatureStatus.PLANNING:
                engine.features.update(feature_id, status="active")

    # 4. Create task
    task = Task(
        project_id=pid,
        feature_id=feature_id,
        title=task_title,
        description=task_description,
        type=TaskType.FEATURE,
        status=TaskStatus.IN_PROGRESS,
        priority=2,
    )
    task = engine.tasks.create(task)
    result["task"] = {"id": task.id, "title": task.title, "status": "in_progress", "feature_id": feature_id}

    # 5. Store intent memory
    intent_mem = engine.memory.store(MemoryEntry(
        project_id=pid,
        type=MemoryType.CONTEXT,
        title=f"Intent: {task_title}",
        content=f"User request: {user_request}\n\nIntent: {intent}\n\nThought process: {thought_process}",
        tags=["intent", "user-request"],
        source="claude",
        source_ref=task.id,
        importance=2,
    ))
    engine.memory.link(intent_mem.id, "task", task.id, "intent")
    result["memories_stored"].append({"id": intent_mem.id, "type": "intent"})

    # 6. Store thought process if provided
    if thought_process:
        thought_mem = engine.memory.store(MemoryEntry(
            project_id=pid,
            type=MemoryType.PLAN,
            title=f"Approach: {task_title}",
            content=thought_process,
            tags=["thought-process", "plan"],
            source="claude",
            source_ref=task.id,
            importance=3,
        ))
        engine.memory.link(thought_mem.id, "task", task.id, "plan")
        result["memories_stored"].append({"id": thought_mem.id, "type": "plan"})

    return result


def _handle_complete_work(engine: OperoEngine, pid: str, args: dict) -> dict:
    """Complete a task: store outcome, learnings, decisions as memories."""
    from opero.core.memory import MemoryEntry, MemoryType

    task_id = args["task_id"]
    outcome = args["outcome"]
    learnings = args.get("learnings", "")
    decisions = args.get("decisions", "")
    files_changed = args.get("files_changed", [])

    task = engine.tasks.get(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    result = {"task_id": task_id, "memories_stored": []}

    # 1. Store outcome memory
    files_str = "\n".join(f"- {f}" for f in files_changed) if files_changed else ""
    outcome_content = f"{outcome}\n\nFiles changed:\n{files_str}" if files_str else outcome
    outcome_mem = engine.memory.store(MemoryEntry(
        project_id=pid,
        type=MemoryType.CONTEXT,
        title=f"Outcome: {task.title}",
        content=outcome_content,
        tags=["outcome", "completed"],
        source="claude",
        source_ref=task_id,
        importance=2,
    ))
    engine.memory.link(outcome_mem.id, "task", task_id, "outcome")
    result["memories_stored"].append({"id": outcome_mem.id, "type": "outcome"})

    # 2. Store learnings if provided
    if learnings:
        learning_mem = engine.memory.store(MemoryEntry(
            project_id=pid,
            type=MemoryType.LEARNING,
            title=f"Learning: {task.title}",
            content=learnings,
            tags=["learning", "retrospective"],
            source="claude",
            source_ref=task_id,
            importance=2,
        ))
        engine.memory.link(learning_mem.id, "task", task_id, "learning")
        result["memories_stored"].append({"id": learning_mem.id, "type": "learning"})

    # 3. Store decisions if provided
    if decisions:
        decision_mem = engine.memory.store(MemoryEntry(
            project_id=pid,
            type=MemoryType.DECISION,
            title=f"Decision: {task.title}",
            content=decisions,
            tags=["decision", "architecture"],
            source="claude",
            source_ref=task_id,
            importance=1,
        ))
        engine.memory.link(decision_mem.id, "task", task_id, "decision")
        result["memories_stored"].append({"id": decision_mem.id, "type": "decision"})

    # 4. Mark task done
    engine.tasks.update(task_id, status="done", outputs=outcome)
    result["task_status"] = "done"

    # 5. Check if feature is complete
    if task.feature_id:
        is_complete = engine.features.check_completion(task.feature_id)
        feature = engine.features.get(task.feature_id)
        if feature:
            progress = engine.features.get_progress(task.feature_id)
            result["feature"] = {
                "id": feature.id, "title": feature.title,
                "status": feature.status.value, "progress": progress,
                "completed": is_complete,
            }

            # If feature just completed, store a summary memory
            if is_complete:
                tasks = engine.features.get_tasks(task.feature_id)
                task_list = "\n".join(f"- {t.title}" for t in tasks)
                engine.memory.store(MemoryEntry(
                    project_id=pid,
                    type=MemoryType.ARCHITECTURE,
                    title=f"Feature complete: {feature.title}",
                    content=f"Feature: {feature.title}\n{feature.description}\n\nTasks completed:\n{task_list}",
                    tags=["feature-complete", "milestone"],
                    source="claude",
                    source_ref=feature.id,
                    importance=1,
                ))
                result["memories_stored"].append({"type": "feature_complete"})

    return result


def handle_tool(name: str, arguments: dict) -> Any:
    engine = get_engine()
    project = engine.projects.get_by_path()

    if not project and name != "opero_status":
        return {"error": "Project not initialized. Run opero init first."}

    pid = project.id if project else ""

    if name == "opero_status":
        return engine.status()

    elif name == "opero_tasks_list":
        status = TaskStatus(arguments["status"]) if arguments.get("status") else None
        task_type = TaskType(arguments["type"]) if arguments.get("type") else None
        tasks = engine.tasks.list_tasks(project_id=pid, status=status, task_type=task_type)
        return {"tasks": [
            {"id": t.id, "title": t.title, "status": t.status.value, "type": t.type.value,
             "priority": t.priority, "description": t.description, "assigned_agent": t.assigned_agent}
            for t in tasks
        ]}

    elif name == "opero_tasks_next":
        task = engine.tasks.get_next_task(pid)
        if not task:
            return {"message": "No tasks ready to execute"}
        return {"id": task.id, "title": task.title, "type": task.type.value,
                "priority": task.priority, "description": task.description,
                "success_criteria": task.success_criteria}

    elif name == "opero_task_create":
        task = Task(
            project_id=pid,
            title=arguments["title"],
            description=arguments.get("description", ""),
            type=TaskType(arguments.get("type", "feature")),
            priority=arguments.get("priority", 3),
            success_criteria=arguments.get("success_criteria", ""),
        )
        created = engine.tasks.create(task)
        return {"id": created.id, "title": created.title, "status": created.status.value}

    elif name == "opero_task_update":
        task_id = arguments.pop("task_id")
        updates = {k: v for k, v in arguments.items() if v is not None}
        task = engine.tasks.update(task_id, **updates)
        if not task:
            return {"error": f"Task {task_id} not found"}
        return {"id": task.id, "title": task.title, "status": task.status.value}

    elif name == "opero_memory_store":
        entry = MemoryEntry(
            project_id=pid,
            type=MemoryType(arguments["type"]),
            title=arguments["title"],
            content=arguments["content"],
            tags=arguments.get("tags", []),
            source="claude",
            importance=arguments.get("importance", 3),
        )
        stored = engine.memory.store(entry)
        return {"id": stored.id, "title": stored.title, "type": stored.type.value}

    elif name == "opero_memory_search":
        results = engine.memory.search(pid, arguments["query"], top_k=arguments.get("top_k", 5))
        return {"results": [
            {"id": m.id, "title": m.title, "content": m.content, "type": m.type.value,
             "score": round(s, 4), "tags": m.tags}
            for m, s in results
        ]}

    elif name == "opero_memory_list":
        mem_type = MemoryType(arguments["type"]) if arguments.get("type") else None
        memories = engine.memory.list_memories(pid, memory_type=mem_type, source=arguments.get("source"))
        return {"memories": [
            {"id": m.id, "title": m.title, "type": m.type.value, "importance": m.importance,
             "content": m.content[:200], "tags": m.tags}
            for m in memories
        ]}

    elif name == "opero_context":
        return engine.memory.build_context(
            project_id=pid,
            query=arguments.get("query"),
            task_id=arguments.get("task_id"),
            tool="claude",
        )

    elif name == "opero_feature_create":
        feature = Feature(
            project_id=pid,
            title=arguments["title"],
            description=arguments.get("description", ""),
            priority=arguments.get("priority", 3),
        )
        created = engine.features.create(feature)
        return {"id": created.id, "title": created.title, "status": created.status.value}

    elif name == "opero_feature_list":
        status = FeatureStatus(arguments["status"]) if arguments.get("status") else None
        features = engine.features.list_features(pid, status=status)
        result = []
        for f in features:
            progress = engine.features.get_progress(f.id)
            result.append({
                "id": f.id, "title": f.title, "status": f.status.value,
                "priority": f.priority, "description": f.description,
                "progress": progress,
            })
        return {"features": result}

    elif name == "opero_feature_task":
        task = Task(
            project_id=pid,
            feature_id=arguments["feature_id"],
            title=arguments["title"],
            description=arguments.get("description", ""),
            type=TaskType(arguments.get("type", "feature")),
            priority=arguments.get("priority", 3),
            success_criteria=arguments.get("success_criteria", ""),
        )
        created = engine.features.add_task(arguments["feature_id"], task)
        return {"id": created.id, "title": created.title, "feature_id": arguments["feature_id"]}

    elif name == "opero_feature_get":
        feature = engine.features.get(arguments["feature_id"])
        if not feature:
            return {"error": "Feature not found"}
        tasks = engine.features.get_tasks(arguments["feature_id"])
        progress = engine.features.get_progress(arguments["feature_id"])
        return {
            "feature": {"id": feature.id, "title": feature.title, "status": feature.status.value},
            "tasks": [{"id": t.id, "title": t.title, "status": t.status.value, "type": t.type.value} for t in tasks],
            "progress": progress,
        }

    elif name == "opero_feature_update":
        fid = arguments["feature_id"]
        updates = {k: v for k, v in arguments.items() if k != "feature_id" and v is not None}
        feature = engine.features.update(fid, **updates)
        if not feature:
            return {"error": "Feature not found"}
        return {"id": feature.id, "title": feature.title, "status": feature.status.value}

    elif name == "opero_start_work":
        return _handle_start_work(engine, pid, arguments)

    elif name == "opero_complete_work":
        return _handle_complete_work(engine, pid, arguments)

    elif name == "opero_git_sync":
        return engine.sync()

    elif name == "opero_memory_link":
        link = engine.memory.link(
            arguments["memory_id"],
            arguments["linked_type"],
            arguments["linked_id"],
            arguments.get("relationship", "related"),
        )
        return {"id": link.id, "relationship": link.relationship}

    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# JSON-RPC stdio transport
# ---------------------------------------------------------------------------

def read_message() -> dict | None:
    """Read a JSON-RPC message from stdin (binary mode)."""
    stdin = sys.stdin.buffer if hasattr(sys.stdin, 'buffer') else sys.stdin

    # Read headers
    headers = {}
    while True:
        line = stdin.readline()
        if not line:
            return None
        line = line.decode('utf-8') if isinstance(line, bytes) else line
        line = line.strip()
        if not line:
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", 0))
    if content_length == 0:
        return None

    body = stdin.read(content_length)
    if isinstance(body, bytes):
        body = body.decode('utf-8')
    return json.loads(body)


def send_message(msg: dict) -> None:
    """Send a JSON-RPC message to stdout (binary mode)."""
    body = json.dumps(msg)
    body_bytes = body.encode('utf-8')
    header = f"Content-Length: {len(body_bytes)}\r\n\r\n"

    stdout = sys.stdout.buffer if hasattr(sys.stdout, 'buffer') else sys.stdout
    if hasattr(stdout, 'write') and isinstance(b'', bytes):
        try:
            stdout.write(header.encode('utf-8'))
            stdout.write(body_bytes)
            stdout.flush()
            return
        except TypeError:
            pass
    # Fallback to text mode
    sys.stdout.write(header)
    sys.stdout.write(body)
    sys.stdout.flush()


def send_result(request_id: Any, result: Any) -> None:
    send_message({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    })


def send_error(request_id: Any, code: int, message: str) -> None:
    send_message({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    })


def main():
    """Run the MCP stdio server."""
    import traceback

    while True:
        try:
            msg = read_message()
            if msg is None:
                break

            method = msg.get("method", "")
            request_id = msg.get("id")
            params = msg.get("params", {})

            if method == "initialize":
                send_result(request_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "opero",
                        "version": "0.1.0",
                    },
                })

            elif method == "notifications/initialized":
                pass

            elif method == "tools/list":
                send_result(request_id, {"tools": TOOLS})

            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                try:
                    result = handle_tool(tool_name, arguments)
                    send_result(request_id, {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    })
                except Exception as e:
                    sys.stderr.write(f"Tool error ({tool_name}): {e}\n")
                    send_result(request_id, {
                        "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                        "isError": True,
                    })

            elif method == "ping":
                send_result(request_id, {})

            else:
                if request_id is not None:
                    send_error(request_id, -32601, f"Method not found: {method}")

        except json.JSONDecodeError as e:
            sys.stderr.write(f"JSON parse error: {e}\n")
            continue
        except Exception as e:
            sys.stderr.write(f"MCP server error: {traceback.format_exc()}\n")
            break


if __name__ == "__main__":
    main()
