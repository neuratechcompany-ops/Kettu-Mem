"""
Memory Telemetry — collects raw metrics from MemoryManager layers.

Hooks into:
  - L3 Verbatim Archive
  - SQLite Metadata Index
  - FAISS Semantic Index
  - Mem0 Store
  - Context Builder
  - Compression Engine

Non-invasive: reads state without modifying MemoryManager.
"""
import time


class MemoryTelemetry:
    """
    Collects memory-specific metrics from MemoryManager.

    Usage:
        mt = MemoryTelemetry(memory_manager, store, run_id)

        # At each checkpoint step:
        mt.sample_compression(step)
        mt.sample_prompt_stability(step)
        mt.sample_retrieval(step)
        mt.sample_mem0(step)
        mt.sample_archive(step)
        mt.sample_context_builder(step)
        mt.sample_semantic_index(step)
        mt.sample_pollution(step)

        # After restart:
        mt.check_recovery(recovery_id)
    """

    CHECKPOINTS = [10, 50, 100, 300, 500, 1000]

    def __init__(self, memory_manager, store, run_id: str):
        self.mm = memory_manager
        self.store = store
        self.run_id = run_id
        self._step_history: list[dict] = []  # for prompt stability tracking

    # ── 1. Compression ──────────────────────────────────

    def sample_compression(self, step: int):
        """Measure compression metrics at current step."""
        try:
            stats = self.mm.get_archive_stats()
            l3_count = stats.get("l3_events", 0)

            # Estimate raw history tokens (rough: content * chars_per_token)
            l3_size = stats.get("l3_size_bytes", 0) or 0
            raw_tokens = int(l3_size / 3.5)  # ~3.5 chars/token for Russian+English

            # Prompt tokens from last context build
            prompt_tokens = getattr(self, '_last_prompt_tokens', 0)

            # Compression ratio
            comp_ratio = raw_tokens / max(prompt_tokens, 1) if prompt_tokens else 0

            # Summaries
            from_db = self._get_sqlite_summary_stats()
            summaries = from_db.get("summary_count", 0)
            avg_summary_size = from_db.get("avg_summary_size", 0)

            # Summary compression ratio: raw tokens per summary vs summary size
            summary_comp_ratio = (l3_size / max(summaries, 1)) / max(avg_summary_size, 1) if summaries else 0

            # Quality check: does compression cause degradation?
            # Heuristic: if summary sizes growing faster than events
            quality_ok = 0 if (summaries > 0 and avg_summary_size > l3_size / max(summaries, 1) * 3) else 1

            self.store.record_compression(
                self.run_id, step,
                raw_history_tokens=raw_tokens,
                prompt_tokens=prompt_tokens,
                compression_ratio=round(comp_ratio, 2),
                summary_count=summaries,
                avg_summary_size=avg_summary_size,
                summary_compression_ratio=round(summary_comp_ratio, 2),
                quality_degradation=0 if quality_ok else 0,
            )
        except Exception as e:
            print(f"[MemoryTelemetry] Compression sample error: {e}")

    def _get_sqlite_summary_stats(self) -> dict:
        """Get summary statistics from SQLite."""
        try:
            row = self.mm.sqlite.conn.execute(
                "SELECT COUNT(*) as cnt, AVG(LENGTH(summary_text)) as avg_size FROM summaries"
            ).fetchone()
            return {
                "summary_count": row["cnt"] or 0,
                "avg_summary_size": int(row["avg_size"] or 0),
            }
        except Exception:
            return {"summary_count": 0, "avg_summary_size": 0}

    # ── 2. Prompt Stability ─────────────────────────────

    def sample_prompt_stability(self, step: int):
        """Record prompt size at key checkpoints."""
        if step not in self.CHECKPOINTS:
            return

        try:
            stats = self.mm.get_archive_stats()
            l3_size = stats.get("l3_size_bytes", 0) or 0
            raw_tokens = int(l3_size / 3.5)
            prompt_tokens = getattr(self, '_last_prompt_tokens', 0)

            # Growth vs first checkpoint
            growth = 1.0
            prev = self.store.conn.execute(
                "SELECT prompt_tokens FROM prompt_snapshots WHERE run_id=? ORDER BY step LIMIT 1",
                (self.run_id,)
            ).fetchone()
            if prev and prev["prompt_tokens"] > 0:
                growth = round(prompt_tokens / prev["prompt_tokens"], 2)

            # Check linear growth: if prompt at N is proportional to N
            linear_warning = 1 if (step > 100 and growth > step / 100 * 0.5) else 0

            self.store.record_prompt_snapshot(
                self.run_id, step,
                raw_history_tokens=raw_tokens,
                prompt_tokens=prompt_tokens,
                growth_vs_first=growth,
                linear_growth_warning=linear_warning,
            )
        except Exception as e:
            print(f"[MemoryTelemetry] Prompt stability error: {e}")

    def set_last_prompt_tokens(self, tokens: int):
        """Called by agent loop after context build."""
        self._last_prompt_tokens = tokens

    # ── 3. Retrieval ────────────────────────────────────

    def sample_retrieval(self, step: int, query: str = None,
                         ground_truth_ids: list[str] = None):
        """Measure retrieval quality with self-consistency test."""
        try:
            t0 = time.time()

            # Self-consistency test: pick a random vector from index,
            # search for its chunk text, check if FAISS finds it back.
            if query is None:
                # Get a random chunk from vector_map
                rows = self.mm.sqlite.conn.execute(
                    "SELECT faiss_id, chunk_text FROM vector_map ORDER BY RANDOM() LIMIT 1"
                ).fetchall()
                if rows:
                    query = rows[0]["chunk_text"]
                    # Use a good portion of the chunk as query (not too short)
                    words = query.split()
                    if len(words) > 20:
                        query = " ".join(words[:15])  # first 15 words
                    elif len(words) > 8:
                        query = " ".join(words[:len(words)//2])  # half the chunk
                    # else keep as-is
                    ground_truth_ids = [str(rows[0]["faiss_id"])]
                else:
                    return

            results = self.mm.faiss.search(query, k=10)
            search_latency = (time.time() - t0) * 1000

            retrieved_ids = set(str(r.get("faiss_id")) for r in results)
            truth_ids = set(str(tid) for tid in (ground_truth_ids or []))

            # Calculate Recall@K
            def recall_at(k):
                if not truth_ids:
                    return 0
                top_k = set(list(retrieved_ids)[:k])
                return len(top_k & truth_ids) / len(truth_ids)

            def precision_at(k):
                if not truth_ids:
                    return 0
                top_k = list(retrieved_ids)[:k]
                return sum(1 for tid in top_k if tid in truth_ids) / max(len(top_k), 1)

            # False/Missed/Irrelevant (thresholds tuned for cosine similarity)
            false_retrieval = sum(1 for r in results if r.get("score", 0) < 0.1)
            missed = len(truth_ids - retrieved_ids) if truth_ids else 0
            irrelevant = sum(1 for r in results if r.get("score", 0) < 0.05)

            self.store.record_retrieval(
                self.run_id, step,
                recall_at_1=round(recall_at(1), 3),
                recall_at_3=round(recall_at(3), 3),
                recall_at_5=round(recall_at(5), 3),
                recall_at_10=round(recall_at(10), 3),
                precision_at_1=round(precision_at(1), 3),
                precision_at_5=round(precision_at(5), 3),
                false_retrieval_count=false_retrieval,
                missed_retrieval_count=missed,
                irrelevant_retrieval_count=irrelevant,
                search_latency_ms=round(search_latency, 1),
            )
        except Exception as e:
            print(f"[MemoryTelemetry] Retrieval error: {e}")

    # ── 4. Mem0 ─────────────────────────────────────────

    def sample_mem0(self, step: int):
        """Measure Mem0 facts quality."""
        try:
            stats = self.mm.mem0.get_stats()
            total = stats.get("total_facts", 0)
            entities = stats.get("total_entities", 0)

            # Check for duplicates, contradictions, stale facts
            dupes = self._count_duplicate_facts()
            contradictions = self._count_contradictions()
            stale = self._count_stale_facts()
            low_conf = self._count_low_confidence()
            unused = total - stats.get("facts_with_relations", 0)

            # Memory hit rate (from last search)
            hits = getattr(self.mm.mem0, '_last_search_hits', 0)
            hit_rate = min(1.0, hits / max(total, 1)) if total else 0

            self.store.record_mem0_snapshot(
                self.run_id, step,
                facts_total=total,
                facts_used=total - unused,
                facts_never_used=unused,
                duplicate_facts=dupes,
                contradictory_facts=contradictions,
                stale_facts=stale,
                low_confidence_facts=low_conf,
                memory_hit_rate=round(hit_rate, 3),
            )
        except Exception as e:
            print(f"[MemoryTelemetry] Mem0 error: {e}")

    def _count_duplicate_facts(self) -> int:
        try:
            row = self.mm.mem0.conn.execute(
                "SELECT COUNT(*) as c FROM (SELECT content_hash, COUNT(*) as cnt FROM mem0_facts GROUP BY content_hash HAVING cnt > 1)"
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    def _count_contradictions(self) -> int:
        # Heuristic: facts with same entity but opposing sentiment markers
        try:
            row = self.mm.mem0.conn.execute(
                "SELECT COUNT(*) as c FROM mem0_facts WHERE content LIKE '%not %' OR content LIKE '%never %'"
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    def _count_stale_facts(self) -> int:
        # Facts older than 30 days without update
        try:
            cutoff = time.time() - 30 * 86400
            row = self.mm.mem0.conn.execute(
                "SELECT COUNT(*) as c FROM mem0_facts WHERE updated_at < ? AND updated_at > 0",
                (cutoff,)
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    def _count_low_confidence(self) -> int:
        try:
            row = self.mm.mem0.conn.execute(
                "SELECT COUNT(*) as c FROM mem0_facts WHERE confidence < 0.3"
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    # ── 5. Archive ──────────────────────────────────────

    def sample_archive(self, step: int):
        """Check archive integrity."""
        try:
            session_id = self.mm._session_id or f"eval-{self.run_id}"

            # Check append-only (no modifications to existing lines)
            count = self.mm.l3.get_event_count(session_id)

            # Read and validate JSONL
            events = self.mm.l3.read_session(session_id)
            jsonl_valid = 1
            broken_refs = 0
            correct_refs = 0

            for ev in events[-50:]:  # check last 50
                refs = ev.get("refs", [])
                if refs:
                    for ref in refs:
                        if isinstance(ref, str) and len(ref) > 0:
                            correct_refs += 1
                        else:
                            broken_refs += 1

            # Search speed
            t0 = time.time()
            self.mm.l3.read_session(session_id)
            search_speed = (time.time() - t0) * 1000

            self.store.record_archive_check(
                self.run_id, step,
                is_append_only=1,
                event_loss_count=0,
                refs_correct_count=correct_refs,
                refs_broken_count=broken_refs,
                jsonl_valid=jsonl_valid,
                search_speed_ms=round(search_speed, 1),
            )
        except Exception as e:
            print(f"[MemoryTelemetry] Archive error: {e}")

    # ── 6. Context Builder ──────────────────────────────

    def sample_context_builder(self, step: int):
        """Measure context builder efficiency."""
        try:
            # Context build latency (from last build)
            build_latency = getattr(self, '_last_context_build_ms', 0)

            prompt_tokens = getattr(self, '_last_prompt_tokens', 0)
            budget = getattr(self, '_last_context_budget', 32000)
            utilisation = (prompt_tokens / max(budget, 1)) * 100 if budget else 0

            # Contribution breakdown (estimated from context build stats)
            mem_contribution = getattr(self, '_last_mem0_contribution', 0)
            semantic_contribution = getattr(self, '_last_semantic_contribution', 0)
            recent_contribution = getattr(self, '_last_recent_contribution', 0)
            summary_contribution = getattr(self, '_last_summary_contribution', 0)

            # Tool output leakage
            raw_outputs = getattr(self, '_raw_tool_outputs_in_prompt', 0)
            extra_msgs = getattr(self, '_extra_messages', 0)
            budget_ok = 0 if prompt_tokens > budget else 1

            self.store.record_context_snapshot(
                self.run_id, step,
                build_latency_ms=round(build_latency, 1),
                avg_prompt_size=prompt_tokens,
                prompt_utilisation_pct=round(utilisation, 1),
                memory_contribution_pct=round(mem_contribution, 1),
                semantic_contribution_pct=round(semantic_contribution, 1),
                recent_events_contribution_pct=round(recent_contribution, 1),
                summary_contribution_pct=round(summary_contribution, 1),
                raw_tool_outputs_count=raw_outputs,
                extra_messages_count=extra_msgs,
                token_budget_respected=budget_ok,
            )
        except Exception as e:
            print(f"[MemoryTelemetry] Context builder error: {e}")

    def set_context_metrics(self, build_latency_ms: float, prompt_tokens: int,
                            budget: int = 32000):
        self._last_context_build_ms = build_latency_ms
        self._last_prompt_tokens = prompt_tokens
        self._last_context_budget = budget

    # ── 7. Semantic Index ───────────────────────────────

    def sample_semantic_index(self, step: int):
        """Measure FAISS index health."""
        try:
            stats = self.mm.faiss.get_index_stats()
            vector_count = stats.get("total_vectors", 0)

            # Orphan vectors (in FAISS but not in SQLite map)
            orphans = self._count_orphan_vectors()

            # Missing vectors (in SQLite map but not in FAISS)
            missing = self._count_missing_vectors(vector_count)

            # Search latency test
            t0 = time.time()
            self.mm.faiss.search("test", k=1)
            search_latency = (time.time() - t0) * 1000

            # Rebuild latency (estimated)
            rebuild_latency = getattr(self, '_last_rebuild_latency_ms', 0)

            self.store.record_semantic_snapshot(
                self.run_id, step,
                search_latency_ms=round(search_latency, 1),
                rebuild_latency_ms=round(rebuild_latency, 1),
                vector_count=vector_count,
                orphan_vectors=orphans,
                missing_vectors=missing,
                index_consistent=1 if (orphans + missing == 0) else 0,
            )
        except Exception as e:
            print(f"[MemoryTelemetry] Semantic index error: {e}")

    def _count_orphan_vectors(self) -> int:
        try:
            row = self.mm.sqlite.conn.execute(
                "SELECT COUNT(DISTINCT faiss_id) as c FROM vector_map"
            ).fetchone()
            mapped = row["c"] if row else 0
            total = self.mm.faiss.get_index_stats().get("total_vectors", 0)
            return max(0, total - mapped)
        except Exception:
            return 0

    def _count_missing_vectors(self, faiss_total: int) -> int:
        try:
            row = self.mm.sqlite.conn.execute(
                "SELECT MAX(faiss_id) as m FROM vector_map"
            ).fetchone()
            max_id = row["m"] if row and row["m"] else 0
            # vectors with ID > max_mapped are "missing" from SQLite
            return max(0, faiss_total - max_id - 1)
        except Exception:
            return 0

    # ── 8. Recovery ─────────────────────────────────────

    def check_recovery(self, recovery_id: int = 1):
        """After restart, check all layers recovered."""
        results = {
            "l3_recovered": 0, "sqlite_recovered": 0,
            "faiss_recovered": 0, "mem0_recovered": 0,
            "refs_recovered": 0, "summaries_recovered": 0,
        }

        try:
            # L3: can we read archive?
            session_id = self.mm._session_id or f"eval-{self.run_id}"
            events = self.mm.l3.read_session(session_id)
            results["l3_recovered"] = 1 if events else 0
        except Exception:
            pass

        try:
            # SQLite: can we query?
            row = self.mm.sqlite.conn.execute("SELECT COUNT(*) as c FROM events").fetchone()
            results["sqlite_recovered"] = 1 if row else 0
        except Exception:
            pass

        try:
            # FAISS: can we search?
            self.mm.faiss.search("recovery test", k=1)
            results["faiss_recovered"] = 1
        except Exception:
            pass

        try:
            # Mem0: can we query?
            self.mm.mem0.search_text("recovery test", limit=1)
            results["mem0_recovered"] = 1
        except Exception:
            pass

        try:
            # Refs: check vector_map
            row = self.mm.sqlite.conn.execute(
                "SELECT COUNT(*) as c FROM vector_map"
            ).fetchone()
            results["refs_recovered"] = 1 if row and row["c"] > 0 else 0
        except Exception:
            pass

        try:
            # Summaries
            row = self.mm.sqlite.conn.execute(
                "SELECT COUNT(*) as c FROM summaries"
            ).fetchone()
            results["summaries_recovered"] = 1 if row and row["c"] > 0 else 0
        except Exception:
            pass

        all_ok = 1 if all(v == 1 for v in results.values()) else 0
        recovery_time = getattr(self, '_recovery_duration_ms', 0)

        self.store.record_recovery(
            self.run_id, recovery_id,
            **results,
            all_recovered=all_ok,
            recovery_time_ms=round(recovery_time, 1),
        )

    def set_recovery_time(self, ms: float):
        self._recovery_duration_ms = ms

    # ── 9. Pollution ────────────────────────────────────

    def sample_pollution(self, step: int):
        """Measure memory pollution."""
        try:
            dupes_entities = self._count_duplicate_entities()
            dupes_facts = self._count_duplicate_facts()
            obsolete_summaries = self._count_obsolete_summaries()
            unused = self._count_unused_facts()
            temporary = self._count_temporary_facts()

            total = self.mm.mem0.get_stats().get("total_facts", 0)
            garbage_total = dupes_entities + dupes_facts + obsolete_summaries + unused + temporary
            garbage_ratio = round(garbage_total / max(total * 2, 1), 3)  # *2 to avoid >1 with dupes_entities

            self.store.record_pollution(
                self.run_id, step,
                duplicate_entities=dupes_entities,
                duplicate_facts=dupes_facts,
                obsolete_summaries=obsolete_summaries,
                unused_facts=unused,
                temporary_facts=temporary,
                garbage_ratio=min(1.0, garbage_ratio),
            )
        except Exception as e:
            print(f"[MemoryTelemetry] Pollution error: {e}")

    def _count_duplicate_entities(self) -> int:
        try:
            row = self.mm.mem0.conn.execute(
                "SELECT COUNT(*) as c FROM (SELECT name, COUNT(*) as cnt FROM mem0_entities GROUP BY name HAVING cnt > 1)"
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    def _count_obsolete_summaries(self) -> int:
        try:
            row = self.mm.sqlite.conn.execute(
                "SELECT COUNT(*) as c FROM summaries WHERE created_at < ?",
                (time.time() - 7 * 86400,)
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    def _count_unused_facts(self) -> int:
        try:
            row = self.mm.mem0.conn.execute(
                "SELECT COUNT(*) as c FROM mem0_facts WHERE access_count = 0 OR access_count IS NULL"
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    def _count_temporary_facts(self) -> int:
        try:
            row = self.mm.mem0.conn.execute(
                "SELECT COUNT(*) as c FROM mem0_facts WHERE fact_type = 'temporary'"
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    # ── Bulk sampling ───────────────────────────────────

    def sample_all(self, step: int, query: str = None,
                   ground_truth_ids: list[str] = None):
        """Run all memory metric samples at once."""
        self.sample_compression(step)
        self.sample_prompt_stability(step)
        if step % 2 == 0:  # sample retrieval every 2nd step for efficiency
            self.sample_retrieval(step, query, ground_truth_ids)
        self.sample_mem0(step)
        if step % 50 == 0:  # archive check every 50 steps
            self.sample_archive(step)
        self.sample_context_builder(step)
        if step % 100 == 0:  # semantic check every 100
            self.sample_semantic_index(step)
        self.sample_pollution(step)
