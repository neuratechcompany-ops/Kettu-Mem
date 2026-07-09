# Kettu Mem v0.2.0-rc1 Release Notes

## Fixed (от v0.1.0)

1. **FactType string→enum conversion** — `trigger_extract_fact()` called `mem0.add_fact(fact_type, ...)` with a string instead of `FactType` enum, causing AttributeError on string `.value` access.
2. **Middleware ASGI compatibility** — Structlog middleware wrapping corrected to work with FastAPI's ASGI lifecycle (request_id not propagated).
3. **IngestionFilter actually enforces** — v0.1.0 had the filter but `record_event` ignored its result; now filtered events return `filtered:<reason>` and are not persisted.
4. **Mem0 source_session isolation** — `get_all()` and `search_text()` now accept `source_session` parameter, preventing cross-session fact leakage.
5. **MemoryQualityScorer integration** — Facts are now scored and filtered on retrieval (expired facts excluded, ranked by composite score).
6. **Auto-extract batch size reduced** — `_extract_batch_size` reduced from 20 to 10 for more responsive Mem0 fact extraction.
7. **Vector near-duplicate dedup** — `_embed_and_store()` skips chunks that differ only in digits from the previous chunk.
8. **Event ID collision via UUID** — L3 event IDs use `uuid.uuid4().hex[:12]` instead of sequential + timestamp.
9. **FAISS fallback chain** — OpenAI → sentence-transformers → random: each backend failure gracefully descends to the next without crash.
10. **L3 corrupted JSONL resilience** — `read_session()` now wraps `json.loads()` in try/except, skipping corrupted lines with a structured warning instead of crashing.

## Breaking Changes

None. All v0.1.0 endpoints preserved.

## Known Limitations

- **Concurrent FAISS writes**: Multiple MemoryManager instances writing to the same FAISS index can race on `faiss.index` file. WAL-mode SQLite is fine, but FAISS file itself has no locking. Fix pending in v0.3.0.
- **Mem0 extraction heuristic-only**: No LLM extraction — pure regex + pattern matching. Works for Russian and English but misses implicit preferences.
- **FAISS can't read corrupted `.index` file**: If `faiss.index` is corrupted on disk, `load_index()` will raise. Auto-rebuild on failure not implemented.
- **No WAL checkpoint**: SQLite WAL can grow unboundedly in write-heavy workloads. No periodic checkpoint runs.
- **10MB+ payloads**: Content is truncated by `IngestionFilter.normalize()` but the full payload is stored in L3. Embedding uses only first 500 chars.

## Rollback

To roll back to v0.1.0:

```bash
# 1. Stop the server
kill $(pgrep -f "uvicorn api.server")

# 2. Restore backup (if you made one before upgrading)
cp -r backup/v0.1.0-src/* src/

# 3. Restart
cd src && python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765
```

Data formats are backward-compatible: SQLite schema unchanged, JSONL format unchanged, FAISS index format unchanged. Data from v0.2.0 can be read by v0.1.0.

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `KETTU_MEM_DATA_DIR` | Root data directory | `~/.openclaw/memory-store` |
| `OPENAI_API_KEY` | OpenAI API key for embeddings | (from secrets file) |
| `KETTU_MEM_PORT` | HTTP server port | 8765 |
| `KETTU_MEM_API_KEY` | API key for auth middleware | (none) |
| `KETTU_MEM_LOG_LEVEL` | Logging level | INFO |
| `KETTU_MEM_TTL_DAYS` | Fact TTL in days | 90 |
| `KETTU_MEM_EMBEDDING_BACKEND` | Embedding backend (auto/openai/sentence_transformers/random) | auto |
| `KETTU_MEM_EMBEDDING_MODEL` | Embedding model name | text-embedding-3-small |
| `KETTU_MEM_OPENAI_BASE_URL` | OpenAI API base URL (for proxies) | (none) |

## Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start server
python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765

# Health check
curl http://127.0.0.1:8765/health

# Deep health (all layers)
curl http://127.0.0.1:8765/ready

# Start a session
curl -X POST http://127.0.0.1:8765/session/start \
  -H "Content-Type: application/json" \
  -d '{"session_id":"my-session","project_id":"my-project"}'

# Record events
curl -X POST http://127.0.0.1:8765/turn/after \
  -H "Content-Type: application/json" \
  -d '{"events":[{"role":"user","type":"message","content":"Hello world test message for recording"}]}'

# Search memory
curl "http://127.0.0.1:8765/mem0/search?q=Hello+world&limit=5"

# Run soak test (no server needed)
python3 scripts/soak_test.py
```
