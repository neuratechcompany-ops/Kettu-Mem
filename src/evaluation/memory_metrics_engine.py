"""
Memory Metrics Engine — calculates all memory-specific metrics.

8 component groups:
  1. Compression (20 pts)
  2. Prompt Stability (15 pts)
  3. Retrieval (15 pts)
  4. Mem0 (10 pts)
  5. Archive (10 pts)
  6. Context Builder (10 pts)
  7. Latency (10 pts)
  8. Recovery (10 pts)
  9. Pollution (10 pts) — bonus/deduction
"""


class MemoryMetricsEngine:
    """
    Calculates all memory-specific metrics from recorded snapshots.

    Input: dict with keys matching table names (from MemoryEvalStore queries)
    Output: dict with 9 component groups and MES component data.
    """

    def calculate(self, run_meta: dict, compression_snapshots: list[dict],
                  prompt_snapshots: list[dict], retrieval_snapshots: list[dict],
                  mem0_snapshots: list[dict], archive_checks: list[dict],
                  context_snapshots: list[dict], semantic_snapshots: list[dict],
                  recovery_logs: list[dict], pollution_snapshots: list[dict]) -> dict:
        """
        Calculate all memory metrics from snapshot data.

        Returns:
            {
                "compression": {...},
                "prompt_stability": {...},
                "retrieval": {...},
                "mem0": {...},
                "archive": {...},
                "context_builder": {...},
                "latency": {...},
                "recovery": {...},
                "pollution": {...},
            }
        """
        return {
            "compression": self._calc_compression(compression_snapshots),
            "prompt_stability": self._calc_prompt_stability(prompt_snapshots),
            "retrieval": self._calc_retrieval(retrieval_snapshots),
            "mem0": self._calc_mem0(mem0_snapshots),
            "archive": self._calc_archive(archive_checks),
            "context_builder": self._calc_context(context_snapshots),
            "semantic_index": self._calc_semantic(semantic_snapshots),
            "recovery": self._calc_recovery(recovery_logs),
            "pollution": self._calc_pollution(pollution_snapshots),
            "latency": self._calc_latency(
                retrieval_snapshots, context_snapshots,
                semantic_snapshots, compression_snapshots,
                recovery_logs
            ),
        }

    # ── Compression (20 pts) ─────────────────────────────

    def _calc_compression(self, snaps: list[dict]) -> dict:
        if not snaps:
            return self._empty("compression")

        last = snaps[-1]
        ratios = [s.get("compression_ratio", 0) for s in snaps]
        avg_ratio = self._avg(ratios)
        max_ratio = max(ratios)
        summary_sizes = [s.get("avg_summary_size", 0) for s in snaps if s.get("avg_summary_size")]
        avg_summary_size = self._avg(summary_sizes)
        comp_count = len(snaps)

        # Quality degradation: any snapshots with degradation?
        degradations = sum(1 for s in snaps if s.get("quality_degradation", 0) > 0)

        score = 0
        if max_ratio >= 100:
            score += 10
        elif max_ratio >= 50:
            score += 8
        elif max_ratio >= 20:
            score += 6
        elif max_ratio >= 5:
            score += 4
        elif max_ratio >= 2:
            score += 2

        if degradations == 0:
            score += 5  # no quality loss
        elif degradations <= 2:
            score += 3

        if avg_summary_size < 500:
            score += 3  # concise summaries
        elif avg_summary_size < 1000:
            score += 2

        # Linear prompt growth check
        if len(ratios) >= 2:
            growth = ratios[-1] / max(ratios[0], 0.01)
            if growth >= 0.8:  # ratio stable or improving
                score += 2

        return {
            "compression_ratio": round(max_ratio, 1),
            "avg_compression_ratio": round(avg_ratio, 1),
            "compression_count": comp_count,
            "avg_summary_size": round(avg_summary_size, 1),
            "quality_degradations": degradations,
            "raw_score": score,
            "max_score": 20,
        }

    # ── Prompt Stability (15 pts) ────────────────────────

    def _calc_prompt_stability(self, snaps: list[dict]) -> dict:
        if not snaps:
            return self._empty("prompt_stability")

        tokens = [s.get("prompt_tokens", 0) for s in snaps]
        growth = self._growth_ratio(tokens)

        # Check if prompt at 300 is significantly higher than at 50
        at_50 = None
        at_300 = None
        for s in snaps:
            if s.get("step") == 50:
                at_50 = s.get("prompt_tokens")
            if s.get("step") == 300:
                at_300 = s.get("prompt_tokens")

        linear_warning = False
        if at_50 and at_300 and at_50 > 0:
            expected_linear = at_50 * 6  # 300/50 = 6x
            linear_warning = at_300 > expected_linear * 0.7

        linear_warnings = sum(1 for s in snaps if s.get("linear_growth_warning"))
        checkpoints_hit = len(snaps)

        score = 0
        if growth < 1.1:
            score += 8  # near-flat
        elif growth < 1.5:
            score += 6
        elif growth < 2.0:
            score += 4
        elif growth < 3.0:
            score += 2

        if not linear_warning:
            score += 4
        elif linear_warnings <= 1:
            score += 2

        if checkpoints_hit >= 4:
            score += 3  # good coverage
        elif checkpoints_hit >= 2:
            score += 2
        else:
            score += 1

        return {
            "prompt_at_10": next((s["prompt_tokens"] for s in snaps if s["step"] == 10), None),
            "prompt_at_50": at_50,
            "prompt_at_100": next((s["prompt_tokens"] for s in snaps if s["step"] == 100), None),
            "prompt_at_300": at_300,
            "prompt_at_500": next((s["prompt_tokens"] for s in snaps if s["step"] == 500), None),
            "prompt_at_1000": next((s["prompt_tokens"] for s in snaps if s["step"] == 1000), None),
            "prompt_growth_ratio": round(growth, 2),
            "linear_growth_warnings": linear_warnings,
            "checkpoints_hit": checkpoints_hit,
            "raw_score": score,
            "max_score": 15,
        }

    # ── Retrieval (15 pts) ───────────────────────────────

    def _calc_retrieval(self, snaps: list[dict]) -> dict:
        if not snaps:
            return self._empty("retrieval")

        recall5 = [s.get("recall_at_5", 0) for s in snaps]
        precision5 = [s.get("precision_at_5", 0) for s in snaps]
        recall1 = [s.get("recall_at_1", 0) for s in snaps]
        recall10 = [s.get("recall_at_10", 0) for s in snaps]

        avg_recall5 = self._avg(recall5)
        avg_precision5 = self._avg(precision5)
        avg_recall1 = self._avg(recall1)
        avg_recall10 = self._avg(recall10)

        false_total = sum(s.get("false_retrieval_count", 0) for s in snaps)
        missed_total = sum(s.get("missed_retrieval_count", 0) for s in snaps)
        irrelevant_total = sum(s.get("irrelevant_retrieval_count", 0) for s in snaps)

        latencies = [s.get("search_latency_ms", 0) for s in snaps if s.get("search_latency_ms")]
        avg_latency = self._avg(latencies)

        score = 0
        # recall@10 — main: did we find it at all?
        if avg_recall10 >= 0.95:
            score += 6  # found almost everything
        elif avg_recall10 >= 0.8:
            score += 4
        elif avg_recall10 >= 0.5:
            score += 2

        # recall@5 — bonus for finding early in top-5
        if avg_recall5 >= 0.5:
            score += 3
        elif avg_recall5 >= 0.3:
            score += 2
        elif avg_recall5 > 0:
            score += 1

        # precision@5 — bonus for relevance in top results
        if avg_precision5 >= 0.15:
            score += 2  # 1/5 is fine with homogeneous data
        elif avg_precision5 > 0:
            score += 1

        # false/missed — cleanliness
        if false_total == 0:
            score += 2
        if missed_total == 0:
            score += 2
        return {
            "recall_at_1": round(avg_recall1, 3),
            "recall_at_3": round(self._avg([s.get("recall_at_3", 0) for s in snaps]), 3),
            "recall_at_5": round(avg_recall5, 3),
            "recall_at_10": round(avg_recall10, 3),
            "precision_at_1": round(self._avg([s.get("precision_at_1", 0) for s in snaps]), 3),
            "precision_at_5": round(avg_precision5, 3),
            "false_retrievals": false_total,
            "missed_retrievals": missed_total,
            "irrelevant_retrievals": irrelevant_total,
            "avg_search_latency_ms": round(avg_latency, 1),
            "raw_score": score,
            "max_score": 15,
        }

    # ── Mem0 (10 pts) ────────────────────────────────────

    def _calc_mem0(self, snaps: list[dict]) -> dict:
        if not snaps:
            return self._empty("mem0")

        last = snaps[-1]
        total_facts = last.get("facts_total", 0)
        facts_used = last.get("facts_used", 0)
        never_used = last.get("facts_never_used", 0)
        dupes = last.get("duplicate_facts", 0)
        contradictions = last.get("contradictory_facts", 0)
        stale = last.get("stale_facts", 0)
        low_conf = last.get("low_confidence_facts", 0)
        hit_rate = last.get("memory_hit_rate", 0)

        dupe_pct = (dupes / max(total_facts, 1)) * 100
        stale_pct = (stale / max(total_facts, 1)) * 100

        score = 0
        if hit_rate >= 0.4:
            score += 4
        elif hit_rate >= 0.2:
            score += 2
        elif hit_rate > 0:
            score += 1

        if dupe_pct < 2:
            score += 2  # clean
        elif dupe_pct < 5:
            score += 1

        if contradictions == 0:
            score += 2
        if stale_pct < 5:
            score += 1
        if low_conf / max(total_facts, 1) < 0.1:
            score += 1

        return {
            "facts_total": total_facts,
            "facts_used": facts_used,
            "facts_never_used": never_used,
            "duplicate_facts": dupes,
            "duplicate_facts_pct": round(dupe_pct, 1),
            "contradictory_facts": contradictions,
            "stale_facts": stale,
            "low_confidence_facts": low_conf,
            "memory_hit_rate": round(hit_rate, 3),
            "raw_score": score,
            "max_score": 10,
        }

    # ── Archive (10 pts) ─────────────────────────────────

    def _calc_archive(self, checks: list[dict]) -> dict:
        if not checks:
            return self._empty("archive")

        all_append = all(c.get("is_append_only") for c in checks)
        jsonl_ok = all(c.get("jsonl_valid") for c in checks)
        total_loss = sum(c.get("event_loss_count", 0) for c in checks)
        total_broken = sum(c.get("refs_broken_count", 0) for c in checks)
        total_correct = sum(c.get("refs_correct_count", 0) for c in checks)
        speeds = [c.get("search_speed_ms", 0) for c in checks if c.get("search_speed_ms")]
        avg_speed = self._avg(speeds)

        score = 0
        if all_append:
            score += 3
        if jsonl_ok:
            score += 3
        if total_loss == 0:
            score += 2
        if total_broken == 0 and total_correct > 0:
            score += 1
        if avg_speed < 10:
            score += 1

        return {
            "append_only": all_append,
            "jsonl_valid": jsonl_ok,
            "event_loss": total_loss,
            "refs_correct": total_correct,
            "refs_broken": total_broken,
            "avg_search_speed_ms": round(avg_speed, 1),
            "raw_score": score,
            "max_score": 10,
        }

    # ── Context Builder (10 pts) ─────────────────────────

    def _calc_context(self, snaps: list[dict]) -> dict:
        if not snaps:
            return self._empty("context_builder")

        latencies = [s.get("build_latency_ms", 0) for s in snaps if s.get("build_latency_ms")]
        avg_latency = self._avg(latencies)
        util_pcts = [s.get("prompt_utilisation_pct", 0) for s in snaps]
        avg_util = self._avg(util_pcts)
        raw_outputs = sum(s.get("raw_tool_outputs_count", 0) for s in snaps)
        extra_msgs = sum(s.get("extra_messages_count", 0) for s in snaps)
        budget_ok = all(s.get("token_budget_respected", 1) for s in snaps)

        # Contribution breakdown
        mem_contribs = [s.get("memory_contribution_pct", 0) for s in snaps]
        sem_contribs = [s.get("semantic_contribution_pct", 0) for s in snaps]

        score = 0
        if avg_util <= 80:
            score += 2  # not overfilling
        if avg_util >= 20:
            score += 1  # not underfilling

        if raw_outputs == 0:
            score += 3  # clean context
        elif raw_outputs <= 2:
            score += 1

        if budget_ok:
            score += 2

        if avg_latency < 50:
            score += 2
        elif avg_latency < 100:
            score += 1

        return {
            "avg_build_latency_ms": round(avg_latency, 1),
            "avg_prompt_utilisation_pct": round(avg_util, 1),
            "memory_contribution_pct": round(self._avg(mem_contribs), 1),
            "semantic_contribution_pct": round(self._avg(sem_contribs), 1),
            "raw_tool_outputs_count": raw_outputs,
            "extra_messages_count": extra_msgs,
            "token_budget_respected": budget_ok,
            "raw_score": score,
            "max_score": 10,
        }

    # ── Semantic Index (10 pts) ──────────────────────────

    def _calc_semantic(self, snaps: list[dict]) -> dict:
        if not snaps:
            return self._empty("semantic_index")

        last = snaps[-1]
        vector_count = last.get("vector_count", 0)
        orphans = last.get("orphan_vectors", 0)
        missing = last.get("missing_vectors", 0)
        consistent = last.get("index_consistent", 1)
        latencies = [s.get("search_latency_ms", 0) for s in snaps if s.get("search_latency_ms")]
        avg_latency = self._avg(latencies)

        score = 0
        if consistent:
            score += 4
        if orphans == 0:
            score += 2
        if missing == 0:
            score += 2
        if avg_latency < 20:
            score += 2
        elif avg_latency < 50:
            score += 1

        return {
            "vector_count": vector_count,
            "orphan_vectors": orphans,
            "missing_vectors": missing,
            "index_consistent": bool(consistent),
            "avg_search_latency_ms": round(avg_latency, 1),
            "raw_score": score,
            "max_score": 10,
        }

    # ── Recovery (10 pts) ───────────────────────────────

    def _calc_recovery(self, logs: list[dict]) -> dict:
        if not logs:
            return self._empty("recovery")

        all_recovered = all(log.get("all_recovered") for log in logs)
        l3_ok = all(log.get("l3_recovered") for log in logs)
        sqlite_ok = all(log.get("sqlite_recovered") for log in logs)
        faiss_ok = all(log.get("faiss_recovered") for log in logs)
        mem0_ok = all(log.get("mem0_recovered") for log in logs)

        times = [log.get("recovery_time_ms", 0) for log in logs if log.get("recovery_time_ms")]
        avg_time = self._avg(times)

        score = 0
        if all_recovered:
            score += 5
        elif l3_ok and sqlite_ok and faiss_ok:
            score += 3

        if l3_ok: score += 1
        if sqlite_ok: score += 1
        if faiss_ok: score += 1
        if mem0_ok: score += 1

        if avg_time < 500 and all_recovered:
            score += 1  # fast recovery

        return {
            "all_recovered": all_recovered,
            "l3_recovered": l3_ok,
            "sqlite_recovered": sqlite_ok,
            "faiss_recovered": faiss_ok,
            "mem0_recovered": mem0_ok,
            "avg_recovery_time_ms": round(avg_time, 1),
            "raw_score": score,
            "max_score": 10,
        }

    # ── Pollution (10 pts) ───────────────────────────────

    def _calc_pollution(self, snaps: list[dict]) -> dict:
        if not snaps:
            return self._empty("pollution")

        last = snaps[-1]
        garbage = last.get("garbage_ratio", 0)

        dup_entities = last.get("duplicate_entities", 0)
        dup_facts = last.get("duplicate_facts", 0)
        obsolete = last.get("obsolete_summaries", 0)
        unused = last.get("unused_facts", 0)
        temp = last.get("temporary_facts", 0)

        score = 0
        if garbage < 0.05:
            score += 5  # very clean
        elif garbage < 0.10:
            score += 4
        elif garbage < 0.20:
            score += 2
        elif garbage < 0.30:
            score += 1

        if dup_entities == 0:
            score += 2
        if obsolete == 0:
            score += 2
        if unused / max(last.get("duplicate_facts", 1) + last.get("duplicate_entities", 1), 1) < 0.2:
            score += 1

        return {
            "garbage_ratio": round(garbage, 3),
            "duplicate_entities": dup_entities,
            "duplicate_facts": dup_facts,
            "obsolete_summaries": obsolete,
            "unused_facts": unused,
            "temporary_facts": temp,
            "raw_score": min(10, score),
            "max_score": 10,
        }

    # ── Latency (10 pts) ─────────────────────────────────

    def _calc_latency(self, retrieval, context, semantic, compression, recovery) -> dict:
        # Average latencies per component
        ret_lat = self._avg([r.get("search_latency_ms", 0) for r in retrieval if r.get("search_latency_ms")])
        ctx_lat = self._avg([c.get("build_latency_ms", 0) for c in context if c.get("build_latency_ms")])
        sem_lat = self._avg([s.get("search_latency_ms", 0) for s in semantic if s.get("search_latency_ms")])
        rec_lat = self._avg([r.get("recovery_time_ms", 0) for r in recovery if r.get("recovery_time_ms")])

        # Compression latency is implicit (context build includes it)
        comp_lat = self._avg([c.get("build_latency_ms", 0) for c in context if c.get("build_latency_ms")]) * 0.2

        total_overhead = ret_lat + ctx_lat + sem_lat + rec_lat + comp_lat

        score = 0
        if total_overhead < 50:
            score += 5
        elif total_overhead < 100:
            score += 4
        elif total_overhead < 200:
            score += 3
        elif total_overhead < 500:
            score += 2
        elif total_overhead > 0:
            score += 1

        if ret_lat < 20:
            score += 2
        if ctx_lat < 50:
            score += 2
        if rec_lat < 500:
            score += 1

        return {
            "sqlite_latency_ms": 0,  # not separately measurable without instrumentation
            "faiss_latency_ms": round(ret_lat, 1),
            "mem0_latency_ms": 0,
            "compression_latency_ms": round(comp_lat, 1),
            "context_build_latency_ms": round(ctx_lat, 1),
            "recovery_latency_ms": round(rec_lat, 1),
            "total_memory_overhead_ms": round(total_overhead, 1),
            "raw_score": score,
            "max_score": 10,
        }

    # ── Helpers ─────────────────────────────────────────

    @staticmethod
    def _avg(values: list) -> float:
        if not values:
            return 0.0
        return sum(v for v in values if v is not None) / len(values)

    @staticmethod
    def _growth_ratio(values: list) -> float:
        if not values or len(values) < 2:
            return 1.0
        window = max(1, len(values) // 5)
        first = MemoryMetricsEngine._avg(values[:window])
        last = MemoryMetricsEngine._avg(values[-window:])
        return round(last / first, 2) if first > 0 else 1.0

    @staticmethod
    def _empty(component: str) -> dict:
        return {"raw_score": 0, "max_score": 10, f"{component}_data": "no snapshots"}
