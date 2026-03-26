"""Daemon / event loop for Opero Core — file watching, git monitoring, task evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from opero.core.engine import OperoEngine
from opero.db.schema import get_connection

logger = logging.getLogger("opero.daemon")


class OperoEvent:
    FILE_CHANGED = "file_changed"
    GIT_COMMIT = "git_commit"
    TASK_READY = "task_ready"
    TASK_COMPLETED = "task_completed"


class OperoDaemon:
    """Background intelligence layer that monitors and reacts to project changes."""

    def __init__(self, project_path: str | None = None):
        self.project_path = project_path or os.getcwd()
        self.engine = OperoEngine(self.project_path)
        self.running = False
        self._last_git_head: str | None = None
        self._file_hashes: dict[str, str] = {}
        self._poll_interval = 3  # seconds

    def _emit_event(self, event_type: str, payload: dict) -> None:
        """Store an event in the database for processing."""
        project = self.engine.projects.get_by_path()
        if not project:
            return
        conn = get_connection(self.project_path)
        conn.execute(
            "INSERT INTO events (project_id, event_type, payload) VALUES (?, ?, ?)",
            (project.id, event_type, json.dumps(payload)),
        )
        conn.commit()
        conn.close()
        logger.info(f"Event: {event_type} — {payload}")

    def _get_git_head(self) -> str | None:
        try:
            result = self.engine.git._run("rev-parse", "HEAD", check=False)
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    def _check_git_changes(self) -> None:
        """Detect new commits and sync them."""
        current_head = self._get_git_head()
        if current_head and current_head != self._last_git_head:
            if self._last_git_head is not None:
                self._emit_event(OperoEvent.GIT_COMMIT, {"sha": current_head})
                self.engine.sync()
            self._last_git_head = current_head

    def _hash_file(self, filepath: str) -> str | None:
        try:
            with open(filepath, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except (OSError, PermissionError):
            return None

    def _scan_files(self) -> dict[str, str]:
        """Scan project files and return hash map (excluding .opero, .git, __pycache__)."""
        hashes = {}
        exclude = {".opero", ".git", "__pycache__", "node_modules", ".venv", "venv"}
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in dirs if d not in exclude]
            for fname in files:
                fpath = os.path.join(root, fname)
                h = self._hash_file(fpath)
                if h:
                    rel = os.path.relpath(fpath, self.project_path)
                    hashes[rel] = h
        return hashes

    def _check_file_changes(self) -> None:
        """Detect file system changes."""
        current_hashes = self._scan_files()

        # New or modified files
        for fpath, fhash in current_hashes.items():
            old_hash = self._file_hashes.get(fpath)
            if old_hash is None:
                self._emit_event(OperoEvent.FILE_CHANGED, {"file": fpath, "action": "created"})
            elif old_hash != fhash:
                self._emit_event(OperoEvent.FILE_CHANGED, {"file": fpath, "action": "modified"})

        # Deleted files
        for fpath in set(self._file_hashes.keys()) - set(current_hashes.keys()):
            self._emit_event(OperoEvent.FILE_CHANGED, {"file": fpath, "action": "deleted"})

        self._file_hashes = current_hashes

    def _check_task_state(self) -> None:
        """Evaluate task state and emit events for ready tasks."""
        project = self.engine.projects.get_by_path()
        if not project:
            return

        next_task = self.engine.tasks.get_next_task(project.id)
        if next_task:
            self._emit_event(OperoEvent.TASK_READY, {
                "task_id": next_task.id,
                "title": next_task.title,
                "type": next_task.type.value,
            })

    async def _loop(self) -> None:
        """Main event loop."""
        logger.info(f"Opero daemon started — watching {self.project_path}")

        # Initial scan
        self._last_git_head = self._get_git_head()
        self._file_hashes = self._scan_files()

        tick = 0
        while self.running:
            try:
                # Every tick: check git
                self._check_git_changes()

                # Every 3 ticks: check files
                if tick % 3 == 0:
                    self._check_file_changes()

                # Every 5 ticks: evaluate tasks
                if tick % 5 == 0:
                    self._check_task_state()

                tick += 1
                await asyncio.sleep(self._poll_interval)

            except Exception as e:
                logger.error(f"Daemon error: {e}")
                await asyncio.sleep(self._poll_interval)

    def start(self) -> None:
        """Start the daemon event loop."""
        if not self.engine.is_initialized():
            logger.error("Project not initialized. Run 'opero init' first.")
            return

        self.running = True

        def _handle_signal(sig, frame):
            logger.info("Shutdown signal received")
            self.running = False

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        asyncio.run(self._loop())

    def stop(self) -> None:
        self.running = False


def run_daemon(project_path: str | None = None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [opero] %(message)s",
        datefmt="%H:%M:%S",
    )
    daemon = OperoDaemon(project_path)
    daemon.start()
