# Changelog

## [0.2.0] — 2026-07-09

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

### 📦 Release
- pyproject.toml with proper metadata
- Dockerfile (Python 3.12 slim)
- docker-compose.yml with healthchecks
- CHANGELOG.md

### 🐛 Fixes
- Fixed FactType conversion in trigger_extract_fact (string → enum)
- Fixed middleware ASGI compatibility

## [0.1.0] — 2026-07-09
### Initial Release
- 6-layer MemoryManager (L3, SQLite, FAISS, Mem0, Context Builder, Compression)
- Cognitive Runtime (Planning, Reflection, Tool Intelligence)
- HTTP API (20+ endpoints)
- MES evaluation framework (83/100)
- Fault tolerance (10/10 scenarios)
- OpenClaw plugin integration
