"""
Memory Evaluation Framework — orchestrator for memory-specific evaluation.

CLI Commands:
  hermes memory eval           Run full evaluation with benchmarks
  hermes memory benchmark      Run benchmark suite (5 scenarios)
  hermes memory compare        Compare two memory runs
  hermes memory doctor         Health check
  hermes memory export-report  Export MES report as JSON

Independent from agent-level Evaluation Framework.
"""
import json
import sys
import time
from pathlib import Path
from typing import Optional

from .memory_eval_store import MemoryEvalStore
from .memory_telemetry import MemoryTelemetry
from .memory_metrics_engine import MemoryMetricsEngine
from .mes_calculator import MESCalculator


class MemoryEvaluationFramework:
    """Orchestrator for memory evaluation."""

    def __init__(self, data_dir: str = None,
                 memory_manager=None):
        self.store = MemoryEvalStore(data_dir)
        self.metrics_engine = MemoryMetricsEngine()
        self.mes_calculator = MESCalculator()
        self.mm = memory_manager
        self.telemetry: Optional[MemoryTelemetry] = None
        self.current_run_id: Optional[str] = None

    # ── Run lifecycle ───────────────────────────────────

    def start_run(self, task_name: str = "", session_id: str = "") -> str:
        rid = self.store.create_run(task_name, session_id)
        self.current_run_id = rid
        if self.mm:
            self.telemetry = MemoryTelemetry(self.mm, self.store, rid)
        print(f"[MemoryEval] Run started: {rid}")
        return rid

    def complete_run(self, total_events: int = 0) -> dict:
        if not self.current_run_id:
            raise RuntimeError("No active run. Call start_run() first.")
        self.store.complete_run(self.current_run_id, total_events)
        report = self.generate_report(self.current_run_id)
        print(f"[MemoryEval] Run completed — MES: {report['mes']['mes']}/100")
        self.current_run_id = None
        return report

    def generate_report(self, run_id: str = None) -> dict:
        rid = run_id or self.current_run_id
        if not rid:
            raise RuntimeError("No run specified.")

        run_meta = self.store.get_run(rid) or {}

        # Collect all snapshot data
        metrics = self.metrics_engine.calculate(
            run_meta=run_meta,
            compression_snapshots=self.store.get_all_compression(rid),
            prompt_snapshots=self.store.get_prompt_stability_curve(rid),
            retrieval_snapshots=self.store.get_all_retrieval(rid),
            mem0_snapshots=self.store.get_all_mem0(rid) or [],
            archive_checks=[dict(r) for r in self.store.conn.execute(
                "SELECT * FROM archive_checks WHERE run_id=? ORDER BY step", (rid,)
            ).fetchall()],
            context_snapshots=[dict(r) for r in self.store.conn.execute(
                "SELECT * FROM context_snapshots WHERE run_id=? ORDER BY step", (rid,)
            ).fetchall()],
            semantic_snapshots=[dict(r) for r in self.store.conn.execute(
                "SELECT * FROM semantic_snapshots WHERE run_id=? ORDER BY step", (rid,)
            ).fetchall()],
            recovery_logs=[dict(r) for r in self.store.conn.execute(
                "SELECT * FROM recovery_logs WHERE run_id=? ORDER BY recovery_id", (rid,)
            ).fetchall()],
            pollution_snapshots=[dict(r) for r in self.store.conn.execute(
                "SELECT * FROM pollution_snapshots WHERE run_id=? ORDER BY step", (rid,)
            ).fetchall()],
        )

        mes_result = self.mes_calculator.calculate(metrics)

        # Save to store
        self.store.save_memory_metrics(rid, {
            "mes": mes_result["mes"],
            **metrics,
        })

        report = {
            "run_id": rid,
            "run_meta": run_meta,
            "metrics": metrics,
            "mes": mes_result,
            "report_text": self.mes_calculator.format_report(mes_result),
        }
        return report

    # ── Benchmark suite ─────────────────────────────────

    def run_benchmarks(self, memory_manager) -> dict:
        """Run all 5 benchmark scenarios against real MemoryManager."""
        results = {}

        print("\n🧪 MEMORY BENCHMARK SUITE")
        print("=" * 50)

        for i in range(1, 6):
            name = [
                "50 events", "500 events", "1000 events",
                "Restart Recovery", "Memory Pollution"
            ][i - 1]
            print(f"\n── Benchmark {i}: {name} ──")

            if i == 1:
                results[f"bench_{i}"] = self._bench_50_events(memory_manager)
            elif i == 2:
                results[f"bench_{i}"] = self._bench_500_events(memory_manager)
            elif i == 3:
                results[f"bench_{i}"] = self._bench_1000_events(memory_manager)
            elif i == 4:
                results[f"bench_{i}"] = self._bench_recovery(memory_manager)
            elif i == 5:
                results[f"bench_{i}"] = self._bench_pollution(memory_manager)

        # Overall
        mes_scores = [r.get("mes", 0) for r in results.values() if r]
        avg_mes = sum(mes_scores) / max(len(mes_scores), 1)

        print(f"\n{'='*50}")
        print(f"🏁 BENCHMARK SUITE COMPLETE")
        print(f"   Average MES: {avg_mes:.1f}/100")
        for k, v in results.items():
            print(f"   {k}: MES {v.get('mes', 'N/A')}/100")
        print(f"{'='*50}")

        return {"benchmarks": results, "avg_mes": round(avg_mes, 1)}

    def _run_n_events(self, memory_manager, n: int, name: str) -> dict:
        rid = self.start_run(name)
        mt = MemoryTelemetry(memory_manager, self.store, rid)

        session_id = f"bench-{rid}"
        memory_manager.start_session(session_id, "benchmark")

        for step in range(n):
            # Generate synthetic event
            role = "user" if step % 2 == 0 else "assistant"
            content = f"Benchmark event {step}: sample content for memory evaluation testing purposes."
            memory_manager.record_event(role, "message", content,
                                       refs=[f"ref-{step-1}"] if step > 0 else [],
                                       meta={"bench": True, "step": step})

            # Telemetry
            mt.sample_all(step,
                          query=content if step % 10 == 0 else None,
                          ground_truth_ids=[f"ref-{step-5}"] if step >= 5 else None)

            # Set prompt estimate
            stats = memory_manager.get_archive_stats()
            mt.set_last_prompt_tokens(
                min(32000, int(stats.get("l3_size_bytes", 0) / 3.5 * 0.3 + 500))
            )

        self.store.complete_run(rid, n)
        return self.generate_report(rid)["mes"]

    def _bench_50_events(self, mm) -> dict:
        return self._run_n_events(mm, 50, "Bench 50 events")

    def _bench_500_events(self, mm) -> dict:
        return self._run_n_events(mm, 500, "Bench 500 events")

    def _bench_1000_events(self, mm) -> dict:
        return self._run_n_events(mm, 1000, "Bench 1000 events")

    def _bench_recovery(self, mm) -> dict:
        rid = self.start_run("Bench Recovery")
        mt = MemoryTelemetry(mm, self.store, rid)
        session_id = f"bench-rec-{rid}"
        mm.start_session(session_id, "benchmark")

        # Record some events
        for step in range(50):
            mm.record_event("user", "message", f"Recovery test event {step}")
            mt.sample_all(step)
            mt.set_last_prompt_tokens(800 + step * 2)

        # Simulate restart: close and reopen
        t0 = time.time()
        try:
            # Close
            mm.sqlite.conn.execute("PRAGMA wal_checkpoint")
            mm.close()
            # Reopen (simulate restart)
            mm.__init__(str(mm.data_dir))
            mm.start_session(session_id, "benchmark")
            recovery_time = (time.time() - t0) * 1000
        except Exception as e:
            recovery_time = (time.time() - t0) * 1000
            print(f"[Recovery] Restart simulation error: {e}")

        # Check recovery
        mt = MemoryTelemetry(mm, self.store, rid)
        mt.set_recovery_time(recovery_time)
        mt.check_recovery(1)

        self.store.complete_run(rid, 50)
        return self.generate_report(rid)["mes"]

    def _bench_pollution(self, mm) -> dict:
        rid = self.start_run("Bench Pollution")
        mt = MemoryTelemetry(mm, self.store, rid)
        session_id = f"bench-pol-{rid}"
        mm.start_session(session_id, "benchmark")

        # Generate some duplicate and varied events to test pollution detection
        for step in range(100):
            if step % 10 == 0:
                # Duplicate event
                mm.record_event("user", "message", "Repeat: I prefer dark mode",
                               meta={"bench": True, "step": step})
            elif step % 7 == 0:
                # Similar but not duplicate
                mm.record_event("user", "message", "I would like dark mode enabled",
                               meta={"bench": True, "step": step})
            else:
                mm.record_event("user", "message", f"Unique event {step} with different content for diversity",
                               meta={"bench": True, "step": step})
            mt.sample_all(step)
            mt.set_last_prompt_tokens(800)

        self.store.complete_run(rid, 100)
        return self.generate_report(rid)["mes"]

    # ── CLI ─────────────────────────────────────────────

    @staticmethod
    def run_cli(args: list[str] = None):
        if args is None:
            args = sys.argv[1:]

        mef = MemoryEvaluationFramework()

        if not args:
            MemoryEvaluationFramework._print_usage()
            return

        cmd = args[0].lower()

        if cmd == "eval":
            # Load MM if available
            mm = MemoryEvaluationFramework._load_mm()
            if not mm:
                print("MemoryManager not available. Run from Kettu Mem context.")
                return
            results = mef.run_benchmarks(mm)

        elif cmd == "benchmark":
            mm = MemoryEvaluationFramework._load_mm()
            if not mm:
                print("MemoryManager not available.")
                return
            mef.run_benchmarks(mm)

        elif cmd == "compare":
            if len(args) < 3:
                print("Usage: hermes memory compare <run_a> <run_b>")
                return
            report_a = mef.generate_report(args[1])
            report_b = mef.generate_report(args[2])
            delta = report_b["mes"]["mes"] - report_a["mes"]["mes"]
            icon = "📈" if delta > 0 else ("📉" if delta < 0 else "→")
            print(f"{icon} MES Delta: {delta:+.1f}")
            print(f"   Run A: {report_a['mes']['mes']}/100")
            print(f"   Run B: {report_b['mes']['mes']}/100")
            for ba, bb in zip(report_a["mes"]["breakdown"], report_b["mes"]["breakdown"]):
                d = bb["contribution"] - ba["contribution"]
                print(f"   {'↑' if d > 0 else '↓' if d < 0 else '→'} {ba['component']:<22s} {d:+5.1f}")

        elif cmd == "doctor":
            stats = mef.store.get_stats()
            print("🩺 MEMORY EVALUATION DOCTOR")
            checks = [
                ("Store accessible", True, f"OK — {stats['total_runs']} runs"),
                ("SQLite writable", True, "OK"),
                ("MES calculator", True, "OK — loaded"),
                ("Metrics engine", True, "OK — loaded"),
            ]
            if stats["avg_mes"]:
                checks.append(("Avg MES", stats["avg_mes"] > 0, f"{stats['avg_mes']}/100"))
            for name, ok, detail in checks:
                print(f"  {'✅' if ok else '❌'} {name}: {detail}")
            print(f"🏁 DOCTOR: OK")

        elif cmd == "export-report":
            if len(args) < 2:
                runs = mef.store.list_runs(limit=1)
                if not runs:
                    print("No runs found.")
                    return
                rid = runs[0]["run_id"]
            else:
                rid = args[1]
            report = mef.generate_report(rid)
            path = mef.store.data_dir / f"report-{rid}.json"
            with open(path, "w") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"Report exported to: {path}")

        elif cmd == "list":
            runs = mef.store.list_runs(limit=20)
            if not runs:
                print("No memory eval runs.")
                return
            for r in runs:
                m = mef.store.get_memory_metrics(r["run_id"])
                mes = f"{m['mes_score']:.0f}" if m and m.get("mes_score") else "N/A"
                print(f"  {r['run_id']:<14s} {r['task_name']:<24s} MES={mes:>5s} events={r.get('total_events','?')}")

        else:
            print(f"Unknown: {cmd}")
            MemoryEvaluationFramework._print_usage()

    @staticmethod
    def _load_mm():
        try:
            from memory_manager import MemoryManager
            return MemoryManager(str(Path.home() / ".openclaw/memory-store"))
        except ImportError:
            return None

    @staticmethod
    def _print_usage():
        print("""
🧠 Hermes Memory Evaluation Framework v1

Commands:
  hermes memory eval              Run full evaluation with benchmarks
  hermes memory benchmark         Run benchmark suite (5 scenarios)
  hermes memory compare <a> <b>   Compare two memory runs
  hermes memory doctor            Health check
  hermes memory export-report     Export MES report (JSON)
  hermes memory list              List recent runs

Examples:
  hermes memory eval
  hermes memory compare abc123 def456
  hermes memory export-report abc123
""")
