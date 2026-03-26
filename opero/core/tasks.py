"""Task management system for Opero Core."""

from __future__ import annotations

import json
from datetime import datetime

from opero.core.models import Task, TaskStatus, TaskType, _new_id, _now
from opero.core.events import emit
from opero.db.schema import get_connection


class TaskManager:
    def __init__(self, project_path: str):
        self.project_path = project_path

    def _conn(self):
        return get_connection(self.project_path)

    def create(self, task: Task) -> Task:
        if not task.id:
            task.id = _new_id()
        task.created_at = _now()
        task.updated_at = _now()
        conn = self._conn()
        d = task.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        conn.execute(f"INSERT INTO tasks ({cols}) VALUES ({placeholders})", list(d.values()))
        conn.commit()
        conn.close()
        emit(self.project_path, "task.created", {"task_id": task.id, "title": task.title, "type": task.type.value, "status": task.status.value})
        return task

    def get(self, task_id: str) -> Task | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        if row:
            return Task.from_row(dict(row))
        return None

    def list_tasks(
        self,
        project_id: str | None = None,
        status: TaskStatus | None = None,
        task_type: TaskType | None = None,
        assigned_agent: str | None = None,
    ) -> list[Task]:
        conn = self._conn()
        query = "SELECT * FROM tasks WHERE 1=1"
        params: list = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if task_type:
            query += " AND type = ?"
            params.append(task_type.value)
        if assigned_agent:
            query += " AND assigned_agent = ?"
            params.append(assigned_agent)

        query += " ORDER BY priority ASC, created_at ASC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [Task.from_row(dict(r)) for r in rows]

    def update(self, task_id: str, **kwargs) -> Task | None:
        task = self.get(task_id)
        if not task:
            return None

        kwargs["updated_at"] = _now()

        if kwargs.get("status") == TaskStatus.DONE.value or kwargs.get("status") == TaskStatus.DONE:
            kwargs["completed_at"] = _now()
            if isinstance(kwargs.get("status"), TaskStatus):
                kwargs["status"] = kwargs["status"].value

        if "dependencies" in kwargs and isinstance(kwargs["dependencies"], list):
            kwargs["dependencies"] = json.dumps(kwargs["dependencies"])

        conn = self._conn()
        sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
        conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", list(kwargs.values()) + [task_id])
        conn.commit()
        conn.close()
        updated = self.get(task_id)
        if updated:
            emit(self.project_path, "task.updated", {"task_id": task_id, "title": updated.title, "status": updated.status.value})
        return updated

    def delete(self, task_id: str) -> bool:
        conn = self._conn()
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        return cur.rowcount > 0

    def get_next_task(self, project_id: str) -> Task | None:
        """Get highest priority todo task with all dependencies met."""
        tasks = self.list_tasks(project_id=project_id, status=TaskStatus.TODO)
        for task in tasks:
            if not task.dependencies:
                return task
            all_done = all(
                self.get(dep_id) and self.get(dep_id).status == TaskStatus.DONE
                for dep_id in task.dependencies
            )
            if all_done:
                return task
        return None

    def assign_agent(self, task_id: str, agent_name: str) -> Task | None:
        return self.update(task_id, assigned_agent=agent_name)
