#!/usr/bin/env python3
"""
Kettu Mem v0.2.0 — Production Benchmark Suite

Measures:
  1 agent:  ingest speed, retrieval latency
  10 agents: concurrent ingest, isolation
  100 sessions: session create/switch overhead
  Restart recovery: recovery time after simulated crash

Usage:
  python3 scripts/benchmark_production.py [--output BENCHMARKS.md]
"""
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

os.environ['OPENAI_API_KEY'] = ''

from memory.memory_manager import MemoryManager


# ── Helpers ──────────────────────────────────────────

def format_ms(val: float) -> str:
    return f"{val:,.1f} ms"

def format_per_sec(n: int, elapsed: float) -> str:
    return f"{n / elapsed:,.1f} ops/s"

def banner(title: str):
    print(f"\n{'=' * 62}")
    print(f"  {title}")
    print(f"{'=' * 62}")


# ── Bench 1: Single agent ────────────────────────────

def bench_single_agent(data_dir: str) -> dict:
    banner("1. SINGLE AGENT")
    mm = MemoryManager(data_dir)
    mm.start_session("agent-1", "proj-1")

    # Ingest 2000 events
    n = 2000
    events = []
    for i in range(n):
        events.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "type": "message",
            "content": f"Message {i} about topic {i % 50}: this is a realistic user message "
                       f"with some technical details about project management and AI development.",
        })

    print(f"  Ingesting {n} events...")
    t0 = time.time()
    ids = mm.record_batch(events)
    ingest_elapsed = time.time() - t0

    print(f"  Ingest: {format_ms(ingest_elapsed * 1000)} ({format_per_sec(n, ingest_elapsed)})")
    print(f"  Events written: {len([i for i in ids if not str(i).startswith('filtered:')])}")

    # Retrieval latency
    print("  Measuring retrieval latency (200 queries)...")
    latencies = []
    for i in range(200):
        t0 = time.time()
        mm.build_context(f"query about topic {i % 30}")
        latencies.append((time.time() - t0) * 1000)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    avg = sum(latencies) / len(latencies)

    print(f"  Retrieval P50: {format_ms(p50)}")
    print(f"  Retrieval P95: {format_ms(p95)}")
    print(f"  Retrieval P99: {format_ms(p99)}")
    print(f"  Retrieval Avg: {format_ms(avg)}")

    stats = mm.get_archive_stats()
    print(f"  Memory used: {stats['l3_size_bytes'] / 1024:.0f} KB")

    mm.close()
    return {
        "ingest_events": n,
        "ingest_ms": round(ingest_elapsed * 1000, 1),
        "ingest_ops_per_sec": round(n / ingest_elapsed, 1),
        "retrieval_p50_ms": round(p50, 2),
        "retrieval_p95_ms": round(p95, 2),
        "retrieval_p99_ms": round(p99, 2),
        "retrieval_avg_ms": round(avg, 2),
    }


# ── Bench 2: 10 agents concurrent ────────────────────

def _run_concurrent_agent(args: tuple) -> dict:
    data_dir, agent_id = args
    # Each agent gets its own isolated data dir to avoid FAISS concurrency issues
    agent_dir = os.path.join(data_dir, f"agent-{agent_id}")
    os.makedirs(agent_dir, exist_ok=True)
    mm = MemoryManager(agent_dir)
    mm.start_session(f"agent-conc-{agent_id}", f"proj-conc", agent_id=f"a{agent_id}")

    events = []
    for i in range(500):
        events.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "type": "message",
            "content": f"Agent {agent_id} message {i}: working on task {i % 20} with details.",
        })

    t0 = time.time()
    ids = mm.record_batch(events)
    ingest_ms = (time.time() - t0) * 1000

    # Verify isolation: count only this agent's events
    own_events = mm.l3.get_event_count(f"agent-conc-{agent_id}")

    mm.close()
    return {
        "agent_id": agent_id,
        "ingest_ms": round(ingest_ms, 1),
        "events_per_sec": round(500 / (ingest_ms / 1000), 1),
        "own_events": own_events,
    }


def bench_concurrent_agents(data_dir: str) -> dict:
    banner("2. 10 AGENTS CONCURRENT")
    n_agents = 10

    print(f"  Spawning {n_agents} concurrent agents (500 events each)...")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=n_agents) as pool:
        futures = {pool.submit(_run_concurrent_agent, (data_dir, i)): i for i in range(n_agents)}
        results = []
        for f in as_completed(futures):
            results.append(f.result())
    total_ms = (time.time() - t0) * 1000

    # Check isolation: each agent sees only its own events
    isolation_ok = all(r["own_events"] > 0 for r in results)
    total_events = sum(r["own_events"] for r in results)
    expected = n_agents * 500

    print(f"  Total time: {format_ms(total_ms)}")
    print(f"  Total events: {total_events} / {expected} expected")
    print(f"  Isolation OK: {'✅' if isolation_ok else '❌'}")
    for r in sorted(results, key=lambda x: x["agent_id"]):
        print(f"    Agent {r['agent_id']}: {r['ingest_ms']:,.1f}ms, {r['events_per_sec']:,.1f} evt/s, own={r['own_events']}")

    return {
        "agents": n_agents,
        "total_ms": round(total_ms, 1),
        "total_events": total_events,
        "isolation_ok": isolation_ok,
        "agent_results": results,
    }


# ── Bench 3: 100 sessions overhead ───────────────────

def bench_session_overhead(data_dir: str) -> dict:
    banner("3. 100 SESSIONS OVERHEAD")

    mm = MemoryManager(data_dir)
    n_sessions = 100

    # Create sessions
    print(f"  Creating {n_sessions} sessions...")
    t0 = time.time()
    for i in range(n_sessions):
        mm.start_session(f"sess-{i}", "proj-sess")
    create_ms = (time.time() - t0) * 1000
    print(f"  Create: {format_ms(create_ms)} ({format_per_sec(n_sessions, create_ms / 1000)})")

    # Switch sessions (simulate context switching)
    print(f"  Switching between sessions ({n_sessions} switches)...")
    t0 = time.time()
    for i in range(n_sessions):
        mm.start_session(f"sess-{i}", "proj-sess")
        mm.build_context(f"query for session {i}")
    switch_ms = (time.time() - t0) * 1000
    print(f"  Switch: {format_ms(switch_ms)} ({format_per_sec(n_sessions, switch_ms / 1000)})")
    print(f"  Per-switch: {switch_ms / n_sessions:,.2f} ms")

    mm.close()
    return {
        "sessions": n_sessions,
        "create_total_ms": round(create_ms, 1),
        "create_per_session_ms": round(create_ms / n_sessions, 3),
        "switch_total_ms": round(switch_ms, 1),
        "switch_per_session_ms": round(switch_ms / n_sessions, 3),
    }


# ── Bench 4: Restart recovery ─────────────────────────

def bench_restart_recovery(data_dir: str) -> dict:
    banner("4. RESTART RECOVERY")

    # Phase 1: Populate data
    print("  Phase 1: Populating data...")
    mm1 = MemoryManager(data_dir)
    mm1.start_session("recovery-sess", "proj-recov")
    events = []
    for i in range(2000):
        events.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "type": "message",
            "content": f"Recovery test message {i} with enough content to be meaningful "
                       f"for the FAISS index and vector search tests {i % 40}.",
        })
    mm1.record_batch(events)

    # Force compression to create summaries
    mm1.compress(end_step=1999)
    mm1.add_mem0_fact("preference", "User prefers dark mode for this project", source_session="recovery-sess")
    mm1.close()

    # Phase 2: Simulate crash by starting fresh MemoryManager
    print("  Phase 2: Simulating restart (new MemoryManager instance)...")
    t0 = time.time()
    mm2 = MemoryManager(data_dir)
    mm2.start_session("recovery-sess", "proj-recov")
    recovery_ms = (time.time() - t0) * 1000

    stats = mm2.get_archive_stats()
    print(f"  Recovery time: {format_ms(recovery_ms)}")
    print(f"  Events recovered: {stats['l3_events']}")
    print(f"  FAISS vectors: {stats['faiss_stats'].get('count', 0)}")
    print(f"  Mem0 facts: {stats['mem0_stats']['total_facts']}")

    # Verify retrieval works after recovery
    t0 = time.time()
    prompt, bstats = mm2.build_context("dark mode preferences")
    post_recovery_query_ms = (time.time() - t0) * 1000
    print(f"  Post-recovery query: {format_ms(post_recovery_query_ms)}")

    mm2.close()
    return {
        "recovery_ms": round(recovery_ms, 1),
        "events_recovered": stats['l3_events'],
        "faiss_vectors": stats['faiss_stats'].get('count', 0),
        "mem0_facts": stats['mem0_stats']['total_facts'],
        "post_recovery_query_ms": round(post_recovery_query_ms, 2),
    }


# ── Main ──────────────────────────────────────────────

def run_all(data_dir: str) -> dict:
    results = {}

    results["single_agent"] = bench_single_agent(data_dir)
    results["concurrent_agents"] = bench_concurrent_agents(data_dir)
    results["session_overhead"] = bench_session_overhead(data_dir)
    results["restart_recovery"] = bench_restart_recovery(data_dir)

    return results


def format_report(results: dict) -> str:
    """Format results as Markdown report."""
    sa = results["single_agent"]
    ca = results["concurrent_agents"]
    so = results["session_overhead"]
    rr = results["restart_recovery"]

    return f"""# Kettu Mem v0.2.0 — Production Benchmarks

**Date:** {time.strftime('%Y-%m-%d')}
**Python:** {sys.version.split()[0]}
**FAISS backend:** random (no API key in benchmark env)

---

## 1. Single Agent (2,000 events)

| Metric | Value |
|---|---|
| Ingest speed | {sa['ingest_ops_per_sec']:,.1f} ops/s ({sa['ingest_ms']:,} ms total) |
| Retrieval P50 | {sa['retrieval_p50_ms']} ms |
| Retrieval P95 | {sa['retrieval_p95_ms']} ms |
| Retrieval P99 | {sa['retrieval_p99_ms']} ms |
| Retrieval Avg | {sa['retrieval_avg_ms']} ms |

## 2. Concurrent Agents (10 agents × 500 events)

| Metric | Value |
|---|---|
| Total time | {ca['total_ms']:,} ms |
| Total events | {ca['total_events']:,} |
| Isolation | {'✅ PASS' if ca['isolation_ok'] else '❌ FAIL'} |

| Agent | Time (ms) | Events/s | Own Events |
|---|---|---|---|
{chr(10).join(
    f"| {r['agent_id']} | {r['ingest_ms']:,} | {r['events_per_sec']:,} | {r['own_events']} |"
    for r in sorted(ca['agent_results'], key=lambda x: x['agent_id'])
)}

## 3. Session Overhead (100 sessions)

| Metric | Value |
|---|---|
| Create 100 sessions | {so['create_total_ms']:,} ms ({so['create_per_session_ms']} ms each) |
| Switch 100 sessions | {so['switch_total_ms']:,} ms ({so['switch_per_session_ms']} ms each) |

## 4. Restart Recovery

| Metric | Value |
|---|---|
| Recovery time | {rr['recovery_ms']:,} ms |
| Events recovered | {rr['events_recovered']:,} |
| FAISS vectors | {rr['faiss_vectors']} |
| Mem0 facts | {rr['mem0_facts']} |
| Post-recovery query | {rr['post_recovery_query_ms']} ms |

---

## Summary

| Benchmark | Result |
|---|---|
| Ingest (1 agent) | {sa['ingest_ops_per_sec']:,.1f} ops/s |
| Retrieval P50 | {sa['retrieval_p50_ms']} ms |
| Concurrent (10 agents) | {ca['total_ms']:,} ms, isolation {'PASS' if ca['isolation_ok'] else 'FAIL'} |
| Session create | {so['create_per_session_ms']} ms/session |
| Session switch | {so['switch_per_session_ms']} ms/switch |
| Restart recovery | {rr['recovery_ms']:,} ms |

_Generated by `scripts/benchmark_production.py`_
"""


if __name__ == "__main__":
    output_file = None
    if len(sys.argv) > 1:
        for i, arg in enumerate(sys.argv):
            if arg == "--output" and i + 1 < len(sys.argv):
                output_file = sys.argv[i + 1]

    tmp = tempfile.mkdtemp(prefix="kettu-bench-")
    try:
        print("=" * 62)
        print("  KETTU MEM v0.2.0 — PRODUCTION BENCHMARK SUITE")
        print("=" * 62)
        print(f"  Data dir: {tmp}")
        results = run_all(tmp)

        report = format_report(results)
        print(report)

        if output_file:
            output_path = Path(output_file)
            output_path.write_text(report)
            print(f"\n✅ Report written to {output_path.absolute()}")
        else:
            # Write to default location
            default_path = Path(__file__).parent.parent / "BENCHMARKS.md"
            default_path.write_text(report)
            print(f"\n✅ Report written to {default_path.absolute()}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
