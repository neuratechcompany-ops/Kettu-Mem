"""
L3 Verbatim Archive — append-only JSONL storage.

Design:
- Immutable: append only, no update/delete.
- Each line = one event (message, tool_call, tool_output, error, patch).
- Fields: session_id, step_id, timestamp, role, type, refs, content, meta.
- refs: list of (layer, id) tuples for cross-referencing.
"""
import json
import os
import time
import uuid
from pathlib import Path


class L3VerbatimArchive:
    """Append-only JSONL archive for all session events."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, str] = {}  # session_id -> filepath

    def _session_file(self, session_id: str) -> str:
        if session_id not in self._files:
            fpath = self.data_dir / f"session-{session_id}.jsonl"
            self._files[session_id] = str(fpath)
        return self._files[session_id]

    def record_event(self, session_id: str, step_id: int, *,
                     role: str, type: str, content: str,
                     refs: list = None, meta: dict = None,
                     timestamp: float = None) -> str:
        """
        Append one event to the session log.

        Returns the event_id (UUID).
        """
        event = {
            "event_id": uuid.uuid4().hex[:12],
            "session_id": session_id,
            "step_id": step_id,
            "timestamp": timestamp or time.time(),
            "role": role,        # system, user, assistant, tool
            "type": type,        # message, tool_call, tool_output, error, summary, decision, patch
            "content": content,
            "refs": refs or [],  # [(layer, id), ...]
            "meta": meta or {},
        }
        fpath = self._session_file(session_id)
        with open(fpath, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event["event_id"]

    def read_session(self, session_id: str) -> list[dict]:
        """Read all events for a session (returns list).
        Corrupted JSON lines are skipped with a warning."""
        fpath = self._session_file(session_id)
        if not os.path.exists(fpath):
            return []
        events = []
        with open(fpath) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    import structlog
                    logger = structlog.get_logger("l3_verbatim")
                    logger.warning(
                        "l3_corrupted_line_skipped",
                        session_id=session_id,
                        line_num=line_num,
                        error=str(e),
                    )
        return events

    def read_events_by_type(self, session_id: str, event_type: str) -> list[dict]:
        """Read events filtered by type."""
        return [e for e in self.read_session(session_id) if e["type"] == event_type]

    def get_event_count(self, session_id: str) -> int:
        """Count events in session."""
        fpath = self._session_file(session_id)
        if not os.path.exists(fpath):
            return 0
        with open(fpath) as f:
            return sum(1 for _ in f)

    def get_size_bytes(self, session_id: str) -> int:
        """Get file size in bytes."""
        fpath = self._session_file(session_id)
        if not os.path.exists(fpath):
            return 0
        return os.path.getsize(fpath)
