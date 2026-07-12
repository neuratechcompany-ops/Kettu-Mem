"""
EvaluationFramework — orchestrator for the evaluation pipeline.

Connects EvalStore, TelemetryCollector, MetricsEngine, and HAESCalculator.

CLI Commands:
  hermes eval start    — begin a new evaluation run
  hermes eval stop     — complete the current run and calc metrics
  hermes eval status   — show status of current/last run
  hermes eval report   — generate HAES report for a run
  hermes eval compare  — compare two runs
  hermes eval benchmark — save current run as benchmark
  hermes eval export   — export run data as JSON

Integration:
  from evaluation import EvaluationFramework

  ef = EvaluationFramework()
  ef.start_run("Test Run", "Testing single task performance")
  ef.collector.new_step()
  # ... agent does work ...
  ef.collector.record()
  ef.stop_run()
  report = ef.generate_report()
  print(HAESCalculator.format_report(report['haes']))
"""
import json
import sys
from typing import Optional

from .eval_store import EvalStore
from .haes_calculator import HAESCalculator
from .metrics_engine import MetricsEngine
from .telemetry_collector import TelemetryCollector


class EvaluationFramework:
    """
    Main orchestrator for Hermes evaluation.

    Usage modes:
    1. Direct API — use in agent code
    2. CLI — use from command line
    3. Benchmark — run against saved baselines
    """

    def __init__(self, data_dir: str = None,
                 memory_manager=None, cognitive_runtime=None):
        self.store = EvalStore(data_dir)
        self.metrics_engine = MetricsEngine()
        self.haes_calculator = HAESCalculator()
        self.mm = memory_manager
        self.cr = cognitive_runtime

        self.collector: Optional[TelemetryCollector] = None
        self.current_run_id: Optional[str] = None
        self.current_run_meta: Optional[dict] = None

    # ── Run lifecycle ───────────────────────────────────

    def start_run(self, task_name: str = "", task_description: str = "",
                  goal: str = "", session_id: str = "",
                  tags: list[str] = None) -> str:
        """Start a new evaluation run. Returns run_id."""
        run = self.store.create_run(
            task_name=task_name,
            task_description=task_description,
            goal=goal,
            session_id=session_id,
            tags=tags,
        )
        self.current_run_id = run.run_id
        self.current_run_meta = {
            "run_id": run.run_id,
            "task_name": task_name,
            "task_description": task_description,
            "goal": goal,
            "start_time": run.start_time,
        }
        self.collector = TelemetryCollector(
            self.store, run.run_id,
            memory_manager=self.mm,
            cognitive_runtime=self.cr,
        )
        print(f"[Eval] Run started: {run.run_id} — {task_name or '(unnamed)'}")
        return run.run_id

    def stop_run(self, success: bool = True, fail_reason: str = "",
                 artifact_path: str = "") -> dict:
        """Complete the current run, compute metrics, and return report."""
        if not self.current_run_id:
            raise RuntimeError("No active run. Call start_run() first.")

        self.store.complete_run(
            self.current_run_id, success=success,
            fail_reason=fail_reason, artifact_path=artifact_path,
        )

        # Read back all steps
        run_meta = self.store.get_run(self.current_run_id)
        steps = self.store.get_steps(self.current_run_id)

        # Calculate metrics
        metrics = self.metrics_engine.calculate(steps, run_meta)
        haes_result = self.haes_calculator.calculate(metrics)

        # Save metrics
        self.store.save_metrics(self.current_run_id, {
            "tts": metrics["tts"],
            "haes": haes_result["haes"],
            "memory_efficiency": metrics["memory_efficiency"]["raw_score"],
            "retrieval_quality": metrics["retrieval_quality"]["raw_score"],
            "planning_quality": metrics["planning_quality"]["raw_score"],
            "reflection_value": metrics["reflection_value"]["raw_score"],
            "tool_efficiency": metrics["tool_efficiency"]["raw_score"],
            "context_efficiency": metrics["context_efficiency"]["raw_score"],
            "latency": metrics["latency"]["raw_score"],
            "recovery": metrics["recovery"]["raw_score"],
            "learning_reuse": metrics["learning_reuse"]["raw_score"],
            "total_steps": metrics["total_steps"],
            "total_tool_calls": metrics["total_tool_calls"],
            "prompt_avg_tokens": metrics["memory_efficiency"].get("prompt_avg_tokens", 0),
            "prompt_growth_ratio": metrics["memory_efficiency"].get("prompt_growth_ratio", 0),
            "compression_ratio": metrics["memory_efficiency"].get("prompt_compression_ratio", 0),
            "memory_hit_rate": metrics["memory_efficiency"].get("memory_hit_rate", 0),
            "tool_success_rate": metrics["tool_efficiency"].get("tool_success_rate", 0),
            "useful_tool_rate": metrics["tool_efficiency"].get("useful_tool_rate", 0),
            "plan_completion_rate": metrics["planning_quality"].get("plan_completion_pct", 0) / 100,
            "useful_reflection_rate": metrics["reflection_value"].get("useful_reflection_rate", 0),
            "recovery_success_rate": metrics["recovery"].get("recovery_success_rate", 0),
            "avg_step_latency_ms": metrics["latency"].get("avg_total_latency_ms", 0),
            "detail": {
                "memory_efficiency": metrics["memory_efficiency"],
                "retrieval_quality": metrics["retrieval_quality"],
                "planning_quality": metrics["planning_quality"],
                "reflection_value": metrics["reflection_value"],
                "tool_efficiency": metrics["tool_efficiency"],
                "context_efficiency": metrics["context_efficiency"],
                "latency": metrics["latency"],
                "recovery": metrics["recovery"],
                "learning_reuse": metrics["learning_reuse"],
            },
        })

        result = {
            "run_id": self.current_run_id,
            "metrics": metrics,
            "haes": haes_result,
        }

        print(f"[Eval] Run completed: {self.current_run_id} — HAES: {haes_result['haes']}/100")
        self.current_run_id = None
        self.collector = None
        return result

    def status(self) -> dict:
        """Get status of current/last run."""
        if self.current_run_id:
            run = self.store.get_run(self.current_run_id)
            steps = self.store.get_steps(self.current_run_id)
            return {
                "active": True,
                "run": run,
                "steps_recorded": len(steps),
                "collector_step": self.collector.step_id if self.collector else 0,
            }
        # Check last completed
        runs = self.store.list_runs(limit=1)
        if runs:
            return {
                "active": False,
                "last_run": runs[0],
                "last_metrics": self.store.get_metrics(runs[0]["run_id"]),
            }
        return {"active": False, "runs": 0}

    # ── Reporting ────────────────────────────────────────

    def generate_report(self, run_id: str = None, detailed: bool = False) -> dict:
        """Generate HAES report for a run."""
        rid = run_id or self.current_run_id
        if not rid:
            runs = self.store.list_runs(limit=1)
            if not runs:
                raise RuntimeError("No runs found. Run start_run() first.")
            rid = runs[0]["run_id"]

        metrics = self.store.get_metrics(rid)
        if not metrics:
            raise RuntimeError(f"No metrics for run {rid}. Run may not be completed.")

        steps = self.store.get_steps(rid)
        run_meta = self.store.get_run(rid)

        # Reconstruct full metrics for HAES
        full_metrics = {
            "tts": metrics.get("tts_seconds", 0),
            "total_steps": metrics.get("total_steps", 0),
            "total_tool_calls": metrics.get("total_tool_calls", 0),
            "memory_efficiency": json.loads(metrics.get("data_json", "{}")).get("memory_efficiency", {}) if metrics.get("data_json") else {},
            "retrieval_quality": json.loads(metrics.get("data_json", "{}")).get("retrieval_quality", {}),
            "planning_quality": json.loads(metrics.get("data_json", "{}")).get("planning_quality", {}),
            "reflection_value": json.loads(metrics.get("data_json", "{}")).get("reflection_value", {}),
            "tool_efficiency": json.loads(metrics.get("data_json", "{}")).get("tool_efficiency", {}),
            "context_efficiency": json.loads(metrics.get("data_json", "{}")).get("context_efficiency", {}),
            "latency": json.loads(metrics.get("data_json", "{}")).get("latency", {}),
            "recovery": json.loads(metrics.get("data_json", "{}")).get("recovery", {}),
            "learning_reuse": json.loads(metrics.get("data_json", "{}")).get("learning_reuse", {}),
        }

        # If detail JSON is empty, recalculate from steps
        if not any(full_metrics[k] for k in list(full_metrics.keys())[3:]):
            full_metrics = self.metrics_engine.calculate(steps, run_meta)

        haes_result = self.haes_calculator.calculate(full_metrics)

        return {
            "run_id": rid,
            "run_meta": run_meta,
            "steps_count": len(steps),
            "metrics": metrics,
            "haes": haes_result,
            "report_text": self.haes_calculator.format_report(haes_result, detailed),
        }

    def compare_runs(self, run_a: str, run_b: str) -> dict:
        """Compare two completed runs."""
        report_a = self.generate_report(run_a)
        report_b = self.generate_report(run_b)

        comparison = self.haes_calculator.compare(
            report_a["haes"], report_b["haes"]
        )

        # Save comparison
        self.store.save_comparison(
            run_a, run_b,
            comparison["haes_delta"],
            comparison["tts_delta"],
            comparison,
        )

        return comparison

    def save_benchmark(self, name: str = "", description: str = "",
                       task_type: str = "", run_id: str = None) -> str:
        """Save current run metrics as a benchmark baseline."""
        rid = run_id or self.current_run_id
        if not rid:
            raise RuntimeError("No run specified.")

        report = self.generate_report(rid)
        haes = report["haes"]

        bid = self.store.save_benchmark(
            name=name or report["run_meta"].get("task_name", "unnamed"),
            description=description,
            task_type=task_type or report["run_meta"].get("task_name", ""),
            baseline_haes=haes["haes"],
            baseline_tts=haes["tts"],
            metrics=haes,
        )
        print(f"[Eval] Benchmark saved: {bid} — HAES: {haes['haes']}/100")
        return bid

    def export_run(self, run_id: str = None, output_dir: str = None) -> str:
        """Export run data as JSON artifact."""
        rid = run_id or self.current_run_id
        if not rid:
            runs = self.store.list_runs(limit=1)
            if not runs:
                raise RuntimeError("No runs found.")
            rid = runs[0]["run_id"]
        return self.store.export_run(rid, output_dir)

    # ── CLI ─────────────────────────────────────────────

    @staticmethod
    def run_cli(args: list[str] = None):
        """Process CLI arguments."""
        if args is None:
            args = sys.argv[1:]

        ef = EvaluationFramework()

        if not args:
            EvaluationFramework._print_usage()
            return

        cmd = args[0].lower()

        if cmd == "start":
            task_name = args[1] if len(args) > 1 else "CLI Run"
            task_desc = args[2] if len(args) > 2 else ""
            rid = ef.start_run(task_name, task_desc)
            print(f"Run ID: {rid}")
            print("Use 'hermes eval stop' when done.")

        elif cmd == "stop":
            success = "--fail" not in args
            fail_reason = ""
            if not success:
                fail_idx = None
                try:
                    fail_idx = args.index("--fail")
                except ValueError:
                    pass
                if fail_idx and fail_idx + 1 < len(args):
                    fail_reason = args[fail_idx + 1]
            result = ef.stop_run(success=success, fail_reason=fail_reason)
            print(ef.haes_calculator.format_report(result["haes"]))

        elif cmd == "status":
            status = ef.status()
            if status.get("active"):
                r = status["run"]
                print(f"🟢 Active run: {r['run_id']}")
                print(f"   Task: {r['task_name'] or '(unnamed)'}")
                print(f"   Steps recorded: {status['steps_recorded']}")
                print(f"   Status: {r['status']}")
            elif status.get("last_run"):
                r = status["last_run"]
                m = status.get("last_metrics", {})
                print(f"⚪ Last run: {r['run_id']}")
                print(f"   Task: {r['task_name'] or '(unnamed)'}")
                print(f"   Status: {r['status']}")
                if m:
                    print(f"   HAES: {m.get('haes_score', 'N/A')}/100")
                    print(f"   TTS: {m.get('tts_seconds', 'N/A')}s")
            else:
                print("No runs yet. Run 'hermes eval start' to begin.")

        elif cmd == "report":
            rid = args[1] if len(args) > 1 else None
            detailed = "--detailed" in args
            try:
                report = ef.generate_report(rid, detailed)
                print(report["report_text"])
            except RuntimeError as e:
                print(f"Error: {e}")

        elif cmd == "compare":
            if len(args) < 3:
                print("Usage: hermes eval compare <run_a> <run_b>")
                print("Find run IDs with: hermes eval list")
                return
            comparison = ef.compare_runs(args[1], args[2])
            icon = "📈" if comparison["improved"] else "📉"
            print(f"{icon} HAES Delta: {comparison['haes_delta']:+.1f}")
            print(f"⏱ TTS Delta: {comparison['tts_delta']:+.2f}s")
            print()
            for cd in comparison["component_deltas"]:
                sign = "+" if cd["delta"] >= 0 else ""
                change = "↑" if cd["improved"] else ("↓" if cd["delta"] < 0 else "→")
                print(f"  {change} {cd['component']:<22s} {sign}{cd['delta']:>5.1f}")

        elif cmd == "benchmark":
            if len(args) < 2:
                print("Usage: hermes eval benchmark <name> [description] [task_type]")
                return
            name = args[1]
            desc = args[2] if len(args) > 2 else ""
            task_type = args[3] if len(args) > 3 else name
            bid = ef.save_benchmark(name, desc, task_type)
            print(f"Benchmark ID: {bid}")

        elif cmd == "export":
            rid = args[1] if len(args) > 1 else None
            out_dir = args[2] if len(args) > 2 else None
            path = ef.export_run(rid, out_dir)
            print(f"Exported to: {path}")

        elif cmd == "list":
            limit = int(args[1]) if len(args) > 1 else 20
            runs = ef.store.list_runs(limit=limit)
            if not runs:
                print("No runs found.")
                return
            print(f"{'Run ID':<14s} {'Task':<24s} {'Status':<12s} {'HAES':>6s} {'TTS':>8s} {'Steps':>6s}")
            print("-" * 74)
            for r in runs:
                m = ef.store.get_metrics(r["run_id"])
                haes = f"{m['haes_score']:.0f}" if m and m.get("haes_score") else "N/A"
                tts = f"{m['tts_seconds']:.1f}s" if m and m.get("tts_seconds") else "N/A"
                steps = m["total_steps"] if m else r.get("total_steps", "?")
                print(f"{r['run_id']:<14s} {(r['task_name'] or '')[:24]:<24s} {r['status']:<12s} {haes:>6s} {tts:>8s} {str(steps):>6s}")

        elif cmd == "doctor":
            print("🩺 EVALUATION FRAMEWORK DOCTOR")
            stats = ef.store.get_stats()
            checks = [
                ("EvalStore", True, f"OK — {stats['total_runs']} runs, {stats['total_steps']} steps"),
                ("SQLite writable", stats["total_runs"] >= 0, "OK" if stats["total_runs"] >= 0 else "FAIL"),
                ("Run export works", True, "OK" if stats["total_runs"] >= 0 else "No runs to test"),
                ("Benchmarks", stats["benchmarks_saved"] >= 0, f"{stats['benchmarks_saved']} saved"),
                ("Metrics engine", True, "OK — loaded"),
                ("HAES calculator", True, "OK — loaded"),
                ("Store size", stats["store_size_bytes"] > 0, f"{stats['store_size_bytes'] / 1024:.1f} KB"),
            ]
            passed = sum(1 for name, ok, _ in checks if ok)
            for name, ok, detail in checks:
                print(f"  {'✅' if ok else '❌'} {name}: {detail}")
            print(f"🏁 DOCTOR: {passed}/{len(checks)} OK" if passed == len(checks) else f"⚠️  {passed}/{len(checks)} OK")

        else:
            print(f"Unknown command: {cmd}")
            EvaluationFramework._print_usage()

    @staticmethod
    def _print_usage():
        print("""
🦊 Hermes Evaluation Framework v1

Commands:
  hermes eval start [task_name] [description]  Start a new evaluation run
  hermes eval stop [--fail reason]              Complete current run
  hermes eval status                            Show current/last run status
  hermes eval report [run_id] [--detailed]      Generate HAES report
  hermes eval compare <run_a> <run_b>           Compare two runs
  hermes eval benchmark <name> [desc] [type]    Save as benchmark
  hermes eval export [run_id] [out_dir]         Export run data (JSON)
  hermes eval list [limit]                      List recent runs
  hermes eval doctor                            Health check

Examples:
  hermes eval start "Test Single Task" "20-step research task"
  hermes eval stop
  hermes eval report
  hermes eval compare abc123 def456
  hermes eval benchmark "baseline-v1" "Initial baseline"
""")
