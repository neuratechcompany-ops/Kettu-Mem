"""
MemoryManager v2 — full orchestration with Mem0.

Vertical slice:
  record_event → index_event → embed_event → extract_facts → search → build_context

Architecture:
  L1: Context Builder (token budget, weighted assembly, Mem0-aware)
  L2: Compression Engine (auto-trigger at 70%, incremental)
  L3: Verbatim Archive (JSONL, immutable)
  SQLite: Metadata index + Mem0 facts
  FAISS: Semantic index (events + Mem0 facts)
  Mem0: Long-term memory (preferences, decisions, entities)
"""

import time
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)

from embeddings.faiss_index import FAISSSemanticIndex
from extractors.compression import CompressionEngine
from extractors.ingestion_filter import IngestionFilter
from extractors.mem0 import FactType, Mem0Store
from retrieval.context_builder import BudgetStrategy, ContextBuilder, ContextConfig, ToolSchema
from storage.l3_verbatim import L3VerbatimArchive
from storage.session_isolation import SessionIsolation, SessionNamespace
from storage.sqlite_index import SQLiteMetadataIndex


class MemoryManager:
    """
    Full MemoryManager with all 6 layers.

    Usage:
        mm = MemoryManager("/path/to/data")
        mm.start_session("session-1")

        # Record events
        mm.record_event("user", "message", "Я предпочитаю работать в Notion")
        mm.record_event("assistant", "message", "Понял, запомнил.")

        # Build context (with Mem0 facts)
        prompt, stats = mm.build_context("план контента на неделю")

        # The prompt includes recent events + relevant Mem0 facts + summaries.
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Core layers
        self.l3 = L3VerbatimArchive(str(self.data_dir / "l3_archive"))
        self.sqlite = SQLiteMetadataIndex(str(self.data_dir / "metadata.db"))
        self.faiss = FAISSSemanticIndex(str(self.data_dir / "faiss"))
        self.session_isolation = SessionIsolation(self.sqlite)

        # Higher layers
        self.compression = CompressionEngine(self.sqlite, self.l3)
        self.ingestion_filter = IngestionFilter()
        self.mem0 = Mem0Store(str(self.data_dir / "mem0.db"), faiss_index=self.faiss)

        # State
        self._session_id: str = None
        self._step_counter: int = 0
        self._next_faiss_id: int = 0
        self._auto_extract_enabled: bool = True
        self._extract_batch_size: int = 10  # extract Mem0 facts every N events (было 20)

    # ── Session management ──────────────────────────────

    def start_session(
        self,
        session_id: str,
        project_id: str = None,
        workspace_id: str = "default",
        agent_id: str = "main",
        user_id: str = "default",
    ):
        """Start or resume a session with namespace isolation."""
        self._session_id = session_id
        self.sqlite.ensure_session(
            session_id, project_id, workspace=workspace_id, agent=agent_id, user_id=user_id
        )

        # Register session with isolation namespace
        self._namespace = SessionNamespace(
            project=project_id or "default",
            workspace=workspace_id,
            agent=agent_id,
            user=user_id,
            session_id=session_id,
        )
        self.session_isolation.register_session(self._namespace)

        existing = self.sqlite.get_recent_events(session_id, limit=1)
        if existing:
            self._step_counter = existing[0]["step_id"] + 1
        else:
            self._step_counter = 0

        faiss_ids = self.sqlite.get_faiss_ids_for_session(session_id)
        self._next_faiss_id = max(v["faiss_id"] for v in faiss_ids) + 1 if faiss_ids else 0

        # Auto-rebuild FAISS if index is corrupted or missing but we have data
        if not self.faiss.is_index_healthy():
            count_in_sqlite = len(faiss_ids)
            if count_in_sqlite > 0:
                logger.warning(
                    "faiss_index_corrupted_auto_rebuilding",
                    chunks_in_sqlite=count_in_sqlite,
                    session_id=session_id,
                )
                self._auto_rebuild_faiss(session_id, faiss_ids)

        mem0_stats = self.mem0.get_stats()
        arc_count = self.l3.get_event_count(session_id)

        logger.info(
            "session_started",
            session_id=session_id,
            step_counter=self._step_counter,
            faiss_vectors=len(faiss_ids),
            mem0_facts=mem0_stats["total_facts"],
            archived_events=arc_count,
        )

    @property
    def namespace(self) -> SessionNamespace:
        """Current session namespace for isolation checks."""
        if not hasattr(self, "_namespace"):
            return SessionNamespace()
        return self._namespace

    # ── Recording ───────────────────────────────────────

    def record_event(
        self,
        role: str,
        event_type: str,
        content: str,
        refs: list = None,
        meta: dict = None,
        session_id: str = None,
    ) -> str:
        """
        Record event across all layers:
        IngestFilter → L3 → SQLite → FAISS → Mem0 (auto-extract every N events)

        Returns event_id.
        """
        sid = session_id or self._session_id
        if not sid:
            raise RuntimeError("No active session. Call start_session() first.")

        # Ingestion filter: reject system prompts, tool traces, json blobs, reasoning, duplicates
        should_ingest, reason = self.ingestion_filter.should_ingest(content, role, event_type, sid)
        if not should_ingest:
            # Log rejection and return a dummy id
            return f"filtered:{reason}"

        # Normalize content through filter
        content = self.ingestion_filter.normalize(content)

        step_id = self._step_counter
        self._step_counter += 1

        # L3: immutable archive
        event_id = self.l3.record_event(
            sid, step_id, role=role, type=event_type, content=content, refs=refs, meta=meta
        )

        # SQLite: metadata
        self.sqlite.index_event(
            event_id,
            sid,
            step_id,
            role=role,
            type=event_type,
            content=content,
            refs=refs,
            meta=meta,
        )

        # FAISS: embed messages
        if event_type == "message" and len(content) > 20:
            self._embed_and_store(event_id, content)

        # Mem0: auto-extract every N events
        if self._auto_extract_enabled and self._step_counter % self._extract_batch_size == 0:
            self._auto_extract_facts()

        # Mem0: trigger-based extraction for important patterns
        if self._auto_extract_enabled and isinstance(content, str):
            self._trigger_extract_fact(content, role)

        return event_id

    def record_batch(self, events: list[dict]) -> list[str]:
        """Record multiple events efficiently."""
        event_ids = []
        for ev in events:
            eid = self.record_event(
                ev["role"], ev["type"], ev["content"], ev.get("refs"), ev.get("meta")
            )
            event_ids.append(eid)
        return event_ids

    def _embed_and_store(self, event_id: str, content: str):
        faiss_id = self._next_faiss_id
        self._next_faiss_id += 1
        chunk = content[:500]

        # Skip near-duplicates: if last chunk differs only by numbers, skip
        if hasattr(self, "_last_chunk") and self._last_chunk:
            # Normalize: replace digits with placeholder
            import re

            norm_prev = re.sub(r"\d+", "#", self._last_chunk)
            norm_curr = re.sub(r"\d+", "#", chunk)
            if norm_prev == norm_curr:
                return  # skip duplicate
        self._last_chunk = chunk

        self.faiss.add_vectors([chunk], faiss_id)
        self.sqlite.map_vector(event_id, self._session_id, faiss_id, chunk)

    # ── Mem0 extraction ─────────────────────────────────

    # Trigger patterns for immediate fact extraction
    _TRIGGER_PATTERNS = {
        "preference": [
            "я предпочитаю",
            "мне нравится",
            "я люблю",
            "я не люблю",
            "удобнее",
            "привык",
            "мой любимый",
            "предпочитаю",
            "i prefer",
            "i like",
            "i love",
            "my favorite",
        ],
        "decision": [
            "решили",
            "договорились",
            "принято",
            "утвердили",
            "согласовали",
            "постановили",
            "решено",
            "выбрали",
            "decided",
            "agreed",
            "approved",
            "confirmed",
        ],
    }

    def _trigger_extract_fact(self, content: str, role: str):
        """Extract a Mem0 fact if content matches trigger patterns."""
        if role not in ("user", "assistant"):
            return
        content_lower = content.lower()
        for fact_type, patterns in self._TRIGGER_PATTERNS.items():
            for pat in patterns:
                if pat in content_lower:
                    # Extract clean fact text (trim to first 300 chars)
                    fact_text = content[:300].strip()
                    self.mem0.add_fact(
                        FactType(fact_type),
                        fact_text,
                        source_session=self._session_id,
                        confidence=0.85,
                    )
                    return  # one fact per message

    def _auto_extract_facts(self):
        """Extract Mem0 facts from current session events."""
        events = self.l3.read_session(self._session_id)
        # Only process unprocessed events (last batch)
        recent = events[-self._extract_batch_size :]
        self.mem0.extract_facts(recent, self._session_id)

    def extract_all_facts(self):
        """Extract facts from entire session."""
        events = self.l3.read_session(self._session_id)
        return self.mem0.extract_facts(events, self._session_id)

    def add_mem0_fact(self, fact_type: str, content: str, **kwargs):
        """Manually add a Mem0 fact."""
        ft = FactType(fact_type)
        kwargs.setdefault("source_session", self._session_id)
        return self.mem0.add_fact(ft, content, **kwargs)

    # ── Context building ────────────────────────────────

    def build_context(
        self,
        query: str = None,
        *,
        system_prompt: str = None,
        tools: list[dict] = None,
        strategy: BudgetStrategy = None,
        config: ContextConfig = None,
        session_id: str = None,
    ) -> tuple[str, dict]:
        """
        Build prompt context under token budget.

        Layers in context:
        0. System prompt + active tools
        1. Recent events (last N)
        2. Mem0 long-term facts (if query provided)
        2. FAISS semantic results (if query provided)
        2. Session summaries
        3. Archive references (on demand)

        Returns (prompt_text, stats_dict).
        """
        sid = session_id or self._session_id
        if strategy:
            cfg = ContextConfig.from_strategy(strategy)
        else:
            cfg = config or ContextConfig()

        builder = ContextBuilder(cfg)

        # 0. System prompt
        if not system_prompt:
            system_prompt = (
                "You are a helpful AI assistant with long-term memory. "
                "Use the provided context to answer accurately. "
                "Long-term Memory section contains facts learned about the user and project. "
                "Respect user preferences from Long-term Memory."
            )
        builder.set_system(system_prompt)

        # Tools (filter only active ones)
        if tools:
            tool_schemas = []
            for t in tools:
                if isinstance(t, ToolSchema):
                    tool_schemas.append(t)
                elif isinstance(t, dict):
                    tool_schemas.append(
                        ToolSchema(
                            name=t.get("name", "?"),
                            description=t.get("description", ""),
                            parameters=t.get("parameters"),
                        )
                    )
            if tool_schemas:
                builder.set_tools(tool_schemas)

        # 1. Recent events
        recent = self.sqlite.get_recent_events(sid, limit=cfg.recent_events_limit)
        if recent:
            builder.set_recent_events(recent)

        # 2a. Mem0 facts (if query) — enforce session isolation
        if query:
            mem0_facts = self.mem0.search_text(query, limit=cfg.max_mem0_facts, source_session=sid)
            if mem0_facts:
                builder.set_mem0_facts(mem0_facts)

        # 2b. FAISS semantic search (if query)
        if query:
            faiss_results = self.faiss.search(query, k=cfg.max_semantic_chunks)
            enriched = self._enrich_faiss_results(faiss_results)
            if enriched:
                builder.set_semantic_results(enriched)

        # 2c. Summaries
        summaries = self.sqlite.get_summaries(sid)
        if summaries:
            builder.set_summaries(summaries)

        # Build
        prompt, stats = builder.build()

        # Compression check (after build, check utilization)
        if stats["compression_needed"]:
            self._auto_compress(stats["utilization_pct"])

        stats["session_id"] = sid
        stats["total_events_archived"] = self.l3.get_event_count(sid)
        stats["mem0_facts_total"] = self.mem0.get_stats()["total_facts"]

        return prompt, stats

    def _enrich_faiss_results(
        self, faiss_results: list[dict], session_id: str = None
    ) -> list[dict]:
        """Enrich FAISS results with metadata from SQLite, filtered by session."""
        sid = session_id or self._session_id
        enriched = []
        for fr in faiss_results:
            faiss_id = fr["faiss_id"]
            if sid:
                vector_rows = self.sqlite.conn.execute(
                    "SELECT event_id, chunk_text FROM vector_map WHERE faiss_id = ?
                    AND session_id = ?",
                    (faiss_id, sid),
                ).fetchall()
            else:
                vector_rows = self.sqlite.conn.execute(
                    "SELECT event_id, chunk_text FROM vector_map WHERE faiss_id = ?", (faiss_id,)
                ).fetchall()
            for vr in vector_rows:
                evt_row = self.sqlite.conn.execute(
                    "SELECT role, type, content_preview FROM events WHERE event_id = ?",
                    (vr["event_id"],),
                ).fetchone()
                enriched.append(
                    {
                        "faiss_id": faiss_id,
                        "score": fr["score"],
                        "event_id": vr["event_id"],
                        "chunk_text": vr["chunk_text"],
                        "role": evt_row["role"] if evt_row else "?",
                        "type": evt_row["type"] if evt_row else "?",
                        "content_preview": evt_row["content_preview"] if evt_row else "",
                    }
                )
        return enriched

    def _auto_compress(self, utilization_pct: float):
        """Auto-trigger compression if budget is tight."""
        logger.warning("auto_compress_triggered", utilization_pct=round(utilization_pct, 0))
        result = self.compression.incremental_compress(
            self._session_id, threshold_pct=0.60  # slightly lower than check to prevent flapping
        )
        if result:
            logger.info(
                "auto_compress_complete",
                events_compressed=result.events_compressed,
                tokens_saved=result.tokens_saved,
            )

    # ── Manual operations ───────────────────────────────

    def _auto_rebuild_faiss(self, session_id: str, faiss_ids: list[dict]):
        """
        Auto-rebuild FAISS index from SQLite vector_map + L3 data.
        Called when index is corrupted or missing at session start.
        """
        texts = []
        ids_out = []

        for entry in faiss_ids:
            chunk = entry.get("chunk_text", "")
            if not chunk or len(chunk) <= 20:
                continue
            # Dedup by normalized text
            import re

            norm = re.sub(r"\d+", "#", chunk)
            if texts:
                last_norm = re.sub(r"\d+", "#", texts[-1])
                if last_norm == norm:
                    continue
            faiss_id = len(texts)
            texts.append(chunk)
            ids_out.append(faiss_id)

        if not texts:
            logger.warning("faiss_auto_rebuild_no_chunks", session_id=session_id)
            return

        t0 = time.time()
        self.faiss.build_index(texts, ids_out)
        elapsed = (time.time() - t0) * 1000

        self._next_faiss_id = len(texts)
        logger.info(
            "faiss_auto_rebuild_complete",
            chunks=len(texts),
            from_sqlite=True,
            latency_ms=round(elapsed, 0),
        )

    def rebuild_index(self, session_id: str = None):
        """
        Rebuild FAISS index from all events in a session.
        Reads L3 archive, re-embeds all messages, updates vector_map.
        """
        sid = session_id or self._session_id
        if not sid:
            raise RuntimeError("No session specified.")

        events = self.l3.read_session(sid)
        if not events:
            logger.info("rebuild_index_no_events")
            return

        # Clear existing
        self.sqlite.conn.execute("DELETE FROM vector_map WHERE session_id = ?", (sid,))
        self.sqlite.conn.commit()

        texts = []
        ids = []
        for ev in events:
            content = ev.get("content", "")
            if isinstance(content, str) and len(content) > 20:
                chunk = content[:500]
                # Dedup: normalize digits
                import re

                norm = re.sub(r"\d+", "#", chunk)
                if texts and re.sub(r"\d+", "#", texts[-1]) == norm:
                    continue  # skip near-duplicate
                faiss_id = len(texts)
                texts.append(chunk)
                ids.append(faiss_id)

        if not texts:
            logger.info("rebuild_index_no_content")
            return

        # Build FAISS
        t0 = time.time()
        self.faiss.build_index(texts, ids)
        elapsed = (time.time() - t0) * 1000

        # Update vector_map
        for i, (chunk, faiss_id) in enumerate(zip(texts, ids)):
            self.sqlite.conn.execute(
                "INSERT INTO vector_map (event_id, session_id, faiss_id, chunk_text) VALUES (?, ?, ?, ?)",
                (f"idx-{i}", sid, faiss_id, chunk),
            )
        self.sqlite.conn.commit()

        logger.info("index_rebuilt", vectors=len(texts), latency_ms=round(elapsed, 0))
        return {"vectors": len(texts), "latency_ms": elapsed}

    # ── Original manual operations ───────────────────────

    def compress(self, end_step: int = None) -> dict:
        """Manual compression."""
        if end_step is None:
            end_step = self._step_counter - 1
        start_step = max(0, end_step - max(30, end_step // 2))
        result = self.compression.compress_range(self._session_id, start_step, end_step)
        return {
            "summary_id": result.summary_id,
            "events_compressed": result.events_compressed,
            "tokens_saved": result.tokens_saved,
            "range": f"{start_step}-{end_step}",
            "decisions": len(result.decisions),
            "entities": result.entities,
        }

    def get_archive_stats(self) -> dict:
        """Full statistics across all layers."""
        return {
            "session_id": self._session_id,
            "l3_events": self.l3.get_event_count(self._session_id),
            "l3_size_bytes": self.l3.get_size_bytes(self._session_id),
            "sqlite_stats": self.sqlite.get_session_info(self._session_id),
            "faiss_stats": self.faiss.get_index_stats(),
            "mem0_stats": self.mem0.get_stats(),
            "step_counter": self._step_counter,
        }

    def get_mem0_context(self, query: str = None, limit: int = 10) -> list[dict]:
        """Get Mem0 facts for external use — enforce session isolation."""
        if query:
            return self.mem0.search_text(query, limit, source_session=self._session_id)
        return self.mem0.get_all(limit, source_session=self._session_id)

    def close(self):
        """Clean shutdown."""
        if hasattr(self, "_namespace"):
            self.session_isolation.unregister_session(self._namespace)
        self.sqlite.close()
        self.mem0.close()
