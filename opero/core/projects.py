"""Project management for Opero Core."""

from __future__ import annotations

from opero.core.models import Project, Task, TaskType, _new_id, _now
from opero.core.tasks import TaskManager
from opero.db.schema import get_connection, init_db


class ProjectManager:
    def __init__(self, project_path: str):
        self.project_path = project_path
        self.tasks = TaskManager(project_path)

    def _conn(self):
        return get_connection(self.project_path)

    def create(self, project: Project) -> Project:
        if not project.id:
            project.id = _new_id()
        project.path = self.project_path
        project.created_at = _now()
        project.updated_at = _now()
        conn = self._conn()
        d = project.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        conn.execute(f"INSERT INTO projects ({cols}) VALUES ({placeholders})", list(d.values()))
        conn.commit()
        conn.close()
        return project

    def get(self, project_id: str) -> Project | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        conn.close()
        if row:
            return Project(**dict(row))
        return None

    def get_by_path(self) -> Project | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM projects WHERE path = ?", (self.project_path,)).fetchone()
        conn.close()
        if row:
            return Project(**dict(row))
        return None

    def update(self, project_id: str, **kwargs) -> Project | None:
        kwargs["updated_at"] = _now()
        conn = self._conn()
        sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
        conn.execute(f"UPDATE projects SET {sets} WHERE id = ?", list(kwargs.values()) + [project_id])
        conn.commit()
        conn.close()
        return self.get(project_id)

    def get_context(self, project_id: str) -> dict:
        """Get full project context including memory and active tasks."""
        project = self.get(project_id)
        if not project:
            return {}

        active_tasks = self.tasks.list_tasks(project_id=project_id)
        conn = self._conn()
        memory_rows = conn.execute(
            "SELECT key, value, category FROM project_memory WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        conn.close()

        memory = {row["key"]: {"value": row["value"], "category": row["category"]} for row in memory_rows}

        return {
            "project": project.to_dict(),
            "tasks": [t.to_dict() for t in active_tasks],
            "memory": memory,
        }

    def set_memory(self, project_id: str, key: str, value: str, category: str = "general") -> None:
        conn = self._conn()
        conn.execute(
            """INSERT INTO project_memory (id, project_id, key, value, category, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, key) DO UPDATE SET value=?, category=?, updated_at=?""",
            (_new_id(), project_id, key, value, category, _now(), value, category, _now()),
        )
        conn.commit()
        conn.close()

    def get_memory(self, project_id: str, key: str) -> str | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT value FROM project_memory WHERE project_id = ? AND key = ?",
            (project_id, key),
        ).fetchone()
        conn.close()
        return row["value"] if row else None

    def create_initial_tasks(self, project_id: str) -> list[Task]:
        """Create bootstrap tasks for a new project."""
        initial_tasks = [
            Task(
                project_id=project_id,
                title="Project setup",
                description="Initialize project structure, dependencies, and configuration",
                type=TaskType.SETUP,
                priority=1,
                success_criteria="Project directory structure created, dependencies installed",
            ),
            Task(
                project_id=project_id,
                title="Environment configuration",
                description="Configure development environment, linters, formatters, and CI",
                type=TaskType.SETUP,
                priority=2,
                success_criteria="Dev environment fully configured and documented",
            ),
            Task(
                project_id=project_id,
                title="Base architecture definition",
                description="Define core architecture, module boundaries, and data flow",
                type=TaskType.RESEARCH,
                priority=2,
                success_criteria="Architecture documented with clear module responsibilities",
            ),
        ]
        created = []
        for task in initial_tasks:
            created.append(self.tasks.create(task))
        return created
