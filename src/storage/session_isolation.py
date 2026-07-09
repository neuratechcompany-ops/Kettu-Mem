"""
Session Isolation — hierarchical namespace for memory partitioning.

Hierarchy:
  project → workspace → agent → user → session

Each level is fully isolated:
- Sessions can only see their own events
- Optional cross-session search within same agent/user/workspace/project
- No global variables — all state is scoped

Usage:
  from storage.session_isolation import SessionNamespace
  ns = SessionNamespace(project="myproject", workspace="default", agent="main", user="user1")
  ns.session_path()  # → "myproject/default/main/user1/session5"
"""
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class SessionNamespace:
    """
    Hierarchical namespace for session isolation.

    Path format: {project}/{workspace}/{agent}/{user}/{session_id}
    """
    project: str = "default"
    workspace: str = "default"
    agent: str = "main"
    user: str = "default"
    session_id: str = ""

    def path(self) -> str:
        """Build hierarchical path."""
        return f"{self.project}/{self.workspace}/{self.agent}/{self.user}/{self.session_id}"

    def parent_path(self) -> str:
        """Path without session_id."""
        return f"{self.project}/{self.workspace}/{self.agent}/{self.user}"

    def ancestor_paths(self) -> list[str]:
        """List all ancestor paths (project, workspace, agent, user)."""
        parts = [self.project, self.workspace, self.agent, self.user]
        paths = []
        current = ""
        for p in parts:
            current = f"{current}/{p}" if current else p
            paths.append(current)
        return paths

    def matches(self, other: "SessionNamespace") -> bool:
        """Check if this namespace matches another (same project/workspace/agent/user)."""
        return (self.project == other.project and
                self.workspace == other.workspace and
                self.agent == other.agent and
                self.user == other.user)

    def is_ancestor_of(self, other: "SessionNamespace") -> bool:
        """Check if this namespace is ancestor of another."""
        return other.path().startswith(self.path())

    @classmethod
    def from_path(cls, path: str) -> "SessionNamespace":
        """Parse from hierarchical path."""
        parts = path.strip("/").split("/")
        return cls(
            project=parts[0] if len(parts) > 0 else "default",
            workspace=parts[1] if len(parts) > 1 else "default",
            agent=parts[2] if len(parts) > 2 else "main",
            user=parts[3] if len(parts) > 3 else "default",
            session_id=parts[4] if len(parts) > 4 else "",
        )

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "workspace": self.workspace,
            "agent": self.agent,
            "user": self.user,
            "session_id": self.session_id,
        }


class SessionIsolation:
    """
    Manages session isolation across all layers.

    Responsibilities:
    - Track active sessions per namespace
    - Enforce isolation boundary
    - Provide cross-session search within same namespace
    - Clean up expired sessions
    """

    def __init__(self, sqlite_index):
        self.sqlite = sqlite_index
        self._active_sessions: dict[str, SessionNamespace] = {}

    def register_session(self, namespace: SessionNamespace):
        """Register an active session."""
        path = namespace.path()
        self._active_sessions[path] = namespace

        # Update SQLite with namespace info
        self.sqlite.conn.execute(
            """UPDATE sessions SET
               project_id = ?,
               workspace = ?,
               agent = ?,
               user_id = ?
               WHERE session_id = ?""",
            (namespace.project, namespace.workspace,
             namespace.agent, namespace.user,
             namespace.session_id)
        )
        self.sqlite.conn.commit()

    def unregister_session(self, namespace: SessionNamespace):
        """Unregister a session."""
        path = namespace.path()
        self._active_sessions.pop(path, None)

    def get_sessions_in_namespace(self, namespace: SessionNamespace,
                                  include_descendants: bool = False) -> list[str]:
        """
        Get sessions in the same namespace (or descendants).

        Args:
            namespace: The namespace to search in
            include_descendants: If True, include child sessions too
        """
        if include_descendants:
            return [
                sid for sid, ns in self._active_sessions.items()
                if namespace.is_ancestor_of(ns) or ns.matches(namespace)
            ]
        else:
            parent = namespace.parent_path()
            return [
                sid for sid, ns in self._active_sessions.items()
                if ns.parent_path() == parent
            ]

    def get_events_across_sessions(self, namespace: SessionNamespace,
                                   limit: int = 50) -> list[dict]:
        """
        Get recent events across all sessions in the same namespace.

        Cross-session retrieval — finds related context from sibling sessions.
        """
        sessions = self.get_sessions_in_namespace(namespace)
        if not sessions:
            return []

        placeholders = ",".join("?" * len(sessions))
        rows = self.sqlite.conn.execute(
            f"""SELECT * FROM events
                WHERE session_id IN ({placeholders})
                ORDER BY timestamp DESC
                LIMIT ?""",
            sessions + [limit]
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_mem0_facts_in_namespace(self, namespace: SessionNamespace,
                                    limit: int = 20) -> list[dict]:
        """
        Get Mem0 facts from all sessions in namespace.
        Useful for cross-session knowledge sharing.
        """
        sessions = self.get_sessions_in_namespace(namespace)
        if not sessions:
            return []

        placeholders = ",".join("?" * len(sessions))
        rows = self.sqlite.conn.execute(
            f"""SELECT * FROM mem0_facts
                WHERE source_session IN ({placeholders})
                ORDER BY confidence DESC, access_count DESC
                LIMIT ?""",
            sessions + [limit]
        ).fetchall()
        return [dict(r) for r in rows]

    def cleanup_expired(self, max_age_hours: int = 72):
        """Remove sessions inactive for > N hours."""
        import time
        cutoff = time.time() - max_age_hours * 3600
        self.sqlite.conn.execute(
            "DELETE FROM sessions WHERE created_at < ? AND status = 'active'",
            (cutoff,)
        )
        self.sqlite.conn.commit()
        # Also clean in-memory registry
        removed = []
        for path, ns in list(self._active_sessions.items()):
            if path not in self._active_sessions:  # already removed from db
                removed.append(path)
        for path in removed:
            self._active_sessions.pop(path, None)

    def get_stats(self) -> dict:
        """Get session isolation statistics."""
        by_project = {}
        for ns in self._active_sessions.values():
            by_project.setdefault(ns.project, 0)
            by_project[ns.project] += 1
        return {
            "active_sessions": len(self._active_sessions),
            "by_project": by_project,
        }
