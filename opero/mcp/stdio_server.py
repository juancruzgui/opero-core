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
from opero.core.models import Task, TaskType, TaskStatus


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
    """Read a JSON-RPC message from stdin."""
    # MCP uses Content-Length header framing
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", 0))
    if content_length == 0:
        return None

    body = sys.stdin.read(content_length)
    return json.loads(body)


def send_message(msg: dict) -> None:
    """Send a JSON-RPC message to stdout."""
    body = json.dumps(msg)
    header = f"Content-Length: {len(body)}\r\n\r\n"
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
    # Disable buffering
    sys.stdin = open(sys.stdin.fileno(), 'r', buffering=1)
    sys.stdout = open(sys.stdout.fileno(), 'w', buffering=1)

    while True:
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
            pass  # No response needed for notifications

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
                send_result(request_id, {
                    "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                    "isError": True,
                })

        elif method == "ping":
            send_result(request_id, {})

        else:
            if request_id is not None:
                send_error(request_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
