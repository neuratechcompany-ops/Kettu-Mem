"""
Ingestion Filter — filters content before it enters the memory pipeline.

Responsibility:
  1. Exclude system prompts, metadata, tool traces, reasoning, service JSON
  2. Normalize content
  3. Deduplicate against recent history
  4. Log reasons for filtered content (structured)

All values from config.

Usage:
  from extractors.ingestion_filter import IngestionFilter
  filt = IngestionFilter()
  filtered, reason = filt.should_ingest(content, role, event_type)
"""
import re
import hashlib
from typing import Optional, Tuple
from config import settings


class IngestionFilter:
    """
    Pre-ingestion content filter.

    Rejects:
    - System prompts (large blocks of instructions)
    - Tool call/tool output traces that are structural
    - Reasoning blocks (e.g. <｜end▁of▁thinking｜>,  thinking)
    - Service JSON blobs
    - Empty or whitespace-only content
    - Content below minimum length threshold
    - Near-duplicates of recently ingested content

    Returns (should_ingest: bool, reason: str).
    """

    # Patterns that indicate non-ingestible content
    _REJECT_CONTENT_PATTERNS = [
        # Empty/whitespace only
        (r'^\s*$', 'empty_content'),
        # System prompt fragments
        (r'^(?:You are|You\'re)\s+a\s+(?:helpful|AI|assistant)', 'system_prompt'),
        (r'^(?:Ты|Вы)\s+(?:—|–|-)\s*(?:полезный|AI|ассистент)', 'system_prompt'),
        # Tool output JSON structure (but not meaningful content)
        (r'^\s*[{[]\s*"[a-z_]+":\s*[{[]', 'json_blob'),
        # Reasoning traces
        (r'^\s*(?:<thinking>|<｜end▁of▁thinking｜>)', 'reasoning_trace'),
        (r'^\s*(?:Let me think|Let\'s think|I need to|First, I should)', 'reasoning_trace'),
        # Pure metadata
        (r'^\s*(?:{"results"|{"data"|{"items"):\s*[{[]', 'metadata_json'),
        # Error tracebacks
        (r'^\s*Traceback\s+\(most\s+recent\s+call\s+last\)', 'error_traceback'),
        (r'^\s*File\s+"[^"]+",\s+line\s+\d+', 'error_traceback'),
    ]

    # Patterns that indicate tool_call metadata (not worth embedding)
    _TOOL_METADATA_PATTERNS = [
        r'^web_search\(',
        r'^web_fetch\(',
        r'^read\(',
        r'^exec\(',
        r'^write\(',
        r'^image\(',
    ]

    def __init__(self):
        self._recent_hashes: list[str] = []
        self._max_recent = 100
        self._reject_log: list[dict] = []

    def should_ingest(self, content: str, role: str, event_type: str,
                      session_id: str = "") -> Tuple[bool, str]:
        """
        Determine if content should be ingested into the memory pipeline.

        Returns (should_ingest, reason).
        """
        if not content or not isinstance(content, str):
            return False, "empty_or_non_string"

        content = content.strip()

        # 1. Length check
        if len(content) < settings.ingest_min_content_length:
            return False, f"too_short:{len(content)}<{settings.ingest_min_content_length}"

        # 2. Never ingest system role
        if role == "system":
            return False, "system_role"

        # 3. Check tool call metadata
        if event_type in ("tool_call",):
            for pat in self._TOOL_METADATA_PATTERNS:
                if re.match(pat, content):
                    return False, f"tool_metadata:{event_type}"

        # 4. Check reject patterns
        for pattern, reason in self._REJECT_CONTENT_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                self._log_reject(reason, content[:50], session_id)
                return False, reason

        # 5. Near-duplicate check
        content_hash = self._hash_content(content)
        if content_hash in self._recent_hashes:
            return False, "duplicate"
        if settings.ingest_dedup_enabled:
            self._recent_hashes.append(content_hash)
            if len(self._recent_hashes) > self._max_recent:
                self._recent_hashes = self._recent_hashes[-self._max_recent:]

        return True, "ok"

    def _hash_content(self, content: str) -> str:
        """Normalize and hash content for dedup."""
        # Normalize: lowercase, strip punctuation, collapse whitespace
        normalized = re.sub(r'\s+', ' ', content.lower().strip())
        normalized = re.sub(r'[^\w\s]', '', normalized)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _log_reject(self, reason: str, preview: str, session_id: str):
        """Log rejected content for analysis."""
        self._reject_log.append({
            "reason": reason,
            "preview": preview,
            "session_id": session_id,
        })
        # Keep log bounded
        if len(self._reject_log) > 1000:
            self._reject_log = self._reject_log[-1000:]

    def get_reject_stats(self) -> dict:
        """Get statistics on rejected content."""
        from collections import Counter
        reason_counts = Counter(r["reason"] for r in self._reject_log)
        return {
            "total_rejected": len(self._reject_log),
            "by_reason": dict(reason_counts.most_common(10)),
        }

    def normalize(self, content: str) -> str:
        """Normalize content for ingestion."""
        # Trim whitespace
        content = content.strip()
        # Limit length
        if len(content) > settings.ingest_max_content_length:
            content = content[:settings.ingest_max_content_length]
        # Collapse multiple newlines
        content = re.sub(r'\n{3,}', '\n\n', content)
        return content
