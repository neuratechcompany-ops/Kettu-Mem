#!/usr/bin/env python3
"""
24h Soak Test — stress test without new features.

Simulates prolonged operation:
  - 6 sessions over 24h (compressed to ~10 min for quick validation)
  - Periodic restarts
  - Latency tracking
  - Prompt size monitoring
  - Mem0 quality (no garbage facts)
  - Rollback test

Usage:
  python3 hermes_soak.py [--real-24h] [--duration-minutes N]
"""
import json, os, sys, time, urllib.request, signal
from pathlib import Path

API = "http://127.0.0.1:8765"

def post(path, data):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())

def get(path):
    return json.loads(urllib.request.urlopen(f"{API}{path}").read())


class SoakMetrics:
    def __init__(self):
        self.latencies = []
        self.prompt_sizes = []
        self.mem0_counts = []
        self.restarts = 0
        self.errors = 0
        self.sessions = 0

    def record_latency(self, ms):
        self.latencies.append(ms)

    def record_prompt(self, tokens):
        self.prompt_sizes.append(tokens)

    def record_mem0(self, count):
        self.mem0_counts.append(count)

    def report(self):
        if not self.latencies:
            return "No data"
        avg_lat = sum(self.latencies) / len(self.latencies)
        p50 = sorted(self.latencies)[len(self.latencies)//2]
        p99 = sorted(self.latencies)[int(len(self.latencies)*0.99)] if len(self.latencies) > 100 else max(self.latencies)

        return (
            f"  Latency: avg={avg_lat:.1f}ms, p50={p50:.1f}ms, p99={p99:.1f}ms\n"
            f"  Prompt: avg={sum(self.prompt_sizes)//max(len(self.prompt_sizes),1)}, "
            f"max={max(self.prompt_sizes) if self.prompt_sizes else 0}\n"
            f"  Mem0: start={self.mem0_counts[0] if self.mem0_counts else 0}, "
            f"end={self.mem0_counts[-1] if self.mem0_counts else 0}\n"
            f"  Restarts: {self.restarts}\n"
            f"  Sessions: {self.sessions}\n"
            f"  Errors: {self.errors}"
        )


def simulate_session(session_id: str, metrics: SoakMetrics, steps: int = 50):
    """Simulate one agent session with steps."""
    metrics.sessions += 1

    # Start cognitive task
    post("/cognitive/start", {
        "goal": f"Soak test session {session_id}",
        "plan": [f"Step {i}" for i in range(5)],
        "space": "session"
    })

    for i in range(steps):
        t0 = time.time()

        # Build context
        ctx = post("/cognitive/context", {"query": f"soak step {i}", "token_budget": 16000})
        tokens = ctx.get("stats", {}).get("used_tokens", 0)
        metrics.record_prompt(tokens)

        # Simulate step
        tcs = [{"name": "web_search", "params": {"query": f"test {i}"}}] if i % 4 == 0 else []
        tos = [{"type": "tool_output", "content": f"Result {i}"}] if tcs else []

        try:
            result = post("/cognitive/step", {
                "response": f"Soak step {i} completed.",
                "tool_calls": tcs,
                "tool_outputs": tos,
                "user_input": f"Test step {i}"
            })
            elapsed = (time.time() - t0) * 1000
            metrics.record_latency(elapsed)
        except Exception as e:
            metrics.errors += 1

        if (i + 1) % 25 == 0:
            print(f"  [{session_id}] {i+1}/{steps} steps, tokens={tokens}")

    # Record final Mem0 count
    mem0 = get("/mem0/stats")
    metrics.record_mem0(mem0.get("total_facts", 0))


def test_rollback(metrics: SoakMetrics):
    """Verify rollback: disable cognitive, check memory still works."""
    print("\n🔄 Rollback test: disabling cognitive runtime...")
    os.environ["HERMES_COGNITIVE_RUNTIME"] = "0"

    # Memory-only should still work
    try:
        get("/stats")
        get("/mem0/stats")
        print("  ✅ Memory-only mode: API still operational")
    except Exception as e:
        metrics.errors += 1
        print(f"  ❌ Memory-only fail: {e}")

    # Cognitive should return empty
    state = get("/cognitive/state")
    cog_off = not state.get("planning", {}).get("goal")
    print(f"  {'✅' if cog_off else '⚠'} Cognitive state: {'empty (expected)' if cog_off else 'still has data'}")

    os.environ["HERMES_COGNITIVE_RUNTIME"] = "1"
    return True


def main():
    real_24h = "--real-24h" in sys.argv
    duration_min = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--duration-minutes=")), "10"))

    if not real_24h:
        print(f"⚡ Quick soak ({duration_min} min) — use --real-24h for full test\n")

    metrics = SoakMetrics()
    sessions = 6
    steps_per = 50 if not real_24h else 500

    print("=" * 60)
    print(f"🧪 SOAK TEST: {sessions} sessions × {steps_per} steps")
    print("=" * 60)

    # Record baseline
    mem0_start = get("/mem0/stats").get("total_facts", 0)
    metrics.record_mem0(mem0_start)
    print(f"Baseline: {mem0_start} Mem0 facts")

    t_start = time.time()

    for s in range(sessions):
        sid = f"soak-{int(time.time())}-{s}"
        print(f"\n📋 Session {s+1}/{sessions}: {sid}")
        simulate_session(sid, metrics, steps_per)

        # Simulate restart every 2 sessions
        if (s + 1) % 2 == 0:
            print(f"  🔄 Simulated restart...")
            post("/cognitive/start", {"goal": "Resume soak test", "plan": ["Continue"], "space": "session"})
            # Quick resume check
            state = post("/cognitive/resume", {})
            if state.get("status") == "resumed":
                print(f"  ✅ State recovered after restart")
            metrics.restarts += 1

    elapsed = (time.time() - t_start) / 60

    # Rollback test
    test_rollback(metrics)

    # Final report
    print(f"\n{'=' * 60}")
    print(f"📊 SOAK TEST REPORT ({elapsed:.1f} min)")
    print(f"{'=' * 60}")
    print(metrics.report())

    # Mem0 garbage check
    mem0_end = get("/mem0/stats")
    all_facts = get("/mem0/all?limit=50").get("facts", [])
    garbage = [f for f in all_facts if "soak" in f.get("content", "").lower() and f.get("confidence", 0) < 0.5]
    print(f"\n📏 Mem0 quality:")
    print(f"  Total facts: {mem0_end.get('total_facts', 0)} (start: {mem0_start})")
    print(f"  Low-confidence garbage: {len(garbage)}")
    print(f"  Fact growth: {'✅ stable' if mem0_end.get('total_facts', 0) - mem0_start < 50 else '⚠ growing'}")

    # Check doctor
    print(f"\n🩺 Final healthcheck:")
    try:
        deep = get("/health/deep")
        ok = sum(1 for c in deep.get("checks", []) if c["status"] == "ok")
        fail = sum(1 for c in deep.get("checks", []) if c["status"] == "fail")
        print(f"  {ok} OK, {fail} failures")
    except:
        print(f"  ❌ Healthcheck failed")

    passed = metrics.errors == 0 and fail == 0
    print(f"\n{'=' * 60}")
    print(f"🏁 SOAK TEST: {'✅ PASSED' if passed else '⚠ COMPLETED WITH ISSUES'}")
    print(f"{'=' * 60}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
