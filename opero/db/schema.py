"""Database schema and initialization for Opero Core."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    path TEXT NOT NULL,
    tech_stack TEXT,
    architecture_notes TEXT,
    decisions TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL CHECK(type IN ('feature', 'bug', 'research', 'agent_task', 'setup')),
    status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo', 'in_progress', 'done', 'blocked')),
    priority INTEGER NOT NULL DEFAULT 3 CHECK(priority BETWEEN 1 AND 5),
    dependencies TEXT,
    assigned_agent TEXT,
    inputs TEXT,
    outputs TEXT,
    success_criteria TEXT,
    parent_task_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (parent_task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    capabilities TEXT NOT NULL,
    tools TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_executions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    output TEXT,
    error TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (agent_name) REFERENCES agents(name)
);

CREATE TABLE IF NOT EXISTS git_commits (
    sha TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    message TEXT,
    author TEXT,
    branch TEXT,
    task_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS project_memory (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, key)
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('decision', 'architecture', 'learning', 'context', 'preference', 'convention', 'issue', 'plan')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    source TEXT DEFAULT 'user',
    source_ref TEXT,
    importance INTEGER NOT NULL DEFAULT 3 CHECK(importance BETWEEN 1 AND 5),
    superseded_by TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    accessed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (superseded_by) REFERENCES memory_entries(id)
);

CREATE TABLE IF NOT EXISTS memory_links (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    linked_type TEXT NOT NULL CHECK(linked_type IN ('task', 'commit', 'memory', 'file')),
    linked_id TEXT NOT NULL,
    relationship TEXT DEFAULT 'related',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (memory_id) REFERENCES memory_entries(id)
);

CREATE TABLE IF NOT EXISTS context_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    session_id TEXT,
    summary TEXT,
    active_task_ids TEXT DEFAULT '[]',
    memory_ids_used TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    processed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS claude_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    session_id TEXT,
    tool_name TEXT NOT NULL,
    action TEXT,
    file_path TEXT,
    task_id TEXT,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS claude_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'stopped')),
    current_task_id TEXT,
    last_heartbeat TIMESTAMP,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMP
);
"""


def get_db_path(project_path: str) -> Path:
    return Path(project_path) / ".opero" / "opero.db"


def init_db(project_path: str) -> sqlite3.Connection:
    db_path = get_db_path(project_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_connection(project_path: str) -> sqlite3.Connection:
    db_path = get_db_path(project_path)
    if not db_path.exists():
        return init_db(project_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
