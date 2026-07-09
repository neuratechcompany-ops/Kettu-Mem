---
name: kettu-mem
description: "Kettu Mem v0.1.0 — когнитивный слой памяти для OpenClaw. Планирование, контекст, рефлексия, память, отказоустойчивость."
version: "0.1.0"
metadata:
  openclaw:
    emoji: "🦊"
    events: []
    requires:
      bins: ["python3", "curl"]
      env: ["HERMES_MEMORY_ENABLED"]
---

# Kettu Mem — Скилл для OpenClaw

## Что это

Kettu Mem — когнитивный слой памяти, встроенный в agent loop OpenClaw. Управляет планированием задач, сборкой контекста, рефлексией после каждого шага, детекцией бесполезных tool calls и иммутабельным архивом всех действий.

## Как использовать

### Проверить статус

```bash
python3 /home/ngus/.openclaw/workspace/spike-memory-manager/hermes_doctor.py
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
python3 /home/ngus/.openclaw/workspace/spike-memory-manager/hermes_backup.py --output /tmp/kettu-backup
```

### Восстановление

Если агент «забыл» контекст после рестарта:

1. Проверить что сервер жив: `curl http://127.0.0.1:8765/health`
2. Если нет — перезапустить: `cd /home/ngus/.openclaw/workspace/spike-memory-manager && python3 server.py --data-dir ~/.openclaw/memory-store --port 8765 &`
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

## Архитектура (кратко)

```
Kettu Mem = L3 JSONL Archive + SQLite Index + FAISS + Mem0 + Cognitive Runtime
           + Context Builder + Compression + Tool Intelligence + Reflection Engine
           + OpenClaw Plugin (5 hooks)
```

## Файлы

- Исходники: `/home/ngus/.openclaw/workspace/spike-memory-manager/`
- Плагин: `/home/ngus/.openclaw/workspace/plugins/hermes-memory/`
- Данные: `~/.openclaw/memory-store/`
- Документация: `/home/ngus/.openclaw/workspace/Kettu Mem/docs/`
- Скрипты: `/home/ngus/.openclaw/workspace/Kettu Mem/scripts/`

## Команды

| Команда | Назначение |
|---|---|
| `hermes_doctor.py` | Полная диагностика всех слоёв |
| `hermes_backup.py` | Бэкап всех данных |
| `hermes_soak.py` | Нагрузочный тест |
| `hermes_fault_test.py` | Тест отказоустойчивости (10 сценариев) |

## Принятие решений

Когда агент использует Kettu Mem:

1. **Перед каждым шагом** — контекст собирается динамически из Goal + Plan + Mem0 + FAISS + Summaries + Recent Events
2. **После каждого шага** — ReflectionEngine анализирует прогресс/застревание/циклы
3. **При смене стратегии** — PlanningState обновляется, старые решения сохраняются
4. **При рестарте** — состояние восстанавливается из `planning_state.json`

## Ограничения

- sentence-transformers не установлен (FAISS с random embeddings, качество поиска снижено)
- Mem0 extraction — regex-based (не LLM)
- Хуки активируются только для сессий, созданных после установки плагина
