"""Agent registry and execution system for Opero Core."""

from __future__ import annotations

import json

from opero.core.models import Agent, Task, TaskExecution, TaskStatus, ExecutionStatus, _new_id, _now
from opero.db.schema import get_connection


# Default agents shipped with Opero
DEFAULT_AGENTS = [
    Agent(
        name="backend_dev",
        capabilities=["write_code", "debug", "refactor", "api_design", "database"],
        tools=["filesystem", "git", "shell", "database"],
        description="Backend development agent — writes server-side code, APIs, and database logic",
    ),
    Agent(
        name="frontend_dev",
        capabilities=["write_code", "ui_design", "component_creation", "styling"],
        tools=["filesystem", "git", "shell", "browser"],
        description="Frontend development agent — builds UI components, pages, and client-side logic",
    ),
    Agent(
        name="fullstack_dev",
        capabilities=["write_code", "debug", "refactor", "api_design", "ui_design", "database"],
        tools=["filesystem", "git", "shell", "database", "browser"],
        description="Full-stack development agent — handles both frontend and backend work",
    ),
    Agent(
        name="researcher",
        capabilities=["research", "analyze", "document", "plan"],
        tools=["filesystem", "git", "web_search"],
        description="Research agent — investigates technologies, architectures, and approaches",
    ),
    Agent(
        name="debugger",
        capabilities=["debug", "analyze", "trace", "profile"],
        tools=["filesystem", "git", "shell", "debugger"],
        description="Debugging agent — diagnoses and fixes issues in code",
    ),
    Agent(
        name="orchestrator",
        capabilities=["plan", "coordinate", "review", "dispatch", "analyze"],
        tools=["filesystem", "git", "shell", "web_search"],
        description="Orchestrator — the main brain that talks to the user, plans work, and dispatches agents",
    ),
    Agent(
        name="pm_analyst",
        capabilities=["analyze", "plan", "decompose", "review", "prioritize"],
        tools=["filesystem", "web_search"],
        description="PM/Spec Analyst — analyzes specs, creates feature/task trees, reviews completed work",
    ),
    Agent(
        name="tester",
        capabilities=["test", "verify", "playwright", "validate", "e2e"],
        tools=["filesystem", "git", "shell", "browser"],
        description="Tester — verifies completed tasks using Playwright and assertions against success_criteria",
    ),
]


class AgentRegistry:
    def __init__(self, project_path: str):
        self.project_path = project_path

    def _conn(self):
        return get_connection(self.project_path)

    def register(self, agent: Agent) -> Agent:
        conn = self._conn()
        d = agent.to_dict()
        conn.execute(
            """INSERT OR REPLACE INTO agents (name, capabilities, tools, description)
               VALUES (?, ?, ?, ?)""",
            (d["name"], d["capabilities"], d["tools"], d["description"]),
        )
        conn.commit()
        conn.close()
        return agent

    def get(self, name: str) -> Agent | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
        conn.close()
        if row:
            return Agent.from_row(dict(row))
        return None

    def list_agents(self) -> list[Agent]:
        conn = self._conn()
        rows = conn.execute("SELECT * FROM agents").fetchall()
        conn.close()
        return [Agent.from_row(dict(r)) for r in rows]

    def register_defaults(self) -> None:
        for agent in DEFAULT_AGENTS:
            self.register(agent)

    def find_agent_for_task(self, task: Task) -> Agent | None:
        """Find the best agent for a task based on task type."""
        type_to_agent = {
            "feature": "fullstack_dev",
            "bug": "debugger",
            "research": "researcher",
            "agent_task": "fullstack_dev",
            "setup": "backend_dev",
            "test": "tester",
            "review": "pm_analyst",
        }
        agent_name = type_to_agent.get(task.type.value, "fullstack_dev")
        return self.get(agent_name)

    def create_execution(self, task_id: str, agent_name: str) -> TaskExecution:
        execution = TaskExecution(
            id=_new_id(),
            task_id=task_id,
            agent_name=agent_name,
            status=ExecutionStatus.PENDING,
        )
        conn = self._conn()
        d = execution.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        conn.execute(f"INSERT INTO task_executions ({cols}) VALUES ({placeholders})", list(d.values()))
        conn.commit()
        conn.close()
        return execution

    def update_execution(self, execution_id: str, **kwargs) -> None:
        if "status" in kwargs and isinstance(kwargs["status"], ExecutionStatus):
            kwargs["status"] = kwargs["status"].value
        conn = self._conn()
        sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
        conn.execute(f"UPDATE task_executions SET {sets} WHERE id = ?", list(kwargs.values()) + [execution_id])
        conn.commit()
        conn.close()

    def run_task(self, task: Task) -> TaskExecution:
        """Execute a task using the assigned or best-fit agent.

        This creates an execution record and marks it for processing.
        Actual execution is handled by the daemon or the external AI tool
        that queries the MCP interface.
        """
        agent_name = task.assigned_agent
        if not agent_name:
            agent = self.find_agent_for_task(task)
            agent_name = agent.name if agent else "fullstack_dev"

        execution = self.create_execution(task.id, agent_name)

        # Mark execution as running
        self.update_execution(
            execution.id,
            status=ExecutionStatus.RUNNING,
            started_at=_now(),
        )

        # Update task status
        from opero.core.tasks import TaskManager
        tm = TaskManager(self.project_path)
        tm.update(task.id, status=TaskStatus.IN_PROGRESS.value, assigned_agent=agent_name)

        return execution

    def complete_execution(self, execution_id: str, output: str = "", error: str = "") -> None:
        status = ExecutionStatus.COMPLETED if not error else ExecutionStatus.FAILED
        self.update_execution(
            execution_id,
            status=status,
            output=output,
            error=error,
            completed_at=_now(),
        )

        # Update the task too
        conn = self._conn()
        row = conn.execute("SELECT task_id FROM task_executions WHERE id = ?", (execution_id,)).fetchone()
        conn.close()

        if row:
            from opero.core.tasks import TaskManager
            tm = TaskManager(self.project_path)
            if status == ExecutionStatus.COMPLETED:
                tm.update(row["task_id"], status=TaskStatus.DONE.value, outputs=output)
            else:
                tm.update(row["task_id"], status=TaskStatus.BLOCKED.value)
