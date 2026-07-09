# Kettu Mem v0.2.0 Release Notes

## Статус: ✅ Stable

Релиз-кандидат v0.2.0-rc1 прошёл приёмку. Исправлены все критические баги, добавлены 10 стабилизаций.

## Fixed (от v0.2.0-rc1)

1. **FactType string→enum conversion** — `trigger_extract_fact()` вызывался с строкой вместо `FactType`, вызывая AttributeError на `.value`.
2. **Middleware ASGI compatibility** — Structlog middleware исправлен для совместимости с FastAPI ASGI lifecycle (request_id теперь пробрасывается).
3. **IngestionFilter actually enforces** — v0.1.0 имел фильтр, но `record_event` игнорировал его результат; теперь отфильтрованные события возвращают `filtered:<reason>` и не персистятся.
4. **Mem0 source_session isolation** — `get_all()` и `search_text()` теперь принимают `source_session`, предотвращая утечку фактов между сессиями.
5. **MemoryQualityScorer integration** — Факты скорируются и фильтруются при retrieval (просроченные исключаются, ранжируются по composite score).
6. **Auto-extract batch size reduced** — `_extract_batch_size` уменьшен с 20 до 10 для более отзывчивой экстракции фактов Mem0.
7. **Vector near-duplicate dedup** — `_embed_and_store()` пропускает чанки, отличающиеся только цифрами от предыдущего.
8. **Event ID collision via UUID** — L3 event IDs используют `uuid.uuid4().hex[:12]` вместо sequential + timestamp.
9. **FAISS fallback chain** — OpenAI → sentence-transformers → random: каждый backend gracefully деградирует без краша.
10. **L3 corrupted JSONL resilience** — `read_session()` оборачивает `json.loads()` в try/except, пропуская битые строки с structured warning.

## What's New (от v0.1.0)

- 🏗 **Модульная архитектура** — api/, memory/, storage/, retrieval/, embeddings/, extractors/, config/, utils/
- 🚀 **FastAPI + Uvicorn** — асинхронный сервер, 30+ endpoints
- ⚙️ **pydantic-settings** — конфигурация через .env / yaml
- 🔍 **BM25 + FAISS hybrid search** — Reciprocal Rank Fusion (RRF)
- 📊 **Memory Quality** — composite scoring, TTL, exponential decay
- 🔒 **Security** — API key auth, rate limiting, input validation
- 📝 **Structlog** — structured logging с request/session tracking
- 📈 **Prometheus /metrics** — counters, histograms, gauges
- 🔀 **Session Isolation** — project → workspace → agent → user → session
- 🧪 **34 теста** + CI/CD (GitHub Actions)
- 📊 **Evaluation Framework** — HAES + MES, вшит в проект

## Breaking Changes

None. Все v0.1.0 endpoints сохранены.

## Known Limitations

- **Concurrent FAISS writes**: несколько инстансов MemoryManager могут race на `faiss.index`. WAL-mode SQLite ок, но сам FAISS файл без блокировок. Fix в v0.3.0.
- **Mem0 extraction heuristic-only**: regex + pattern matching, без LLM. Работает для RU/EN, но пропускает implicit preferences.
- **10MB+ payloads**: Content обрезается `IngestionFilter.normalize()`, но полный payload хранится в L3. Embedding использует только первые 500 символов.

## v0.2.0 Production Hardening

### FAISS Atomic Write
- `build_index()` и `add_vectors()` теперь пишут во временный файл → `fsync` → атомарный `os.replace()`. Защита от битого индекса при обрыве записи.

### Auto-Rebuild FAISS
- `start_session()` автоматически проверяет здоровье FAISS индекса через `is_index_healthy()`. Если индекс битый/отсутствует, но есть данные в SQLite `vector_map` — перестраивает из сохранённых чанков.
- Лог: `faiss_index_corrupted_auto_rebuilding` с количеством восстанавливаемых чанков.

### L3 Hard Payload Cap (100 KB)
- `L3VerbatimArchive`: максимальный размер события — 100 KB. Всё что больше сохраняется в artifact store (`_artifacts/`), в JSONL пишется ссылка `{"artifact_ref": "..."}`.
- `read_session()` автоматически подгружает артефакты при чтении.

### WAL Checkpoint Policy
- `SQLiteMetadataIndex`: автоматический `PRAGMA wal_checkpoint(PASSIVE)` каждые 1000 записей или 60 секунд. Предотвращает неограниченный рост WAL-файла.

### Production Benchmark Suite
- `scripts/benchmark_production.py`: 1 агент (ingest+retrieval), 10 concurrent агентов (изоляция), 100 сессий (create/switch overhead), restart recovery.
- Результаты в `BENCHMARKS.md`.

### Лицензия
- Единая лицензия MIT для всего проекта. Добавлен файл `LICENSE`.

### Версии
- Все файлы синхронизированы на v0.2.0: `src/VERSION.json`, `VERSION.json` (root), `pyproject.toml`, `package.json`, `SKILL.md`, `README.md`, `CHANGELOG.md`, все docs.

## Rollback to v0.1.0

```bash
# 1. Остановить сервер
kill $(pgrep -f "uvicorn api.server")

# 2. Восстановить backup (если сделан перед обновлением)
cp -r backup/v0.1.0-src/* src/

# 3. Перезапустить
cd src && python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765
```

Форматы данных backward-compatible: SQLite schema без изменений, JSONL без изменений, FAISS index без изменений.

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

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Start
python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765

# Docker
docker compose up -d

# Health
curl http://127.0.0.1:8765/health
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for full version history.
