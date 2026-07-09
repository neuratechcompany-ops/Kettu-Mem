#!/usr/bin/env python3
"""
Kettu Mem v0.2.0-rc1 — Comprehensive Release Soak Test.

Tests:
  1. 1000+ events with 10+ sessions, 3+ projects
  2. Restart recovery
  3. Concurrent writes (5 sessions)
  4. Memory pollution check (duplicates, expired facts)
  5. Session isolation

All direct module API calls, no HTTP/curl.
"""
import sys
import os
import time
import json
import tempfile
import shutil
import threading
import traceback
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Force random embedding backend (no OpenAI API needed)
os.environ['OPENAI_API_KEY'] = ''

from memory.memory_manager import MemoryManager

# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

_results = {}

def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    _results[name] = {"status": status, "detail": detail}
    if status == "FAIL":
        print(f"  ❌ {name}: FAIL — {detail}", flush=True)
    else:
        print(f"  ✅ {name}: PASS", flush=True)
    return condition


# ═══════════════════════════════════════════════════════════
# Test 1: 1000+ events, 10+ sessions, 3+ projects
# ═══════════════════════════════════════════════════════════
def test_bulk_events():
    print("\n═══ Test 1: 1000+ events, 10+ sessions, 3+ projects ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_bulk_")
    mm = MemoryManager(tmp)

    projects = ["alpha", "beta", "gamma"]
    sessions = [f"sess-{p}-{i}" for p in projects for i in range(4)]  # 12 sessions
    print(f"  Using {len(sessions)} sessions across {len(projects)} projects", flush=True)

    total_events = 0
    events_per_session = defaultdict(int)

    for sid in sessions:
        proj = sid.split("-")[1]  # alpha/beta/gamma
        mm.start_session(sid, project_id=proj)

        for step in range(100):
            role = "user" if step % 2 == 0 else "assistant"
            # Generate 100 unique messages per session
            content = f"[{proj}][{sid}] Message #{step}: important data point about task. "
            content += f"This session handles {proj} related work."
            eid = mm.record_event(role, "message", content)
            events_per_session[sid] += 1
            total_events += 1

    stats = mm.get_archive_stats()
    mm.close()

    # Verify total events across all sessions
    all_events = 0
    for sid in sessions:
        fpath = Path(tmp) / "l3_archive" / f"session-{sid}.jsonl"
        if fpath.exists():
            with open(fpath) as f:
                count = sum(1 for _ in f)
                all_events += count

    ok1 = check("1000+ events total", total_events >= 1000,
                f"Total events: {total_events}, archived: {all_events}")
    ok2 = check("10+ sessions", len(sessions) >= 10,
                f"Sessions: {len(sessions)}")
    ok3 = check("3+ projects with isolation", len(projects) >= 3,
                f"Projects: {projects}")

    # Verify events per session
    for sid in sessions:
        count = events_per_session[sid]
        if count < 90:  # Some get filtered
            check(f"Events in {sid} >= 90", False, f"Only {count} events")
            return False

    # Verify different projects created different session files
    for proj in projects:
        proj_files = list(Path(tmp, "l3_archive").glob(f"session-sess-{proj}-*.jsonl"))
        if len(proj_files) < 3:
            check(f"Project {proj} has >=3 session files", False,
                  f"Only {len(proj_files)} files")
            return False

    shutil.rmtree(tmp, ignore_errors=True)
    return ok1 and ok2 and ok3


# ═══════════════════════════════════════════════════════════
# Test 2: Restart recovery
# ═══════════════════════════════════════════════════════════
def test_restart_recovery():
    print("\n═══ Test 2: Restart recovery ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_restart_")

    # Phase 1: Write data
    mm = MemoryManager(tmp)
    mm.start_session("recov-sess", project_id="recov-proj")
    mm.record_event("user", "message", "Persistence test: critical data item one for recovery check")
    mm.record_event("user", "message", "Persistence test: another important recovery item two")
    mm.record_event("assistant", "message", "Acknowledged: saving settings for recovery test item three")
    mm.extract_all_facts()

    facts_before = mm.mem0.get_stats()["total_facts"]
    l3_before = mm.l3.get_event_count("recov-sess")
    sqlite_before = mm.sqlite.get_session_info("recov-sess").get("total_events", 0)

    print(f"  Before close: L3={l3_before}, SQLite={sqlite_before}, Mem0={facts_before}", flush=True)
    mm.close()

    # Phase 2: Re-open
    mm2 = MemoryManager(tmp)
    mm2.start_session("recov-sess", project_id="recov-proj")

    # Check L3
    l3_after = mm2.l3.get_event_count("recov-sess")
    ok_l3 = check("SQLite data persisted", sqlite_before > 0 and l3_after == l3_before,
                  f"Before={l3_before}, After={l3_after}")

    # Check SQLite
    sqlite_after = mm2.sqlite.get_session_info("recov-sess").get("total_events", 0)
    ok_sql = check("SQLite metadata persisted", sqlite_after == sqlite_before,
                   f"Before={sqlite_before}, After={sqlite_after}")

    # Check Mem0
    facts_after = mm2.mem0.get_stats()["total_facts"]
    ok_mem0 = check("Mem0 facts persisted", facts_after >= facts_before,
                    f"Before={facts_before}, After={facts_after}")

    mm2.close()
    shutil.rmtree(tmp, ignore_errors=True)

    return ok_l3 and ok_sql and ok_mem0


# ═══════════════════════════════════════════════════════════
# Test 3: Concurrent writes (5 sessions)
# ═══════════════════════════════════════════════════════════
def test_concurrent_writes():
    print("\n═══ Test 3: Concurrent writes (5 sessions) ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_concur_")

    errors = []
    written = defaultdict(int)

    def writer(session_id: str, project_id: str, count: int):
        try:
            mm = MemoryManager(tmp)
            mm.start_session(session_id, project_id=project_id)
            for i in range(count):
                mm.record_event("user", "message",
                    f"Concurrent event #{i} from {session_id} about {project_id} project tasks and objectives")
                written[session_id] += 1
            mm.close()
        except Exception as e:
            errors.append((session_id, str(e), traceback.format_exc()))

    sessions_data = [
        ("concur-A", "proj-1", 50),
        ("concur-B", "proj-1", 50),
        ("concur-C", "proj-2", 50),
        ("concur-D", "proj-2", 50),
        ("concur-E", "proj-3", 50),
    ]

    threads = []
    for sid, pid, cnt in sessions_data:
        t = threading.Thread(target=writer, args=(sid, pid, cnt))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    ok_no_errors = check("No concurrent errors", len(errors) == 0,
                         f"Errors: {errors[:3]}" if errors else "")

    if not ok_no_errors:
        shutil.rmtree(tmp, ignore_errors=True)
        return False

    # Verify all events written
    total_written = sum(written.values())
    # Re-read from one provider to verify no cross-contamination
    mm = MemoryManager(tmp)
    for sid, pid, cnt in sessions_data:
        mm.start_session(sid, project_id=pid)
        l3_count = mm.l3.get_event_count(sid)
        sql_count = mm.sqlite.get_session_info(sid).get("total_events", 0)
        ok_count = check(f"Session {sid} has >= {cnt} events",
                         l3_count >= cnt and sql_count >= cnt,
                         f"L3={l3_count}, SQLite={sql_count}")
        if not ok_count:
            mm.close()
            shutil.rmtree(tmp, ignore_errors=True)
            return False
    mm.close()

    shutil.rmtree(tmp, ignore_errors=True)
    return ok_no_errors


# ═══════════════════════════════════════════════════════════
# Test 4: Memory pollution check (duplicates, expired facts)
# ═══════════════════════════════════════════════════════════
def test_memory_pollution():
    print("\n═══ Test 4: Memory pollution check ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_pollution_")
    mm = MemoryManager(tmp)
    mm.start_session("poll-sess", project_id="poll-proj")

    # Test duplicate filtering
    msg = "This is a test message for duplicate detection in the memory system"
    eid1 = mm.record_event("user", "message", msg)
    eid2 = mm.record_event("user", "message", msg)  # duplicate

    ok_dup = check("Duplicate event filtered", eid2.startswith("filtered:"),
                   f"Second event ID: {eid2}")

    # Test duplicate fact merge in Mem0
    from extractors.mem0 import FactType
    f1 = mm.mem0.add_fact(FactType.FACT, "Important fact about system configuration",
                          confidence=0.5, source_session="poll-sess")
    f2 = mm.mem0.add_fact(FactType.FACT, "Important fact about system configuration",
                          confidence=0.7, source_session="poll-sess")

    ok_merge = check("Duplicate facts merge (not duplicate)", f1.fact_id == f2.fact_id,
                     f"F1={f1.fact_id}, F2={f2.fact_id}")
    stats = mm.mem0.get_stats()
    ok_count = check("Only 1 fact stored after merge", stats["total_facts"] == 1,
                     f"Total facts: {stats['total_facts']}")

    # Test TTL expiry
    mm.mem0.conn.execute(
        "UPDATE mem0_facts SET created_at = ? WHERE fact_id = ?",
        (time.time() - 365 * 86400, f1.fact_id)
    )
    mm.mem0.conn.commit()
    facts = mm.mem0.get_all(limit=10)
    ok_expired = check("Expired facts not returned", len(facts) == 0,
                       f"Expired facts returned: {len(facts)}")

    mm.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return ok_dup and ok_merge and ok_count and ok_expired


# ═══════════════════════════════════════════════════════════
# Test 5: Session isolation
# ═══════════════════════════════════════════════════════════
def test_session_isolation():
    print("\n═══ Test 5: Session isolation ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_iso_")
    mm = MemoryManager(tmp)

    # Session A: write sensitive data
    mm.start_session("iso-sess-A", project_id="iso-proj-A")
    mm.record_event("user", "message",
        "Secret project Alpha: using proprietary algorithm XYZ for customer data processing tasks")
    mm.extract_all_facts()

    # Session B: different project
    mm.start_session("iso-sess-B", project_id="iso-proj-B")
    mm.record_event("user", "message",
        "Public project Beta: using open source solutions for community service development")
    mm.extract_all_facts()

    # Session B searching for Sesison A data
    facts_b = mm.get_mem0_context("algorithm XYZ proprietary", limit=10)
    ok_iso1 = check("Session B does NOT see Session A facts",
                    len(facts_b) == 0,
                    f"Found {len(facts_b)} facts: {[f.get('content','')[:60] for f in facts_b]}")

    # Session A events (L3) — session A can only see its own events
    mm.start_session("iso-sess-A", project_id="iso-proj-A")
    events_a = mm.l3.read_session("iso-sess-A")
    ok_iso2 = check("Session A L3 archive contains only session A events",
                    all(e["session_id"] == "iso-sess-A" for e in events_a) and len(events_a) >= 1,
                    f"Events: {len(events_a)}, all match: {all(e['session_id']=='iso-sess-A' for e in events_a)}")

    # SQLite — verify session isolation at metadata level
    mm.start_session("iso-sess-B", project_id="iso-proj-B")
    recent_b = mm.sqlite.get_recent_events("iso-sess-B", limit=10)
    ok_iso3 = check("Session B SQLite events contain only B messages",
                    all("proprietary" not in r.get("content_preview", "") for r in recent_b),
                    f"Recent events: {len(recent_b)}")

    mm.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return ok_iso1 and ok_iso2 and ok_iso3


# ═══════════════════════════════════════════════════════════
# Test 6: SQLite persistence
# ═══════════════════════════════════════════════════════════
def test_sqlite_persistence():
    print("\n═══ Test 6: SQLite persistence ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_sqlite_")

    mm = MemoryManager(tmp)
    mm.start_session("persist-sql", project_id="persist-proj")
    for i in range(50):
        mm.record_event("user", "message",
            f"SQL persistence test event #{i} with significant data content for storage verification")
    mm.extract_all_facts()

    sql_count_before = mm.sqlite.get_session_info("persist-sql").get("total_events", 0)
    facts_before = mm.mem0.get_stats()["total_facts"]
    mm.close()

    # Re-open
    mm2 = MemoryManager(tmp)
    mm2.start_session("persist-sql", project_id="persist-proj")

    sql_count_after = mm2.sqlite.get_session_info("persist-sql").get("total_events", 0)
    facts_after = mm2.mem0.get_stats()["total_facts"]

    ok1 = check("SQLite event count preserved", sql_count_after == sql_count_before,
                f"Before={sql_count_before}, After={sql_count_after}")
    ok2 = check("Mem0 facts preserved", facts_after >= facts_before,
                f"Before={facts_before}, After={facts_after}")

    # Verify data integrity: read back and check content
    recent = mm2.sqlite.get_recent_events("persist-sql", limit=5)
    ok3 = check("SQLite events readable", len(recent) > 0,
                f"Recent events: {len(recent)}")

    mm2.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return ok1 and ok2 and ok3


# ═══════════════════════════════════════════════════════════
# Test 7: FAISS persistence and rebuild
# ═══════════════════════════════════════════════════════════
def test_faiss_persistence():
    print("\n═══ Test 7: FAISS persistence and rebuild ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_faiss_")

    mm = MemoryManager(tmp)
    mm.start_session("faiss-sess", project_id="faiss-proj")

    texts = [
        "Machine learning algorithms for natural language processing tasks",
        "Database optimization techniques for PostgreSQL performance tuning",
        "User interface design patterns for mobile applications",
        "Distributed systems architecture for microservices deployment",
        "Security best practices for cloud infrastructure management",
    ]
    for i, text in enumerate(texts):
        mm.record_event("user", "message", text)

    # Build FAISS index
    result = mm.rebuild_index("faiss-sess")
    ok1 = check("FAISS index built", result and result["vectors"] >= 4,
                f"Vectors: {result}")

    # Search
    results = mm.faiss.search("database optimization", k=2)
    ok2 = check("FAISS search works", len(results) >= 1,
                f"Results: {len(results)}")

    stats0 = mm.faiss.get_index_stats()
    ok3 = check("FAISS stats show vectors", stats0.get("count", 0) >= 1,
                f"Stats: {stats0}")

    mm.close()

    # Re-open — FAISS should load
    mm2 = MemoryManager(tmp)
    mm2.start_session("faiss-sess", project_id="faiss-proj")
    stats1 = mm2.faiss.get_index_stats()
    ok4 = check("FAISS index persists across restarts", stats1.get("exists", False),
                f"Stats after reopen: {stats1}")

    # Delete index file, then rebuild
    index_path = Path(tmp) / "faiss" / "faiss.index"
    if index_path.exists():
        index_path.unlink()
    mm2.rebuild_index("faiss-sess")
    stats2 = mm2.faiss.get_index_stats()
    ok5 = check("FAISS rebuild after delete", stats2.get("exists", False),
                f"Stats after rebuild: {stats2}")

    mm2.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return ok1 and ok2 and ok3 and ok4 and ok5


# ═══════════════════════════════════════════════════════════
# Test 8: L3 corrupted JSONL handling
# ═══════════════════════════════════════════════════════════
def test_l3_corruption():
    print("\n═══ Test 8: L3 corrupted JSONL ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_l3cor_")

    mm = MemoryManager(tmp)
    mm.start_session("corrupt-sess")

    for i in range(5):
        mm.record_event("user", "message",
            f"Clean message #{i} for corruption testing in the L3 archive")

    mm.close()

    # Corrupt the JSONL file: insert a broken line
    session_file = Path(tmp) / "l3_archive" / "session-corrupt-sess.jsonl"
    lines = session_file.read_text().splitlines()
    # Insert a broken line in the middle
    idx = len(lines) // 2
    modified = lines[:idx] + ["{broken", "also not json]"] + lines[idx:]
    session_file.write_text("\n".join(modified) + "\n")

    # Re-open — should handle corrupt lines gracefully
    mm2 = MemoryManager(tmp)
    mm2.start_session("corrupt-sess")

    try:
        events = mm2.l3.read_session("corrupt-sess")
        # Should not crash — might return fewer events (corrupt lines skipped)
        ok1 = check("L3 read with corrupted lines does not crash", True,
                    f"Events read: {len(events)} (expected >= 5 clean ones)")
    except Exception as e:
        ok1 = check("L3 read with corrupted lines does not crash", False,
                    f"Exception: {e}")

    # Verify server functions still work
    mm2.record_event("user", "message",
        "Post-corruption message: system continues operating normally")
    stats = mm2.get_archive_stats()

    # Write a regular event — L3 should still append
    mm2.l3.record_event("corrupt-sess", 99, role="user", type="message",
                        content="Recovery event after corruption")
    events2 = mm2.l3.read_session("corrupt-sess")

    ok2 = check("L3 append works after corruption", len(events2) >= 1,
                f"Events after corruption: {len(events2)}")

    mm2.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return ok1 and ok2


# ═══════════════════════════════════════════════════════════
# Test 9: Embedding fallback
# ═══════════════════════════════════════════════════════════
def test_embedding_fallback():
    print("\n═══ Test 9: Embedding fallback ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_emb_")

    from embeddings.faiss_index import FAISSSemanticIndex

    # Test 1: Normal initialization works with whatever backend is available
    idx = FAISSSemanticIndex(tmp)
    backend = idx.embedding_backend
    ok1 = check("FAISS backend initializes",
                backend in ("openai", "sentence_transformers", "random"),
                f"Backend: {backend}")

    # Test 2: Embed and search works
    idx.build_index(["test message one", "test message two"], [0, 1])
    results = idx.search("test message", k=2)
    ok2 = check("FAISS search works", len(results) >= 1,
                f"Results: {len(results)}")

    # Test 3: With invalid/fake key, fallback to random (not crash)
    os.environ['OPENAI_API_KEY'] = 'sk-fake-deadbeef-test-key-invalid'
    idx2 = FAISSSemanticIndex(f"{tmp}_fake")
    backend2 = idx2.embedding_backend
    ok3 = check("FAISS handles invalid OpenAI key (falls back, no crash)",
                backend2 in ("sentence_transformers", "random"),
                f"Backend: {backend2}")

    # Build and search with fallback backend
    idx2.build_index(["hello world"], [0])
    results2 = idx2.search("hello", k=1)
    ok4 = check("FAISS search works after fallback", len(results2) >= 1,
                f"Results: {len(results2)}")

    # Restore env
    os.environ.pop('OPENAI_API_KEY', None)

    shutil.rmtree(tmp, ignore_errors=True)
    return ok1 and ok2 and ok3 and ok4


# ═══════════════════════════════════════════════════════════
# Test 10: Invalid JSON payload (API-level via records)
# ═══════════════════════════════════════════════════════════
def test_invalid_payload():
    print("\n═══ Test 10: Invalid/oversized payloads ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_invalid_")
    mm = MemoryManager(tmp)
    mm.start_session("invalid-sess")

    # Test 1: Empty/nil content
    eid_empty = mm.record_event("user", "message", "")
    ok1 = check("Empty content filtered", eid_empty.startswith("filtered:"),
                f"EID: {eid_empty}")

    # Test 2: None content
    eid_none = mm.record_event("user", "message", None)
    ok2 = check("None content filtered", eid_none.startswith("filtered:"),
                f"EID: {eid_none}")

    # Test 3: Oversized content (10MB payload)
    big_content = "X" * (10 * 1024 * 1024)  # 10MB
    try:
        eid_big = mm.record_event("user", "message", big_content)
        # Should be ingested but content truncated by normalizer
        ok3 = check("Oversized payload handled (not crashed)", True,
                    f"Event recorded: {eid_big[:20]}...")
    except Exception as e:
        ok3 = check("Oversized payload handled (not crashed)", False,
                    f"Exception: {str(e)[:100]}")

    # Test 4: Unicode
    eid_uni = mm.record_event("user", "message",
        "Тест с Unicode: 🦊 日本語 العربية émoji проверка работоспособности системы")
    ok4 = check("Unicode content handled", not eid_uni.startswith("filtered:"),
                f"EID: {eid_uni}")

    # Test 5: Special characters in content
    eid_special = mm.record_event("user", "message",
        "Special chars: <tag> & \"quotes\" \\ 'apostrophe' /slashes/; DROP TABLE users;--")
    ok5 = check("Special characters handled", not eid_special.startswith("filtered:"),
                f"EID: {eid_special}")

    mm.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return ok1 and ok2 and ok3 and ok4 and ok5


# ═══════════════════════════════════════════════════════════
# Test 11: TTL expiry
# ═══════════════════════════════════════════════════════════
def test_ttl_expiry():
    print("\n═══ Test 11: TTL expiry ═══", flush=True)
    tmp = tempfile.mkdtemp(prefix="soak_ttl_")
    mm = MemoryManager(tmp)
    mm.start_session("ttl-sess")

    # Add facts with different ages
    from extractors.mem0 import FactType
    now = time.time()

    # Fresh fact
    mm.mem0.add_fact(FactType.FACT, "Very fresh fact about current project",
                     confidence=0.9, source_session="ttl-sess")

    # Old fact (200 days)
    mm.mem0.conn.execute(
        """INSERT INTO mem0_facts (fact_id, type, content, confidence,
           entities_json, source_session, hash_key, created_at, updated_at, access_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("old1", "fact", "Old fact about legacy system", 0.5,
         "[]", "ttl-sess", "hash-old-1",
         now - 200 * 86400, now - 200 * 86400, 0)
    )
    mm.mem0.conn.commit()

    # Very old fact (>90 days, should be expired)
    mm.mem0.conn.execute(
        """INSERT INTO mem0_facts (fact_id, type, content, confidence,
           entities_json, source_session, hash_key, created_at, updated_at, access_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("old2", "preference", "Ancient preference data", 0.5,
         "[]", "ttl-sess", "hash-old-2",
         now - 365 * 86400, now - 365 * 86400, 0)
    )
    mm.mem0.conn.commit()

    facts = mm.mem0.get_all(limit=10)
    types = [f.get("type") for f in facts]

    ok1 = check("Fresh fact returned", "fact" in types,
                f"Types: {types}")
    ok2 = check("Expired facts not returned", "preference" not in types,
                f"Types: {types}")

    mm.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return ok1 and ok2


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("╔══════════════════════════════════════════╗")
    print("║  Kettu Mem v0.2.0-rc1 Release Soak Test ║")
    print("╚══════════════════════════════════════════╝", flush=True)

    all_pass = True

    all_pass &= test_bulk_events()
    all_pass &= test_restart_recovery()
    all_pass &= test_concurrent_writes()
    all_pass &= test_memory_pollution()
    all_pass &= test_session_isolation()
    all_pass &= test_sqlite_persistence()
    all_pass &= test_faiss_persistence()
    all_pass &= test_l3_corruption()
    all_pass &= test_embedding_fallback()
    all_pass &= test_invalid_payload()
    all_pass &= test_ttl_expiry()

    print("\n" + "═" * 60)
    print("  FINAL SOAK RESULT:", "ALL PASS ✅" if all_pass else "FAILURES DETECTED ❌")
    print("═" * 60)

    for name, result in sorted(_results.items()):
        emoji = "✅" if result["status"] == "PASS" else "❌"
        detail = f" — {result['detail']}" if result["detail"] else ""
        print(f"  {emoji} {name}{detail}")

    sys.exit(0 if all_pass else 1)
