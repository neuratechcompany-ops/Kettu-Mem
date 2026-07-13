"""
L3 Verbatim Archive — append-only JSONL storage.

Design:
- Immutable: append only, no update/delete.
- Each line = one event (message, tool_call, tool_output, error, patch).
- Fields: session_id, step_id, timestamp, role, type, refs, content, meta.
- refs: list of (layer, id) tuples for cross-referencing.
- Hard payload cap: events > MAX_PAYLOAD_SIZE are stored in artifact store;
  JSONL line contains {"artifact_ref": "path/to/file"} instead of raw content.
"""

import json
import os
import time
import uuid
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)

# Maximum payload size kept inline in JSONL (100 KB).
# Larger payloads are offloaded to the artifact store.
MAX_PAYLOAD_SIZE = 100 * 1024  # 100 KB


class L3VerbatimArchive:
    """Append-only JSONL archive for all session events."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._artifact_dir = self.data_dir / "_artifacts"
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, str] = {}  # session_id -> filepath

    def _session_file(self, session_id: str) -> str:
        if session_id not in self._files:
            fpath = self.data_dir / f"session-{session_id}.jsonl"
            self._files[session_id] = str(fpath)
        return self._files[session_id]

    def _store_artifact(self, session_id: str, event_id: str, content: str) -> str:
        """
        Store oversized payload in the artifact store.
        Returns the relative artifact path to embed in the JSONL reference.
        """
        session_dir = self._artifact_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        artifact_name = f"{event_id}.txt"
        artifact_path = session_dir / artifact_name
        artifact_path.write_text(content, encoding="utf-8")
        rel_path = f"_artifacts/{session_id}/{artifact_name}"
        logger.info(
            "l3_artifact_stored",
            session_id=session_id,
            event_id=event_id,
            size_bytes=len(content.encode("utf-8")),
            path=rel_path,
        )
        return rel_path

    def read_artifact(self, artifact_ref: str) -> str:
        """
        Read back an artifact from the store.
        Paths are relative to the L3 data_dir.
        """
        full_path = self.data_dir / artifact_ref
        if not full_path.exists():
            logger.warning("l3_artifact_missing", ref=artifact_ref)
            return ""
        return full_path.read_text(encoding="utf-8")

    def record_event(
        self,
        session_id: str,
        step_id: int,
        *,
        role: str,
        type: str,
        content: str,
        refs: list = None,
        meta: dict = None,
        timestamp: float = None,
    ) -> str:
        """
        Append one event to the session log.

        If content exceeds MAX_PAYLOAD_SIZE (100 KB), it is stored as
        an artifact file and the JSONL line contains a reference instead.

        Returns the event_id (UUID).
        """
        event_id = uuid.uuid4().hex[:12]
        content_size = len(content.encode("utf-8"))

        if content_size > MAX_PAYLOAD_SIZE:
            artifact_ref = self._store_artifact(session_id, event_id, content)
            logger.info(
                "l3_payload_capped",
                session_id=session_id,
                event_id=event_id,
                original_size=content_size,
                cap=MAX_PAYLOAD_SIZE,
                artifact_ref=artifact_ref,
            )
            event = {
                "event_id": event_id,
                "session_id": session_id,
                "step_id": step_id,
                "timestamp": timestamp or time.time(),
                "role": role,
                "type": type,
                "artifact_ref": artifact_ref,
                "content_size": content_size,
                "refs": refs or [],
                "meta": meta or {},
            }
        else:
            event = {
                "event_id": event_id,
                "session_id": session_id,
                "step_id": step_id,
                "timestamp": timestamp or time.time(),
                "role": role,
                "type": type,
                "content": content,
                "refs": refs or [],
                "meta": meta or {},
            }

        fpath = self._session_file(session_id)
        with open(fpath, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event_id

    def read_session(self, session_id: str) -> list[dict]:
        """Read all events for a session (returns list).
        Corrupted JSON lines are skipped with a warning.
        Events with artifact_ref have their content loaded from the artifact store.
        """
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
                    ev = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(
                        "l3_corrupted_line_skipped",
                        session_id=session_id,
                        line_num=line_num,
                        error=str(e),
                    )
                    continue

                # Resolve artifact reference if present
                if "artifact_ref" in ev and "content" not in ev:
                    ev["content"] = self.read_artifact(ev["artifact_ref"])

                events.append(ev)
        return events

    def read_events_by_type(self, session_id: str, event_type: str) -> list[dict]:
        """Read events filtered by type."""
        return [e for e in self.read_session(session_id) if e["type"] == event_type]

    def get_event_count(self, session_id: str) -> int:
        """Count events in session (including those in artifact store)."""
        fpath = self._session_file(session_id)
        if not os.path.exists(fpath):
            return 0
        count = 0
        with open(fpath) as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    def get_size_bytes(self, session_id: str) -> int:
        """Get total size: JSONL file + all artifact files."""
        fpath = self._session_file(session_id)
        total = os.path.getsize(fpath) if os.path.exists(fpath) else 0
        artifact_session_dir = self._artifact_dir / session_id
        if artifact_session_dir.exists():
            for af in artifact_session_dir.iterdir():
                total += af.stat().st_size
        return total
