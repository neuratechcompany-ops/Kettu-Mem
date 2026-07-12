"""
Compression Engine v2 — triggers at 70% token budget.

Compresses recent events into structured summaries:
- Stage summary (human-readable narrative)
- Decisions log (extracted from markers)
- Open issues (pending questions/tasks)
- Artifact references (tool calls/outputs)
- Entity mentions (people, projects, tools)

Raw events remain in L3 archive — compression only adds summaries.
Supports incremental compression: compress oldest uncompressed range.

Strategy:
  1. Check if utilization > threshold (default 70%)
  2. If yes, compress oldest uncompressed third of session
  3. Store structured summary in SQLite
  4. Context builder uses summary instead of raw events
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompressionResult:
    """Output of one compression pass."""
    summary: str
    decisions: list[str]
    open_issues: list[str]
    artifact_refs: list[str]
    entities: list[str]
    compressed_range: tuple  # (start_step, end_step)
    events_compressed: int
    tokens_saved: int
    summary_id: str = ""


class CompressionEngine:
    """
    Rule-based (v1) + incremental (v2) compression of session events.

    Extracts:
    - Stage summary from assistant messages
    - Decisions from explicit markers
    - Open issues from questions and pending actions
    - Artifact references from tool calls
    - Entity mentions from content analysis
    """

    # Decision keywords (RU + EN)
    DECISION_MARKERS = [
        "решил", "решение", "decided", "decision",
        "согласовано", "утверждено", "выбрал вариант",
        "остановились на", "договорились", "принято",
        "resolved", "agreed", "final decision",
    ]

    ISSUE_MARKERS = [
        "todo", "нужно сделать", "осталось", "pending",
        "не решено", "вопрос", "на потом", "позже",
        "to do", "TBD", "backlog", "задача",
    ]

    ENTITY_PATTERNS = [
        # Projects
        (r"проект[а-я]*\s+[\"«](.+?)[\"»]", "project"),
        (r"project\s+[\"](.+?)[\"]", "project"),
        # Tools
        (r"(AmoCRM|Bitrix24|Яндекс\.Директ|Google\s*Analytics|Excel|Figma|Notion|Jira|Trello|Slack|Telegram|Miro)", "tool"),
        # People
        (r"@(\w+)", "person"),
    ]

    def __init__(self, sqlite_idx, l3_archive):
        self.sqlite = sqlite_idx
        self.l3 = l3_archive

    def compress_range(self, session_id: str, start_step: int, end_step: int) -> CompressionResult:
        """
        Compress events in [start_step, end_step] range.
        """
        events = self.l3.read_session(session_id)
        range_events = [e for e in events if start_step <= e["step_id"] <= end_step]

        if not range_events:
            return CompressionResult(
                summary="", decisions=[], open_issues=[],
                artifact_refs=[], entities=[],
                compressed_range=(start_step, end_step),
                events_compressed=0, tokens_saved=0,
            )

        decisions = self._extract_decisions(range_events)
        open_issues = self._extract_open_issues(range_events)
        artifact_refs = self._extract_artifact_refs(range_events)
        entities = self._extract_entities(range_events)
        summary = self._build_summary(range_events, decisions, open_issues, entities)

        # Store in SQLite
        summary_id = self.sqlite.add_summary(
            session_id, start_step, end_step, "compression", summary
        )

        return CompressionResult(
            summary=summary,
            decisions=decisions,
            open_issues=open_issues,
            artifact_refs=artifact_refs,
            entities=entities,
            compressed_range=(start_step, end_step),
            events_compressed=len(range_events),
            tokens_saved=self._estimate_tokens_saved(range_events, summary),
            summary_id=summary_id,
        )


        """
        Check if compression is needed and compress oldest uncompressed range.

        Args:
            session_id: Session to compress
            threshold_pct: Trigger threshold
            token_count_fn: Function returning current token count (optional)

        Returns CompressionResult if compression happened, None otherwise.
        """
        # Check if we have uncompressed events
    def incremental_compress(
        self,
        session_id: str,
        threshold_pct: float = 0.70,
        token_count_fn=None,
    ) -> Optional[CompressionResult]:
        """Check if compression is needed and compress oldest uncompressed range.

        Args:
            session_id: Session to compress
            threshold_pct: Trigger threshold (default 0.70)
            token_count_fn: Optional function returning current token count

        Returns:
            Optional[CompressionResult]: Compression result or None if no compression performed.
        """
        summaries = self.sqlite.get_summaries(session_id)
        events = self.l3.read_session(session_id)

        if not events:
            return None

        # Determine which steps are already compressed
        compressed_steps = set()
        for s in summaries:
            compressed_steps.update(range(s["start_step"], s["end_step"] + 1))

        uncompressed = [e for e in events if e["step_id"] not in compressed_steps]

        # Need enough events to make compression worthwhile
        if len(uncompressed) < 10:
            return None

        # Optional token‑budget threshold
        if token_count_fn:
            actual = token_count_fn()
            budget = 32000
            if (actual / budget) < threshold_pct:
                return None

        # Compress the oldest half of uncompressed events
        mid = len(uncompressed) // 2
        to_compress = uncompressed[:mid]
        start = to_compress[0]["step_id"]
        end = to_compress[-1]["step_id"]
 
        return self.compress_range(session_id, start, end)

    def _extract_decisions(self, events: list[dict]) -> list[str]:
        decisions = []
        for e in events:
            if e["type"] != "message":
                continue
            text = e["content"].lower()
            for kw in self.DECISION_MARKERS:
                if kw in text:
                    decisions.append(e["content"][:300])
                    break
        # Deduplicate
        seen = set()
        unique = []
        for d in decisions:
            key = d[:60]
            if key not in seen:
                seen.add(key)
                unique.append(d)
        return unique

    def _extract_open_issues(self, events: list[dict]) -> list[str]:
        issues = []
        for e in events:
            if e["type"] != "message":
                continue
            text = e["content"].lower()
            for marker in self.ISSUE_MARKERS:
                if marker in text:
                    issues.append(e["content"][:300])
                    break
        return issues

    def _extract_artifact_refs(self, events: list[dict]) -> list[str]:
        refs = []
        for e in events:
            if e["type"] in ("tool_call", "tool_output"):
                refs.append(f"[{e['type']}] step {e['step_id']}: {e['content'][:200]}")
            elif e["type"] == "patch":
                refs.append(f"[patch] step {e['step_id']}: {e['content'][:200]}")
        return refs

    def _extract_entities(self, events: list[dict]) -> list[str]:
        """Extract named entities from messages."""
        import re
        entities = set()
        for e in events:
            if e["type"] != "message":
                continue
            content = e["content"]
            for pattern, _ in self.ENTITY_PATTERNS:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for m in matches:
                    entities.add(m)
        return sorted(entities)[:20]

    def _build_summary(self, events: list[dict], decisions: list[str],
                        issues: list[str], entities: list[str]) -> str:
        user_msgs = [e for e in events if e["role"] == "user" and e["type"] == "message"]
        assistant_msgs = [e for e in events if e["role"] == "assistant" and e["type"] == "message"]
        errors = [e for e in events if e["type"] == "error"]

        parts = []
        parts.append(f"# Stage Summary (steps {events[0]['step_id']}-{events[-1]['step_id']})")
        parts.append(f"Events: {len(events)} total ({len(user_msgs)} user, {len(assistant_msgs)} assistant, {len(errors)} errors)")

        # User intents
        if user_msgs:
            parts.append("\n## User Requests")
            for m in user_msgs[:5]:
                parts.append(f"- {m['content'][:200]}")

        # Key outputs
        if assistant_msgs:
            parts.append("\n## Key Outputs")
            for m in assistant_msgs[-3:]:
                parts.append(f"- {m['content'][:200]}")

        # Decisions
        if decisions:
            parts.append(f"\n## Decisions ({len(decisions)})")
            for d in decisions[:5]:
                parts.append(f"- {d[:200]}")

        # Entities
        if entities:
            parts.append(f"\n## Entities Mentioned")
            parts.append(f"- {', '.join(entities[:15])}")

        # Open issues
        if issues:
            parts.append(f"\n## Open Issues ({len(issues)})")
            for i in issues[:5]:
                parts.append(f"- {i[:200]}")

        # Errors
        if errors:
            parts.append(f"\n## Errors ({len(errors)})")
            for e in errors[:3]:
                parts.append(f"- {e['content'][:200]}")

        return "\n".join(parts)

    def _estimate_tokens_saved(self, events: list[dict], summary: str) -> int:
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
        except (ImportError, ValueError, ModuleNotFoundError):
            return len(events) * 50
        raw_tokens = sum(len(enc.encode(e.get("content", ""))) for e in events)
        summary_tokens = len(enc.encode(summary))
        return max(0, raw_tokens - summary_tokens)
