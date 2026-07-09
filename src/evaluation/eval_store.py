"""
EvalStore — SQLite + JSON storage for evaluation metrics.

Stores:
  - runs: evaluation runs with metadata
  - steps: per-step metrics for each run
  - metrics: aggregated metrics per run
  - scores: HAES and component scores
  - benchmarks: saved benchmark baselines
  - comparisons: comparison history between runs

Storage path: ~/.openclaw/evaluation-store/
"""
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class RunMeta:
    """Metadata for one evaluation run."""
    run_id: str
    session_id: str = ""
    task_name: str = ""
    task_description: str = ""
    goal: str = ""
    start_time: float = 0
    end_time: float = 0
    status: str = "pending"  # pending, running, completed, failed
    fail_reason: str = ""
    total_steps: int = 0
    total_tool_calls: int = 0
    artifact_path: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class StepMetrics:
    """Per-step telemetry data."""
    step_id: int
    run_id: str
    timestamp: float = 0

    # Prompt
    prompt_tokens: int = 0
    context_budget: int = 0
    utilization_pct: float = 0.0
    raw_history_size: int = 0

    # Memory
    mem0_facts_count: int = 0
    memory_hit: bool = False
    memory_pollution: float = 0.0
    archive_growth_bytes: int = 0
    compression_count: int = 0

    # Retrieval
    recall_at_5: float = 0.0
    precision_at_5: float = 0.0
    false_retrieval: bool = False
    semantic_search_latency_ms: float = 0.0
    archive_ref_lookup_success: bool = False
    relevant_memories_used: int = 0

    # Planning
    goal_completion: float = 0.0
    plan_completion: float = 0.0
    plan_revisions: int = 0
    blockers_resolved: int = 0
    open_questions_resolved: int = 0
    deviation_from_plan: float = 0.0

    # Reflection
    reflection_ran: bool = False
    useful_reflection: bool = False
    stuck_detected: bool = False
    loop_detected: bool = False
    strategy_changed: bool = False

    # Tools
    tool_calls_this_step: int = 0
    useful_tool_calls: int = 0
    duplicate_tool_calls: int = 0
    failed_tool_calls: int = 0
    cached_tool_calls: int = 0
    tool_latency_ms: float = 0.0

    # Runtime
    build_context_latency_ms: float = 0.0
    retrieval_latency_ms: float = 0.0
    memory_update_latency_ms: float = 0.0
    reflection_latency_ms: float = 0.0
    llm_latency_ms: float = 0.0
    total_step_latency_ms: float = 0.0

    # Context efficiency
    no_raw_tool_outputs: bool = True
    output_reserve_respected: bool = True

    # Recovery (sampled)
    recovery_triggered: bool = False
    recovery_success: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class EvalStore:
    """
    Evaluation data store.

    Creates and manages:
    - eval-store.db (SQLite) for relational data
    - runs/ directory for JSON artifacts
    - benchmarks/ directory for saved benchmarks
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or str(Path.home() / ".openclaw/evaluation-store"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._runs_dir = self.data_dir / "runs"
        self._benchmarks_dir = self.data_dir / "benchmarks"
        self._comparisons_dir = self.data_dir / "comparisons"
        for d in [self._runs_dir, self._benchmarks_dir, self._comparisons_dir]:
            d.mkdir(exist_ok=True)

        self.db_path = str(self.data_dir / "eval-store.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT,
                task_name TEXT,
                task_description TEXT,
                goal TEXT,
                start_time REAL,
                end_time REAL,
                status TEXT DEFAULT 'pending',
                fail_reason TEXT DEFAULT '',
                total_steps INTEGER DEFAULT 0,
                total_tool_calls INTEGER DEFAULT 0,
                artifact_path TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS steps (
                step_id INTEGER,
                run_id TEXT,
                timestamp REAL,
                -- Prompt
                prompt_tokens INTEGER DEFAULT 0,
                context_budget INTEGER DEFAULT 0,
                utilization_pct REAL DEFAULT 0,
                raw_history_size INTEGER DEFAULT 0,
                -- Memory
                mem0_facts_count INTEGER DEFAULT 0,
                memory_hit INTEGER DEFAULT 0,
                memory_pollution REAL DEFAULT 0,
                archive_growth_bytes INTEGER DEFAULT 0,
                compression_count INTEGER DEFAULT 0,
                -- Retrieval
                recall_at_5 REAL DEFAULT 0,
                precision_at_5 REAL DEFAULT 0,
                false_retrieval INTEGER DEFAULT 0,
                semantic_search_latency_ms REAL DEFAULT 0,
                archive_ref_lookup_success INTEGER DEFAULT 0,
                relevant_memories_used INTEGER DEFAULT 0,
                -- Planning
                goal_completion REAL DEFAULT 0,
                plan_completion REAL DEFAULT 0,
                plan_revisions INTEGER DEFAULT 0,
                blockers_resolved INTEGER DEFAULT 0,
                open_questions_resolved INTEGER DEFAULT 0,
                deviation_from_plan REAL DEFAULT 0,
                -- Reflection
                reflection_ran INTEGER DEFAULT 0,
                useful_reflection INTEGER DEFAULT 0,
                stuck_detected INTEGER DEFAULT 0,
                loop_detected INTEGER DEFAULT 0,
                strategy_changed INTEGER DEFAULT 0,
                -- Tools
                tool_calls_this_step INTEGER DEFAULT 0,
                useful_tool_calls INTEGER DEFAULT 0,
                duplicate_tool_calls INTEGER DEFAULT 0,
                failed_tool_calls INTEGER DEFAULT 0,
                cached_tool_calls INTEGER DEFAULT 0,
                tool_latency_ms REAL DEFAULT 0,
                -- Runtime
                build_context_latency_ms REAL DEFAULT 0,
                retrieval_latency_ms REAL DEFAULT 0,
                memory_update_latency_ms REAL DEFAULT 0,
                reflection_latency_ms REAL DEFAULT 0,
                llm_latency_ms REAL DEFAULT 0,
                total_step_latency_ms REAL DEFAULT 0,
                -- Context
                no_raw_tool_outputs INTEGER DEFAULT 1,
                output_reserve_respected INTEGER DEFAULT 1,
                -- Recovery
                recovery_triggered INTEGER DEFAULT 0,
                recovery_success INTEGER DEFAULT 0,
                PRIMARY KEY (run_id, step_id)
            );

            CREATE TABLE IF NOT EXISTS metrics (
                run_id TEXT PRIMARY KEY,
                tts_seconds REAL,
                haes_score REAL,
                -- Component scores
                memory_efficiency_score REAL,
                retrieval_quality_score REAL,
                planning_quality_score REAL,
                reflection_value_score REAL,
                tool_efficiency_score REAL,
                context_efficiency_score REAL,
                latency_score REAL,
                recovery_score REAL,
                learning_reuse_score REAL,
                -- Aggregate metrics
                total_steps INTEGER,
                total_tool_calls INTEGER,
                prompt_avg_tokens REAL,
                prompt_growth_ratio REAL,
                compression_ratio REAL,
                memory_hit_rate REAL,
                tool_success_rate REAL,
                useful_tool_rate REAL,
                plan_completion_rate REAL,
                useful_reflection_rate REAL,
                recovery_success_rate REAL,
                avg_step_latency_ms REAL,
                data_json TEXT DEFAULT '{}',
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS benchmarks (
                benchmark_id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                task_type TEXT,
                baseline_haes REAL,
                baseline_tts REAL,
                metrics_json TEXT DEFAULT '{}',
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS comparisons (
                comparison_id TEXT PRIMARY KEY,
                run_a TEXT,
                run_b TEXT,
                haes_delta REAL,
                tts_delta REAL,
                detail_json TEXT DEFAULT '{}',
                created_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id);
            CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
            CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
        """)
        self.conn.commit()

    # ── Run management ───────────────────────────────────

    def create_run(self, task_name: str = "", task_description: str = "",
                   goal: str = "", session_id: str = "",
                   tags: list[str] = None) -> RunMeta:
        """Create a new evaluation run."""
        run_id = str(uuid.uuid4())[:12]
        run = RunMeta(
            run_id=run_id,
            session_id=session_id,
            task_name=task_name,
            task_description=task_description,
            goal=goal,
            start_time=time.time(),
            status="running",
            tags=tags or [],
        )
        self.conn.execute(
            """INSERT INTO runs (run_id, session_id, task_name, task_description,
               goal, start_time, status, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run.run_id, run.session_id, run.task_name, run.task_description,
             run.goal, run.start_time, run.status, json.dumps(run.tags), time.time())
        )
        self.conn.commit()
        return run

    def complete_run(self, run_id: str, success: bool = True,
                     fail_reason: str = "", artifact_path: str = ""):
        """Mark run as completed or failed."""
        status = "completed" if success else "failed"
        self.conn.execute(
            """UPDATE runs SET end_time=?, status=?, fail_reason=?,
               artifact_path=? WHERE run_id=?""",
            (time.time(), status, fail_reason, artifact_path, run_id)
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        """Get run metadata."""
        row = self.conn.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 20, status: str = None) -> list[dict]:
        """List recent runs."""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM runs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Step recording ──────────────────────────────────

    def record_step(self, step: StepMetrics):
        """Record a single step's metrics."""
        d = step.to_dict()
        columns = list(d.keys())
        placeholders = ["?"] * len(columns)
        values = [d[c] for c in columns]

        self.conn.execute(
            f"INSERT OR REPLACE INTO steps ({','.join(columns)}) "
            f"VALUES ({','.join(placeholders)})",
            values
        )
        self.conn.commit()

    def get_steps(self, run_id: str) -> list[dict]:
        """Get all steps for a run."""
        rows = self.conn.execute(
            "SELECT * FROM steps WHERE run_id=? ORDER BY step_id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_step_count(self, run_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM steps WHERE run_id=?", (run_id,)
        ).fetchone()
        return row["cnt"] if row else 0

    # ── Metrics ─────────────────────────────────────────

    def save_metrics(self, run_id: str, metrics: dict):
        """Save aggregated metrics for a run."""
        data_json = json.dumps(metrics.get("detail", {}), ensure_ascii=False)
        now = time.time()
        params = {
            "run_id": run_id,
            "tts_seconds": metrics.get("tts"),
            "haes_score": metrics.get("haes"),
            "memory_efficiency_score": metrics.get("memory_efficiency", 0),
            "retrieval_quality_score": metrics.get("retrieval_quality", 0),
            "planning_quality_score": metrics.get("planning_quality", 0),
            "reflection_value_score": metrics.get("reflection_value", 0),
            "tool_efficiency_score": metrics.get("tool_efficiency", 0),
            "context_efficiency_score": metrics.get("context_efficiency", 0),
            "latency_score": metrics.get("latency", 0),
            "recovery_score": metrics.get("recovery", 0),
            "learning_reuse_score": metrics.get("learning_reuse", 0),
            "total_steps": metrics.get("total_steps", 0),
            "total_tool_calls": metrics.get("total_tool_calls", 0),
            "prompt_avg_tokens": metrics.get("prompt_avg_tokens", 0),
            "prompt_growth_ratio": metrics.get("prompt_growth_ratio", 0),
            "compression_ratio": metrics.get("compression_ratio", 0),
            "memory_hit_rate": metrics.get("memory_hit_rate", 0),
            "tool_success_rate": metrics.get("tool_success_rate", 0),
            "useful_tool_rate": metrics.get("useful_tool_rate", 0),
            "plan_completion_rate": metrics.get("plan_completion_rate", 0),
            "useful_reflection_rate": metrics.get("useful_reflection_rate", 0),
            "recovery_success_rate": metrics.get("recovery_success_rate", 0),
            "avg_step_latency_ms": metrics.get("avg_step_latency_ms", 0),
            "data_json": data_json,
            "updated_at": now,
        }
        columns = ", ".join(params.keys())
        placeholders = ", ".join(f":{k}" for k in params.keys())
        self.conn.execute(
            f"INSERT OR REPLACE INTO metrics ({columns}) VALUES ({placeholders})",
            params
        )
        self.conn.commit()

    def get_metrics(self, run_id: str) -> Optional[dict]:
        """Get metrics for a run."""
        row = self.conn.execute(
            "SELECT * FROM metrics WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Benchmarks ──────────────────────────────────────

    def save_benchmark(self, name: str, description: str, task_type: str,
                       baseline_haes: float, baseline_tts: float,
                       metrics: dict) -> str:
        """Save a benchmark baseline."""
        bid = str(uuid.uuid4())[:8]
        self.conn.execute(
            """INSERT INTO benchmarks (benchmark_id, name, description, task_type,
               baseline_haes, baseline_tts, metrics_json, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (bid, name, description, task_type, baseline_haes, baseline_tts,
             json.dumps(metrics, ensure_ascii=False), time.time())
        )
        self.conn.commit()
        # Also save as JSON artifact
        bench_path = self._benchmarks_dir / f"{bid}.json"
        with open(bench_path, "w") as f:
            json.dump({
                "benchmark_id": bid, "name": name, "description": description,
                "task_type": task_type, "baseline_haes": baseline_haes,
                "baseline_tts": baseline_tts, "metrics": metrics,
            }, f, ensure_ascii=False, indent=2)
        return bid

    def get_benchmarks(self, task_type: str = None) -> list[dict]:
        """List benchmarks, optionally filtered by task type."""
        if task_type:
            rows = self.conn.execute(
                "SELECT * FROM benchmarks WHERE task_type=? ORDER BY created_at DESC",
                (task_type,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM benchmarks ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Comparisons ─────────────────────────────────────

    def save_comparison(self, run_a: str, run_b: str,
                        haes_delta: float, tts_delta: float,
                        detail: dict) -> str:
        """Save comparison between two runs."""
        cid = str(uuid.uuid4())[:8]
        self.conn.execute(
            """INSERT INTO comparisons (comparison_id, run_a, run_b,
               haes_delta, tts_delta, detail_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (cid, run_a, run_b, haes_delta, tts_delta,
             json.dumps(detail, ensure_ascii=False), time.time())
        )
        self.conn.commit()
        # JSON artifact
        comp_path = self._comparisons_dir / f"{cid}.json"
        with open(comp_path, "w") as f:
            json.dump({
                "comparison_id": cid, "run_a": run_a, "run_b": run_b,
                "haes_delta": haes_delta, "tts_delta": tts_delta,
                "detail": detail,
            }, f, ensure_ascii=False, indent=2)
        return cid

    def get_comparisons(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM comparisons ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Export ──────────────────────────────────────────

    def export_run(self, run_id: str, output_dir: str = None) -> str:
        """Export full run data as JSON artifact."""
        run = self.get_run(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        steps = self.get_steps(run_id)
        metrics = self.get_metrics(run_id)

        export = {
            "run": run,
            "steps": steps,
            "metrics": metrics,
            "exported_at": time.time(),
        }

        output_path = Path(output_dir or self._runs_dir) / f"{run_id}.json"
        with open(output_path, "w") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        return str(output_path)

    def get_stats(self) -> dict:
        """Overall evaluation store statistics."""
        total_runs = self.conn.execute("SELECT COUNT(*) as c FROM runs").fetchone()["c"]
        total_steps = self.conn.execute("SELECT COUNT(*) as c FROM steps").fetchone()["c"]
        completed = self.conn.execute(
            "SELECT COUNT(*) as c FROM runs WHERE status='completed'"
        ).fetchone()["c"]
        failed = self.conn.execute(
            "SELECT COUNT(*) as c FROM runs WHERE status='failed'"
        ).fetchone()["c"]
        benchmarks = self.conn.execute(
            "SELECT COUNT(*) as c FROM benchmarks"
        ).fetchone()["c"]

        avg_haes = None
        if completed > 0:
            row = self.conn.execute(
                "SELECT AVG(haes_score) as avg_haes FROM metrics WHERE haes_score IS NOT NULL"
            ).fetchone()
            avg_haes = round(row["avg_haes"], 1) if row and row["avg_haes"] else None

        return {
            "total_runs": total_runs,
            "total_steps": total_steps,
            "completed_runs": completed,
            "failed_runs": failed,
            "benchmarks_saved": benchmarks,
            "avg_haes": avg_haes,
            "store_size_bytes": sum(
                f.stat().st_size for f in self.data_dir.rglob("*") if f.is_file()
            ),
        }

    def close(self):
        self.conn.close()
