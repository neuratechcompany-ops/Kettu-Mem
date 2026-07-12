"""Persistent error ring buffer — survives restarts."""

import json
import threading
import time
from pathlib import Path
from typing import Optional


class ErrorRingBuffer:
    """Thread-safe ring buffer for runtime errors. Persists to disk."""

    def __init__(self, path: Path, max_entries: int = 50):
        self._path = path
        self._max = max_entries
        self._lock = threading.Lock()
        self._errors: list[dict] = []
        self._load()

    def record(
        self,
        component: str,
        message: str,
        error_type: str = "runtime",
        request_id: str = "",
        recovered: bool = False,
    ):
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "component": component,
            "error_type": error_type,
            "message": message,
            "request_id": request_id[:40],
            "recovered": recovered,
            "restart_detected": not self._errors,
        }
        with self._lock:
            self._errors.append(entry)
            if len(self._errors) > self._max:
                self._errors = self._errors[-self._max :]
            self._save()

    @property
    def last_error(self) -> Optional[dict]:
        with self._lock:
            return self._errors[-1] if self._errors else None

    @property
    def recent_errors(self) -> list[dict]:
        with self._lock:
            return list(self._errors[-10:])

    def clear(self):
        with self._lock:
            self._errors.clear()
            self._save()

    def _save(self):
        try:
            self._path.write_text(json.dumps(self._errors, indent=2))
        except Exception:
            pass

    def _load(self):
        try:
            if self._path.exists():
                self._errors = json.loads(self._path.read_text())[-self._max :]
        except Exception:
            pass
