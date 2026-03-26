"""MCP Server — FastAPI interface for AI tools to interact with Opero Core."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from opero.core.engine import OperoEngine
from opero.core.models import Task, TaskType, TaskStatus, Feature, FeatureStatus
from opero.core.memory import MemoryEntry, MemoryType

app = FastAPI(title="Opero Core MCP", version="0.1.0")

# Serve the dashboard UI
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Engine is initialized with current working directory or OPERO_PROJECT_PATH
_engine: OperoEngine | None = None


def get_engine() -> OperoEngine:
    global _engine
    if _engine is None:
        project_path = os.environ.get("OPERO_PROJECT_PATH", os.getcwd())
        _engine = OperoEngine(project_path)
    return _engine


# --- Request / Response models ---

class CreateProjectRequest(BaseModel):
    name: str = ""
    description: str = ""


class CreateTaskRequest(BaseModel):
    project_id: str
    title: str
    description: str = ""
    type: str = "feature"
    priority: int = 3
    dependencies: list[str] = []
    assigned_agent: Optional[str] = None
    inputs: str = ""
    success_criteria: str = ""


class UpdateTaskRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    assigned_agent: Optional[str] = None
    outputs: Optional[str] = None
    dependencies: Optional[list[str]] = None


class AssignAgentRequest(BaseModel):
    task_id: str
    agent_name: str


class RunTaskRequest(BaseModel):
    task_id: str


class SetMemoryRequest(BaseModel):
    project_id: str
    key: str
    value: str
    category: str = "general"


class StoreMemoryRequest(BaseModel):
    project_id: str
    type: str = "context"
    title: str
    content: str
    tags: list[str] = []
    source: str = "user"
    source_ref: str = ""
    importance: int = 3


class UpdateMemoryRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    type: Optional[str] = None
    tags: Optional[list[str]] = None
    importance: Optional[int] = None
    active: Optional[bool] = None


class SearchMemoryRequest(BaseModel):
    project_id: str
    query: str
    top_k: int = 10


class BuildContextRequest(BaseModel):
    project_id: str
    query: Optional[str] = None
    task_id: Optional[str] = None
    max_entries: int = 20
    tool: str = "claude"


class LinkMemoryRequest(BaseModel):
    memory_id: str
    linked_type: str
    linked_id: str
    relationship: str = "related"


class CreateFeatureRequest(BaseModel):
    project_id: str
    title: str
    description: str = ""
    priority: int = 3


class UpdateFeatureRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None


class AddFeatureTaskRequest(BaseModel):
    feature_id: str
    title: str
    description: str = ""
    type: str = "feature"
    priority: int = 3
    success_criteria: str = ""


# --- Endpoints ---

@app.get("/")
def dashboard():
    """Serve the Opero dashboard."""
    index = Path(__file__).parent / "static" / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Dashboard not found. API is running.", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/project/create")
def create_project(req: CreateProjectRequest):
    engine = get_engine()
    if engine.is_initialized():
        project = engine.projects.get_by_path()
        return {"project": project.to_dict(), "message": "Already initialized"}
    project = engine.initialize(name=req.name, description=req.description)
    return {"project": project.to_dict(), "message": "Project created"}


@app.get("/project/context/{project_id}")
def get_project_context(project_id: str):
    engine = get_engine()
    ctx = engine.projects.get_context(project_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Project not found")
    return ctx


@app.get("/project/status")
def project_status():
    engine = get_engine()
    return engine.status()


@app.post("/task/create")
def create_task(req: CreateTaskRequest):
    engine = get_engine()
    task = Task(
        project_id=req.project_id,
        title=req.title,
        description=req.description,
        type=TaskType(req.type),
        priority=req.priority,
        dependencies=req.dependencies,
        assigned_agent=req.assigned_agent,
        inputs=req.inputs,
        success_criteria=req.success_criteria,
    )
    created = engine.tasks.create(task)
    return {"task": created.to_dict()}


@app.put("/task/{task_id}")
def update_task(task_id: str, req: UpdateTaskRequest):
    engine = get_engine()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    task = engine.tasks.update(task_id, **updates)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task.to_dict()}


@app.get("/tasks")
def list_tasks(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    type: Optional[str] = None,
    agent: Optional[str] = None,
):
    engine = get_engine()
    tasks = engine.tasks.list_tasks(
        project_id=project_id,
        status=TaskStatus(status) if status else None,
        task_type=TaskType(type) if type else None,
        assigned_agent=agent,
    )
    return {"tasks": [t.to_dict() for t in tasks]}


@app.post("/agent/assign")
def assign_agent(req: AssignAgentRequest):
    engine = get_engine()
    task = engine.tasks.assign_agent(req.task_id, req.agent_name)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": task.to_dict()}


@app.post("/task/run")
def run_task(req: RunTaskRequest):
    engine = get_engine()
    task = engine.tasks.get(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    execution = engine.agents.run_task(task)
    return {"execution": execution.to_dict()}


# --- Features ---

@app.post("/feature/create")
def create_feature(req: CreateFeatureRequest):
    engine = get_engine()
    feature = Feature(
        project_id=req.project_id,
        title=req.title,
        description=req.description,
        priority=req.priority,
    )
    created = engine.features.create(feature)
    return {"feature": created.to_dict()}


@app.get("/features/{project_id}")
def list_features(project_id: str, status: Optional[str] = None):
    engine = get_engine()
    features = engine.features.list_features(
        project_id, status=FeatureStatus(status) if status else None,
    )
    return {"features": [f.to_dict() for f in features]}


@app.get("/feature/{feature_id}")
def get_feature(feature_id: str):
    engine = get_engine()
    feature = engine.features.get(feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    tasks = engine.features.get_tasks(feature_id)
    progress = engine.features.get_progress(feature_id)
    return {"feature": feature.to_dict(), "tasks": [t.to_dict() for t in tasks], "progress": progress}


@app.put("/feature/{feature_id}")
def update_feature(feature_id: str, req: UpdateFeatureRequest):
    engine = get_engine()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    feature = engine.features.update(feature_id, **updates)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    return {"feature": feature.to_dict()}


@app.post("/feature/task")
def add_feature_task(req: AddFeatureTaskRequest):
    engine = get_engine()
    feature = engine.features.get(req.feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")
    task = Task(
        project_id=feature.project_id,
        feature_id=req.feature_id,
        title=req.title,
        description=req.description,
        type=TaskType(req.type),
        priority=req.priority,
        success_criteria=req.success_criteria,
    )
    created = engine.features.add_task(req.feature_id, task)
    return {"task": created.to_dict()}


@app.get("/features/board/{project_id}")
def features_board(project_id: str):
    """Full board view: all features with tasks and progress."""
    engine = get_engine()
    return {"features": engine.features.get_full_view(project_id)}


@app.get("/executions/active")
def active_executions():
    engine = get_engine()
    from opero.db.schema import get_connection
    conn = get_connection(engine.project_path)
    rows = conn.execute(
        "SELECT * FROM task_executions WHERE status IN ('pending', 'running') ORDER BY started_at DESC"
    ).fetchall()
    conn.close()
    return {"executions": [dict(r) for r in rows]}


@app.get("/claude/activity")
def claude_activity(limit: int = 50):
    """Get recent Claude Code activity for the live feed."""
    engine = get_engine()
    from opero.db.schema import get_connection
    conn = get_connection(engine.project_path)
    rows = conn.execute(
        "SELECT * FROM claude_activity ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"activity": [dict(r) for r in rows]}


@app.get("/claude/sessions")
def claude_sessions():
    """Get Claude Code sessions — shows if Claude is actively working."""
    engine = get_engine()
    from opero.db.schema import get_connection
    conn = get_connection(engine.project_path)
    rows = conn.execute(
        "SELECT * FROM claude_sessions ORDER BY started_at DESC LIMIT 10"
    ).fetchall()
    conn.close()

    sessions = []
    for r in rows:
        d = dict(r)
        # Mark sessions with no heartbeat in 30s as stale
        if d["status"] == "active" and d.get("last_heartbeat"):
            from datetime import datetime
            try:
                last = datetime.fromisoformat(d["last_heartbeat"])
                age = (datetime.utcnow() - last).total_seconds()
                if age > 30:
                    d["status"] = "stale"
            except (ValueError, TypeError):
                pass
        sessions.append(d)
    return {"sessions": sessions}


@app.get("/agents")
def list_agents():
    engine = get_engine()
    agents = engine.agents.list_agents()
    return {"agents": [a.to_dict() for a in agents]}


@app.post("/git/sync")
def sync_git():
    engine = get_engine()
    result = engine.sync()
    return result


@app.get("/git/log")
def git_log(count: int = 20):
    engine = get_engine()
    return {"commits": engine.git.get_log(count)}


@app.get("/git/status")
def git_status():
    engine = get_engine()
    return {
        "branch": engine.git.current_branch(),
        "has_changes": engine.git.has_changes(),
        "status": engine.git.status(),
    }


@app.post("/memory/set")
def set_memory(req: SetMemoryRequest):
    engine = get_engine()
    engine.projects.set_memory(req.project_id, req.key, req.value, req.category)
    return {"status": "ok"}


@app.get("/memory/{project_id}/{key}")
def get_memory(project_id: str, key: str):
    engine = get_engine()
    value = engine.projects.get_memory(project_id, key)
    if value is None:
        raise HTTPException(status_code=404, detail="Memory key not found")
    return {"key": key, "value": value}


# --- Structured Memory (vector-backed) ---

@app.post("/memory/store")
def store_memory(req: StoreMemoryRequest):
    engine = get_engine()
    entry = MemoryEntry(
        project_id=req.project_id,
        type=MemoryType(req.type),
        title=req.title,
        content=req.content,
        tags=req.tags,
        source=req.source,
        source_ref=req.source_ref,
        importance=req.importance,
    )
    stored = engine.memory.store(entry)
    return {"memory": stored.to_dict()}


@app.get("/memory/entry/{memory_id}")
def get_memory_entry(memory_id: str):
    engine = get_engine()
    entry = engine.memory.get(memory_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return {"memory": entry.to_dict()}


@app.put("/memory/entry/{memory_id}")
def update_memory_entry(memory_id: str, req: UpdateMemoryRequest):
    engine = get_engine()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    entry = engine.memory.update(memory_id, **updates)
    if not entry:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return {"memory": entry.to_dict()}


@app.delete("/memory/entry/{memory_id}")
def delete_memory_entry(memory_id: str):
    engine = get_engine()
    deleted = engine.memory.delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return {"status": "deleted"}


@app.get("/memory/list/{project_id}")
def list_memories(
    project_id: str,
    type: Optional[str] = None,
    source: Optional[str] = None,
    active_only: bool = True,
    min_importance: Optional[int] = None,
):
    engine = get_engine()
    memories = engine.memory.list_memories(
        project_id=project_id,
        memory_type=MemoryType(type) if type else None,
        source=source,
        active_only=active_only,
        min_importance=min_importance,
    )
    return {"memories": [m.to_dict() for m in memories]}


@app.post("/memory/search")
def search_memory(req: SearchMemoryRequest):
    engine = get_engine()
    results = engine.memory.search(req.project_id, req.query, top_k=req.top_k)
    return {
        "results": [
            {"memory": m.to_dict(), "score": round(s, 4)}
            for m, s in results
        ]
    }


@app.post("/memory/context")
def build_context(req: BuildContextRequest):
    """Main entry point for AI tools to get project context with relevant memories."""
    engine = get_engine()
    ctx = engine.memory.build_context(
        project_id=req.project_id,
        query=req.query,
        task_id=req.task_id,
        max_entries=req.max_entries,
        tool=req.tool,
    )
    return ctx


@app.post("/memory/link")
def link_memory(req: LinkMemoryRequest):
    engine = get_engine()
    link = engine.memory.link(req.memory_id, req.linked_type, req.linked_id, req.relationship)
    return {"link": link.to_dict()}


@app.get("/memory/links/{memory_id}")
def get_memory_links(memory_id: str):
    engine = get_engine()
    links = engine.memory.get_links(memory_id)
    return {"links": [l.to_dict() for l in links]}


@app.get("/memory/by-link/{linked_type}/{linked_id}")
def find_memories_by_link(linked_type: str, linked_id: str):
    engine = get_engine()
    memories = engine.memory.find_by_link(linked_type, linked_id)
    return {"memories": [m.to_dict() for m in memories]}


@app.post("/memory/reindex/{project_id}")
def reindex_memory(project_id: str):
    engine = get_engine()
    count = engine.memory.reindex(project_id)
    return {"reindexed": count}
