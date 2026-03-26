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
import socket
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


# Default preferred ports (will find alternatives if taken)
DEFAULT_PORTS = {
    "frontend": 5173,
    "backend": 8000,
    "database": 54321,
}

# Service templates — port is filled in dynamically
SERVICE_TEMPLATES = {
    "frontend": {
        "name": "Frontend (React)",
        "cwd_subdir": "",
        "icon": "⚛️",
    },
    "backend": {
        "name": "Backend (FastAPI)",
        "cwd_subdir": "backend",
        "icon": "🐍",
    },
    "database": {
        "name": "Database (Supabase)",
        "cwd_subdir": "",
        "icon": "🗄️",
    },
}


def _is_port_free(port: int) -> bool:
    """Check if a port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _find_free_port(preferred: int, range_size: int = 100) -> int:
    """Find a free port starting from preferred, scanning upward."""
    for port in range(preferred, preferred + range_size):
        if _is_port_free(port):
            return port
    # Fallback: let OS pick
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServiceManager:
    """Manages project services (frontend, backend, database)."""

    def __init__(self, project_path: str):
        self.project_path = str(Path(project_path).resolve())
        self._pid_dir = Path(self.project_path) / ".opero" / "pids"
        self._pid_dir.mkdir(parents=True, exist_ok=True)
        self._ports_file = Path(self.project_path) / ".opero" / "ports.json"
        self._ports = self._load_ports()

    def _load_ports(self) -> dict:
        """Load assigned ports or assign new ones."""
        if self._ports_file.exists():
            try:
                return json.loads(self._ports_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        # Assign ports — find free ones
        ports = {}
        for svc, preferred in DEFAULT_PORTS.items():
            ports[svc] = _find_free_port(preferred)
        self._save_ports(ports)
        return ports

    def _save_ports(self, ports: dict):
        self._ports_file.write_text(json.dumps(ports, indent=2))

    def _get_service_config(self, service: str) -> dict:
        """Get full service config with assigned port."""
        tmpl = SERVICE_TEMPLATES.get(service, {})
        port = self._ports.get(service, DEFAULT_PORTS.get(service, 0))
        # Build start command with the assigned port
        if service == "frontend":
            start_cmd = ["npm", "run", "dev", "--", "--port", str(port)]
        elif service == "backend":
            start_cmd = ["uvicorn", "main:app", "--reload", "--port", str(port)]
        elif service == "database":
            start_cmd = ["supabase", "start"]
            port = 54321  # supabase uses fixed ports
        else:
            start_cmd = []
        return {
            **tmpl,
            "port": port,
            "start_cmd": start_cmd,
            "health_url": f"http://localhost:{port}",
        }

    def _pid_file(self, service: str) -> Path:
        return self._pid_dir / f"{service}.pid"

    def _save_pid(self, service: str, pid: int):
        self._pid_file(service).write_text(str(pid))

    def _read_pid(self, service: str) -> int | None:
        pf = self._pid_file(service)
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
                os.kill(pid, 0)
                return pid
            except (ValueError, ProcessLookupError, PermissionError):
                pf.unlink(missing_ok=True)
        return None

    def _clear_pid(self, service: str):
        self._pid_file(service).unlink(missing_ok=True)

    def is_running(self, service: str) -> bool:
        """Check if a service WE started is running (by PID only)."""
        return self._read_pid(service) is not None

    def status(self, service: str) -> dict:
        """Get status of a service."""
        svc = self._get_service_config(service)
        running = self.is_running(service)
        pid = self._read_pid(service)
        port = svc.get("port", 0)
        port_busy = not _is_port_free(port) if port else False
        return {
            "service": service,
            "name": svc.get("name", service),
            "port": port,
            "running": running,
            "port_busy": port_busy and not running,  # port taken by something else
            "pid": pid,
            "url": f"http://localhost:{port}" if running else None,
            "icon": svc.get("icon", ""),
        }

    def status_all(self) -> list[dict]:
        """Get status of all services."""
        return [self.status(s) for s in SERVICE_TEMPLATES]

    def start(self, service: str) -> dict:
        """Start a service."""
        if service not in SERVICE_TEMPLATES:
            return {"error": f"Unknown service: {service}"}
        if self.is_running(service):
            return self.status(service)

        svc = self._get_service_config(service)

        # Re-check port is still free, reassign if not
        if not _is_port_free(svc["port"]):
            new_port = _find_free_port(svc["port"])
            self._ports[service] = new_port
            self._save_ports(self._ports)
            svc = self._get_service_config(service)

        cwd = self.project_path
        if svc.get("cwd_subdir"):
            cwd = str(Path(self.project_path) / svc["cwd_subdir"])
            if not Path(cwd).exists():
                return {"error": f"Directory not found: {svc['cwd_subdir']}/"}

        log_file = self._pid_dir / f"{service}.log"

        try:
            with open(log_file, "a") as log:
                proc = subprocess.Popen(
                    svc["start_cmd"],
                    cwd=cwd,
                    stdout=log,
                    stderr=log,
                    env={**os.environ, "OPERO_PROJECT_PATH": self.project_path},
                    preexec_fn=os.setsid,
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
