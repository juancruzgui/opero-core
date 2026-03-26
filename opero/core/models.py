"""Data models for Opero Core."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskType(str, Enum):
    FEATURE = "feature"
    BUG = "bug"
    RESEARCH = "research"
    AGENT_TASK = "agent_task"
    SETUP = "setup"


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class FeatureStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    DONE = "done"
    PAUSED = "paused"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.utcnow().isoformat()


@dataclass
class Project:
    id: str = field(default_factory=_new_id)
    name: str = ""
    description: str = ""
    path: str = ""
    tech_stack: str = ""
    architecture_notes: str = ""
    decisions: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Feature:
    id: str = field(default_factory=_new_id)
    project_id: str = ""
    title: str = ""
    description: str = ""
    status: FeatureStatus = FeatureStatus.PLANNING
    priority: int = 3
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_row(cls, row: dict) -> Feature:
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            description=row.get("description", ""),
            status=FeatureStatus(row["status"]),
            priority=row.get("priority", 3),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            completed_at=row.get("completed_at"),
        )


@dataclass
class Task:
    id: str = field(default_factory=_new_id)
    project_id: str = ""
    feature_id: Optional[str] = None
    title: str = ""
    description: str = ""
    type: TaskType = TaskType.FEATURE
    status: TaskStatus = TaskStatus.TODO
    priority: int = 3
    dependencies: list[str] = field(default_factory=list)
    assigned_agent: Optional[str] = None
    inputs: str = ""
    outputs: str = ""
    success_criteria: str = ""
    parent_task_id: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        d["dependencies"] = json.dumps(self.dependencies)
        return d

    @classmethod
    def from_row(cls, row: dict) -> Task:
        deps = row.get("dependencies", "[]")
        try:
            deps_list = json.loads(deps) if deps else []
        except (json.JSONDecodeError, TypeError):
            deps_list = []
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            feature_id=row.get("feature_id"),
            title=row["title"],
            description=row.get("description", ""),
            type=TaskType(row["type"]),
            status=TaskStatus(row["status"]),
            priority=row.get("priority", 3),
            dependencies=deps_list,
            assigned_agent=row.get("assigned_agent"),
            inputs=row.get("inputs", ""),
            outputs=row.get("outputs", ""),
            success_criteria=row.get("success_criteria", ""),
            parent_task_id=row.get("parent_task_id"),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            completed_at=row.get("completed_at"),
        )


@dataclass
class Agent:
    name: str = ""
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["capabilities"] = json.dumps(self.capabilities)
        d["tools"] = json.dumps(self.tools)
        return d

    @classmethod
    def from_row(cls, row: dict) -> Agent:
        return cls(
            name=row["name"],
            capabilities=json.loads(row.get("capabilities", "[]")),
            tools=json.loads(row.get("tools", "[]")),
            description=row.get("description", ""),
        )


@dataclass
class TaskExecution:
    id: str = field(default_factory=_new_id)
    task_id: str = ""
    agent_name: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    output: str = ""
    error: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class GitCommit:
    sha: str = ""
    project_id: str = ""
    message: str = ""
    author: str = ""
    branch: str = ""
    task_id: Optional[str] = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)
