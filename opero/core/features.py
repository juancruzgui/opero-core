"""Feature/epic management for Opero Core.

Features group related tasks under a common goal.
Project → Features → Tasks

Example:
  Project: Todo App
    Feature: Authentication System
      Task: Create user model
      Task: Build login endpoint
      Task: Add JWT middleware
    Feature: Task Management
      Task: CRUD API
      Task: Drag-and-drop UI
"""

from __future__ import annotations

from opero.core.models import Feature, FeatureStatus, Task, TaskType, TaskStatus, _new_id, _now
from opero.core.events import emit
from opero.core.tasks import TaskManager
from opero.db.schema import get_connection


class FeatureManager:
    def __init__(self, project_path: str):
        self.project_path = project_path
        self.tasks = TaskManager(project_path)

    def _conn(self):
        return get_connection(self.project_path)

    def create(self, feature: Feature) -> Feature:
        if not feature.id:
            feature.id = _new_id()
        feature.created_at = _now()
        feature.updated_at = _now()
        conn = self._conn()
        d = feature.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        conn.execute(f"INSERT INTO features ({cols}) VALUES ({placeholders})", list(d.values()))
        conn.commit()
        conn.close()
        emit(self.project_path, "feature.created", {"feature_id": feature.id, "title": feature.title, "status": feature.status.value})
        return feature

    def get(self, feature_id: str) -> Feature | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM features WHERE id = ?", (feature_id,)).fetchone()
        conn.close()
        return Feature.from_row(dict(row)) if row else None

    def list_features(
        self,
        project_id: str,
        status: FeatureStatus | None = None,
    ) -> list[Feature]:
        conn = self._conn()
        query = "SELECT * FROM features WHERE project_id = ?"
        params: list = [project_id]
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY priority ASC, created_at ASC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [Feature.from_row(dict(r)) for r in rows]

    def update(self, feature_id: str, **kwargs) -> Feature | None:
        feature = self.get(feature_id)
        if not feature:
            return None

        kwargs["updated_at"] = _now()

        if kwargs.get("status") == FeatureStatus.DONE.value or kwargs.get("status") == FeatureStatus.DONE:
            kwargs["completed_at"] = _now()
            if isinstance(kwargs.get("status"), FeatureStatus):
                kwargs["status"] = kwargs["status"].value

        conn = self._conn()
        sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
        conn.execute(f"UPDATE features SET {sets} WHERE id = ?", list(kwargs.values()) + [feature_id])
        conn.commit()
        conn.close()
        updated = self.get(feature_id)
        if updated:
            emit(self.project_path, "feature.updated", {"feature_id": feature_id, "title": updated.title, "status": updated.status.value})
        return updated

    def delete(self, feature_id: str) -> bool:
        conn = self._conn()
        cur = conn.execute("DELETE FROM features WHERE id = ?", (feature_id,))
        conn.commit()
        conn.close()
        return cur.rowcount > 0

    def get_tasks(self, feature_id: str) -> list[Task]:
        """Get all tasks belonging to a feature."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE feature_id = ? ORDER BY priority ASC, created_at ASC",
            (feature_id,),
        ).fetchall()
        conn.close()
        return [Task.from_row(dict(r)) for r in rows]

    def get_progress(self, feature_id: str) -> dict:
        """Get feature completion progress."""
        tasks = self.get_tasks(feature_id)
        total = len(tasks)
        if total == 0:
            return {"total": 0, "done": 0, "percent": 0}
        done = sum(1 for t in tasks if t.status == TaskStatus.DONE)
        return {"total": total, "done": done, "percent": round(done / total * 100)}

    def add_task(self, feature_id: str, task: Task) -> Task:
        """Create a task under this feature."""
        task.feature_id = feature_id
        feature = self.get(feature_id)
        if feature:
            task.project_id = feature.project_id
        return self.tasks.create(task)

    def check_completion(self, feature_id: str) -> bool:
        """Check if all tasks are done and auto-complete the feature."""
        tasks = self.get_tasks(feature_id)
        if not tasks:
            return False
        all_done = all(t.status == TaskStatus.DONE for t in tasks)
        if all_done:
            self.update(feature_id, status=FeatureStatus.DONE.value)
        return all_done

    def get_full_view(self, project_id: str) -> list[dict]:
        """Get all features with their tasks and progress — for dashboard."""
        features = self.list_features(project_id)
        result = []
        for f in features:
            tasks = self.get_tasks(f.id)
            progress = self.get_progress(f.id)
            result.append({
                "feature": f.to_dict(),
                "tasks": [t.to_dict() for t in tasks],
                "progress": progress,
            })
        return result
