---
name: kettu-mem
description: "Kettu Mem v0.4.0 — когнитивный слой памяти для OpenClaw. Модульная архитектура, FastAPI, hybrid search, Prometheus, вшитый Evaluation Framework (HAES + MES)."
version: "0.4.0"
metadata:
  openclaw:
    emoji: "🦊"
    events: []
    requires:
      bins: ["python3", "curl"]
      env: ["HERMES_MEMORY_ENABLED"]
---

# Kettu Mem v0.4.0 — Скилл для OpenClaw

## Что это

Kettu Mem — когнитивный слой памяти, встроенный в agent loop OpenClaw. Управляет планированием задач, сборкой контекста, рефлексией после каждого шага, детекцией бесполезных tool calls и иммутабельным архивом всех действий.

**Новое в v0.4.0:** модульная архитектура, FastAPI + Uvicorn, BM25 + FAISS hybrid search, Memory Quality Scoring, Prometheus метрики, Session Isolation, вшитый Evaluation Framework.

## Как использовать

### Проверить статус

```bash
python3 scripts/hermes_doctor.py
```

### Поиск в памяти

```bash
curl "http://127.0.0.1:8765/mem0/search?q=<запрос>&limit=5"
```

### Состояние планирования

```bash
curl http://127.0.0.1:8765/cognitive/state
```

### Бэкап

```bash
python3 scripts/hermes_backup.py --output /tmp/kettu-backup
```

### Восстановление

Если агент «забыл» контекст после рестарта:

1. Проверить что сервер жив: `curl http://127.0.0.1:8765/health`
2. Если нет — перезапустить: `cd src && python3 -m uvicorn api.server:app --host 127.0.0.1 --port 8765 &`
3. Проверить состояние: `curl http://127.0.0.1:8765/cognitive/state`
4. Если PlanningState есть — агент должен автоматически подхватить

### Очистка памяти сессии (без удаления global/user)

```bash
# Удалить L3 сессии
rm ~/.openclaw/memory-store/l3_archive/session-<id>.jsonl
# Очистить SQLite для сессии
sqlite3 ~/.openclaw/memory-store/metadata.db "DELETE FROM events WHERE session_id='<id>';"
```

### Отключение

```bash
# Только Cognitive Runtime (память продолжает работать)
export HERMES_COGNITIVE_RUNTIME=0

# Полное отключение
export HERMES_MEMORY_ENABLED=0
```

### Prometheus метрики

```bash
curl http://127.0.0.1:8765/metrics
```

### Evaluation (вшитый eval framework)

```bash
cd src && python3 tests/test_evaluation.py
```

## Архитектура (v0.4.0)

```
Kettu Mem = FastAPI Server + Memory Manager (thin orchestrator)
           + L3 JSONL Archive + SQLite Index + FAISS + Mem0
           + BM25+FAISS Hybrid Search (RRF) + Context Builder
           + Memory Quality Scorer (TTL, decay, ranking)
           + Compression + Ingestion Filter
           + Cognitive Runtime (Planning, Reflection, Tool Intelligence)
           + Session Isolation (project/workspace/agent/user/session)
           + Security (API key auth, rate limiting)
           + Structlog + Prometheus
           + Evaluation Framework (HAES + MES, embedded)
           + OpenClaw Plugin (5 hooks)
```

## Файлы

- Исходники: `src/`
- Плагин: `src/plugin/`
- Данные: `~/.openclaw/memory-store/`
- Документация: `docs/`
- Скрипты: `scripts/`

## Команды

| Команда | Назначение |
|---|---|
| `hermes_doctor.py` | Полная диагностика всех слоёв |
| `hermes_backup.py` | Бэкап всех данных |
| `hermes_soak.py` | Нагрузочный тест |
| `hermes_fault_test.py` | Тест отказоустойчивости (10 сценариев) |
| `soak_test.py` | Общий soak test |
| `benchmark.py` | Бенчмарк производительности |
| `agent_sim.py` | Симуляция агента для тестирования |

## Установка для агентов

См. [INSTALL.md](INSTALL.md) или используй скилл `kettu-mem-install` в OpenClaw Skill Workshop.

## Принятие решений

Когда агент использует Kettu Mem:

1. **Перед каждым шагом** — контекст собирается динамически из Goal + Plan + Mem0 + FAISS + Summaries + Recent Events
2. **После каждого шага** — ReflectionEngine анализирует прогресс/застревание/циклы
3. **При смене стратегии** — PlanningState обновляется, старые решения сохраняются
4. **При рестарте** — состояние восстанавливается из `planning_state.json`

## Ограничения

- sentence-transformers не установлен по умолчанию (FAISS fallback: OpenAI → sentence-transformers → random)
- Mem0 extraction — regex-based (не LLM)
- Concurrent FAISS writes не защищены (fix в v0.4.0)
- Хуки активируются только для сессий, созданных после установки плагина
