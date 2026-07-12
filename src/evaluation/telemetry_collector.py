"""
TelemetryCollector — hooks into agent loop to collect raw metrics.

Integrates with MemoryManager and CognitiveRuntime without modifying them.
Collects data at each step of the agent loop and feeds it to EvalStore.

Agent Loop hook points:
  before_prompt_build → capture input state
  after_llm_call       → capture LLM metrics
  after_tool_call      → capture tool metrics
  after_step           → capture reflection + full step
"""

import time
from dataclasses import dataclass
from typing import Optional

from .eval_store import EvalStore, StepMetrics


@dataclass
class StepTrace:
    """Raw data collected during one step of agent loop."""

    # Timing
    t_start: float = 0
    t_llm_done: float = 0
    t_tools_done: float = 0
    t_step_done: float = 0

    # Pre-prompt state
    context_budget: int = 0
    raw_history_size: int = 0
    mem0_facts_count: int = 0
    archive_growth_bytes: int = 0

    # Retrieval metrics
    semantic_search_latency_ms: float = 0
    archive_ref_lookup_success: bool = False
    relevant_memories_used: int = 0
    recall_at_5: float = 0
    precision_at_5: float = 0
    false_retrieval: bool = False

    # LLM output
    prompt_tokens: int = 0
    llm_latency_ms: float = 0
    utilization_pct: float = 0

    # Tool metrics
    tool_calls_this_step: int = 0
    useful_tool_calls: int = 0
    duplicate_tool_calls: int = 0
    failed_tool_calls: int = 0
    cached_tool_calls: int = 0
    tool_latency_ms: float = 0
    no_raw_tool_outputs: bool = True
    output_reserve_respected: bool = True

    # Reflection
    reflection_ran: bool = False
    useful_reflection: bool = False
    stuck_detected: bool = False
    loop_detected: bool = False
    strategy_changed: bool = False
    reflection_latency_ms: float = 0

    # Planning
    goal_completion: float = 0
    plan_completion: float = 0
    plan_revisions: int = 0
    blockers_resolved: int = 0
    open_questions_resolved: int = 0
    deviation_from_plan: float = 0

    # Memory update
    memory_hit: bool = False
    memory_pollution: float = 0
    compression_count: int = 0
    memory_update_latency_ms: float = 0

    # Recovery (if applicable)
    recovery_triggered: bool = False
    recovery_success: bool = False


class TelemetryCollector:
    """
    Non-invasive telemetry collector for the agent loop.

    Usage:
        tc = TelemetryCollector(store, run_id)

        # Before building prompt
        tc.before_prompt(context_budget=32000, ...)

        # After LLM response
        tc.after_llm(prompt_tokens=1500, ...)

        # After tool calls
        tc.after_tools(tool_calls=[...], tool_outputs=[...], ...)

        # After reflection
        tc.after_reflection(reflection={...}, ...)

        # End of step — persists all data
        tc.end_step().record()
    """

    def __init__(self, store: EvalStore, run_id: str, memory_manager=None, cognitive_runtime=None):
        self.store = store
        self.run_id = run_id
        self.mm = memory_manager
        self.cr = cognitive_runtime
        self._step_id: int = 0
        self._trace: Optional[StepTrace] = None
        self._all_steps: list[dict] = []  # for after-the-fact exports

    def new_step(self) -> "TelemetryCollector":
        """Begin a new step trace."""
        self._step_id += 1
        self._trace = StepTrace(t_start=time.time())
        return self

    @property
    def step_id(self) -> int:
        return self._step_id

    def before_prompt(self, **kwargs):
        """Called before context assembly."""
        trace = self._assert_trace()
        for k, v in kwargs.items():
            if hasattr(trace, k):
                setattr(trace, k, v)

    def after_llm(self, **kwargs):
        """Called after LLM generates response."""
        trace = self._assert_trace()
        trace.t_llm_done = time.time()
        if "prompt_tokens" in kwargs:
            trace.prompt_tokens = kwargs["prompt_tokens"]
        if "utilization_pct" in kwargs:
            trace.utilization_pct = kwargs["utilization_pct"]
        trace.llm_latency_ms = (trace.t_llm_done - trace.t_start) * 1000
        for k, v in kwargs.items():
            if hasattr(trace, k):
                setattr(trace, k, v)

    def after_tools(self, tool_calls: list[dict] = None, tool_outputs: list[dict] = None, **kwargs):
        """Called after tool calls complete."""
        trace = self._assert_trace()
        trace.t_tools_done = time.time()

        tcs = tool_calls or []
        tos = tool_outputs or []

        trace.tool_calls_this_step = len(tcs)

        # Classify tool calls
        trace.useful_tool_calls = sum(
            1 for to in tos if to.get("type") != "error" and len(to.get("content", "")) > 20
        )
        trace.failed_tool_calls = sum(1 for to in tos if to.get("type") == "error")
        trace.cached_tool_calls = kwargs.get("cached_tool_calls", 0)

        # Check for raw tool outputs in prompt (they shouldn't be there)
        trace.no_raw_tool_outputs = kwargs.get("no_raw_tool_outputs", True)
        trace.output_reserve_respected = kwargs.get("output_reserve_respected", True)

        # Tool latency
        if tcs:
            trace.tool_latency_ms = (trace.t_tools_done - trace.t_llm_done) * 1000

        for k, v in kwargs.items():
            if hasattr(trace, k):
                setattr(trace, k, v)

    def after_reflection(self, reflection: dict = None, **kwargs):
        """Called after reflection engine runs."""
        trace = self._assert_trace()
        trace.reflection_ran = True

        if reflection:
            outcome = reflection.get("outcome", "")
            trace.useful_reflection = outcome in ("progress", "stuck", "loop", "strategy_change")
            trace.stuck_detected = outcome == "stuck"
            trace.loop_detected = outcome == "loop"
            trace.strategy_changed = reflection.get("should_change_strategy", False)

        trace.reflection_latency_ms = (time.time() - trace.t_tools_done) * 1000
        for k, v in kwargs.items():
            if hasattr(trace, k):
                setattr(trace, k, v)

    def after_recovery(self, success: bool = False, **kwargs):
        """Called if recovery was triggered."""
        trace = self._assert_trace()
        trace.recovery_triggered = True
        trace.recovery_success = success
        for k, v in kwargs.items():
            if hasattr(trace, k):
                setattr(trace, k, v)

    def set_memory_metrics(self, **kwargs):
        """Set memory-specific metrics from MemoryManager."""
        trace = self._assert_trace()
        for k, v in kwargs.items():
            if hasattr(trace, k):
                setattr(trace, k, v)

    def set_planning_metrics(self, planning_state):
        """Extract planning metrics from PlanningState."""
        trace = self._assert_trace()
        if planning_state:
            trace.goal_completion = planning_state.progress_pct() * 100
            total = len(planning_state.plan)
            if total > 0:
                completed = len(planning_state.completed_steps)
                trace.plan_completion = (completed / total) * 100
            # Deviation: count plan revisions
            trace.plan_revisions = getattr(planning_state, "revision_count", 0)

    def set_retrieval_metrics(self, search_results: list = None, faiss_results: list = None):
        """Set retrieval quality metrics."""
        trace = self._assert_trace()
        if faiss_results is not None:
            trace.relevant_memories_used = len(faiss_results)
            trace.semantic_search_latency_ms = getattr(self, "_last_faiss_latency", 0)

    def build_step_metrics(self) -> StepMetrics:
        """Convert trace to StepMetrics dataclass."""
        t = self._assert_trace()
        sm = StepMetrics(
            step_id=self._step_id,
            run_id=self.run_id,
            timestamp=t.t_start,
            # Prompt
            prompt_tokens=t.prompt_tokens,
            context_budget=t.context_budget,
            utilization_pct=t.utilization_pct,
            raw_history_size=t.raw_history_size,
            # Memory
            mem0_facts_count=t.mem0_facts_count,
            memory_hit=t.memory_hit,
            memory_pollution=t.memory_pollution,
            archive_growth_bytes=t.archive_growth_bytes,
            compression_count=t.compression_count,
            # Retrieval
            recall_at_5=t.recall_at_5,
            precision_at_5=t.precision_at_5,
            false_retrieval=t.false_retrieval,
            semantic_search_latency_ms=t.semantic_search_latency_ms,
            archive_ref_lookup_success=t.archive_ref_lookup_success,
            relevant_memories_used=t.relevant_memories_used,
            # Planning
            goal_completion=t.goal_completion,
            plan_completion=t.plan_completion,
            plan_revisions=t.plan_revisions,
            blockers_resolved=t.blockers_resolved,
            open_questions_resolved=t.open_questions_resolved,
            deviation_from_plan=t.deviation_from_plan,
            # Reflection
            reflection_ran=t.reflection_ran,
            useful_reflection=t.useful_reflection,
            stuck_detected=t.stuck_detected,
            loop_detected=t.loop_detected,
            strategy_changed=t.strategy_changed,
            # Tools
            tool_calls_this_step=t.tool_calls_this_step,
            useful_tool_calls=t.useful_tool_calls,
            duplicate_tool_calls=t.duplicate_tool_calls,
            failed_tool_calls=t.failed_tool_calls,
            cached_tool_calls=t.cached_tool_calls,
            tool_latency_ms=t.tool_latency_ms,
            # Runtime
            build_context_latency_ms=t.semantic_search_latency_ms + t.memory_update_latency_ms,
            retrieval_latency_ms=t.semantic_search_latency_ms,
            memory_update_latency_ms=t.memory_update_latency_ms,
            reflection_latency_ms=t.reflection_latency_ms,
            llm_latency_ms=t.llm_latency_ms,
            total_step_latency_ms=(t.t_step_done - t.t_start) * 1000 if t.t_step_done else 0,
            # Context
            no_raw_tool_outputs=t.no_raw_tool_outputs,
            output_reserve_respected=t.output_reserve_respected,
            # Recovery
            recovery_triggered=t.recovery_triggered,
            recovery_success=t.recovery_success,
        )
        return sm

    def record(self) -> StepMetrics:
        """Finalise step trace and persist to store."""
        t = self._assert_trace()
        t.t_step_done = time.time()

        sm = self.build_step_metrics()
        self.store.record_step(sm)
        self._all_steps.append(sm.to_dict())
        return sm

    def record_with_collection(self):
        """
        Full collect + record cycle. Reads live state from MM and CR.

        Call this once per agent turn — it samples everything.
        """
        # MemoryManager state
        if self.mm:
            try:
                stats = self.mm.get_archive_stats()
                t = self._assert_trace()
                t.mem0_facts_count = stats.get("mem0_stats", {}).get("total_facts", 0)
                t.archive_growth_bytes = stats.get("l3_size_bytes", 0) or 0
                # Check if memory was hit this step
                mem0_hits = (
                    self.mm.mem0._last_search_hits
                    if hasattr(self.mm.mem0, "_last_search_hits")
                    else 0
                )
                t.memory_hit = mem0_hits > 0
            except Exception:
                pass

        # CognitiveRuntime state
        if self.cr:
            try:
                ps = self.cr.planning_state
                self.set_planning_metrics(ps)
                t = self._assert_trace()
                t.blockers_resolved = len(ps.completed_steps) if ps else 0
            except Exception:
                pass

        return self.record()

    def get_all_steps(self) -> list[dict]:
        """Get all recorded steps (in-memory)."""
        return self._all_steps

    def _assert_trace(self) -> StepTrace:
        if self._trace is None:
            raise RuntimeError("No active trace. Call new_step() first.")
        return self._trace

    def close(self):
        """Cleanup."""
        self._trace = None
