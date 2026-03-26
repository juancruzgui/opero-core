"""Git integration layer for Opero Core."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from opero.core.models import GitCommit, _now
from opero.db.schema import get_connection


@dataclass
class DiffSummary:
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: list[str] = None

    def __post_init__(self):
        if self.files is None:
            self.files = []


class GitManager:
    def __init__(self, project_path: str):
        self.project_path = project_path

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", self.project_path, *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def is_repo(self) -> bool:
        result = self._run("rev-parse", "--is-inside-work-tree", check=False)
        return result.returncode == 0

    def init_repo(self) -> bool:
        if self.is_repo():
            return False
        self._run("init")
        return True

    def current_branch(self) -> str:
        result = self._run("branch", "--show-current", check=False)
        return result.stdout.strip() or "main"

    def status(self) -> str:
        result = self._run("status", "--porcelain", check=False)
        return result.stdout.strip()

    def has_changes(self) -> bool:
        return bool(self.status())

    def add_all(self) -> None:
        self._run("add", "-A")

    def commit(self, message: str, task_id: str | None = None) -> str | None:
        if not self.has_changes():
            return None

        if task_id:
            message = f"[{task_id}] {message}"

        self.add_all()
        self._run("commit", "-m", message)
        result = self._run("rev-parse", "HEAD")
        return result.stdout.strip()

    def get_log(self, count: int = 20) -> list[dict]:
        result = self._run(
            "log", f"-{count}", "--format=%H|%an|%s|%aI", check=False
        )
        if not result.stdout.strip():
            return []

        commits = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "sha": parts[0],
                    "author": parts[1],
                    "message": parts[2],
                    "date": parts[3],
                })
        return commits

    def get_diff(self, ref: str = "HEAD~1") -> str:
        result = self._run("diff", ref, check=False)
        return result.stdout

    def get_diff_summary(self, ref: str = "HEAD~1") -> DiffSummary:
        result = self._run("diff", "--stat", ref, check=False)
        summary = DiffSummary()
        if not result.stdout.strip():
            return summary

        lines = result.stdout.strip().split("\n")
        for line in lines[:-1]:
            filename = line.split("|")[0].strip()
            if filename:
                summary.files.append(filename)

        last_line = lines[-1] if lines else ""
        nums = re.findall(r"(\d+)", last_line)
        if len(nums) >= 1:
            summary.files_changed = int(nums[0])
        if len(nums) >= 2:
            summary.insertions = int(nums[1])
        if len(nums) >= 3:
            summary.deletions = int(nums[2])

        return summary

    def list_branches(self) -> list[str]:
        result = self._run("branch", "--format=%(refname:short)", check=False)
        return [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]

    def extract_task_id_from_message(self, message: str) -> str | None:
        match = re.match(r"\[(\w+)\]", message)
        return match.group(1) if match else None

    def sync_commits(self, project_id: str) -> list[GitCommit]:
        """Sync recent git commits into the database."""
        conn = get_connection(self.project_path)
        log = self.get_log(50)
        synced = []

        for entry in log:
            existing = conn.execute(
                "SELECT sha FROM git_commits WHERE sha = ?", (entry["sha"],)
            ).fetchone()
            if existing:
                continue

            task_id = self.extract_task_id_from_message(entry["message"])
            commit = GitCommit(
                sha=entry["sha"],
                project_id=project_id,
                message=entry["message"],
                author=entry["author"],
                branch=self.current_branch(),
                task_id=task_id,
                created_at=entry["date"],
            )
            d = commit.to_dict()
            cols = ", ".join(d.keys())
            placeholders = ", ".join(["?"] * len(d))
            conn.execute(f"INSERT INTO git_commits ({cols}) VALUES ({placeholders})", list(d.values()))
            synced.append(commit)

        conn.commit()
        conn.close()
        return synced
