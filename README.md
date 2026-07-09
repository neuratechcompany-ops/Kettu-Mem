# 🦊 Kettu Mem v0.2.1

**Когнитивный слой памяти для OpenClaw-агентов**

---

## Что нового в v0.2.1

| Компонент | Статус |
|---|---|
| Модульная архитектура (api/memory/storage/retrieval/embeddings/extractors) | ✅ STABLE |
| FastAPI + Uvicorn (30+ эндпоинтов) | ✅ STABLE |
| BM25 + FAISS Hybrid Search (RRF) | ✅ STABLE |
| Memory Quality Scoring (TTL, decay, ranking) | ✅ STABLE |
| Security (API key auth, rate limiting) | ✅ ENABLED |
| Structlog (structured logging) | ✅ STABLE |
| Prometheus /metrics | ✅ STABLE |
| Session Isolation (hierarchical namespace) | ✅ STABLE |
| Evaluation Framework (HAES + MES, вшит) | ✅ STABLE |
| 10 стабилизаций (см. CHANGELOG) | ✅ FIXED |
| 34 теста + CI/CD (GitHub Actions) | ✅ PASSING |

## Быстрый старт

```bash
# Проверить всё
python3 scripts/hermes_doctor.py

# DEV MODE (без API key, warning в логах)
cd src && python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765 &

# PRODUCTION (с API key)
export HERMES_MEMORY_API_KEY=your-secret-key
cd src && python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765 &

# Или Docker
docker compose up -d

# Healthcheck (публичный, без ключа)
curl http://127.0.0.1:8765/health

# Защищённый endpoint (требует X-API-Key)
curl -H "X-API-Key: $HERMES_MEMORY_API_KEY" http://127.0.0.1:8765/session/start \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test"}'
```

### Публичные и защищённые endpoints

| Endpoint | Доступ |
|---|---|
| `/health` | 🔓 Публичный |
| `/ready` | 🔓 Публичный |
| `/live` | 🔓 Публичный |
| `/metrics` | 🔓 Публичный |
| Все остальные (`/session/*`, `/turn/*`, `/mem0/*`, etc.) | 🔒 Требуют `X-API-Key` |

### DEV MODE

Если `HERMES_MEMORY_API_KEY` не задан — сервер работает в DEV MODE. Все endpoints публичные. В логах выводится WARNING:

```
SECURITY: No API key configured (HERMES_MEMORY_API_KEY not set).
Server running in DEV MODE — all endpoints are public.
Set HERMES_MEMORY_API_KEY for production.
```

### PRODUCTION

```bash
export HERMES_MEMORY_API_KEY=your-secure-random-key
# Все защищённые endpoints требуют заголовок:
curl -H "X-API-Key: $HERMES_MEMORY_API_KEY" http://127.0.0.1:8765/session/start
```

## Установка для агентов

См. [INSTALL.md](INSTALL.md) — пошаговое руководство для AI-агентов, которые хотят установить Kettu Mem.

Также доступен скилл `kettu-mem-install` в OpenClaw Skill Workshop.

## Документация

- `docs/TECHNICAL_SPEC.md` — полная техническая спецификация
- `docs/BUILD_GUIDE.md` — руководство по сборке и развёртыванию
- `docs/ERROR_CATALOG.md` — каталог ошибок и восстановление
- `docs/README_RUNBOOK.md` — эксплуатационная документация
- `docs/PLUGIN_BLUEPRINT.md` — схема интеграции с OpenClaw
- `docs/EVALUATION_SPEC.md` — спецификация Evaluation Framework (HAES + MES)
- `docs/MEMORY_EVAL_SPEC.md` — спецификация MES (Memory Evaluation Score)
- `SKILL.md` — скилл для использования в OpenClaw
- `INSTALL.md` — руководство по установке для агентов
- `CHANGELOG.md` — история версий
- `RELEASE_NOTES.md` — детальные release notes v0.2.0

## Структура

```
Kettu Mem/
├── README.md
├── INSTALL.md                 # Установка для агентов
├── SKILL.md                   # Скилл для OpenClaw
├── CHANGELOG.md               # История версий
├── RELEASE_NOTES.md           # Release notes
├── VERSION.json               # src/VERSION.json
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── kettu_mem.yaml
├── src/
│   ├── api/                   # FastAPI сервер
│   ├── memory/                # Оркестратор памяти
│   ├── storage/               # L3, SQLite
│   ├── retrieval/             # Hybrid search, context builder
│   ├── embeddings/            # FAISS
│   ├── extractors/            # Mem0, compression, cognitive
│   ├── evaluation/            # HAES + MES framework (вшит)
│   ├── config/                # pydantic-settings
│   ├── utils/                 # Logging, helpers
│   ├── layers/                # v0.1 compat shims
│   ├── plugin/                # OpenClaw plugin
│   └── tests/                 # 34 теста
├── scripts/                   # Утилиты
│   ├── hermes_doctor.py       # Диагностика
│   ├── hermes_backup.py       # Бэкап
│   ├── hermes_soak.py         # Нагрузочный тест
│   └── hermes_fault_test.py   # Fault tolerance
├── docs/                      # Документация
└── backup/                    # Бэкапы
```

## Ключевые метрики

- **Экономия токенов:** >90% (359 vs 75 000 на 500 шагах)
- **Latency:** 8.3ms avg, p99=20.5ms
- **Prompt stability:** 1.1x growth (не линейный)
- **Fault tolerance:** 10/10 сценариев
- **Storage:** 4.7 MB на 500 событий
- **Recovery:** 9/10 автоматическое, 1/10 ручное
- **HAES:** composite 0-100 (вшитый eval framework)
- **MES:** 83/100 (memory evaluation score)

## Владелец

Aurum Kettunen  
Разработано: Аурум Вейкко Кеттунен  
Дата: 2026-07-09
