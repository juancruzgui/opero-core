"""Event bus for Opero Core.

All managers emit events here. The activity log, dashboard, and
any future integrations subscribe to these events.

Usage:
    from opero.core.events import emit

    emit(project_path, "task.created", {"task_id": "abc", "title": "Build login"})
    emit(project_path, "feature.created", {"feature_id": "xyz", "title": "Auth"})
    emit(project_path, "memory.stored", {"memory_id": "123", "type": "decision"})
"""

from __future__ import annotations

import json
from opero.db.schema import get_connection


def emit(project_path: str, event_type: str, detail: dict | None = None):
    """Log an event to claude_activity and events tables."""
    try:
        conn = get_connection(project_path)

        # Get project ID
        row = conn.execute("SELECT id FROM projects WHERE path = ?", (project_path,)).fetchone()
        project_id = row["id"] if row else None

        # Map event type to activity fields
        tool_name = event_type.split(".")[0]  # task, feature, memory, git
        action = event_type.split(".")[-1]    # created, updated, stored, etc.

        # Build detail string
        detail_str = ""
        task_id = None
        file_path = ""

        if detail:
            task_id = detail.get("task_id")
            file_path = detail.get("file_path", "")

            # Build human-readable detail
            parts = []
            for key in ("title", "status", "type", "query", "outcome"):
                if key in detail:
                    parts.append(f"{key}={detail[key]}")
            detail_str = ", ".join(parts) if parts else json.dumps(detail)[:200]

        # Log to claude_activity (shows in dashboard live feed)
        conn.execute(
            "INSERT INTO claude_activity (project_id, session_id, tool_name, action, file_path, task_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, "system", tool_name, action, file_path, task_id, detail_str),
        )

        # Log to events table
        conn.execute(
            "INSERT INTO events (project_id, event_type, payload) VALUES (?, ?, ?)",
            (project_id, event_type, json.dumps(detail) if detail else None),
        )

        conn.commit()
        conn.close()
    except Exception:
        pass  # Events must never break the caller
