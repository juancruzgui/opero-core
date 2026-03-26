"""Opero Core engine — the central coordinator."""

from __future__ import annotations

import os
from pathlib import Path

from opero.core.models import Project, Task, TaskType
from opero.core.projects import ProjectManager
from opero.core.tasks import TaskManager
from opero.core.memory import MemoryManager, MemoryEntry, MemoryType
from opero.agents.registry import AgentRegistry
from opero.git_integration.git_ops import GitManager
from opero.db.schema import init_db


class OperoEngine:
    """Central coordinator for Opero Core operations."""

    def __init__(self, project_path: str | None = None):
        self.project_path = project_path or os.getcwd()
        self.projects = ProjectManager(self.project_path)
        self.tasks = TaskManager(self.project_path)
        self.memory = MemoryManager(self.project_path)
        self.agents = AgentRegistry(self.project_path)
        self.git = GitManager(self.project_path)

    def is_initialized(self) -> bool:
        opero_dir = Path(self.project_path) / ".opero"
        return opero_dir.exists() and (opero_dir / "opero.db").exists()

    def initialize(self, name: str = "", description: str = "") -> Project:
        """Full initialization flow for a new project.

        One command bootstraps everything:
        1. Git repo (if not already)
        2. SQLite database
        3. Default agents
        4. Initial tasks
        5. Project memory
        6. Claude Code integration (CLAUDE.md, hooks, MCP)
        """
        opero_dir = Path(self.project_path) / ".opero"
        opero_dir.mkdir(parents=True, exist_ok=True)

        # Init git if needed
        self.git.init_repo()

        # Init database
        init_db(self.project_path)

        # Register default agents
        self.agents.register_defaults()

        # Create project
        project_name = name or Path(self.project_path).name
        project = Project(
            name=project_name,
            description=description,
            path=self.project_path,
        )
        project = self.projects.create(project)

        # Create initial tasks
        self.projects.create_initial_tasks(project.id)

        # Set initial memory
        self.projects.set_memory(project.id, "status", "initialized", "system")
        self.projects.set_memory(project.id, "name", project_name, "identity")

        # Ensure project .gitignore covers all opero-generated files
        self._update_project_gitignore()

        # Wire up Claude Code automatically
        from opero.integrations.claude_code import ClaudeCodeIntegration
        claude = ClaudeCodeIntegration(self.project_path)
        claude.write_claude_md()
        claude.install_hooks()
        claude.install_mcp()

        return project

    def _update_project_gitignore(self) -> None:
        """Add opero entries to the project's .gitignore."""
        gitignore_path = Path(self.project_path) / ".gitignore"

        existing = ""
        if gitignore_path.exists():
            existing = gitignore_path.read_text()

        entries = [
            "# Opero Core (auto-added)",
            ".opero/",
            ".opero-core/",
            ".claude/",
            "CLAUDE.md",
        ]

        to_add = []
        for entry in entries:
            if entry not in existing:
                to_add.append(entry)

        if to_add:
            separator = "\n" if existing and not existing.endswith("\n") else ""
            addition = separator + "\n".join(to_add) + "\n"
            gitignore_path.write_text(existing + addition)

    def status(self) -> dict:
        """Get full system status."""
        project = self.projects.get_by_path()
        if not project:
            return {"initialized": False}

        all_tasks = self.tasks.list_tasks(project_id=project.id)
        agents = self.agents.list_agents()

        task_summary = {
            "total": len(all_tasks),
            "todo": sum(1 for t in all_tasks if t.status.value == "todo"),
            "in_progress": sum(1 for t in all_tasks if t.status.value == "in_progress"),
            "done": sum(1 for t in all_tasks if t.status.value == "done"),
            "blocked": sum(1 for t in all_tasks if t.status.value == "blocked"),
        }

        return {
            "initialized": True,
            "project": project.to_dict(),
            "tasks": task_summary,
            "agents": [a.name for a in agents],
            "git": {
                "branch": self.git.current_branch(),
                "has_changes": self.git.has_changes(),
            },
        }

    def sync(self) -> dict:
        """Sync git state and return update summary."""
        project = self.projects.get_by_path()
        if not project:
            return {"error": "Project not initialized"}

        synced_commits = self.git.sync_commits(project.id)

        # Update tasks based on commit references
        for commit in synced_commits:
            if commit.task_id:
                task = self.tasks.get(commit.task_id)
                if task and task.status.value == "in_progress":
                    self.tasks.update(commit.task_id, status="done")

        return {
            "commits_synced": len(synced_commits),
            "branch": self.git.current_branch(),
        }
