#!/usr/bin/env python3
"""
hermes memory doctor — диагностика всех слоёв MemoryManager + Cognitive Runtime.

Usage:
  python3 -m hermes.memory doctor
  curl http://127.0.0.1:8765/health/deep
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = os.environ.get("HERMES_API", "http://127.0.0.1:8765")
STORE = os.path.expanduser("~/.openclaw/memory-store")


def check(service, status, detail=""):
    icon = {"ok": "✅", "fail": "❌", "warn": "⚠️"}.get(status, "❓")
    return {"service": service, "status": status, "icon": icon, "detail": detail}


def run_doctor():
    print("=" * 60)
    print("🩺 HERMES MEMORY DOCTOR")
    print("=" * 60)
    results = []

    # 1. API connectivity
    print("\n🔌 Connectivity")
    try:
        resp = urllib.request.urlopen(f"{API}/health", timeout=5)
        data = json.loads(resp.read())
        r = check("API server", "ok", f"port {API.split(':')[-1]}")
        results.append(r)
        print(f"  {r['icon']} {r['service']}: {r['detail']}")
    except Exception as e:
        r = check("API server", "fail", str(e))
        results.append(r)
        print(f"  {r['icon']} {r['service']}: {r['detail']}")

    # 2. Deep healthcheck
    print("\n🩻 Deep healthcheck")
    try:
        resp = urllib.request.urlopen(f"{API}/health/deep", timeout=10)
        data = json.loads(resp.read())
        for c in data.get("checks", []):
            r = check(c["layer"], c["status"], c.get("detail", ""))
            results.append(r)
            print(f"  {r['icon']} {r['service']}: {r['detail']}")
    except Exception as e:
        r = check("deep_healthcheck", "fail", str(e))
        results.append(r)
        print(f"  {r['icon']} {r['service']}: {r['detail']}")

    # 3. Storage
    print("\n💾 Storage")
    total_size = 0
    for item in ["l3_archive", "metadata.db", "mem0.db", "faiss", "cognitive"]:
        path = os.path.join(STORE, item)
        if os.path.exists(path):
            if os.path.isfile(path):
                size = os.path.getsize(path)
            else:
                size = sum(os.path.getsize(os.path.join(dp, f))
                          for dp, _, files in os.walk(path) for f in files)
            total_size += size
            r = check(f"storage:{item}", "ok", f"{size:,} bytes")
        else:
            r = check(f"storage:{item}", "warn", "not found")
        results.append(r)
        print(f"  {r['icon']} {r['service']}: {r['detail']}")
    print(f"  📦 Total: {total_size:,} bytes ({total_size/1024:.1f} KB)")

    # 4. Stats
    print("\n📊 Stats")
    try:
        resp = urllib.request.urlopen(f"{API}/stats", timeout=5)
        stats = json.loads(resp.read())
        print(f"  📦 L3: {stats.get('l3_events', 0)} events")
        print(f"  🔍 FAISS: {stats.get('faiss_stats', {}).get('count', 0)} vectors")

        resp2 = urllib.request.urlopen(f"{API}/mem0/stats", timeout=5)
        mem0 = json.loads(resp2.read())
        print(f"  🧠 Mem0: {mem0.get('total_facts', 0)} facts ({mem0.get('by_type', {})})")

        resp3 = urllib.request.urlopen(f"{API}/cognitive/state", timeout=5)
        cr = json.loads(resp3.read())
        plan = cr.get("planning", {})
        print(f"  🧿 Cognitive: goal='{plan.get('goal', '')[:50]}', steps={cr.get('step_counter', 0)}, progress={plan.get('progress', '?')}")
        print(f"  📝 Memory space: {cr.get('memory_space', '?')}")
        useless = cr.get("useless_tools", [])
        if useless:
            print(f"  ⚠ Useless tool patterns: {useless}")

    except Exception as e:
        r = check("stats", "fail", str(e))
        results.append(r)
        print(f"  {r['icon']} Stats: {e}")

    # 5. Gateway
    print("\n🔌 Gateway")
    try:
        import subprocess
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "openclaw-gateway.service"],
            capture_output=True, text=True, timeout=5
        )
        is_active = active.stdout.strip() == "active"
        r = check("gateway", "ok" if is_active else "fail", active.stdout.strip())
        results.append(r)
        print(f"  {r['icon']} Gateway: {r['detail']}")

        # Check plugin
        info = subprocess.run(
            ["openclaw", "plugins", "info", "hermes-memory"],
            capture_output=True, text=True, timeout=10
        )
        loaded = "loaded" in info.stdout.lower()
        r = check("plugin", "ok" if loaded else "fail",
                  "loaded" if loaded else "not loaded")
        results.append(r)
        print(f"  {r['icon']} Plugin: {r['detail']}")

    except Exception as e:
        r = check("gateway", "warn", str(e))
        results.append(r)
        print(f"  {r['icon']} Gateway: {e}")

    # 6. Env flags
    print("\n🏁 Feature flags")
    for flag in ["HERMES_MEMORY_ENABLED", "HERMES_COGNITIVE_RUNTIME"]:
        val = os.environ.get(flag, "unset")
        r = check(flag, "ok" if val == "1" else "warn", val)
        results.append(r)
        print(f"  {r['icon']} {flag}: {r['detail']}")

    # Summary
    fails = sum(1 for r in results if r["status"] == "fail")
    warns = sum(1 for r in results if r["status"] == "warn")
    oks = sum(1 for r in results if r["status"] == "ok")

    print(f"\n{'=' * 60}")
    if fails == 0:
        print(f"🏁 DOCTOR: HEALTHY ({oks} OK, {warns} warnings)")
    else:
        print(f"🏁 DOCTOR: DEGRADED ({oks} OK, {warns} warnings, {fails} FAILURES)")
    print(f"{'=' * 60}")

    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(run_doctor())
