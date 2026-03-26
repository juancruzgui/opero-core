"""Structured memory system for Opero Core with vector search.

Provides semantic memory for AI tools (Claude, Cursor, etc.) with:
- Typed memory entries (decisions, architecture, learnings, etc.)
- Vector embeddings for semantic search
- Context assembly for AI sessions
- Memory linking to tasks, commits, and files
- Importance ranking and active/superseded lifecycle
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections import Counter
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from opero.core.models import _new_id, _now
from opero.db.schema import get_connection


class MemoryType(str, Enum):
    DECISION = "decision"
    ARCHITECTURE = "architecture"
    LEARNING = "learning"
    CONTEXT = "context"
    PREFERENCE = "preference"
    CONVENTION = "convention"
    ISSUE = "issue"
    PLAN = "plan"


@dataclass
class MemoryEntry:
    id: str = field(default_factory=_new_id)
    project_id: str = ""
    type: MemoryType = MemoryType.CONTEXT
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = "user"       # user, claude, cursor, system, git
    source_ref: str = ""       # task id, commit sha, file path, etc.
    importance: int = 3        # 1=critical, 5=low
    superseded_by: Optional[str] = None
    active: bool = True
    accessed_at: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["tags"] = json.dumps(self.tags)
        d["active"] = 1 if self.active else 0
        return d

    @classmethod
    def from_row(cls, row: dict) -> MemoryEntry:
        tags = row.get("tags", "[]")
        try:
            tags_list = json.loads(tags) if tags else []
        except (json.JSONDecodeError, TypeError):
            tags_list = []
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            type=MemoryType(row["type"]),
            title=row["title"],
            content=row["content"],
            tags=tags_list,
            source=row.get("source", "user"),
            source_ref=row.get("source_ref", ""),
            importance=row.get("importance", 3),
            superseded_by=row.get("superseded_by"),
            active=bool(row.get("active", 1)),
            accessed_at=row.get("accessed_at"),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )

    def search_text(self) -> str:
        """Combined text for search indexing."""
        parts = [self.title, self.content, self.type.value]
        parts.extend(self.tags)
        return " ".join(parts).lower()


@dataclass
class MemoryLink:
    id: str = field(default_factory=_new_id)
    memory_id: str = ""
    linked_type: str = ""    # task, commit, memory, file
    linked_id: str = ""
    relationship: str = "related"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ContextSnapshot:
    id: str = field(default_factory=_new_id)
    project_id: str = ""
    tool: str = ""           # claude, cursor, openclaw
    session_id: str = ""
    summary: str = ""
    active_task_ids: list[str] = field(default_factory=list)
    memory_ids_used: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)


# ---------------------------------------------------------------------------
# TF-IDF vector engine — pure Python, no external deps
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens, strip punctuation."""
    return re.findall(r"[a-z0-9_]+", text.lower())


class TFIDFEngine:
    """Lightweight TF-IDF vector engine stored in SQLite.

    Each memory entry gets a sparse vector stored as a JSON dict of
    {term: tfidf_weight}. Search computes cosine similarity against a
    query vector built the same way.

    This is intentionally simple — no numpy, no external services.
    Suitable for thousands of memory entries in a local project.
    """

    def __init__(self, project_path: str):
        self.project_path = project_path
        self._ensure_table()

    def _conn(self):
        return get_connection(self.project_path)

    def _ensure_table(self):
        conn = self._conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_vectors (
                memory_id TEXT PRIMARY KEY,
                vector TEXT NOT NULL,
                tokens TEXT NOT NULL,
                FOREIGN KEY (memory_id) REFERENCES memory_entries(id)
            )
        """)
        conn.commit()
        conn.close()

    def index(self, memory_id: str, text: str) -> None:
        """Index a memory entry's text as a TF-IDF vector."""
        tokens = _tokenize(text)
        if not tokens:
            return

        # TF (term frequency)
        tf = Counter(tokens)
        total = len(tokens)
        tf_norm = {t: c / total for t, c in tf.items()}

        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO memory_vectors (memory_id, vector, tokens) VALUES (?, ?, ?)",
            (memory_id, json.dumps(tf_norm), json.dumps(tokens)),
        )
        conn.commit()
        conn.close()

    def remove(self, memory_id: str) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory_id,))
        conn.commit()
        conn.close()

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Search for memory entries similar to query. Returns (memory_id, score)."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        query_tf = Counter(query_tokens)
        query_total = len(query_tokens)
        query_vec = {t: c / query_total for t, c in query_tf.items()}

        conn = self._conn()
        rows = conn.execute("SELECT memory_id, vector FROM memory_vectors").fetchall()
        conn.close()

        if not rows:
            return []

        # IDF from corpus
        doc_count = len(rows)
        doc_freq: dict[str, int] = Counter()
        doc_vecs: list[tuple[str, dict]] = []

        for row in rows:
            vec = json.loads(row["vector"])
            doc_vecs.append((row["memory_id"], vec))
            for term in vec:
                doc_freq[term] += 1

        # Apply IDF to query vector
        query_tfidf = {}
        for term, tf_val in query_vec.items():
            idf = math.log((doc_count + 1) / (doc_freq.get(term, 0) + 1)) + 1
            query_tfidf[term] = tf_val * idf

        # Score each document
        results = []
        for mem_id, doc_vec in doc_vecs:
            # Apply IDF to doc vector
            doc_tfidf = {}
            for term, tf_val in doc_vec.items():
                idf = math.log((doc_count + 1) / (doc_freq.get(term, 0) + 1)) + 1
                doc_tfidf[term] = tf_val * idf

            # Cosine similarity
            dot = sum(query_tfidf.get(t, 0) * doc_tfidf.get(t, 0) for t in set(query_tfidf) | set(doc_tfidf))
            mag_q = math.sqrt(sum(v * v for v in query_tfidf.values()))
            mag_d = math.sqrt(sum(v * v for v in doc_tfidf.values()))

            if mag_q > 0 and mag_d > 0:
                score = dot / (mag_q * mag_d)
            else:
                score = 0.0

            if score > 0.01:
                results.append((mem_id, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def reindex_all(self, memories: list[MemoryEntry]) -> int:
        """Reindex all memory entries. Returns count indexed."""
        count = 0
        for mem in memories:
            self.index(mem.id, mem.search_text())
            count += 1
        return count


# ---------------------------------------------------------------------------
# Memory Manager
# ---------------------------------------------------------------------------

class MemoryManager:
    """Full memory system with CRUD, search, linking, and context assembly."""

    def __init__(self, project_path: str):
        self.project_path = project_path
        self.vectors = TFIDFEngine(project_path)

    def _conn(self):
        return get_connection(self.project_path)

    # --- CRUD ---

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        """Store a new memory entry and index it for search."""
        if not entry.id:
            entry.id = _new_id()
        entry.created_at = _now()
        entry.updated_at = _now()

        conn = self._conn()
        d = entry.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        conn.execute(f"INSERT INTO memory_entries ({cols}) VALUES ({placeholders})", list(d.values()))
        conn.commit()
        conn.close()

        # Index for vector search
        self.vectors.index(entry.id, entry.search_text())

        return entry

    def get(self, memory_id: str) -> MemoryEntry | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM memory_entries WHERE id = ?", (memory_id,)).fetchone()
        if row:
            # Update accessed_at
            conn.execute("UPDATE memory_entries SET accessed_at = ? WHERE id = ?", (_now(), memory_id))
            conn.commit()
        conn.close()
        return MemoryEntry.from_row(dict(row)) if row else None

    def update(self, memory_id: str, **kwargs) -> MemoryEntry | None:
        entry = self.get(memory_id)
        if not entry:
            return None

        kwargs["updated_at"] = _now()

        if "tags" in kwargs and isinstance(kwargs["tags"], list):
            kwargs["tags"] = json.dumps(kwargs["tags"])
        if "type" in kwargs and isinstance(kwargs["type"], MemoryType):
            kwargs["type"] = kwargs["type"].value
        if "active" in kwargs:
            kwargs["active"] = 1 if kwargs["active"] else 0

        conn = self._conn()
        sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
        conn.execute(f"UPDATE memory_entries SET {sets} WHERE id = ?", list(kwargs.values()) + [memory_id])
        conn.commit()
        conn.close()

        # Re-index
        updated = self.get(memory_id)
        if updated:
            self.vectors.index(memory_id, updated.search_text())

        return updated

    def supersede(self, old_id: str, new_entry: MemoryEntry) -> MemoryEntry:
        """Create a new memory that supersedes an old one."""
        new_entry = self.store(new_entry)
        self.update(old_id, active=False, superseded_by=new_entry.id)
        return new_entry

    def delete(self, memory_id: str) -> bool:
        conn = self._conn()
        cur = conn.execute("DELETE FROM memory_entries WHERE id = ?", (memory_id,))
        conn.execute("DELETE FROM memory_links WHERE memory_id = ?", (memory_id,))
        conn.commit()
        conn.close()
        self.vectors.remove(memory_id)
        return cur.rowcount > 0

    # --- Query ---

    def list_memories(
        self,
        project_id: str,
        memory_type: MemoryType | None = None,
        active_only: bool = True,
        source: str | None = None,
        tags: list[str] | None = None,
        min_importance: int | None = None,
    ) -> list[MemoryEntry]:
        conn = self._conn()
        query = "SELECT * FROM memory_entries WHERE project_id = ?"
        params: list = [project_id]

        if active_only:
            query += " AND active = 1"
        if memory_type:
            query += " AND type = ?"
            params.append(memory_type.value)
        if source:
            query += " AND source = ?"
            params.append(source)
        if min_importance:
            query += " AND importance <= ?"
            params.append(min_importance)

        query += " ORDER BY importance ASC, updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()

        results = [MemoryEntry.from_row(dict(r)) for r in rows]

        # Tag filter (post-query since tags are JSON)
        if tags:
            tag_set = set(t.lower() for t in tags)
            results = [m for m in results if tag_set & set(t.lower() for t in m.tags)]

        return results

    def search(self, project_id: str, query: str, top_k: int = 10) -> list[tuple[MemoryEntry, float]]:
        """Semantic search over memory entries using TF-IDF vectors."""
        vector_results = self.vectors.search(query, top_k=top_k * 2)  # over-fetch then filter

        results = []
        for mem_id, score in vector_results:
            entry = self.get(mem_id)
            if entry and entry.project_id == project_id and entry.active:
                results.append((entry, score))
            if len(results) >= top_k:
                break

        return results

    # --- Linking ---

    def link(self, memory_id: str, linked_type: str, linked_id: str, relationship: str = "related") -> MemoryLink:
        link_obj = MemoryLink(
            memory_id=memory_id,
            linked_type=linked_type,
            linked_id=linked_id,
            relationship=relationship,
        )
        conn = self._conn()
        d = link_obj.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        conn.execute(f"INSERT INTO memory_links ({cols}) VALUES ({placeholders})", list(d.values()))
        conn.commit()
        conn.close()
        return link_obj

    def get_links(self, memory_id: str) -> list[MemoryLink]:
        conn = self._conn()
        rows = conn.execute("SELECT * FROM memory_links WHERE memory_id = ?", (memory_id,)).fetchall()
        conn.close()
        return [MemoryLink(**dict(r)) for r in rows]

    def find_by_link(self, linked_type: str, linked_id: str) -> list[MemoryEntry]:
        """Find all memories linked to a given entity."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT me.* FROM memory_entries me
               JOIN memory_links ml ON me.id = ml.memory_id
               WHERE ml.linked_type = ? AND ml.linked_id = ?
               AND me.active = 1""",
            (linked_type, linked_id),
        ).fetchall()
        conn.close()
        return [MemoryEntry.from_row(dict(r)) for r in rows]

    # --- Context Assembly ---

    def build_context(
        self,
        project_id: str,
        query: str | None = None,
        task_id: str | None = None,
        max_entries: int = 20,
        tool: str = "claude",
    ) -> dict:
        """Assemble a context package for an AI tool session.

        This is the main entry point for Claude/Cursor/etc. to get
        everything they need to understand the project state.

        Returns a dict with:
        - project: basic project info
        - decisions: active architectural decisions
        - conventions: coding conventions and preferences
        - relevant: semantically relevant memories (if query provided)
        - task_context: memories linked to the current task
        - recent: recently updated memories
        """
        from opero.core.projects import ProjectManager
        from opero.core.tasks import TaskManager

        pm = ProjectManager(self.project_path)
        tm = TaskManager(self.project_path)

        project = pm.get(project_id)
        if not project:
            return {"error": "Project not found"}

        context: dict = {
            "project": {
                "name": project.name,
                "description": project.description,
                "tech_stack": project.tech_stack,
                "architecture": project.architecture_notes,
            },
            "decisions": [],
            "conventions": [],
            "architecture": [],
            "relevant": [],
            "task_context": [],
            "recent": [],
        }

        # Always include high-importance decisions and conventions
        decisions = self.list_memories(project_id, memory_type=MemoryType.DECISION, min_importance=2)
        context["decisions"] = [{"title": m.title, "content": m.content, "importance": m.importance} for m in decisions[:10]]

        conventions = self.list_memories(project_id, memory_type=MemoryType.CONVENTION)
        context["conventions"] = [{"title": m.title, "content": m.content} for m in conventions[:10]]

        architecture = self.list_memories(project_id, memory_type=MemoryType.ARCHITECTURE)
        context["architecture"] = [{"title": m.title, "content": m.content} for m in architecture[:5]]

        # Semantic search if query provided
        memory_ids_used = []
        if query:
            search_results = self.search(project_id, query, top_k=max_entries)
            context["relevant"] = [
                {"title": m.title, "content": m.content, "type": m.type.value, "score": round(s, 3)}
                for m, s in search_results
            ]
            memory_ids_used.extend(m.id for m, _ in search_results)

        # Task-linked memories
        if task_id:
            task_memories = self.find_by_link("task", task_id)
            context["task_context"] = [
                {"title": m.title, "content": m.content, "type": m.type.value}
                for m in task_memories
            ]
            memory_ids_used.extend(m.id for m in task_memories)

            # Also include the task itself
            task = tm.get(task_id)
            if task:
                context["current_task"] = {
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "type": task.type.value,
                    "status": task.status.value,
                    "success_criteria": task.success_criteria,
                }

        # Recent memories (last 5 updated)
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM memory_entries WHERE project_id = ? AND active = 1 ORDER BY updated_at DESC LIMIT 5",
            (project_id,),
        ).fetchall()
        conn.close()
        context["recent"] = [
            {"title": MemoryEntry.from_row(dict(r)).title, "type": MemoryEntry.from_row(dict(r)).type.value}
            for r in rows
        ]

        # Save context snapshot
        snapshot = ContextSnapshot(
            project_id=project_id,
            tool=tool,
            summary=query or "general context request",
            active_task_ids=[task_id] if task_id else [],
            memory_ids_used=memory_ids_used,
        )
        conn = self._conn()
        conn.execute(
            "INSERT INTO context_snapshots (id, project_id, tool, session_id, summary, active_task_ids, memory_ids_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (snapshot.id, snapshot.project_id, snapshot.tool, snapshot.session_id, snapshot.summary,
             json.dumps(snapshot.active_task_ids), json.dumps(snapshot.memory_ids_used), snapshot.created_at),
        )
        conn.commit()
        conn.close()

        return context

    def reindex(self, project_id: str) -> int:
        """Rebuild the entire search index for a project."""
        memories = self.list_memories(project_id, active_only=False)
        return self.vectors.reindex_all(memories)
