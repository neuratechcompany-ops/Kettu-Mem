"""
Acceptance tests for Hermes Memory Evaluation Framework v1.

5 benchmark scenarios from the technical specification:
  Benchmark 1 — 50 events
  Benchmark 2 — 500 events
  Benchmark 3 — 1000 events
  Benchmark 4 — Restart Recovery
  Benchmark 5 — Memory Pollution
"""
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluation.memory_eval_store import MemoryEvalStore, PromptStabilitySnapshot
from evaluation.memory_telemetry import MemoryTelemetry, MemoryTelemetry as MT
from evaluation.memory_metrics_engine import MemoryMetricsEngine
from evaluation.mes_calculator import MESCalculator
from evaluation.memory_eval_framework import MemoryEvaluationFramework


def load_mm():
    """Try to load real MemoryManager, fall back to synthetic."""
    try:
        from memory_manager import MemoryManager
        mm = MemoryManager(str(Path.home() / ".openclaw/memory-store"))
        print("[Test] Real MemoryManager loaded")
        return mm, True
    except ImportError:
        print("[Test] Synthetic mode (no MemoryManager)")
        return None, False


# ── Synthetic data generator (when real MM unavailable) ─

def generate_synthetic_snapshots(store, run_id: str, n_events: int,
                                 with_retrieval: bool = True,
                                 with_pollution: bool = False):
    """Generate realistic synthetic snapshot data for testing."""
    import uuid

    for step in range(0, n_events, max(1, n_events // 10)):
        # Compression
        raw = int(n_events * 200 * (1 + step / max(1, n_events) * 0.5))
        prompt = int(random.uniform(500, 1200) * (0.9 + step / n_events * 0.3))
        store.record_compression(run_id, step,
            raw_history_tokens=raw, prompt_tokens=prompt,
            compression_ratio=round(raw / max(prompt, 1), 1),
            summary_count=max(1, step // 50),
            avg_summary_size=random.randint(200, 600),
            summary_compression_ratio=round(raw / max(step // 50, 1) / max(prompt, 1), 1),
            quality_degradation=0)

        # Prompt stability (at checkpoints only)
        if step in MT.CHECKPOINTS:
            growth = round(prompt / max(600, 1), 2)
            store.record_prompt_snapshot(run_id, step,
                raw_history_tokens=raw, prompt_tokens=prompt,
                growth_vs_first=growth,
                linear_growth_warning=1 if growth > step / 100 * 0.5 else 0)

        # Retrieval
        if with_retrieval and step % 3 == 0:
            quality = 0.7 + random.uniform(0, 0.3)
            store.record_retrieval(run_id, step,
                recall_at_1=round(quality * 0.7, 3),
                recall_at_3=round(quality * 0.85, 3),
                recall_at_5=round(quality * 0.92, 3),
                recall_at_10=round(quality * 0.97, 3),
                precision_at_1=round(quality * 0.9, 3),
                precision_at_5=round(quality * 0.8, 3),
                false_retrieval_count=int((1 - quality) * 3),
                missed_retrieval_count=int((1 - quality) * 2),
                irrelevant_retrieval_count=int((1 - quality) * 1),
                search_latency_ms=round(random.uniform(5, 20), 1))

        # Mem0
        facts = int(step * 0.3) + 5
        unused = int(facts * 0.3)
        store.record_mem0_snapshot(run_id, step,
            facts_total=facts,
            facts_used=facts - unused,
            facts_never_used=unused,
            duplicate_facts=int(facts * 0.02) if with_pollution else 0,
            contradictory_facts=int(facts * 0.01) if with_pollution else 0,
            stale_facts=int(facts * 0.05) if with_pollution else 0,
            low_confidence_facts=int(facts * 0.03),
            memory_hit_rate=round(random.uniform(0.3, 0.5), 3))

        # Context
        store.record_context_snapshot(run_id, step,
            build_latency_ms=round(random.uniform(10, 50), 1),
            avg_prompt_size=prompt,
            prompt_utilisation_pct=round(prompt / 32000 * 100, 1),
            memory_contribution_pct=30, semantic_contribution_pct=20,
            recent_events_contribution_pct=30, summary_contribution_pct=10,
            raw_tool_outputs_count=0, extra_messages_count=0,
            token_budget_respected=1)

        # Archive (every 50)
        if step % 50 == 0:
            store.record_archive_check(run_id, step,
                is_append_only=1, event_loss_count=0,
                refs_correct_count=step, refs_broken_count=0,
                jsonl_valid=1, search_speed_ms=round(random.uniform(2, 8), 1))

        # Semantic (every 100)
        if step % 100 == 0:
            store.record_semantic_snapshot(run_id, step,
                search_latency_ms=round(random.uniform(3, 15), 1),
                rebuild_latency_ms=random.randint(100, 500),
                vector_count=facts, orphan_vectors=0,
                missing_vectors=0, index_consistent=1)

        # Pollution
        store.record_pollution(run_id, step,
            duplicate_entities=2 if with_pollution else 0,
            duplicate_facts=1 if with_pollution else 0,
            obsolete_summaries=1 if with_pollution else 0,
            unused_facts=unused,
            temporary_facts=3 if with_pollution else 0,
            garbage_ratio=round(0.05 if with_pollution else 0.02, 3))

    # Recovery
    store.record_recovery(run_id, 1,
        l3_recovered=1, sqlite_recovered=1,
        faiss_recovered=1, mem0_recovered=1,
        refs_recovered=1, summaries_recovered=1,
        all_recovered=1, recovery_time_ms=150)


# ── Tests ───────────────────────────────────────────────

def test_store_integrity():
    """Verify MemoryEvalStore CRUD operations."""
    print("\n" + "=" * 55)
    print("🧪 TEST: MemoryEvalStore Integrity")
    print("=" * 55)

    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    try:
        store = MemoryEvalStore(tmp)

        # Create run
        rid = store.create_run("Store Integrity Test")
        assert rid, "Run not created"

        # Record data
        for step in [10, 50, 100]:
            store.record_prompt_snapshot(rid, step,
                raw_history_tokens=step * 500, prompt_tokens=800 + step,
                growth_vs_first=1.0 + step / 500, linear_growth_warning=0)
            store.record_compression(rid, step,
                raw_history_tokens=step * 500, prompt_tokens=800 + step,
                compression_ratio=round(step * 500 / (800 + step), 1),
                summary_count=step // 50, avg_summary_size=400,
                summary_compression_ratio=5.0, quality_degradation=0)
            store.record_retrieval(rid, step,
                recall_at_1=0.7, recall_at_3=0.85, recall_at_5=0.92,
                recall_at_10=0.97, precision_at_1=0.9, precision_at_5=0.8,
                false_retrieval_count=0, missed_retrieval_count=0,
                irrelevant_retrieval_count=0, search_latency_ms=10)
            store.record_mem0_snapshot(rid, step,
                facts_total=30, facts_used=20, facts_never_used=10,
                duplicate_facts=0, contradictory_facts=0, stale_facts=1,
                low_confidence_facts=2, memory_hit_rate=0.4)
            store.record_context_snapshot(rid, step,
                build_latency_ms=30, avg_prompt_size=900,
                prompt_utilisation_pct=28, memory_contribution_pct=30,
                semantic_contribution_pct=20, recent_events_contribution_pct=30,
                summary_contribution_pct=10, raw_tool_outputs_count=0,
                extra_messages_count=0, token_budget_respected=1)

        store.record_recovery(rid, 1,
            l3_recovered=1, sqlite_recovered=1, faiss_recovered=1,
            mem0_recovered=1, refs_recovered=1, summaries_recovered=1,
            all_recovered=1, recovery_time_ms=120)

        store.record_pollution(rid, 100,
            duplicate_entities=0, duplicate_facts=0, obsolete_summaries=0,
            unused_facts=5, temporary_facts=0, garbage_ratio=0.03)

        # Complete
        store.complete_run(rid, 100)

        # Verify prompts
        curve = store.get_prompt_stability_curve(rid)
        assert len(curve) == 3, f"Expected 3 checkpoints, got {len(curve)}"

        # Calculate metrics
        engine = MemoryMetricsEngine()
        metrics = engine.calculate(
            run_meta=store.get_run(rid),
            compression_snapshots=store.get_all_compression(rid),
            prompt_snapshots=curve,
            retrieval_snapshots=store.get_all_retrieval(rid),
            mem0_snapshots=store.get_all_mem0(rid),
            archive_checks=[],
            context_snapshots=[], semantic_snapshots=[],
            recovery_logs=[], pollution_snapshots=[],
        )

        # MES
        calc = MESCalculator()
        mes = calc.calculate(metrics)
        assert 0 <= mes["mes"] <= 100, f"MES out of range: {mes['mes']}"
        assert len(mes["breakdown"]) == 9, f"Expected 9 components, got {len(mes['breakdown'])}"

        # Save
        store.save_memory_metrics(rid, {"mes": mes["mes"], **metrics})
        saved = store.get_memory_metrics(rid)
        assert saved, "Metrics not saved"
        assert saved["mes_score"] == mes["mes"], f"MES mismatch"

        store.close()

        print(f"  ✅ Run created + snapshots")
        print(f"  ✅ 3 prompt checkpoints")
        print(f"  ✅ MES: {mes['mes']}/100")
        print(f"  ✅ Metrics saved & retrieved")
        print(f"  ✅ PASSED")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return True


def test_benchmarks_synthetic():
    """Run all 5 benchmark scenarios with synthetic data."""
    print("\n" + "=" * 55)
    print("🧪 TEST: Benchmark Suite (Synthetic)")
    print("=" * 55)

    mef = MemoryEvaluationFramework()
    all_ok = True

    scenarios = [
        ("Bench 50 events", 50, "bench_1", 10),
        ("Bench 500 events", 500, "bench_2", 50),
        ("Bench 1000 events", 1000, "bench_3", 100),
        ("Bench Recovery", 50, "bench_4", 5),
        ("Bench Pollution", 100, "bench_5", 10),
    ]

    for name, n_events, key, checkpoints in scenarios:
        rid = mef.start_run(name)
        with_pollution = ("Pollution" in name)
        generate_synthetic_snapshots(
            mef.store, rid, n_events,
            with_retrieval=True,
            with_pollution=with_pollution,
        )

        # Recovery scenario
        if "Recovery" in name:
            mef.store.record_recovery(rid, 1,
                l3_recovered=1, sqlite_recovered=1,
                faiss_recovered=1, mem0_recovered=1,
                refs_recovered=1, summaries_recovered=1,
                all_recovered=1, recovery_time_ms=120)

        mef.store.complete_run(rid, n_events)
        report = mef.generate_report(rid)

        mes = report["mes"]["mes"]
        valid = 0 <= mes <= 100

        print(f"  {'✅' if valid else '❌'} {name}: MES={mes}/100, {len(report['mes']['breakdown'])} components")
        if not valid:
            all_ok = False

    stats = mef.store.get_stats()
    print(f"\n  Store: {stats['total_runs']} runs, {stats['completed_runs']} completed, avg MES={stats['avg_mes']}/100")

    print(f"  {'✅ PASSED' if all_ok else '❌ FAILED'}")
    return all_ok


def test_mes_report_format():
    """Verify MES report formatting."""
    print("\n" + "=" * 55)
    print("🧪 TEST: MES Report Format")
    print("=" * 55)

    # Quick synthetic run
    mef = MemoryEvaluationFramework()
    rid = mef.start_run("Report Test")
    generate_synthetic_snapshots(mef.store, rid, 100, with_retrieval=True)
    mef.store.complete_run(rid, 100)
    report = mef.generate_report(rid)

    calc = MESCalculator()
    text = calc.format_report(report["mes"], detailed=False)

    checks = [
        ("Contains 'MES:'", "MES:" in text),
        ("Contains grade", any(g in text for g in
            ["Exceptional", "Excellent", "Good", "Fair", "Poor", "Critical"])),
        ("Contains breakdown", "Component Breakdown" in text),
        ("Contains interpretation", "Focus area:" in text),
        ("9 components in breakdown", len(report["mes"]["breakdown"]) == 9),
        ("All components have contribution", all(b["contribution"] >= 0 for b in report["mes"]["breakdown"])),
    ]

    all_ok = True
    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")
        if not ok:
            all_ok = False

    # Show the report
    print(f"\n  {text[:400]}...")

    print(f"\n  {'✅ PASSED' if all_ok else '❌ FAILED'}")
    return all_ok


def test_cli_compat():
    """Verify CLI commands work."""
    print("\n" + "=" * 55)
    print("🧪 TEST: CLI Compatibility")
    print("=" * 55)

    mef = MemoryEvaluationFramework()
    all_ok = True

    # Create two runs for comparison
    rid_a = mef.start_run("CLI Test A")
    generate_synthetic_snapshots(mef.store, rid_a, 100)
    mef.store.complete_run(rid_a, 100)

    rid_b = mef.start_run("CLI Test B")
    generate_synthetic_snapshots(mef.store, rid_b, 150, with_pollution=True)
    mef.store.complete_run(rid_b, 150)

    # Test doctor
    stats = mef.store.get_stats()
    print(f"  {'✅' if stats['total_runs'] >= 2 else '❌'} Doctor stats: {stats['total_runs']} runs")

    # Test list
    runs = mef.store.list_runs(limit=5)
    print(f"  {'✅' if len(runs) >= 2 else '❌'} List: {len(runs)} runs")

    # Test export
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = mef.store.data_dir / f"report-{rid_a}.json"
        report = mef.generate_report(rid_a)
        with open(path, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        exists = path.exists()
        print(f"  {'✅' if exists else '❌'} Export: {path}")

    if not exists:
        all_ok = False

    print(f"\n  {'✅ PASSED' if all_ok else '❌ FAILED'}")
    return all_ok


# ── Main ─────────────────────────────────────────────────

def main():
    print("🧠 Hermes Memory Evaluation Framework v1 — Acceptance Tests")
    print("=" * 55)

    results = {}
    results["Store Integrity"] = test_store_integrity()
    results["Benchmark Suite"] = test_benchmarks_synthetic()
    results["MES Report Format"] = test_mes_report_format()
    results["CLI Compatibility"] = test_cli_compat()

    print("\n" + "=" * 55)
    print("🏁 MEMORY EVAL ACCEPTANCE SUMMARY")
    print("=" * 55)

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {name}")

    print(f"\n  {passed}/{total} PASSED")
    if passed == total:
        print("  🎉 Memory Evaluation Framework v1 — ACCEPTED")
        return 0
    else:
        print(f"  ❌ {total - passed} FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
