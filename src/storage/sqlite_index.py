"""
SQLite Metadata Index — fast relational lookups.

Tables:
- sessions: session_id, project_id, created_at, status, token_count
- events: event_id, session_id, step_id, timestamp, role, type, content_hash, refs_json, meta_json
- summaries: summary_id, session_id, start_step, end_step, type, content, created_at
- artifacts: artifact_id, session_id, type, name, path, created_at
- vector_map: vec_id, event_id, session_id, faiss_id, chunk_text, created_at

Indexes on: session_id, project_id, type, timestamp, role.

WAL checkpoint policy:
- Automatic PASSIVE checkpoint every CHECKPOINT_INTERVAL writes or
  CHECKPOINT_TIME seconds to prevent unbounded WAL growth.
"""
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

# WAL checkpoint throttling
CHECKPOINT_INTERVAL = 1000  # writes between checkpoints
CHECKPOINT_TIME = 60        # seconds between checkpoints


class SQLiteMetadataIndex:
    """Fast relational metadata on top of L3 archive."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._write_count = 0
        self._last_checkpoint = time.time()
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            project_id TEXT,
            workspace TEXT DEFAULT 'default',
            agent TEXT DEFAULT 'main',
            user_id TEXT DEFAULT 'default',
            created_at REAL,
            status TEXT DEFAULT 'active',
            total_events INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            step_id INTEGER,
            timestamp REAL,
            role TEXT,
            type TEXT,
            content_preview TEXT,
            content_hash TEXT,
            refs_json TEXT,
            meta_json TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE IF NOT EXISTS summaries (
            summary_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            start_step INTEGER,
            end_step INTEGER,
            type TEXT,
            content TEXT,
            created_at REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            type TEXT,
            name TEXT,
            path TEXT,
            created_at REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE IF NOT EXISTS vector_map (
            vec_id TEXT PRIMARY KEY,
            event_id TEXT,
            session_id TEXT NOT NULL,
            faiss_id INTEGER,
            chunk_text TEXT,
            created_at REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
        CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_role ON events(role);
        CREATE INDEX IF NOT EXISTS idx_summaries_session ON summaries(session_id);
        CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);
        CREATE INDEX IF NOT EXISTS idx_vector_map_session ON vector_map(session_id);
        CREATE INDEX IF NOT EXISTS idx_vector_map_faiss ON vector_map(faiss_id);
        """)

    def ensure_session(self, session_id: str, project_id: str = None,
                       workspace: str = "default", agent: str = "main",
                       user_id: str = "default"):
        self.conn.execute(
            """INSERT OR IGNORE INTO sessions
               (session_id, project_id, workspace, agent, user_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, project_id, workspace, agent, user_id, time.time())
        )
        self.conn.commit()
        self._maybe_checkpoint()

    def index_event(self, event_id: str, session_id: str, step_id: int,
                    *, role: str, type: str, content: str,
                    refs: list = None, meta: dict = None,
                    timestamp: float = None):
        self.ensure_session(session_id)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        self.conn.execute(
            """INSERT OR IGNORE INTO events
               (event_id, session_id, step_id, timestamp, role, type, content_preview, content_hash, refs_json, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, session_id, step_id, timestamp or time.time(),
             role, type, content[:500], content_hash,
             json.dumps(refs or []), json.dumps(meta or {}))
        )
        self.conn.execute(
            "UPDATE sessions SET total_events = total_events + 1 WHERE session_id = ?",
            (session_id,)
        )
        self.conn.commit()
        self._maybe_checkpoint()

    def add_summary(self, session_id: str, start_step: int, end_step: int,
                    summary_type: str, content: str) -> str:
        summary_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO summaries (summary_id, session_id, start_step, end_step, type, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (summary_id, session_id, start_step, end_step, summary_type, content, time.time())
        )
        self.conn.commit()
        self._maybe_checkpoint()
        return summary_id

    def add_artifact(self, session_id: str, artifact_type: str, name: str, path: str) -> str:
        artifact_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO artifacts (artifact_id, session_id, type, name, path, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (artifact_id, session_id, artifact_type, name, path, time.time())
        )
        self.conn.commit()
        self._maybe_checkpoint()
        return artifact_id

    def map_vector(self, event_id: str, session_id: str, faiss_id: int, chunk_text: str) -> str:
        vec_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO vector_map (vec_id, event_id, session_id, faiss_id, chunk_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (vec_id, event_id, session_id, faiss_id, chunk_text, time.time())
        )
        self.conn.commit()
        self._maybe_checkpoint()
        return vec_id

    def _maybe_checkpoint(self):
        """Throttled WAL checkpoint to prevent unbounded WAL growth.

        Runs a PASSIVE checkpoint every CHECKPOINT_INTERVAL writes
        or every CHECKPOINT_TIME seconds.
        """
        self._write_count += 1
        now = time.time()
        if (self._write_count >= CHECKPOINT_INTERVAL or
                (now - self._last_checkpoint) >= CHECKPOINT_TIME):
            try:
                self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                self._write_count = 0
                self._last_checkpoint = now
            except Exception:
                pass  # checkpoint is advisory, never fail

    # --- Queries ---

    def get_recent_events(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY step_id DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_events_by_type(self, session_id: str, event_type: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE session_id = ? AND type = ? ORDER BY step_id DESC LIMIT ?",
            (session_id, event_type, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_summaries(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM summaries WHERE session_id = ? ORDER BY start_step",
            (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_info(self, session_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else {}

    def get_faiss_ids_for_session(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM vector_map WHERE session_id = ? ORDER BY faiss_id",
            (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()
