"""Service manager — start/stop/monitor project services.

Manages the three standard services for every opero project:
- Frontend: React (Vite) on port 5173
- Backend: FastAPI on port 8000
- Database: Supabase local on port 54321
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

# Default service definitions
SERVICES = {
    "frontend": {
        "name": "Frontend (React)",
        "port": 5173,
        "start_cmd": ["npm", "run", "dev"],
        "cwd_subdir": "",  # project root
        "health_url": "http://localhost:5173",
        "icon": "⚛️",
    },
    "backend": {
        "name": "Backend (FastAPI)",
        "port": 8000,
        "start_cmd": ["uvicorn", "main:app", "--reload", "--port", "8000"],
        "cwd_subdir": "backend",
        "health_url": "http://localhost:8000/health",
        "icon": "🐍",
    },
    "database": {
        "name": "Database (Supabase)",
        "port": 54321,
        "start_cmd": ["supabase", "start"],
        "cwd_subdir": "",
        "health_url": "http://localhost:54321/rest/v1/",
        "icon": "🗄️",
    },
}


class ServiceManager:
    """Manages project services (frontend, backend, database)."""

    def __init__(self, project_path: str):
        self.project_path = str(Path(project_path).resolve())
        self._pid_dir = Path(self.project_path) / ".opero" / "pids"
        self._pid_dir.mkdir(parents=True, exist_ok=True)

    def _pid_file(self, service: str) -> Path:
        return self._pid_dir / f"{service}.pid"

    def _save_pid(self, service: str, pid: int):
        self._pid_file(service).write_text(str(pid))

    def _read_pid(self, service: str) -> int | None:
        pf = self._pid_file(service)
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
                # Check if process is still alive
                os.kill(pid, 0)
                return pid
            except (ValueError, ProcessLookupError, PermissionError):
                pf.unlink(missing_ok=True)
        return None

    def _clear_pid(self, service: str):
        self._pid_file(service).unlink(missing_ok=True)

    def is_running(self, service: str) -> bool:
        """Check if a service is running via its PID or port."""
        # Check PID first
        if self._read_pid(service):
            return True
        # Fallback: check if port is in use
        svc = SERVICES.get(service)
        if svc:
            try:
                urlopen(svc["health_url"], timeout=1)
                return True
            except (URLError, OSError):
                pass
        return False

    def status(self, service: str) -> dict:
        """Get status of a service."""
        svc = SERVICES.get(service, {})
        running = self.is_running(service)
        pid = self._read_pid(service)
        return {
            "service": service,
            "name": svc.get("name", service),
            "port": svc.get("port"),
            "running": running,
            "pid": pid,
            "url": f"http://localhost:{svc.get('port')}" if running else None,
            "icon": svc.get("icon", ""),
        }

    def status_all(self) -> list[dict]:
        """Get status of all services."""
        return [self.status(s) for s in SERVICES]

    def start(self, service: str) -> dict:
        """Start a service."""
        if service not in SERVICES:
            return {"error": f"Unknown service: {service}"}
        if self.is_running(service):
            return self.status(service)

        svc = SERVICES[service]
        cwd = self.project_path
        if svc["cwd_subdir"]:
            cwd = str(Path(self.project_path) / svc["cwd_subdir"])
            if not Path(cwd).exists():
                return {"error": f"Directory not found: {svc['cwd_subdir']}/"}

        # Build log file path
        log_file = self._pid_dir / f"{service}.log"

        try:
            with open(log_file, "a") as log:
                proc = subprocess.Popen(
                    svc["start_cmd"],
                    cwd=cwd,
                    stdout=log,
                    stderr=log,
                    env={**os.environ, "OPERO_PROJECT_PATH": self.project_path},
                    preexec_fn=os.setsid,  # new process group so we can kill cleanly
                )
            self._save_pid(service, proc.pid)
            return self.status(service)
        except FileNotFoundError as e:
            return {"error": f"Command not found: {svc['start_cmd'][0]}. {e}"}
        except Exception as e:
            return {"error": str(e)}

    def stop(self, service: str) -> dict:
        """Stop a service."""
        pid = self._read_pid(service)
        if pid:
            try:
                # Kill the whole process group
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            self._clear_pid(service)

        # Special handling for supabase
        if service == "database":
            try:
                subprocess.run(
                    ["supabase", "stop"],
                    cwd=self.project_path,
                    capture_output=True,
                    timeout=30,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        return self.status(service)

    def get_log(self, service: str, lines: int = 50) -> str:
        """Get recent log output for a service."""
        log_file = self._pid_dir / f"{service}.log"
        if not log_file.exists():
            return ""
        content = log_file.read_text()
        return "\n".join(content.splitlines()[-lines:])
