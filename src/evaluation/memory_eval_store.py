"""
Memory Eval Store — specialised storage for MemoryManager metrics.

Separate from agent-level EvalStore. Focuses exclusively on:
  - Compression metrics
  - Prompt Stability snapshots
  - Retrieval quality (Recall@1/3/5/10, Precision@1/5)
  - Mem0 facts quality
  - Archive integrity
  - Context Builder contributions
  - Semantic Index health
  - Recovery status
  - Memory Pollution

Storage path: ~/.openclaw/memory-evaluation-store/
"""

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class PromptStabilitySnapshot:
    """Capture prompt vs history at key checkpoints."""

    step: int
    raw_history_tokens: int
    prompt_tokens: int
    compression_ratio: float
    timestamp: float = 0


@dataclass
class RetrievalSnapshot:
    """Detailed retrieval quality per checkpoint."""

    step: int
    recall_at_1: float = 0
    recall_at_3: float = 0
    recall_at_5: float = 0
    recall_at_10: float = 0
    precision_at_1: float = 0
    precision_at_5: float = 0
    false_retrieval_count: int = 0
    missed_retrieval_count: int = 0
    irrelevant_retrieval_count: int = 0
    search_latency_ms: float = 0


class MemoryEvalStore:
    """
    Storage for Memory Evaluation Framework.

    Schema:
      - memory_runs: evaluation run metadata
      - compression_snapshots: per-checkpoint compresion data
      - prompt_snapshots: prompt size at 10/50/100/300/500/1000 steps
      - retrieval_snapshots: recall/precision per checkpoint
      - mem0_snapshots: facts quality
      - archive_checks: integrity verification results
      - context_snapshots: context builder contributions
      - semantic_snapshots: FAISS index health
      - recovery_logs: restart recovery results
      - pollution_snapshots: garbage ratio
      - memory_metrics: aggregated MES and component scores
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or str(Path.home() / ".openclaw/memory-evaluation-store"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = str(self.data_dir / "memory-eval.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT,
                task_name TEXT,
                start_time REAL,
                end_time REAL,
                total_events INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS compression_snapshots (
                run_id TEXT,
                step INTEGER,
                raw_history_tokens INTEGER,
                prompt_tokens INTEGER,
                compression_ratio REAL,
                summary_count INTEGER,
                avg_summary_size INTEGER,
                summary_compression_ratio REAL,
                quality_degradation INTEGER DEFAULT 0,
                timestamp REAL,
                PRIMARY KEY (run_id, step)
            );

            CREATE TABLE IF NOT EXISTS prompt_snapshots (
                run_id TEXT,
                step INTEGER,
                raw_history_tokens INTEGER,
                prompt_tokens INTEGER,
                growth_vs_first REAL,
                linear_growth_warning INTEGER DEFAULT 0,
                timestamp REAL,
                PRIMARY KEY (run_id, step)
            );

            CREATE TABLE IF NOT EXISTS retrieval_snapshots (
                run_id TEXT,
                step INTEGER,
                recall_at_1 REAL, recall_at_3 REAL,
                recall_at_5 REAL, recall_at_10 REAL,
                precision_at_1 REAL, precision_at_5 REAL,
                false_retrieval_count INTEGER DEFAULT 0,
                missed_retrieval_count INTEGER DEFAULT 0,
                irrelevant_retrieval_count INTEGER DEFAULT 0,
                search_latency_ms REAL,
                timestamp REAL,
                PRIMARY KEY (run_id, step)
            );

            CREATE TABLE IF NOT EXISTS mem0_snapshots (
                run_id TEXT,
                step INTEGER,
                facts_total INTEGER,
                facts_used INTEGER,
                facts_never_used INTEGER,
                duplicate_facts INTEGER,
                contradictory_facts INTEGER,
                stale_facts INTEGER,
                low_confidence_facts INTEGER,
                memory_hit_rate REAL,
                timestamp REAL,
                PRIMARY KEY (run_id, step)
            );

            CREATE TABLE IF NOT EXISTS archive_checks (
                run_id TEXT,
                step INTEGER,
                is_append_only INTEGER,
                event_loss_count INTEGER DEFAULT 0,
                refs_correct_count INTEGER,
                refs_broken_count INTEGER,
                jsonl_valid INTEGER,
                search_speed_ms REAL,
                timestamp REAL,
                PRIMARY KEY (run_id, step)
            );

            CREATE TABLE IF NOT EXISTS context_snapshots (
                run_id TEXT,
                step INTEGER,
                build_latency_ms REAL,
                avg_prompt_size INTEGER,
                prompt_utilisation_pct REAL,
                memory_contribution_pct REAL,
                semantic_contribution_pct REAL,
                recent_events_contribution_pct REAL,
                summary_contribution_pct REAL,
                raw_tool_outputs_count INTEGER DEFAULT 0,
                extra_messages_count INTEGER DEFAULT 0,
                token_budget_respected INTEGER DEFAULT 1,
                timestamp REAL,
                PRIMARY KEY (run_id, step)
            );

            CREATE TABLE IF NOT EXISTS semantic_snapshots (
                run_id TEXT,
                step INTEGER,
                search_latency_ms REAL,
                rebuild_latency_ms REAL,
                vector_count INTEGER,
                orphan_vectors INTEGER,
                missing_vectors INTEGER,
                index_consistent INTEGER DEFAULT 1,
                timestamp REAL,
                PRIMARY KEY (run_id, step)
            );

            CREATE TABLE IF NOT EXISTS recovery_logs (
                run_id TEXT,
                recovery_id INTEGER,
                l3_recovered INTEGER DEFAULT 0,
                sqlite_recovered INTEGER DEFAULT 0,
                faiss_recovered INTEGER DEFAULT 0,
                mem0_recovered INTEGER DEFAULT 0,
                refs_recovered INTEGER DEFAULT 0,
                summaries_recovered INTEGER DEFAULT 0,
                all_recovered INTEGER DEFAULT 0,
                recovery_time_ms REAL,
                timestamp REAL,
                PRIMARY KEY (run_id, recovery_id)
            );

            CREATE TABLE IF NOT EXISTS pollution_snapshots (
                run_id TEXT,
                step INTEGER,
                duplicate_entities INTEGER,
                duplicate_facts INTEGER,
                obsolete_summaries INTEGER,
                unused_facts INTEGER,
                temporary_facts INTEGER,
                garbage_ratio REAL,
                timestamp REAL,
                PRIMARY KEY (run_id, step)
            );

            CREATE TABLE IF NOT EXISTS memory_metrics (
                run_id TEXT PRIMARY KEY,
                mes_score REAL,
                compression_score REAL,
                prompt_stability_score REAL,
                retrieval_score REAL,
                mem0_score REAL,
                archive_score REAL,
                context_builder_score REAL,
                latency_score REAL,
                recovery_score REAL,
                pollution_score REAL,
                compression_ratio REAL,
                prompt_growth_ratio REAL,
                recall_at_5 REAL,
                precision_at_5 REAL,
                memory_hit_rate REAL,
                duplicate_facts_pct REAL,
                pollution_pct REAL,
                avg_retrieval_ms REAL,
                context_build_ms REAL,
                total_memory_overhead_ms REAL,
                prompt_leakage INT DEFAULT 0,
                data_json TEXT DEFAULT '{}',
                updated_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_comp_run ON compression_snapshots(run_id);
            CREATE INDEX IF NOT EXISTS idx_prompt_run ON prompt_snapshots(run_id);
            CREATE INDEX IF NOT EXISTS idx_retrieval_run ON retrieval_snapshots(run_id);
            CREATE INDEX IF NOT EXISTS idx_mem0_run ON mem0_snapshots(run_id);
            CREATE INDEX IF NOT EXISTS idx_archive_run ON archive_checks(run_id);
            CREATE INDEX IF NOT EXISTS idx_context_run ON context_snapshots(run_id);
        """)
        self.conn.commit()

    # ── Run management ───────────────────────────────────

    def create_run(self, task_name: str = "", session_id: str = "") -> str:
        run_id = str(uuid.uuid4())[:12]
        self.conn.execute(
            "INSERT INTO memory_runs (run_id, session_id, task_name, start_time, status, created_at) "
            "VALUES (?, ?, ?, ?, 'running', ?)",
            (run_id, session_id, task_name, time.time(), time.time()),
        )
        self.conn.commit()
        return run_id

    def complete_run(self, run_id: str, total_events: int = 0):
        self.conn.execute(
            "UPDATE memory_runs SET end_time=?, total_events=?, status='completed' WHERE run_id=?",
            (time.time(), total_events, run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM memory_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM memory_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Snapshot recorders (each table has its own) ──────

    def _insert_snapshot(self, table: str, data: dict):
        columns = list(data.keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        self.conn.execute(
            f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", data
        )
        self.conn.commit()

    def record_compression(self, run_id: str, step: int, **kwargs):
        kwargs.update(run_id=run_id, step=step, timestamp=time.time())
        self._insert_snapshot("compression_snapshots", kwargs)

    def record_prompt_snapshot(self, run_id: str, step: int, **kwargs):
        kwargs.update(run_id=run_id, step=step, timestamp=time.time())
        self._insert_snapshot("prompt_snapshots", kwargs)

    def record_retrieval(self, run_id: str, step: int, **kwargs):
        kwargs.update(run_id=run_id, step=step, timestamp=time.time())
        self._insert_snapshot("retrieval_snapshots", kwargs)

    def record_mem0_snapshot(self, run_id: str, step: int, **kwargs):
        kwargs.update(run_id=run_id, step=step, timestamp=time.time())
        self._insert_snapshot("mem0_snapshots", kwargs)

    def record_archive_check(self, run_id: str, step: int, **kwargs):
        kwargs.update(run_id=run_id, step=step, timestamp=time.time())
        self._insert_snapshot("archive_checks", kwargs)

    def record_context_snapshot(self, run_id: str, step: int, **kwargs):
        kwargs.update(run_id=run_id, step=step, timestamp=time.time())
        self._insert_snapshot("context_snapshots", kwargs)

    def record_semantic_snapshot(self, run_id: str, step: int, **kwargs):
        kwargs.update(run_id=run_id, step=step, timestamp=time.time())
        self._insert_snapshot("semantic_snapshots", kwargs)

    def record_recovery(self, run_id: str, recovery_id: int, **kwargs):
        kwargs.update(run_id=run_id, recovery_id=recovery_id, timestamp=time.time())
        self._insert_snapshot("recovery_logs", kwargs)

    def record_pollution(self, run_id: str, step: int, **kwargs):
        kwargs.update(run_id=run_id, step=step, timestamp=time.time())
        self._insert_snapshot("pollution_snapshots", kwargs)

    # ── Queries ─────────────────────────────────────────

    def get_prompt_stability_curve(self, run_id: str) -> list[dict]:
        """Get prompt vs history data points for chart."""
        rows = self.conn.execute(
            "SELECT step, raw_history_tokens, prompt_tokens, growth_vs_first "
            "FROM prompt_snapshots WHERE run_id=? ORDER BY step",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_compression(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM compression_snapshots WHERE run_id=? ORDER BY step", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_retrieval(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM retrieval_snapshots WHERE run_id=? ORDER BY step", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_mem0(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM mem0_snapshots WHERE run_id=? ORDER BY step", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Metrics ─────────────────────────────────────────

    def save_memory_metrics(self, run_id: str, metrics: dict):
        params = {
            "run_id": run_id,
            "mes_score": metrics.get("mes"),
            "compression_score": metrics.get("compression", {}).get("raw_score", 0),
            "prompt_stability_score": metrics.get("prompt_stability", {}).get("raw_score", 0),
            "retrieval_score": metrics.get("retrieval", {}).get("raw_score", 0),
            "mem0_score": metrics.get("mem0", {}).get("raw_score", 0),
            "archive_score": metrics.get("archive", {}).get("raw_score", 0),
            "context_builder_score": metrics.get("context_builder", {}).get("raw_score", 0),
            "latency_score": metrics.get("latency", {}).get("raw_score", 0),
            "recovery_score": metrics.get("recovery", {}).get("raw_score", 0),
            "pollution_score": metrics.get("pollution", {}).get("raw_score", 0),
            "compression_ratio": metrics.get("compression", {}).get("compression_ratio", 0),
            "prompt_growth_ratio": metrics.get("prompt_stability", {}).get(
                "prompt_growth_ratio", 0
            ),
            "recall_at_5": metrics.get("retrieval", {}).get("recall_at_5", 0),
            "precision_at_5": metrics.get("retrieval", {}).get("precision_at_5", 0),
            "memory_hit_rate": metrics.get("mem0", {}).get("memory_hit_rate", 0),
            "duplicate_facts_pct": metrics.get("mem0", {}).get("duplicate_facts_pct", 0),
            "pollution_pct": metrics.get("pollution", {}).get("garbage_ratio", 0),
            "avg_retrieval_ms": metrics.get("retrieval", {}).get("avg_search_latency_ms", 0),
            "context_build_ms": metrics.get("context_builder", {}).get("avg_build_latency_ms", 0),
            "total_memory_overhead_ms": metrics.get("latency", {}).get(
                "total_memory_overhead_ms", 0
            ),
            "prompt_leakage": metrics.get("context_builder", {}).get("raw_tool_outputs_count", 0),
            "data_json": json.dumps(metrics, ensure_ascii=False),
            "updated_at": time.time(),
        }
        columns = ", ".join(params.keys())
        placeholders = ", ".join(f":{k}" for k in params.keys())
        self.conn.execute(
            f"INSERT OR REPLACE INTO memory_metrics ({columns}) VALUES ({placeholders})", params
        )
        self.conn.commit()

    def get_memory_metrics(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM memory_metrics WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) as c FROM memory_runs").fetchone()["c"]
        completed = self.conn.execute(
            "SELECT COUNT(*) as c FROM memory_runs WHERE status='completed'"
        ).fetchone()["c"]
        avg_mes = self.conn.execute(
            "SELECT AVG(mes_score) as a FROM memory_metrics WHERE mes_score IS NOT NULL"
        ).fetchone()["a"]
        return {
            "total_runs": total,
            "completed_runs": completed,
            "avg_mes": round(avg_mes, 1) if avg_mes else None,
            "store_size_bytes": sum(
                f.stat().st_size for f in self.data_dir.rglob("*") if f.is_file()
            ),
        }

    def close(self):
        self.conn.close()
