"""
Kettu Mem Performance Benchmarks v0.2.0

Run: python3 scripts/benchmark.py
"""
import time
import tempfile
import shutil
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

os.environ['OPENAI_API_KEY'] = ''

from memory.memory_manager import MemoryManager
from storage.l3_verbatim import L3VerbatimArchive
from storage.sqlite_index import SQLiteMetadataIndex
from retrieval.context_builder import ContextBuilder, ContextConfig
from extractors.ingestion_filter import IngestionFilter
from extractors.memory_quality import MemoryQualityScorer


def benchmark_ingestion(mm: MemoryManager, n: int = 1000) -> dict:
    """Benchmark event ingestion speed."""
    mm.start_session("bench-ingest", "bench")
    events = []
    for i in range(n):
        events.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "type": "message",
            "content": f"Benchmark message number {i} about a random topic {i % 50}",
        })

    t0 = time.time()
    mm.record_batch(events)
    elapsed = time.time() - t0
    return {
        "events": n,
        "total_seconds": round(elapsed, 3),
        "events_per_second": round(n / elapsed, 1),
        "ms_per_event": round(elapsed / n * 1000, 2),
    }


def benchmark_retrieval(mm: MemoryManager, queries: int = 100) -> dict:
    """Benchmark context retrieval latency."""
    latencies = []
    for i in range(queries):
        t0 = time.time()
        mm.build_context(f"query topic {i % 20}")
        latencies.append((time.time() - t0) * 1000)

    latencies.sort()
    return {
        "queries": queries,
        "p50_ms": round(latencies[len(latencies) // 2], 2),
        "p95_ms": round(latencies[int(len(latencies) * 0.95)], 2),
        "p99_ms": round(latencies[int(len(latencies) * 0.99)], 2),
        "avg_ms": round(sum(latencies) / len(latencies), 2),
    }


def benchmark_ingestion_filter() -> dict:
    """Benchmark ingestion filter throughput."""
    filt = IngestionFilter()
    msgs = [f"Normal test message number {i} with enough content" for i in range(1000)]

    t0 = time.time()
    for msg in msgs:
        filt.should_ingest(msg, "user", "message")
    elapsed = time.time() - t0
    return {
        "messages": len(msgs),
        "total_ms": round(elapsed * 1000, 1),
        "ms_per_msg": round(elapsed / len(msgs) * 1000, 3),
        "msgs_per_sec": round(len(msgs) / elapsed, 1),
    }


def benchmark_memory_scoring() -> dict:
    """Benchmark memory quality scoring."""
    scorer = MemoryQualityScorer()
    now = time.time()
    facts = [
        {"type": t, "confidence": 0.8, "created_at": now - i * 86400, "access_count": i % 10}
        for i, t in enumerate(["fact", "preference", "decision", "entity"] * 250)
    ][:1000]

    t0 = time.time()
    scored = scorer.batch_score(facts)
    elapsed = time.time() - t0
    return {
        "facts": len(facts),
        "total_ms": round(elapsed * 1000, 1),
        "ms_per_fact": round(elapsed / len(facts) * 1000, 3),
        "facts_per_sec": round(len(facts) / elapsed, 1),
    }


def benchmark_l3_ops(tmp_dir: str) -> dict:
    """Benchmark L3 archive operations."""
    l3 = L3VerbatimArchive(tmp_dir)

    # Write benchmark
    t0 = time.time()
    for i in range(10000):
        l3.record_event("bench", i, role="user", type="message", content=f"msg-{i}")
    write_elapsed = time.time() - t0

    # Read benchmark
    t0 = time.time()
    events = l3.read_session("bench")
    read_elapsed = time.time() - t0

    return {
        "events": 10000,
        "write_ms": round(write_elapsed * 1000, 1),
        "read_ms": round(read_elapsed * 1000, 1),
        "write_events_per_sec": round(10000 / write_elapsed, 1),
        "read_events_per_sec": round(10000 / read_elapsed, 1),
    }


def benchmark_sqlite_ops(tmp_dir: str) -> dict:
    """Benchmark SQLite operations."""
    db_path = f"{tmp_dir}/bench.db"
    sql = SQLiteMetadataIndex(db_path)

    t0 = time.time()
    for i in range(5000):
        sql.index_event(f"e{i}", "bench", i, role="user", type="message", content=f"msg-{i}")
    write_elapsed = time.time() - t0

    t0 = time.time()
    for _ in range(1000):
        sql.get_recent_events("bench", limit=50)
    read_elapsed = time.time() - t0

    sql.close()
    return {
        "events_written": 5000,
        "queries_run": 1000,
        "write_ms": round(write_elapsed * 1000, 1),
        "read_1000_queries_ms": round(read_elapsed * 1000, 1),
        "ms_per_query": round(read_elapsed / 1000 * 1000, 2),
    }


def run_benchmarks():
    """Run all benchmarks and print report."""
    print("=" * 60)
    print("KETTU MEM v0.2.0 — PERFORMANCE BENCHMARKS")
    print("=" * 60)

    tmp = tempfile.mkdtemp()

    try:
        # 1. Ingestion filter
        print("\n📊 Ingestion Filter")
        r = benchmark_ingestion_filter()
        print(f"  {r['messages']} msgs: {r['ms_per_msg']}ms/msg ({r['msgs_per_sec']} msg/s)")

        # 2. Memory scoring
        print("\n📊 Memory Quality Scoring")
        r = benchmark_memory_scoring()
        print(f"  {r['facts']} facts: {r['ms_per_fact']}ms/fact ({r['facts_per_sec']} facts/s)")

        # 3. L3 Archive
        print("\n📊 L3 Archive (10K events)")
        r = benchmark_l3_ops(tmp)
        print(f"  Write: {r['write_ms']}ms ({r['write_events_per_sec']} evt/s)")
        print(f"  Read:  {r['read_ms']}ms ({r['read_events_per_sec']} evt/s)")

        # 4. SQLite
        print("\n📊 SQLite Index (5K writes, 1K reads)")
        r = benchmark_sqlite_ops(tmp)
        print(f"  Write: {r['write_ms']}ms")
        print(f"  Reads: {r['read_1000_queries_ms']}ms ({r['ms_per_query']}ms/query)")

        # 5. Full pipeline
        print("\n📊 Full Pipeline (1K events)")
        mm = MemoryManager(tmp)
        r = benchmark_ingestion(mm, 1000)
        print(f"  {r['events']} events: {r['total_seconds']}s ({r['events_per_second']} evt/s)")

        print("\n📊 Retrieval Latency (100 queries)")
        r = benchmark_retrieval(mm, 100)
        print(f"  P50: {r['p50_ms']}ms  P95: {r['p95_ms']}ms  P99: {r['p99_ms']}ms  Avg: {r['avg_ms']}ms")

        # RAM usage
        import psutil
        process = psutil.Process(os.getpid())
        mem_mb = process.memory_info().rss / 1024 / 1024

        mm.close()

        print(f"\n📊 Resource Usage")
        print(f"  RAM: {mem_mb:.1f} MB")

        print("\n" + "=" * 60)
        print("✅ BENCHMARKS COMPLETE")
        print("=" * 60)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run_benchmarks()
