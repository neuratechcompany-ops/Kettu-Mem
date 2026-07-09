"""
Context Builder — single entry point for prompt assembly (v2).

Features:
- Token budget management using tiktoken
- Weighted priority layers with minimum guarantees
- Three strategies: tight, normal, generous
- Mem0 long-term facts integration
- Output reserve (configurable %)
- Never passes raw archive — only selected slices
- Tool schema filtering: only active tools included
- Compression-aware: uses summaries instead of raw when available
"""

import json
import time
from dataclasses import dataclass, field
from enum import Enum


class BudgetStrategy(Enum):
    TIGHT = "tight"       # 16K budget, minimal context
    NORMAL = "normal"     # 32K budget, balanced
    GENEROUS = "generous" # 64K budget, rich context


STRATEGY_CONFIGS = {
    BudgetStrategy.TIGHT:    {"budget": 16000, "reserve": 0.15, "recent": 15, "semantic": 5, "summaries": 3},
    BudgetStrategy.NORMAL:   {"budget": 32000, "reserve": 0.20, "recent": 30, "semantic": 10, "summaries": 5},
    BudgetStrategy.GENEROUS: {"budget": 64000, "reserve": 0.20, "recent": 50, "semantic": 15, "summaries": 10},
}


@dataclass
class ContextConfig:
    """Configuration for context assembly."""
    token_budget: int = 32000
    output_reserve_pct: float = 0.20
    recent_events_limit: int = 30
    max_semantic_chunks: int = 10
    max_mem0_facts: int = 10
    max_archive_snippets: int = 5
    max_summaries: int = 5
    model_name: str = "gpt-4"

    # Minimum token guarantees per layer (0 = no minimum)
    min_recent_tokens: int = 200
    min_system_tokens: int = 100
    min_semantic_tokens: int = 100

    # Section weights (higher = more budget allocation)
    weight_system: float = 1.0
    weight_recent: float = 3.0
    weight_semantic: float = 2.0
    weight_mem0: float = 2.5
    weight_summaries: float = 2.0
    weight_archive: float = 0.5
    weight_tools: float = 1.5

    @classmethod
    def from_strategy(cls, strategy: BudgetStrategy) -> "ContextConfig":
        cfg = STRATEGY_CONFIGS[strategy]
        return cls(
            token_budget=cfg["budget"],
            output_reserve_pct=cfg["reserve"],
            recent_events_limit=cfg["recent"],
            max_semantic_chunks=cfg["semantic"],
            max_summaries=cfg["summaries"],
        )


@dataclass
class ContextSlice:
    """A piece of context with token count."""
    name: str
    content: str
    tokens: int = 0
    priority: int = 0   # lower = higher priority
    weight: float = 1.0 # weight for budget allocation
    min_tokens: int = 0  # minimum token guarantee


@dataclass
class ToolSchema:
    """Filtered tool schema for context."""
    name: str
    description: str
    parameters: dict = None


class ContextBuilder:
    """
    Assembles the prompt context under token budget.

    Priority layers (0=highest):
      0: system prompt + active tools
      1: recent session events
      2: Mem0 long-term facts
      2: semantic search results
      2: session summaries
      3: archive references

    Usage:
        builder = ContextBuilder(config)
        builder.set_system("You are...")
        builder.set_recent_events(events)
        builder.set_mem0_facts(facts)
        builder.set_semantic_results(results)
        builder.set_summaries(summaries)
        builder.set_tools([ToolSchema(...)])
        prompt, stats = builder.build()
    """

    def __init__(self, config: ContextConfig = None):
        self.config = config or ContextConfig()
        self._slices: list[ContextSlice] = []
        self._tokenizer = None
        self._init_tokenizer()

    def _init_tokenizer(self):
        try:
            import tiktoken
            self._tokenizer = tiktoken.encoding_for_model(self.config.model_name)
        except (ImportError, ValueError, KeyError, ModuleNotFoundError):
            import tiktoken
            self._tokenizer = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text))

    @property
    def working_budget(self) -> int:
        reserve = int(self.config.token_budget * self.config.output_reserve_pct)
        return self.config.token_budget - reserve

    # ── Setters ──────────────────────────────────────────

    def set_system(self, system_prompt: str):
        content = system_prompt
        self._add_slice("system", content, priority=0,
                        weight=self.config.weight_system,
                        min_tokens=self.config.min_system_tokens)

    def set_tools(self, tools: list[ToolSchema]):
        """Add tool schemas. Filtrered by relevance."""
        if not tools:
            return
        lines = ["## Available Tools"]
        for t in tools:
            params_str = ""
            if t.parameters:
                params_str = json.dumps(t.parameters, ensure_ascii=False)
            lines.append(f"- **{t.name}**: {t.description}")
            if params_str:
                lines.append(f"  params: {params_str}")
        content = "\n".join(lines)
        self._add_slice("tools", content, priority=1,
                        weight=self.config.weight_tools)

    def set_recent_events(self, events: list[dict]):
        """Set recent session events (priority 1). Tool outputs are EXCLUDED (archive only)."""
        lines = []
        for e in events:
            role = e.get("role", "?")
            etype = e.get("type", "?")
            content = e.get("content", "") or e.get("content_preview", "")
            step = e.get("step_id", "?")

            # NEVER include raw tool outputs in prompt
            if etype == "tool_output":
                continue

            if etype == "tool_call":
                lines.append(f"[{step}] {role} → tool_call: {content[:200]}")
            elif etype == "error":
                lines.append(f"[{step}] ⚠ error: {content[:300]}")
            else:
                lines.append(f"[{step}] {role}: {content[:500]}")

            refs = e.get("refs", []) or e.get("refs_json", "")
            if refs:
                if isinstance(refs, str):
                    try:
                        refs = json.loads(refs)
                    except (json.JSONDecodeError, TypeError):
                        refs = []
                if refs:
                    ref_str = ", ".join(
                        f"{r[0]}:{r[1]}" if isinstance(r, list) else str(r)
                        for r in refs
                    )
                    lines.append(f"  ↳ refs: {ref_str}")

        text = "## Recent Session Events\n" + "\n".join(lines[-self.config.recent_events_limit:])
        self._add_slice("recent_events", text, priority=1,
                        weight=self.config.weight_recent,
                        min_tokens=self.config.min_recent_tokens)

    def set_mem0_facts(self, facts: list[dict]):
        """
        Set Mem0 long-term facts (priority 2).

        Each fact: {type, content, confidence, source, entities}
        Types: preference, decision, fact, entity, project
        """
        if not facts:
            return

        by_type = {}
        for f in facts:
            t = f.get("type", "fact")
            by_type.setdefault(t, []).append(f)

        lines = ["## Long-term Memory"]
        type_labels = {
            "preference": "💚 Preferences",
            "decision": "✅ Decisions",
            "fact": "📌 Facts",
            "entity": "🔗 Entities",
            "project": "📁 Projects",
        }

        for ftype, label in type_labels.items():
            items = by_type.get(ftype, [])[:self.config.max_mem0_facts]
            if not items:
                continue
            lines.append(f"\n### {label}")
            for item in items:
                conf = item.get("confidence", 1.0)
                conf_str = f" [{conf:.0%}]" if conf < 1.0 else ""
                lines.append(f"- {item['content']}{conf_str}")
                entities = item.get("entities", [])
                if entities:
                    lines.append(f"  entities: {', '.join(entities)}")

        if len(lines) == 1:  # Only header
            return
        text = "\n".join(lines)
        self._add_slice("mem0", text, priority=2,
                        weight=self.config.weight_mem0)

    def set_semantic_results(self, results: list[dict]):
        """Set FAISS semantic search results (priority 2)."""
        if not results:
            return

        lines = ["## Relevant Memories (semantic search)"]
        for i, r in enumerate(results[:self.config.max_semantic_chunks]):
            score_pct = r.get("score", 0) * 100
            text = r.get("chunk_text", "")[:400]
            lines.append(f"[{i+1}] score={score_pct:.0f}% | {text}")
        text = "\n".join(lines)
        self._add_slice("semantic", text, priority=2,
                        weight=self.config.weight_semantic,
                        min_tokens=self.config.min_semantic_tokens)

    def set_summaries(self, summaries: list[dict]):
        """Set stage summaries (priority 2)."""
        if not summaries:
            return
        lines = ["## Session Summaries"]
        for s in summaries[:self.config.max_summaries]:
            s_type = s.get("type", "summary")
            s_start = s.get("start_step", "?")
            s_end = s.get("end_step", "?")
            content = s.get("content", "")[:500]
            lines.append(f"- [{s_type}] steps {s_start}-{s_end}:")
            lines.append(f"  {content}")
        text = "\n".join(lines)
        self._add_slice("summaries", text, priority=2,
                        weight=self.config.weight_summaries)

    def set_archive_refs(self, refs: list[dict]):
        """Set L3 archive references (priority 3) — pointers, not full content."""
        if not refs:
            return
        lines = ["## Archive References"]
        for r in refs[:self.config.max_archive_snippets]:
            lines.append(f"- [{r.get('type','?')}] step {r.get('step_id','?')}: {r.get('content','')[:300]}")
        text = "\n".join(lines)
        self._add_slice("archive_refs", text, priority=3,
                        weight=self.config.weight_archive)

    # ── Assembly ─────────────────────────────────────────

    def _add_slice(self, name: str, content: str, priority: int,
                   weight: float = 1.0, min_tokens: int = 0):
        tokens = self._count_tokens(content)
        self._slices.append(ContextSlice(
            name=name, content=content, tokens=tokens,
            priority=priority, weight=weight, min_tokens=min_tokens,
        ))

    def _weighted_assembly(self) -> str:
        """
        Assemble context with weighted budget allocation.

        Algorithm:
        1. Allocate minimum guarantees to each slice
        2. Distribute remaining budget by weight
        3. Truncate slices proportionally if over budget
        """
        working = self.working_budget
        self._slices.sort(key=lambda s: (s.priority, -s.weight))

        # Phase 1: allocate minimums
        parts = []
        used = 0
        deferred = []

        for s in self._slices:
            min_t = min(s.min_tokens, s.tokens)
            if used + min_t <= working:
                if s.tokens <= min_t:
                    # Fits entirely
                    parts.append(s.content)
                    used += s.tokens
                else:
                    # Need truncation
                    ratio = min_t / max(s.tokens, 1)
                    keep_chars = int(len(s.content) * ratio)
                    parts.append(s.content[:keep_chars])
                    used += min_t
                    deferred.append(s)
            else:
                deferred.append(s)

        # Phase 2: distribute remaining by weight
        remaining = working - used
        if remaining > 0 and deferred:
            total_weight = sum(s.weight for s in deferred)
            if total_weight == 0:
                total_weight = 1

            for s in deferred:
                alloc = int(remaining * (s.weight / total_weight))
                alloc = min(alloc, s.tokens)
                if alloc <= 0:
                    continue

                if s.tokens <= alloc:
                    # Already in deferred, check if content was already added
                    # Use exact match (not substring) to avoid false positives
                    already_added = any(s.content == p for p in parts)
                    if not already_added:
                        parts.append(s.content)
                        used += s.tokens
                else:
                    ratio = alloc / max(s.tokens, 1)
                    keep_chars = int(len(s.content) * ratio)
                    if keep_chars > 20:
                        parts.append(s.content[:keep_chars] + "\n[...truncated...]")
                        used += alloc

        return "\n\n".join(parts)

    def build(self) -> tuple[str, dict]:
        """Build the final prompt context. Returns (prompt, stats)."""
        prompt = self._weighted_assembly()
        stats = self.get_stats()
        return prompt, stats

    def get_stats(self) -> dict:
        """Return budget usage statistics."""
        used = sum(s.tokens for s in self._slices)
        return {
            "total_budget": self.config.token_budget,
            "working_budget": self.working_budget,
            "used_tokens": used,
            "remaining": self.working_budget - used,
            "output_reserve": int(self.config.token_budget * self.config.output_reserve_pct),
            "utilization_pct": round(used / self.working_budget * 100, 1) if self.working_budget > 0 else 0,
            "compression_needed": (used / self.config.token_budget) >= 0.70,
            "slices": [
                {"name": s.name, "tokens": s.tokens, "priority": s.priority, "weight": s.weight}
                for s in sorted(self._slices, key=lambda x: x.priority)
            ]
        }
