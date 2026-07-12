"""
Cognitive Runtime — ядро когнитивного цикла Hermes.

Orchestrates:
  Planner → MemoryManager → Context Builder → LLM → Tools → Reflection → Next Step

Key invariants:
- LLM never interacts directly with archive
- Planning state survives restart
- Context is dynamically assembled per step
- Reflection runs after every agent turn
"""
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class StepOutcome(Enum):
    PROGRESS = "progress"       # задача продвигается
    STUCK = "stuck"             # задача застряла
    LOOP = "loop"               # повторяются действия
    WRONG_TOOL = "wrong_tool"   # выбран неудачный инструмент
    STRATEGY_CHANGE = "strategy_change"  # стоит изменить стратегию
    COMPLETE = "complete"       # задача выполнена
    BLOCKED = "blocked"         # внешняя блокировка


class MemorySpace(Enum):
    GLOBAL = "global"        # общие знания
    USER = "user"            # предпочтения пользователя
    PROJECT = "project"      # проектный контекст
    SESSION = "session"      # текущая сессия
    TEMPORARY = "temporary"  # рабочая память (volatile)


@dataclass
class PlanStep:
    """One step in the execution plan."""
    step_id: int
    description: str
    status: str = "pending"  # pending, in_progress, completed, blocked, skipped
    tool_hint: str = ""      # suggested tool
    result_summary: str = ""
    started_at: float = 0
    completed_at: float = 0
    attempts: int = 0


@dataclass
class PlanningState:
    """Persistent planning state — survives restarts."""
    goal: str = ""
    plan: list[PlanStep] = field(default_factory=list)
    completed_steps: list[int] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    next_action: str = ""
    strategy_notes: str = ""
    created_at: float = 0
    updated_at: float = 0

    def current_step(self) -> Optional[PlanStep]:
        for s in self.plan:
            if s.status in ("in_progress", "pending"):
                return s
        return None

    def progress_pct(self) -> float:
        if not self.plan:
            return 0
        done = sum(1 for s in self.plan if s.status == "completed")
        return done / len(self.plan)

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "plan": [{"step_id": s.step_id, "description": s.description,
                       "status": s.status, "tool_hint": s.tool_hint,
                       "attempts": s.attempts} for s in self.plan],
            "completed_steps": self.completed_steps,
            "blockers": self.blockers,
            "assumptions": self.assumptions,
            "open_questions": self.open_questions,
            "next_action": self.next_action,
            "strategy_notes": self.strategy_notes,
            "progress": f"{self.progress_pct():.0%}",
        }


class ReflectionEngine:
    """
    Rule-based reflection after each agent turn.
    Classifies the step outcome without LLM dependency.
    """

    # Patterns that indicate progress
    PROGRESS_MARKERS = [
        "найдено", "получено", "создан", "записан", "выполнен",
        "found", "created", "written", "completed", "success",
    ]

    # Patterns that indicate being stuck
    STUCK_MARKERS = [
        "не удалось", "ошибка", "error", "failed", "timeout",
        "недоступен", "отказано", "denied", "blocked",
    ]

    # Patterns that indicate loops
    LOOP_PATTERNS = [
        # Same tool called >3 times with similar params
    ]

    def reflect(self, step_result: dict, plan_state: PlanningState,
                tool_history: list[dict]) -> dict:
        """
        Analyze one agent turn and return structured reflection.

        Args:
            step_result: {role, type, content, tool_calls, tool_outputs}
            plan_state: current planning state
            tool_history: recent tool calls (last 10)

        Returns:
            {
                outcome: StepOutcome,
                confidence: float,
                reasoning: str,
                suggestion: str,  # what to do next
                should_change_strategy: bool,
                should_retry_tool: bool,
                useless_tool_calls: list[str],  # tool names to avoid
            }
        """
        content = step_result.get("content", "")
        content_lower = content.lower()
        tool_calls = step_result.get("tool_calls", [])
        tool_outputs = step_result.get("tool_outputs", [])
        errors = [to for to in tool_outputs if to.get("type") == "error"]

        # 1. Check for errors → STUCK
        if errors:
            return {
                "outcome": StepOutcome.STUCK.value,
                "confidence": 0.9,
                "reasoning": f"Encountered {len(errors)} tool errors: {errors[0].get('content','')[:100]}",
                "suggestion": "Retry with different parameters or switch tool.",
                "should_change_strategy": len(errors) >= 2,
                "should_retry_tool": len(errors) == 1,
                "useless_tool_calls": [],
            }

        # 2. Check for progress markers
        progress_hits = sum(1 for m in self.PROGRESS_MARKERS if m in content_lower)
        stuck_hits = sum(1 for m in self.STUCK_MARKERS if m in content_lower)

        # 3. Check for tool loop (same tool called repeatedly)
        tool_names = [tc.get("name", "") for tc in tool_calls]
        recent_tool_names = [th.get("name", "") for th in tool_history[-5:]]
        loop_detected = False
        useless_tools = []

        if tool_names:
            # Check if last 3+ calls used same tool
            all_calls = recent_tool_names + tool_names
            if len(all_calls) >= 3:
                last_three = all_calls[-3:]
                if len(set(last_three)) == 1:
                    loop_detected = True
                    useless_tools.append(last_three[0])

        # 4. Determine outcome
        if loop_detected:
            outcome = StepOutcome.LOOP
            reasoning = f"Same tool '{useless_tools[0]}' called repeatedly without progress."
            suggestion = "Switch strategy — try a different tool or decompose the task."
        elif stuck_hits > progress_hits:
            outcome = StepOutcome.STUCK
            reasoning = f"More failure markers ({stuck_hits}) than progress ({progress_hits})."
            suggestion = "Check tool parameters, consider alternative approach."
        elif progress_hits > 0:
            outcome = StepOutcome.PROGRESS
            reasoning = f"Progress detected: {progress_hits} positive markers."
            suggestion = "Continue current plan step."

            # Check if current plan step is complete
            if plan_state.current_step():
                plan_state.current_step().status = "completed"
                plan_state.current_step().completed_at = time.time()
                plan_state.completed_steps.append(plan_state.current_step().step_id)
        elif len(tool_outputs) == 0 and len(content) < 50:
            outcome = StepOutcome.STUCK
            reasoning = "No tool outputs, short response — potentially stuck."
            suggestion = "Try a concrete action with tools."
        else:
            outcome = StepOutcome.PROGRESS
            reasoning = "No clear signals, assume progress."
            suggestion = "Continue."

        # 5. Strategy change recommendation
        should_change = (
            loop_detected or
            stuck_hits >= 3 or
            (plan_state.current_step() and plan_state.current_step().attempts >= 3)
        )

        return {
            "outcome": outcome.value,
            "confidence": 0.7 if outcome == StepOutcome.PROGRESS else 0.85,
            "reasoning": reasoning,
            "suggestion": suggestion,
            "should_change_strategy": should_change,
            "should_retry_tool": stuck_hits == 1 and not loop_detected,
            "useless_tool_calls": useless_tools,
        }


class ToolIntelligence:
    """
    Tracks tool call patterns and prevents useless repeats.

    Features:
    - Tool call history with params hashing
    - Duplicate detection
    - Usefulness scoring
    - Cache hints
    """

    def __init__(self):
        self._history: list[dict] = []  # {name, params_hash, result_hash, useful, timestamp}
        self._max_history = 100

    def record_call(self, tool_name: str, params: dict, result: str,
                    useful: bool = True):
        import hashlib
        entry = {
            "name": tool_name,
            "params_hash": hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8],
            "result_hash": hashlib.md5(result[:500].encode()).hexdigest()[:8],
            "useful": useful,
            "timestamp": time.time(),
        }
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def is_duplicate(self, tool_name: str, params: dict) -> bool:
        """Check if the same tool+params was called recently."""
        import hashlib
        params_hash = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
        recent = self._history[-10:]
        for h in recent:
            if h["name"] == tool_name and h["params_hash"] == params_hash:
                return True
        return False

    def should_cache(self, tool_name: str, params: dict) -> bool:
        """Determine if result can be cached (same params = same result)."""
        return tool_name in ("web_search", "web_fetch", "read")

    def get_useless_patterns(self) -> list[str]:
        """Return tool names that are consistently useless."""
        if len(self._history) < 5:
            return []
        from collections import Counter
        name_counts = Counter(h["name"] for h in self._history[-20:])
        useless = Counter(h["name"] for h in self._history[-20:] if not h["useful"])
        patterns = []
        for name, count in useless.items():
            if count >= 3 and count / max(name_counts[name], 1) > 0.5:
                patterns.append(name)
        return patterns


class CognitiveRuntime:
    """
    Core cognitive cycle orchestrator.

    Usage:
        cr = CognitiveRuntime(memory_manager, data_dir)
        cr.start_task("Research smart coffee makers market")

        for step in range(500):
            context = cr.build_context(user_input)
            # ... LLM call with context ...
            cr.record_step(assistant_response, tool_calls, tool_outputs)
            reflection = cr.reflect()

            if reflection["outcome"] == "complete":
                break
            if reflection["should_change_strategy"]:
                cr.adjust_strategy()
    """

    def __init__(self, memory_manager, data_dir: str = None):
        self.mm = memory_manager
        self.data_dir = Path(data_dir or "/tmp/cognitive-runtime")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.reflection_engine = ReflectionEngine()
        self.tool_intelligence = ToolIntelligence()
        self.planning_state: PlanningState = PlanningState()
        self.step_counter: int = 0
        self.reflection_history: list[dict] = []
        self.tool_history: list[dict] = []
        self._current_space: MemorySpace = MemorySpace.PROJECT

        # Load state from disk if exists
        self._load_state()

    # ── Task management ──────────────────────────────────

    def start_task(self, goal: str, plan_steps: list[str] = None,
                   space: MemorySpace = MemorySpace.PROJECT):
        """Initialize a new task with goal and optional plan."""
        self.planning_state = PlanningState(
            goal=goal,
            plan=[
                PlanStep(step_id=i, description=s, tool_hint=self._guess_tool(s))
                for i, s in enumerate(plan_steps or [])
            ],
            created_at=time.time(),
            updated_at=time.time(),
        )
        self._current_space = space
        self.step_counter = 0
        self._save_state()

    def resume_task(self) -> bool:
        """Resume task from persisted state. Returns True if state was loaded."""
        loaded = self._load_state()
        if loaded and self.planning_state.goal:
            # step_counter already loaded from file by _load_state
            return True
        return False

    def _guess_tool(self, description: str) -> str:
        """Heuristic tool suggestion based on step description."""
        dl = description.lower()
        if any(w in dl for w in ["поиск", "найди", "search", "find"]):
            return "web_search"
        if any(w in dl for w in ["файл", "запиши", "write", "file"]):
            return "write"
        if any(w in dl for w in ["прочитай", "read", "открой"]):
            return "read"
        if any(w in dl for w in ["анализ", "analyze", "посчитай"]):
            return "analyze_data"
        if any(w in dl for w in ["запусти", "run", "exec", "выполни"]):
            return "exec"
        return ""

    # ── Context building ─────────────────────────────────

    def build_context(self, user_input: str = "",
                      token_budget: int = 32000) -> tuple[str, dict]:
        """
        Dynamic context assembly from all sources.

        Priority order:
        1. Goal + Plan (always)
        2. Reflection from last step
        3. Mem0 facts (current space)
        4. FAISS semantic search
        5. Session summaries
        6. Recent events (filtered)
        7. Active tool schemas
        """
        from retrieval.context_builder import ContextBuilder, ContextConfig

        cfg = ContextConfig(token_budget=token_budget)
        builder = ContextBuilder(cfg)

        # 1. System prompt with goal + plan
        system = self._build_system_prompt()
        builder.set_system(system)

        # 2. Recent events
        if self.mm._session_id:
            recent = self.mm.sqlite.get_recent_events(
                self.mm._session_id, limit=cfg.recent_events_limit
            )
            if recent:
                builder.set_recent_events(recent)

        # 3. Mem0 facts for current space
        space_facts = self._get_space_facts(limit=cfg.max_mem0_facts)
        if space_facts:
            builder.set_mem0_facts(space_facts)

        # 4. Semantic search
        if user_input:
            faiss_results = self.mm.faiss.search(user_input, k=cfg.max_semantic_chunks)
            enriched = self.mm._enrich_faiss_results(faiss_results)
            if enriched:
                builder.set_semantic_results(enriched)

        # 5. Summaries
        if self.mm._session_id:
            summaries = self.mm.sqlite.get_summaries(self.mm._session_id)
            if summaries:
                builder.set_summaries(summaries)

        # Add reflection from last step
        if self.reflection_history:
            last_r = self.reflection_history[-1]
            reflection_text = (
                f"\n## Last Step Reflection\n"
                f"Outcome: {last_r['outcome']}\n"
                f"Reasoning: {last_r['reasoning']}\n"
                f"Suggestion: {last_r['suggestion']}\n"
            )
            # We inject this by adding to recent_events (hack: modify after build)
            builder._add_slice("reflection", reflection_text, priority=1, weight=3.0)

        prompt, stats = builder.build()
        return prompt, stats

    def _build_system_prompt(self) -> str:
        """Build system prompt with goal, plan, and strategy."""
        ps = self.planning_state
        parts = [
            "You are Hermes, a cognitive AI with planning, memory, and reflection capabilities.",
            "",
            f"## 🎯 Goal\n{ps.goal}",
        ]

        if ps.plan:
            parts.append("\n## 📋 Plan")
            for s in ps.plan:
                icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅",
                        "blocked": "🚫", "skipped": "⏭️"}.get(s.status, "⬜")
                parts.append(f"{icon} Step {s.step_id}: {s.description}")

        current = ps.current_step()
        if current:
            parts.append(f"\n## ▶️ Current Step\n{current.description}")
            if current.tool_hint:
                parts.append(f"Suggested tool: {current.tool_hint}")

        if ps.blockers:
            parts.append("\n## 🚫 Blockers\n" + "\n".join(f"- {b}" for b in ps.blockers))

        if ps.open_questions:
            parts.append("\n## ❓ Open Questions\n" + "\n".join(f"- {q}" for q in ps.open_questions))

        if ps.next_action:
            parts.append(f"\n## 👉 Next Action\n{ps.next_action}")

        if ps.strategy_notes:
            parts.append(f"\n## 💡 Strategy Notes\n{ps.strategy_notes}")

        parts.append(f"\n## 🧠 Memory Space: {self._current_space.value}")
        parts.append(f"Progress: {ps.progress_pct():.0%} ({len(ps.completed_steps)}/{len(ps.plan)} steps)")

        return "\n".join(parts)

    # ── Step recording ───────────────────────────────────

    def record_step(self, response: str, tool_calls: list[dict],
                    tool_outputs: list[dict], user_input: str = ""):
        """Record one agent turn across all layers."""
        self.step_counter += 1

        # Ensure MemoryManager session is active
        if not self.mm._session_id:
            session_id = f"cognitive-{int(time.time())}"
            self.mm.start_session(session_id, self._current_space.value)

        # Record in MemoryManager
        if user_input:
            self.mm.record_event("user", "message", user_input)
        self.mm.record_event("assistant", "message", response)

        for tc in tool_calls:
            self.mm.record_event("assistant", "tool_call",
                                 f"{tc.get('name','?')}({json.dumps(tc.get('params',{}))})")
            self.tool_intelligence.record_call(
                tc.get("name", "?"), tc.get("params", {}),
                "", useful=True  # will update after output
            )

        for to in tool_outputs:
            self.mm.record_event("tool", to.get("type", "tool_output"),
                                 to.get("content", "")[:1000])

        # Update tool intelligence with actual results
        for i, tc in enumerate(tool_calls):
            if i < len(tool_outputs):
                to = tool_outputs[i]
                useful = to.get("type") != "error" and len(to.get("content", "")) > 50
                self.tool_intelligence.record_call(
                    tc.get("name", "?"), tc.get("params", {}),
                    to.get("content", ""), useful=useful
                )

        # Update planning state
        current = self.planning_state.current_step()
        if current:
            current.attempts += 1
            current.started_at = current.started_at or time.time()
            if tool_outputs and all(to.get("type") != "error" for to in tool_outputs):
                current.result_summary = response[:200]

        self.planning_state.updated_at = time.time()

        # Run reflection
        reflection = self.reflect(response, tool_calls, tool_outputs)

        # Auto-update plan based on reflection
        if reflection["outcome"] == "complete":
            if current:
                current.status = "completed"
                current.completed_at = time.time()
                self.planning_state.completed_steps.append(current.step_id)
        elif reflection["outcome"] == "progress":
            # Mark step as completed if we have progress and tool outputs
            if current and tool_outputs:
                current.status = "completed"
                current.completed_at = time.time()
                if current.step_id not in self.planning_state.completed_steps:
                    self.planning_state.completed_steps.append(current.step_id)
                # Activate next step
                next_step = None
                for s in self.planning_state.plan:
                    if s.status == "pending":
                        next_step = s
                        break
                if next_step:
                    next_step.status = "in_progress"
                elif current:
                    # All steps done
                    pass
        elif reflection["should_change_strategy"]:
            self.planning_state.strategy_notes = (
                f"Strategy adjusted at step {self.step_counter}: {reflection['suggestion']}"
            )

        self._save_state()

    def reflect(self, response: str, tool_calls: list[dict],
                tool_outputs: list[dict]) -> dict:
        """Run reflection engine on the current step."""
        step_result = {
            "content": response,
            "tool_calls": tool_calls,
            "tool_outputs": tool_outputs,
        }
        reflection = self.reflection_engine.reflect(
            step_result, self.planning_state, self.tool_history
        )
        self.reflection_history.append(reflection)
        if len(self.reflection_history) > 100:
            self.reflection_history = self.reflection_history[-100:]

        self.tool_history.extend(tool_calls)
        if len(self.tool_history) > 50:
            self.tool_history = self.tool_history[-50:]

        return reflection

    # ── Strategy ─────────────────────────────────────────

    def adjust_strategy(self):
        """Adjust strategy based on reflection history."""
        recent = self.reflection_history[-5:]
        stuck_count = sum(1 for r in recent if r["outcome"] in ("stuck", "loop"))

        if stuck_count >= 3:
            self.planning_state.strategy_notes = (
                "⚠ High failure rate. Consider: "
                "1) Decompose task into smaller steps. "
                "2) Try alternative tools. "
                "3) Ask for clarification."
            )

        useless = self.tool_intelligence.get_useless_patterns()
        if useless:
            self.planning_state.strategy_notes += (
                f"\n⚠ Avoid tools: {', '.join(useless)} — low usefulness detected."
            )

        self._save_state()

    # ── Memory Spaces ────────────────────────────────────

    def set_space(self, space: MemorySpace):
        self._current_space = space

    def _get_space_facts(self, limit: int = 10) -> list[dict]:
        """Get Mem0 facts filtered by current memory space."""
        facts = self.mm.mem0.search_text(self._current_space.value, limit=limit * 3)
        # Also include global facts
        if self._current_space != MemorySpace.GLOBAL:
            global_facts = self.mm.mem0.search_text("global", limit=limit)
            facts = global_facts + facts
        return facts[:limit]

    # ── Persistence ──────────────────────────────────────

    def _save_state(self):
        """Persist planning state to disk (survives restart)."""
        state_path = self.data_dir / "planning_state.json"
        data = {
            "goal": self.planning_state.goal,
            "plan": [
                {"step_id": s.step_id, "description": s.description,
                 "status": s.status, "tool_hint": s.tool_hint,
                 "result_summary": s.result_summary,
                 "started_at": s.started_at, "completed_at": s.completed_at,
                 "attempts": s.attempts}
                for s in self.planning_state.plan
            ],
            "completed_steps": self.planning_state.completed_steps,
            "blockers": self.planning_state.blockers,
            "assumptions": self.planning_state.assumptions,
            "open_questions": self.planning_state.open_questions,
            "next_action": self.planning_state.next_action,
            "strategy_notes": self.planning_state.strategy_notes,
            "created_at": self.planning_state.created_at,
            "updated_at": time.time(),
            "step_counter": self.step_counter,
            "current_space": self._current_space.value,
        }
        with open(state_path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_state(self) -> bool:
        """Load planning state from disk."""
        state_path = self.data_dir / "planning_state.json"
        if not state_path.exists():
            return False
        try:
            with open(state_path) as f:
                data = json.load(f)
            self.planning_state.goal = data.get("goal", "")
            self.planning_state.plan = [
                PlanStep(**s) for s in data.get("plan", [])
            ]
            self.planning_state.completed_steps = data.get("completed_steps", [])
            self.planning_state.blockers = data.get("blockers", [])
            self.planning_state.assumptions = data.get("assumptions", [])
            self.planning_state.open_questions = data.get("open_questions", [])
            self.planning_state.next_action = data.get("next_action", "")
            self.planning_state.strategy_notes = data.get("strategy_notes", "")
            self.planning_state.created_at = data.get("created_at", 0)
            self.planning_state.updated_at = data.get("updated_at", 0)
            self.step_counter = data.get("step_counter", 0)
            self._current_space = MemorySpace(data.get("current_space", "project"))
            return True
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            import structlog
            logger = structlog.get_logger("cognitive_runtime")
            logger.error("load_state_failed", error=str(e)[:200])
            return False

    def get_state(self) -> dict:
        """Get full cognitive state for external use."""
        return {
            "planning": self.planning_state.to_dict(),
            "step_counter": self.step_counter,
            "memory_space": self._current_space.value,
            "last_reflection": self.reflection_history[-1] if self.reflection_history else None,
            "useless_tools": self.tool_intelligence.get_useless_patterns(),
        }
