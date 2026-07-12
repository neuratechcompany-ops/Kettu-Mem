"""
MetricsEngine — calculates all metric groups from raw step data.

Metric groups:
  1. Memory Efficiency (20 pts)
  2. Retrieval Quality (15 pts)
  3. Planning Quality (15 pts)
  4. Reflection Value (10 pts)
  5. Tool Efficiency (10 pts)
  6. Context Efficiency (10 pts)
  7. Latency (10 pts)
  8. Recovery (10 pts)
  9. Learning / Reuse (10 pts) — requires historical comparison

Input: list of step dicts from EvalStore
Output: dict of metric groups with scores and raw numbers
"""
import math


class MetricsEngine:
    """
    Calculates all evaluation metrics from recorded steps.

    Usage:
        engine = MetricsEngine()
        metrics = engine.calculate(steps, run_meta, previous_runs=None)
    """

    # ── Public API ──────────────────────────────────────

    def calculate(self, steps: list[dict], run_meta: dict = None,
                  previous_runs: list[dict] = None) -> dict:
        """
        Calculate all metric groups from step data.

        Args:
            steps: list of per-step metrics dicts
            run_meta: run metadata (start_time, end_time, total_steps, etc.)
            previous_runs: previous completed runs for learning/reuse comparison

        Returns:
            {
                "tts": float,           # Time To Solution in seconds
                "haes": dict,           # HAES components (to be scored by HAESCalculator)
                "memory_efficiency": {...},
                "retrieval_quality": {...},
                "planning_quality": {...},
                "reflection_value": {...},
                "tool_efficiency": {...},
                "context_efficiency": {...},
                "latency": {...},
                "recovery": {...},
                "learning_reuse": {...},
            }
        """
        if not steps:
            return self._empty_result()

        n = len(steps)
        run_meta = run_meta or {}

        result = {
            "tts": self._calc_tts(run_meta, steps),
            "total_steps": n,
            "total_tool_calls": sum(s.get("tool_calls_this_step", 0) for s in steps),
            "memory_efficiency": self._calc_memory_efficiency(steps, run_meta),
            "retrieval_quality": self._calc_retrieval_quality(steps),
            "planning_quality": self._calc_planning_quality(steps),
            "reflection_value": self._calc_reflection_value(steps),
            "tool_efficiency": self._calc_tool_efficiency(steps),
            "context_efficiency": self._calc_context_efficiency(steps),
            "latency": self._calc_latency(steps),
            "recovery": self._calc_recovery(steps),
            "learning_reuse": self._calc_learning_reuse(steps, run_meta, previous_runs),
        }
        return result

    # ── TTS ─────────────────────────────────────────────

    def _calc_tts(self, run_meta: dict, steps: list[dict]) -> float:
        """Time To Solution in seconds."""
        start = run_meta.get("start_time", 0)
        end = run_meta.get("end_time", 0)
        if start and end:
            return round(end - start, 2)
        if steps:
            start = steps[0].get("timestamp", 0)
            end = steps[-1].get("timestamp", 0)
            return round(end - start, 2) if start and end else 0
        return 0

    # ── Memory Efficiency (20 pts) ──────────────────────

    def _calc_memory_efficiency(self, steps: list[dict], run_meta: dict) -> dict:
        n = len(steps)

        # Prompt compression ratio: how much we compress vs raw history
        avg_prompt = self._avg([s.get("prompt_tokens", 0) for s in steps])
        avg_raw = self._avg([s.get("raw_history_size", 0) for s in steps])
        compression_ratio = round(avg_raw / max(avg_prompt, 1), 2) if avg_raw > 0 else 1.0

        # Prompt size stability
        prompt_tokens_list = [s.get("prompt_tokens", 0) for s in steps]
        prompt_growth = self._growth_ratio(prompt_tokens_list)

        # Memory hit rate
        hits = sum(1 for s in steps if s.get("memory_hit"))
        hit_rate = round(hits / max(n, 1), 3)

        # Mem0 facts
        mem0_end = steps[-1].get("mem0_facts_count", 0) if steps else 0
        mem0_start = steps[0].get("mem0_facts_count", 0) if steps else 0

        # Memory pollution: rate of irrelevant memories in context
        pollution_vals = [s.get("memory_pollution", 0) for s in steps]
        avg_pollution = round(self._avg(pollution_vals), 3)

        # Archive growth per step
        growth_vals = [s.get("archive_growth_bytes", 0) for s in steps]
        total_growth = sum(growth_vals)

        # Compression events
        total_compressions = sum(s.get("compression_count", 0) for s in steps)

        # Score: based on compression ratio, hit rate, and pollution
        # Max 20 pts
        score = 0
        if compression_ratio >= 5:
            score += 8   # excellent compression
        elif compression_ratio >= 2:
            score += 5
        elif compression_ratio >= 1:
            score += 3

        if hit_rate >= 0.5:
            score += 6   # memory is actively used
        elif hit_rate >= 0.3:
            score += 4
        elif hit_rate > 0:
            score += 2

        if avg_pollution < 0.1:
            score += 4   # clean memory
        elif avg_pollution < 0.3:
            score += 2

        if prompt_growth < 1.5:
            score += 2   # stable prompt size

        return {
            "prompt_compression_ratio": compression_ratio,
            "prompt_avg_tokens": round(avg_prompt, 1),
            "prompt_growth_ratio": round(prompt_growth, 2),
            "memory_hit_rate": hit_rate,
            "mem0_facts_start": mem0_start,
            "mem0_facts_end": mem0_end,
            "memory_pollution_avg": avg_pollution,
            "archive_growth_total_kb": round(total_growth / 1024, 2),
            "compression_count": total_compressions,
            "raw_score": round(score, 1),
            "max_score": 20,
        }

    # ── Retrieval Quality (15 pts) ──────────────────────

    def _calc_retrieval_quality(self, steps: list[dict]) -> dict:
        n = len(steps)

        recall_vals = [s.get("recall_at_5", 0) for s in steps if s.get("recall_at_5")]
        precision_vals = [s.get("precision_at_5", 0) for s in steps if s.get("precision_at_5")]
        avg_recall = round(self._avg(recall_vals), 3)
        avg_precision = round(self._avg(precision_vals), 3)

        false_retrievals = sum(1 for s in steps if s.get("false_retrieval"))
        false_rate = round(false_retrievals / max(n, 1), 3)

        search_latencies = [s.get("semantic_search_latency_ms", 0) for s in steps
                           if s.get("semantic_search_latency_ms")]
        avg_search_latency = round(self._avg(search_latencies), 1)

        lookup_successes = sum(1 for s in steps if s.get("archive_ref_lookup_success"))
        lookup_rate = round(lookup_successes / max(n, 1), 3)

        relevant_used = sum(s.get("relevant_memories_used", 0) for s in steps)

        # Score: max 15 pts
        score = 0
        if avg_recall >= 0.8:
            score += 5
        elif avg_recall >= 0.5:
            score += 3
        elif avg_recall > 0:
            score += 1

        if avg_precision >= 0.8:
            score += 5
        elif avg_precision >= 0.5:
            score += 3

        if false_rate < 0.1:
            score += 3
        elif false_rate < 0.3:
            score += 1

        if avg_search_latency < 20:
            score += 2
        elif avg_search_latency < 50:
            score += 1

        return {
            "recall_at_5_avg": avg_recall,
            "precision_at_5_avg": avg_precision,
            "false_retrieval_rate": false_rate,
            "semantic_search_latency_ms_avg": avg_search_latency,
            "archive_lookup_success_rate": lookup_rate,
            "relevant_memories_used_total": relevant_used,
            "raw_score": round(score, 1),
            "max_score": 15,
        }

    # ── Planning Quality (15 pts) ───────────────────────

    def _calc_planning_quality(self, steps: list[dict]) -> dict:
        n = len(steps)

        goal_comp = max(s.get("goal_completion", 0) for s in steps) if steps else 0
        plan_comp = max(s.get("plan_completion", 0) for s in steps) if steps else 0

        revisions = sum(s.get("plan_revisions", 0) for s in steps)
        blockers = sum(s.get("blockers_resolved", 0) for s in steps)
        questions = sum(s.get("open_questions_resolved", 0) for s in steps)

        deviation_vals = [s.get("deviation_from_plan", 0) for s in steps]
        avg_deviation = round(self._avg(deviation_vals), 2)

        # Score: max 15 pts
        score = 0
        if goal_comp >= 80:
            score += 6
        elif goal_comp >= 50:
            score += 3
        elif goal_comp > 0:
            score += 1

        if plan_comp >= 80:
            score += 4
        elif plan_comp >= 50:
            score += 2

        if revisions <= 2:
            score += 2  # stable plan
        elif revisions <= 5:
            score += 1

        if avg_deviation < 20:
            score += 2  # low deviation from plan

        if blockers > 0:
            score += 1  # blockers were resolved

        return {
            "goal_completion_pct": round(goal_comp, 1),
            "plan_completion_pct": round(plan_comp, 1),
            "plan_revisions_total": revisions,
            "blockers_resolved": blockers,
            "open_questions_resolved": questions,
            "deviation_from_plan_avg": avg_deviation,
            "raw_score": round(score, 1),
            "max_score": 15,
        }

    # ── Reflection Value (10 pts) ───────────────────────

    def _calc_reflection_value(self, steps: list[dict]) -> dict:
        n = len(steps)

        reflections_ran = sum(1 for s in steps if s.get("reflection_ran"))
        useful = sum(1 for s in steps if s.get("useful_reflection"))
        stuck = sum(1 for s in steps if s.get("stuck_detected"))
        loops = sum(1 for s in steps if s.get("loop_detected"))
        strategy_changes = sum(1 for s in steps if s.get("strategy_changed"))

        useful_rate = round(useful / max(reflections_ran, 1), 3) if reflections_ran else 0
        behavior_change_rate = round(strategy_changes / max(stuck + loops, 1), 3) if (stuck + loops) else 0

        # Score: max 10 pts
        score = 0
        if useful_rate >= 0.7:
            score += 4
        elif useful_rate >= 0.4:
            score += 2
        elif useful_rate > 0:
            score += 1

        if stuck > 0 or loops > 0:
            score += 2  # problems were detected
        if behavior_change_rate >= 0.5:
            score += 2  # problems led to action
        if strategy_changes > 0:
            score += 2  # strategy was adjusted based on reflection

        return {
            "reflection_count": reflections_ran,
            "useful_reflection_count": useful,
            "useful_reflection_rate": useful_rate,
            "stuck_detections": stuck,
            "loop_detections": loops,
            "strategy_changes": strategy_changes,
            "behavior_change_rate": behavior_change_rate,
            "raw_score": round(score, 1),
            "max_score": 10,
        }

    # ── Tool Efficiency (10 pts) ────────────────────────

    def _calc_tool_efficiency(self, steps: list[dict]) -> dict:
        n = len(steps)

        total = sum(s.get("tool_calls_this_step", 0) for s in steps)
        useful = sum(s.get("useful_tool_calls", 0) for s in steps)
        duplicates = sum(s.get("duplicate_tool_calls", 0) for s in steps)
        failed = sum(s.get("failed_tool_calls", 0) for s in steps)
        cached = sum(s.get("cached_tool_calls", 0) for s in steps)

        success_rate = round((total - failed) / max(total, 1), 3) if total else 0
        useful_rate = round(useful / max(total, 1), 3) if total else 0

        latencies = [s.get("tool_latency_ms", 0) for s in steps if s.get("tool_latency_ms")]
        avg_latency = round(self._avg(latencies), 1)

        # Score: max 10 pts
        score = 0
        if success_rate >= 0.95:
            score += 3
        elif success_rate >= 0.8:
            score += 2
        elif total > 0:
            score += 1

        if useful_rate >= 0.8:
            score += 3
        elif useful_rate >= 0.5:
            score += 2

        if duplicates == 0:
            score += 2  # no wasted tool calls
        elif duplicates <= 2:
            score += 1

        if avg_latency < 1000 and total > 0:
            score += 2  # fast tools
        elif avg_latency < 3000:
            score += 1

        return {
            "total_tool_calls": total,
            "useful_tool_calls": useful,
            "duplicate_tool_calls": duplicates,
            "failed_tool_calls": failed,
            "cached_tool_calls": cached,
            "tool_success_rate": success_rate,
            "useful_tool_rate": useful_rate,
            "avg_tool_latency_ms": avg_latency,
            "raw_score": round(score, 1),
            "max_score": 10,
        }

    # ── Context Efficiency (10 pts) ─────────────────────

    def _calc_context_efficiency(self, steps: list[dict]) -> dict:
        n = len(steps)

        util_vals = [s.get("utilization_pct", 0) for s in steps]
        avg_util = round(self._avg(util_vals), 1)
        max_util = max(util_vals) if util_vals else 0

        prompt_tokens = [s.get("prompt_tokens", 0) for s in steps]
        growth = self._growth_ratio(prompt_tokens)

        raw_tools_in_prompt = sum(1 for s in steps if not s.get("no_raw_tool_outputs", True))
        reserve_respected = sum(1 for s in steps if s.get("output_reserve_respected", True))
        reserve_rate = round(reserve_respected / max(n, 1), 3)

        # Score: max 10 pts
        score = 0
        if 30 <= avg_util <= 80:
            score += 4  # sweet spot: using budget but not maxed out
        elif 10 <= avg_util <= 30:
            score += 3
        elif avg_util > 80:
            score += 1  # too tight

        if growth < 1.3:
            score += 3  # stable
        elif growth < 2.0:
            score += 2

        if raw_tools_in_prompt == 0:
            score += 2  # clean context

        if reserve_rate >= 0.95:
            score += 1

        return {
            "avg_utilization_pct": avg_util,
            "max_utilization_pct": max_util,
            "prompt_growth_ratio": round(growth, 2),
            "raw_tool_outputs_in_prompt": raw_tools_in_prompt,
            "output_reserve_respected_rate": reserve_rate,
            "raw_score": round(score, 1),
            "max_score": 10,
        }

    # ── Latency (10 pts) ────────────────────────────────

    def _calc_latency(self, steps: list[dict]) -> dict:
        n = len(steps)

        total_vals = [s.get("total_step_latency_ms", 0) for s in steps if s.get("total_step_latency_ms")]
        llm_vals = [s.get("llm_latency_ms", 0) for s in steps if s.get("llm_latency_ms")]
        tool_vals = [s.get("tool_latency_ms", 0) for s in steps if s.get("tool_latency_ms")]
        retrieval_vals = [s.get("retrieval_latency_ms", 0) for s in steps if s.get("retrieval_latency_ms")]
        reflection_vals = [s.get("reflection_latency_ms", 0) for s in steps if s.get("reflection_latency_ms")]

        avg_total = round(self._avg(total_vals), 1)
        p50_total = self._percentile(total_vals, 50)
        p99_total = self._percentile(total_vals, 99)

        # Score: max 10 pts
        score = 0
        if avg_total < 100:
            score += 5
        elif avg_total < 500:
            score += 4
        elif avg_total < 2000:
            score += 3
        elif avg_total < 5000:
            score += 2
        elif total_vals:
            score += 1

        if p99_total < 500:
            score += 3
        elif p99_total < 2000:
            score += 2
        elif p99_total < 5000:
            score += 1

        # Stability
        if total_vals:
            p95 = self._percentile(total_vals, 95)
            p5 = self._percentile(total_vals, 5)
            if p5 > 0 and p95 / p5 < 5:
                score += 2  # stable latency
            elif p95 / max(p5, 1) < 10:
                score += 1

        return {
            "avg_total_latency_ms": avg_total,
            "p50_latency_ms": p50_total,
            "p99_latency_ms": p99_total,
            "avg_llm_latency_ms": round(self._avg(llm_vals), 1),
            "avg_tool_latency_ms": round(self._avg(tool_vals), 1),
            "avg_retrieval_latency_ms": round(self._avg(retrieval_vals), 1),
            "avg_reflection_latency_ms": round(self._avg(reflection_vals), 1),
            "raw_score": round(score, 1),
            "max_score": 10,
        }

    # ── Recovery (10 pts) ───────────────────────────────

    def _calc_recovery(self, steps: list[dict]) -> dict:
        n = len(steps)

        recoveries = sum(1 for s in steps if s.get("recovery_triggered"))
        successful = sum(1 for s in steps if s.get("recovery_success"))

        success_rate = round(successful / max(recoveries, 1), 3) if recoveries else 1.0

        # Score: max 10 pts
        score = 0
        if recoveries == 0:
            score += 5  # no failures = perfect
        elif success_rate >= 0.9:
            score += 4  # excellent recovery
        elif success_rate >= 0.7:
            score += 3
        elif recoveries > 0:
            score += 2

        # Fewer recovery events is better
        if recoveries <= 1:
            score += 3
        elif recoveries <= 3:
            score += 2
        elif recoveries <= 5:
            score += 1

        # Graceful degradation: agent continued working despite failures
        if recoveries > 0 and success_rate >= 0.5:
            score += 2  # graceful degradation

        return {
            "recovery_events": recoveries,
            "successful_recoveries": successful,
            "recovery_success_rate": success_rate,
            "raw_score": round(score, 1),
            "max_score": 10,
        }

    # ── Learning / Reuse (10 pts) ───────────────────────

    def _calc_learning_reuse(self, steps: list[dict], run_meta: dict,
                             previous_runs: list[dict] = None) -> dict:
        """
        Compare against previous similar runs to measure learning.

        Requires previous_runs with structure:
        [{run_id, task_type, total_steps, tts, haes, steps_count, ...}]
        """
        if not previous_runs:
            return {
                "similar_tasks_found": 0,
                "steps_reduction_pct": 0,
                "tts_reduction_pct": 0,
                "reused_playbooks": 0,
                "raw_score": 0,
                "max_score": 10,
                "note": "No previous runs for comparison",
            }

        n = len(steps)
        tts = self._calc_tts(run_meta, steps)

        similar = [r for r in previous_runs
                   if r.get("task_type") == run_meta.get("task_name", "")]

        steps_reduction = 0
        tts_reduction = 0
        reused = 0

        if similar:
            avg_prev_steps = self._avg([r.get("total_steps", 0) for r in similar])
            avg_prev_tts = self._avg([r.get("tts_seconds", 0) for r in similar])

            if avg_prev_steps > 0:
                steps_reduction = round((avg_prev_steps - n) / avg_prev_steps * 100, 1)
            if avg_prev_tts > 0:
                tts_reduction = round((avg_prev_tts - tts) / avg_prev_tts * 100, 1)

            reused = sum(1 for r in similar if r.get("playbook_reused"))

        # Score: max 10 pts
        score = 0
        if steps_reduction > 20:
            score += 4  # significant improvement
        elif steps_reduction > 5:
            score += 2
        elif steps_reduction >= 0:
            score += 1

        if tts_reduction > 20:
            score += 4
        elif tts_reduction > 5:
            score += 2

        if reused > 0:
            score += 2

        return {
            "similar_tasks_found": len(similar),
            "steps_reduction_pct": steps_reduction,
            "tts_reduction_pct": tts_reduction,
            "reused_playbooks": reused,
            "raw_score": round(score, 1),
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
        """Ratio of last N values to first N values (detects growth)."""
        if not values or len(values) < 2:
            return 1.0
        # Compare first 20% to last 20%
        window = max(1, len(values) // 5)
        first = MetricsEngine._avg(values[:window])
        last = MetricsEngine._avg(values[-window:])
        if first == 0:
            return 1.0
        return round(last / first, 2)

    @staticmethod
    def _percentile(sorted_values: list, p: int) -> float:
        """Calculate percentile (p=0-100)."""
        if not sorted_values:
            return 0.0
        vals = sorted(sorted_values)
        idx = int(math.ceil(p / 100.0 * len(vals))) - 1
        idx = max(0, min(idx, len(vals) - 1))
        return round(vals[idx], 1)

    @staticmethod
    def _empty_result() -> dict:
        def empty_group(max_score):
            return {"raw_score": 0, "max_score": max_score}

        return {
            "tts": 0,
            "total_steps": 0,
            "total_tool_calls": 0,
            "memory_efficiency": empty_group(20),
            "retrieval_quality": empty_group(15),
            "planning_quality": empty_group(15),
            "reflection_value": empty_group(10),
            "tool_efficiency": empty_group(10),
            "context_efficiency": empty_group(10),
            "latency": empty_group(10),
            "recovery": empty_group(10),
            "learning_reuse": empty_group(10),
        }
