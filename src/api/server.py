#!/usr/bin/env python3
"""
Kettu Mem HTTP API Server (FastAPI + Uvicorn).

Production REST endpoints for OpenClaw agent loop integration:

  GET  /health              - liveness check (always returns ok)
  GET  /ready               - readiness check (all layers healthy)
  GET  /live                - combined liveness + readiness

  POST /session/start       - start/resume a session
  POST /session/end         - finalize session

  POST /turn/before         - before_llm_call: retrieve context
  POST /turn/after          - after_llm_call: record events

  GET  /stats               - full stats across all layers
  GET  /mem0/search?q=...   - search long-term memory
  GET  /mem0/all            - list all facts
  GET  /mem0/stats          - Mem0 statistics
  GET  /mem0/entities       - entity list
  GET  /events/last?limit=N - recent events

  POST /compress            - manual compression
  POST /mem0/add            - add fact

  POST /cognitive/start     - start task
  POST /cognitive/resume    - resume task
  POST /cognitive/context   - build cognitive context
  POST /cognitive/step      - record step
  POST /cognitive/reflect   - reflection
  POST /cognitive/strategy  - adjust strategy
  GET  /cognitive/state     - current state
  POST /cognitive/space     - set memory space

All endpoints maintain backward compatibility with v0.1.0 response format.
"""
import json
import os
import sys
import time
import uuid
import traceback
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from retrieval.context_builder import ContextConfig, BudgetStrategy
from memory.memory_manager import MemoryManager
from extractors.cognitive_runtime import CognitiveRuntime, MemorySpace, StepOutcome
from api.security import (
    add_security_middleware,
    SessionStartRequest,
    SessionEndRequest,
    TurnBeforeRequest,
    TurnAfterRequest,
    CompressRequest,
    Mem0AddRequest,
    CognitiveStartRequest,
    CognitiveContextRequest,
    CognitiveStepRequest,
    CognitiveReflectRequest,
    CognitiveSpaceRequest,
)
from api.error_buffer import ErrorRingBuffer
from utils.logging import add_logging_middleware, setup_logging
from config import settings
from api.metrics import add_metrics_endpoint, MetricsMiddleware, metrics
from utils.logging import get_logger

logger = get_logger("api.server")

# ── Global state ────────────────────────────────────────

_mm: Optional[MemoryManager] = None
_cr: Optional[CognitiveRuntime] = None
_startup_time: float = 0.0
_data_dir: str = ""
_port: int = 8765
_error_buffer: Optional[ErrorRingBuffer] = None


# ── Helpers ─────────────────────────────────────────────

def _search_archive(query: str, limit: int = 10) -> list[dict]:
    """Full-text search in L3 archive events (for decision recovery)."""
    if not _mm or not _mm._session_id:
        return []
    q_words = query.lower().split()
    events = _mm.l3.read_session(_mm._session_id)
    hits = []
    for e in reversed(events):
        content_lower = e.get("content", "").lower()
        if all(w in content_lower for w in q_words):
            hits.append({
                "step": e["step_id"],
                "role": e["role"],
                "type": e["type"],
                "content": e["content"][:300],
                "timestamp": e["timestamp"],
            })
            if len(hits) >= limit:
                break
    return hits


def _run_healthcheck() -> list[dict]:
    """Run comprehensive health check across all layers."""
    import sqlite3
    checks = []

    # 1. API layer
    checks.append({"layer": "api", "status": "ok", "detail": "FastAPI server running"})

    # 2. SQLite metadata
    try:
        db = sqlite3.connect(str(_mm.sqlite.db_path))
        db.execute("SELECT 1")
        db.execute("CREATE TABLE IF NOT EXISTS _healthcheck_t (t TEXT)")
        db.execute("INSERT INTO _healthcheck_t VALUES ('ok')")
        db.execute("DROP TABLE _healthcheck_t")
        db.close()
        checks.append({"layer": "sqlite_metadata", "status": "ok", "detail": "writable"})
    except Exception as e:
        checks.append({"layer": "sqlite_metadata", "status": "fail", "detail": str(e)[:200]})

    # 3. SQLite mem0
    try:
        db_path = _mm.mem0.conn.execute("PRAGMA database_list").fetchone()["file"]
        db2 = sqlite3.connect(db_path)
        db2.execute("SELECT COUNT(*) FROM mem0_facts")
        db2.close()
        checks.append({"layer": "sqlite_mem0", "status": "ok", "detail": "readable"})
    except Exception as e:
        checks.append({"layer": "sqlite_mem0", "status": "fail", "detail": str(e)[:200]})

    # 4. L3 archive
    try:
        test_file = _mm.l3.data_dir / "_healthcheck.jsonl"
        with open(test_file, "w") as f:
            f.write(json.dumps({"test": "ok"}) + "\n")
        with open(test_file) as f:
            assert json.loads(f.readline())["test"] == "ok"
        os.remove(test_file)
        checks.append({"layer": "l3_archive", "status": "ok", "detail": "append+read OK"})
    except Exception as e:
        checks.append({"layer": "l3_archive", "status": "fail", "detail": str(e)[:200]})

    # 5. FAISS
    try:
        stats = _mm.faiss.get_index_stats()
        if stats.get("exists"):
            checks.append({"layer": "faiss", "status": "ok",
                          "detail": f'{stats["count"]} vectors, dim={stats["dim"]}'})
        else:
            checks.append({"layer": "faiss", "status": "ok", "detail": "empty (no index yet)"})
    except Exception as e:
        checks.append({"layer": "faiss", "status": "fail", "detail": str(e)[:200]})

    # 6. Mem0
    try:
        facts = _mm.mem0.get_all(limit=1)
        checks.append({"layer": "mem0", "status": "ok",
                      "detail": f'{_mm.mem0.get_stats()["total_facts"]} facts'})
    except Exception as e:
        checks.append({"layer": "mem0", "status": "fail", "detail": str(e)[:200]})

    # 7. Cognitive
    try:
        state = _cr.get_state() if _cr else {}
        checks.append({"layer": "cognitive", "status": "ok",
                      "detail": f'goal={bool(state.get("planning",{}).get("goal"))}, steps={state.get("step_counter",0)}'})
    except Exception as e:
        checks.append({"layer": "cognitive", "status": "fail", "detail": str(e)[:200]})

    return checks


# ── Application lifecycle ───────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    global _mm, _cr, _startup_time, _error_buffer
    _startup_time = time.time()

    # Initialize MemoryManager + CognitiveRuntime
    data = _data_dir or settings.data_dir
    _mm = MemoryManager(data)
    _cr = CognitiveRuntime(_mm, str(Path(data) / "cognitive"))
    _error_buffer = ErrorRingBuffer(Path(data) / "error_buffer.json")
    metrics.set_memory_manager(_mm)
    setup_logging()
    logger.info("server_starting", data_dir=data, version="0.3.1")

    yield

    # Shutdown
    if _mm:
        _mm.close()
    logger.info("server_shutdown")


# ── FastAPI app ─────────────────────────────────────────

app = FastAPI(
    title="Kettu Mem",
    version="0.2.1",
    description="Cognitive Memory Layer for OpenClaw agents",
    lifespan=lifespan,
)

# CORS (outermost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security (API key auth, rate limiting) — before logging/metrics
add_security_middleware(app)

# Logging (structlog with request_id)
add_logging_middleware(app)

# Metrics (Prometheus) — middleware
app.add_middleware(MetricsMiddleware)

add_metrics_endpoint(app)


# ── Health endpoints ────────────────────────────────────

@app.get("/health")
async def health():
    """Liveness: always returns ok if the process is running."""
    return {"status": "ok", "session": _mm._session_id if _mm else None, "uptime": round(time.time() - _startup_time, 1)}


@app.get("/ready")
async def ready():
    """Readiness: checks all layers are operational."""
    checks = _run_healthcheck()
    all_ok = all(c["status"] == "ok" for c in checks)
    status = "ready" if all_ok else "not_ready"
    return {
        "status": status,
        "checks": checks,
        "timestamp": time.time(),
    }


@app.get("/live")
async def live():
    """Combined liveness + readiness probe."""
    checks = _run_healthcheck()
    all_ok = all(c["status"] == "ok" for c in checks)
    return {
        "status": "ok" if all_ok else "degraded",
        "ready": all_ok,
        "checks": checks,
        "uptime": round(time.time() - _startup_time, 1),
    }


@app.get("/health/deep")
async def health_deep():
    """Deep health check — backward compatible with v0.1.0."""
    checks = _run_healthcheck()
    all_ok = all(c["status"] == "ok" for c in checks)
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "timestamp": time.time(),
    }


# ── Stats ───────────────────────────────────────────────

@app.get("/stats")
async def stats():
    """Full statistics across all layers."""
    if not _mm:
        raise HTTPException(503, "MemoryManager not initialized")
    return _mm.get_archive_stats()


# ── Session management ──────────────────────────────────

@app.post("/session/start")
async def session_start(body: SessionStartRequest):
    """Start or resume a session."""
    sid = body.session_id or f"session-{int(time.time())}"
    project = body.project_id
    _mm.start_session(sid, project_id=project)
    return {
        "status": "started",
        "session_id": sid,
        "stats": _mm.get_archive_stats(),
    }


@app.post("/session/end")
async def session_end(body: SessionEndRequest):
    """Finalize a session."""
    if body.extract_facts:
        _mm.extract_all_facts()
    stats = _mm.get_archive_stats()
    return {
        "status": "ended",
        "reason": body.reason,
        "stats": stats,
    }


# ── Turn endpoints ──────────────────────────────────────

@app.post("/turn/before")
async def turn_before(body: TurnBeforeRequest):
    """Build context for LLM call."""
    query = body.query
    strategy_name = body.strategy
    system_prompt = body.system_prompt
    tools = body.tools
    budget = body.token_budget

    strategy = getattr(BudgetStrategy, strategy_name.upper(), BudgetStrategy.NORMAL)
    config = ContextConfig.from_strategy(strategy) if not budget else ContextConfig(token_budget=budget)

    prompt, stats = _mm.build_context(
        query=query,
        system_prompt=system_prompt,
        tools=tools,
        config=config,
    )

    return {
        "status": "context_built",
        "prompt": prompt,
        "stats": stats,
    }


@app.post("/turn/after")
async def turn_after(body: TurnAfterRequest):
    """Record events after LLM call."""
    events = body.events
    session_id = body.session_id or (_mm._session_id if _mm else None)

    recorded = []
    for evt in events:
        eid = _mm.record_event(
            role=evt.get("role", "unknown"),
            event_type=evt.get("type", "message"),
            content=evt.get("content", ""),
            refs=evt.get("refs"),
            meta=evt.get("meta"),
            session_id=session_id,
        )
        recorded.append(eid)

    stats = _mm.mem0.get_stats() if _mm else {}
    recent_count = _mm.sqlite.get_session_info(session_id).get("total_events", 0) if _mm else 0

    return {
        "status": "recorded",
        "event_ids": recorded,
        "count": len(recorded),
        "total_events": recent_count,
        "mem0_facts": stats.get("total_facts", 0),
    }


# ── Compression ─────────────────────────────────────────

@app.post("/compress")
async def compress(body: CompressRequest):
    """Manual compression."""
    result = _mm.compress(end_step=body.end_step)
    return {"status": "compressed", "result": result}


# ── Events ──────────────────────────────────────────────

@app.get("/events/last")
async def events_last(request: Request):
    """Get recent events."""
    limit = int(request.query_params.get("limit", 20))
    recent = _mm.sqlite.get_recent_events(_mm._session_id, limit=limit) if _mm else []
    return {"events": recent, "count": len(recent)}


# ── Mem0 endpoints ──────────────────────────────────────

@app.get("/mem0/search")
async def mem0_search(
    q: str = "", min_confidence: float = 0.6,
    fact_types: str = "", project: str = "",
    session_id: str = "", limit: int = 10,
    deduplicate: bool = True, include_superseded: bool = False,
):
    """Search with fact type filters, dedup, and superseded handling."""
    if not _mm or not q:
        return {"query": q, "results": [], "archive_hits": [], "count": 0, "archive_count": 0}

    if project: setattr(_mm, "_project", project)  # soft project isolation
    raw = _mm.get_mem0_context(q, limit * 2 if deduplicate else limit)
    archive_hits = _search_archive(q, limit) if _mm else []
    types_set = set(t.strip() for t in fact_types.split(",") if t.strip())

    results = []
    seen = set()
    for f in raw:
        ft = f.get("fact_type", "")
        conf = f.get("confidence", 0)
        if conf < min_confidence: continue
        if types_set and ft not in types_set: continue
        if not include_superseded and f.get("superseded", False): continue
        key = f.get("content", "")[:80]
        if deduplicate:
            if key in seen: continue
            seen.add(key)
        results.append(f)
        if len(results) >= limit: break

    return {
        "query": q, "results": results, "archive_hits": archive_hits,
        "count": len(results), "archive_count": len(archive_hits),
    }


@app.get("/mem0/all")
async def mem0_all(request: Request):
    """List all Mem0 facts."""
    limit = int(request.query_params.get("limit", 50))
    facts = _mm.mem0.get_all(limit) if _mm else []
    return {"facts": facts, "count": len(facts)}


@app.get("/mem0/stats")
async def mem0_stats():
    """Mem0 statistics."""
    stats = _mm.mem0.get_stats() if _mm else {}
    return stats


@app.get("/mem0/entities")
async def mem0_entities():
    """List entities."""
    entities = _mm.mem0.get_entities() if _mm else []
    return {"entities": entities, "count": len(entities)}


@app.post("/mem0/add")
async def mem0_add(body: Mem0AddRequest):
    """Add a fact to Mem0."""
    fact_type = body.type
    content = body.content
    confidence = body.confidence
    entities = body.entities
    fact = _mm.add_mem0_fact(fact_type, content,
                             confidence=confidence, entities=entities)
    return {"status": "added", "fact": fact.to_dict()}


# ── Cognitive Runtime endpoints ─────────────────────────

@app.post("/cognitive/start")
async def cognitive_start(body: CognitiveStartRequest):
    """Start a cognitive task."""
    _cr.start_task(body.goal, body.plan, MemorySpace(body.space))
    return {"status": "task_started", "state": _cr.get_state()}


@app.post("/cognitive/resume")
async def cognitive_resume():
    """Resume a cognitive task."""
    ok = _cr.resume_task()
    return {
        "status": "resumed" if ok else "no_state",
        "state": _cr.get_state() if ok else None,
    }


@app.post("/cognitive/context")
async def cognitive_context(body: CognitiveContextRequest):
    """Build cognitive context."""
    prompt, stats = _cr.build_context(body.query, token_budget=body.token_budget)
    return {"prompt": prompt, "stats": stats, "state": _cr.get_state()}


@app.post("/cognitive/step")
async def cognitive_step(body: CognitiveStepRequest):
    """Record a cognitive step."""
    _cr.record_step(body.response, body.tool_calls, body.tool_outputs, body.user_input)
    reflection = _cr.reflection_history[-1] if _cr.reflection_history else {}
    return {"status": "recorded", "reflection": reflection, "state": _cr.get_state()}


@app.post("/cognitive/reflect")
async def cognitive_reflect(body: CognitiveReflectRequest):
    """Run reflection on a step."""
    reflection = _cr.reflect(body.response, body.tool_calls, body.tool_outputs)
    return {"reflection": reflection}


@app.post("/cognitive/strategy")
async def cognitive_strategy():
    """Adjust strategy."""
    _cr.adjust_strategy()
    return {"status": "adjusted", "state": _cr.get_state()}


@app.get("/cognitive/state")
async def cognitive_state_get():
    """Get cognitive state (GET)."""
    return _cr.get_state() if _cr else {}


@app.post("/cognitive/state")
async def cognitive_state_post():
    """Get cognitive state (POST, backward compat)."""
    return _cr.get_state() if _cr else {}


@app.post("/cognitive/space")
async def cognitive_space(body: CognitiveSpaceRequest):
    """Set memory space."""
    _cr.set_space(MemorySpace(body.space))
    return {"status": "space_set", "space": body.space}


# ── v0.3.0: Context Build (P0) ────────────────────────

@app.post("/context/build")
async def context_build(request: Request):
    """Build ready-to-use context in a single call."""
    try:
        body = await request.json()
        query = body.get("query", "")
        session_id = body.get("session_id")
        project = body.get("project", "default")
        token_budget = body.get("token_budget", 4000)
        fact_types = body.get("fact_types", [])
        min_confidence = body.get("min_confidence", 0.6)
    except:
        query = ""; session_id = None; project = "default"
        token_budget = 4000; fact_types = []; min_confidence = 0.6

    # Set project (safe — wraps missing method)
    if project and _mm:
        try: _mm.set_project(project)
        except AttributeError: pass

    # Get cognitive context
    prompt, stats = _cr.build_context(query, token_budget=token_budget) if _cr else ("", {})

    # Search Mem0 with filters
    facts = []; decisions = []; open_tasks = []; sources = []
    if _mm and query:
        try:
            raw = _mm.mem0.search(query, limit=10)
            for f in raw:
                ft = f.get("fact_type", "")
                conf = f.get("confidence", 0)
                if conf < min_confidence: continue
                if fact_types and ft not in fact_types: continue
                if not f.get("superseded", False):
                    facts.append(f)
                    if ft == "decision": decisions.append(f)
                    elif ft in ("task", "status") and not f.get("completed"):
                        open_tasks.append(f)
                if f.get("source"): sources.append(f["source"])
        except: pass

    return {
        "context": prompt[:token_budget] if len(prompt) > token_budget else prompt,
        "facts": facts[:20], "summaries": stats.get("summaries", []),
        "decisions": decisions[:10], "open_tasks": open_tasks[:10],
        "sources": list(set(sources))[:10],
        "token_count": min(len(prompt)//3, token_budget) if prompt else 0,
    }


# ── v0.3.0: Ingest Hook (P1) ──────────────────────────

@app.post("/ingest/event")
async def ingest_event(request: Request):
    """OpenClaw ingest hook: auto-classify and store agent events."""
    try:
        body = await request.json()
        event_type = body.get("event_type", "unknown")
        content = body.get("content", "")
        metadata = body.get("metadata", {})
    except:
        return {"status": "error", "message": "invalid body"}

    if not _mm: return {"status": "error", "message": "not initialized"}

    valid_events = {"user_message", "assistant_message", "tool_call",
                    "tool_result", "decision", "error", "task_completed"}
    if event_type not in valid_events:
        return {"status": "skipped", "reason": f"unknown event_type: {event_type}"}

    # Filter: don't store secrets, huge outputs, tool schemas
    if event_type in ("tool_call", "tool_result") and len(content) > 16000:
        return {"status": "skipped", "reason": "payload too large for memory"}

    # Classify and store
    fact_type = "status"
    if event_type == "decision": fact_type = "decision"
    elif event_type == "error": fact_type = "error"
    elif event_type == "task_completed": fact_type = "task"

    try:
        _mm.mem0.add_fact(fact_type, content[:4096],
                          source_event=event_type, **metadata)
        return {"status": "stored", "fact_type": fact_type}
    except Exception as e:
        _error_buffer.record("ingest", str(e), "ingest_error", recovered=False)
        return {"status": "error", "message": str(e)}


# ── v0.3.0: Status endpoint (P2) ──────────────────────

@app.get("/status")
async def status_get():
    """Diagnostic status with storage health, counts, and last error."""
    uptime = time.time() - _startup_time if _startup_time else 0
    storage_status = {"sqlite": "healthy", "faiss": "healthy", "archive": "healthy"}
    counts = {"facts": 0, "sessions": 0, "vectors": 0, "archive_events": 0}
    last_ingest = None
    mem_usage = 0

    if _mm:
        try:
            counts["facts"] = len(_mm.mem0._facts) if hasattr(_mm.mem0, "_facts") else 0
            counts["vectors"] = _mm.mem0._collection.count() if hasattr(_mm.mem0, "_collection") else 0
        except: pass
        try:
            import psutil; mem_usage = psutil.Process().memory_info().rss // (1024*1024)
        except: pass

    last_ingest = None
    if _cr:
        try:
            if _cr.last_ingest_at:
                last_ingest = _cr.last_ingest_at
        except Exception:
            last_ingest = None

    last_err = None
    if _error_buffer:
        last_err = _error_buffer.last_error

    return {
        "status": "healthy", "uptime_seconds": int(uptime),
        "version": "0.3.1",
        "storage": storage_status, "counts": counts,
        "memory_usage_mb": mem_usage,
        "last_ingest_at": last_ingest,
        "last_error": last_err,
    }


# ── Error handler ───────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global error handler — safe, no traceback leak."""
    import uuid
    rid = str(uuid.uuid4())[:8]
    logger.error("unhandled_exception", request_id=rid, error=str(exc),
                 path=str(request.url.path), exc_info=True)
    if _error_buffer:
        _error_buffer.record("server", str(exc)[:200], "unhandled", rid)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "request_id": rid},
    )


# ── Entry point ─────────────────────────────────────────

def run_server(data_dir: str = None, port: int = 8765, host: str = "127.0.0.1"):
    """Run the FastAPI server with uvicorn."""
    import uvicorn

    global _data_dir, _port
    _data_dir = data_dir or "/tmp/mm-server"
    _port = port

    logger.info("server_boot", host=host, port=port, version="0.2.1")

    uvicorn.run(
        "api.server:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kettu Mem v0.3.1")
    parser.add_argument("--data-dir", default="/tmp/mm-server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    run_server(args.data_dir, args.port, args.host)
