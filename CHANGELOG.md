# Changelog

## [0.2.1] — 2026-07-09 (Security + Validation Hotfix)

### 🔒 Security (Enabled)
- Security middleware now actually enabled in server startup
- API key auth via `HERMES_MEMORY_API_KEY` env var
- DEV MODE when key not set (warning in logs, all endpoints public)
- Protected endpoints require `X-API-Key` header when key is set
- Invalid key returns 401 `{"detail": "Invalid API key"}`
- Public endpoints: `/health`, `/ready`, `/live`, `/metrics`
- Middleware order: CORS → Security → Logging → Metrics

### ✅ Pydantic Validation
- `/session/start` → `SessionStartRequest`
- `/turn/before` → `TurnBeforeRequest`
- `/turn/after` → `TurnAfterRequest`
- `/mem0/add` → `Mem0AddRequest`
- Invalid payloads → 422 (automatic FastAPI)

### 📦 Packaging
- `pyproject.toml`: src-layout with `package-dir = {"" = "src"}` + `where = ["src"]`
- `pip install -e .` verified working

### 🔢 Version Sync
- All version references updated to 0.2.1
- Files: VERSION.json, src/VERSION.json, pyproject.toml, SKILL.md, README.md,
  CHANGELOG.md, RELEASE_NOTES.md, docs/TECHNICAL_SPEC.md, server.py

### 🧪 Tests
- Security tests: API key auth (valid/wrong/missing), Pydantic validation (422),
  health public (200), rate limiting, dev mode, middleware connection

## [0.2.0] — 2026-07-09 (Stable)

### 🏗 Architecture
- Modular package structure: api/, memory/, storage/, retrieval/, embeddings/, extractors/, config/, utils/
- MemoryManager → thin orchestrator with separated concerns
- Each layer has `__init__.py` with proper exports
- Backward-compatible shims for all v0.1 imports

### 🚀 Server
- FastAPI + Uvicorn replacing stdlib http.server
- Async endpoints with concurrent request support
- New endpoints: /ready, /live
- 30 routes total (up from 20+)

### ⚙️ Configuration
- pydantic-settings configuration model
- .env file and kettu_mem.yaml support
- All magic numbers extracted to config

### 🔍 Ingestion
- Pre-ingestion content filter (system prompts, metadata, JSON, reasoning)
- Content normalization and deduplication
- Structured rejection logging

### 🔎 Retrieval
- BM25 + FAISS hybrid search
- Reciprocal Rank Fusion (RRF) for result merging
- Query normalization pipeline

### 📊 Memory Quality
- Composite memory scoring (importance + recency + confidence + access)
- Configurable TTL and exponential decay
- Fact ranking and expiration detection

### 🔒 Security
- API key authentication middleware
- Sliding window rate limiting per client IP
- Pydantic request validation models
- Input sanitization

### 📝 Logging
- Structlog-based structured logging
- Request ID and session ID tracking
- Latency breakdown per endpoint

### 📈 Metrics
- Prometheus /metrics endpoint
- Counters: requests, events, facts, compressions, searches
- Histograms: request latency, ingestion, retrieval, embedding
- Gauges: active sessions, vectors, facts, events, memory usage

### 🔀 Session Isolation
- Hierarchical namespace: project → workspace → agent → user → session
- Cross-session retrieval within same namespace
- Session cleanup for expired entries

### 🧪 Testing
- 34 pytest tests covering all layers
- Coverage: L3 96%, SQLite 89%, Context Builder 75%
- CI/CD pipeline (GitHub Actions): lint, test, build, smoke
- Acceptance tests: Single Task (25 steps), Long Task (300 steps), Similar Tasks (3×)

### 📊 Evaluation (вшит в проект)
- HAES (Hermes Agent Evaluation Score) — composite метрика 0-100
- MES (Memory Evaluation Score) — оценка слоя памяти
- EvalStore — персистентное хранилище прогонов
- Benchmarking — сохранение и сравнение HAES-бенчмарков
- TelemetryCollector — сбор метрик на каждом шаге агента
- MetricsEngine — агрегация и расчёт производных метрик
- Встроенные acceptance-тесты через eval framework

### 📦 Release
- pyproject.toml with proper metadata
- Dockerfile (Python 3.12 slim)
- docker-compose.yml with healthchecks
- CHANGELOG.md

### 🐛 Fixes (10 стабилизаций)
1. **FactType string→enum** — trigger_extract_fact() теперь принимает строку и конвертирует в FactType enum
2. **Middleware ASGI** — structlog middleware совместим с FastAPI ASGI lifecycle
3. **IngestionFilter enforcement** — record_event больше не игнорирует результат фильтра
4. **Mem0 source_session isolation** — get_all() и search_text() изолируют факты по сессиям
5. **MemoryQualityScorer integration** — факты скорируются и фильтруются при retrieval
6. **Auto-extract batch size** — уменьшен с 20 до 10 для более отзывчивого extraction
7. **Vector near-duplicate dedup** — чанки, отличающиеся только цифрами, пропускаются
8. **Event ID collision via UUID** — L3 ID используют uuid4 вместо sequential
9. **FAISS fallback chain** — OpenAI → sentence-transformers → random с graceful degradation
10. **L3 corrupted JSONL resilience** — read_session() пропускает битые строки с предупреждением

### ⚠️ Known Limitations (будут исправлены в v0.3.0)
- Concurrent FAISS writes могут race на faiss.index файле
- Mem0 extraction — heuristic-only (regex), без LLM
- FAISS не может прочитать повреждённый .index файл
- SQLite WAL может расти без периодического checkpoint
- 10MB+ payloads — embedding использует только первые 500 символов

---

## [0.1.0] — 2026-07-09
### Initial Release
- 6-layer MemoryManager (L3, SQLite, FAISS, Mem0, Context Builder, Compression)
- Cognitive Runtime (Planning, Reflection, Tool Intelligence)
- HTTP API (20+ endpoints)
- MES evaluation framework (83/100)
- Fault tolerance (10/10 scenarios)
- OpenClaw plugin integration
