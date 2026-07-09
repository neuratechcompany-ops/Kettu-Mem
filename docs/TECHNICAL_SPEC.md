# Kettu Mem — Technical Specification

**Версия:** 0.2.0 «Production Release»  
**Дата:** 2026-07-09  
**Автор:** Аурум Вейкко Кеттунен  
**Владелец:** Aurum Kettunen

---

## 1. Обзор продукта

Kettu Mem — когнитивный слой памяти для OpenClaw-агентов. Не просто хранилище фактов, а операционная система для цикла мышления агента: планирование, контекст, рефлексия, память, отказоустойчивость.

### Ключевые возможности

- **Многослойная память:** L3 (иммутабельный архив), SQLite (метаданные), FAISS (векторный поиск), Mem0 (долговременные факты)
- **Cognitive Runtime:** PlanningState, ReflectionEngine, ToolIntelligence
- **Dynamic Context Builder:** сборка prompt под token budget из 7 источников
- **Plugin integration:** 5 хуков в agent loop OpenClaw
- **Fault tolerance:** graceful degradation, 10/10 сценариев
- **Экономия токенов:** >90% на контексте (проверено на 500 шагах)

---

## 2. Архитектура

### 2.1 Слои памяти

```
┌──────────────────────────────────────────┐
│ L1: Context Builder                      │
│  • Token budget management               │
│  • 3 стратегии (tight/normal/generous)   │
│  • Взвешенная сборка из 7 источников     │
│  • Tool outputs excluded                 │
├──────────────────────────────────────────┤
│ L2: Cognitive Runtime                    │
│  • PlanningState (Goal→Plan→Steps)       │
│  • ReflectionEngine (progress/stuck/loop)│
│  • ToolIntelligence (duplicate detection)│
├──────────────────────────────────────────┤
│ L3: Verbatim Archive (JSONL)             │
│  • Append-only, immutable                │
│  • Полный аудит-трейл                    │
│  • Session isolation                     │
├──────────────────────────────────────────┤
│ SQLite Metadata Index                    │
│  • Events, summaries, artifacts          │
│  • Vector map (FAISS ID → event)         │
│  • Mem0 facts + entities + relations     │
├──────────────────────────────────────────┤
│ FAISS Semantic Index                     │
│  • Векторный поиск (384-dim)             │
│  • faiss.write_index/read_index          │
│  • Fallback: random embeddings           │
└──────────────────────────────────────────┘
```

### 2.2 Цикл мышления

```
User → Planner → MemoryManager → Context Builder → LLM → Tools → Reflection → Next Step
  │                                                      │
  └── PlanningState (диск) ──────────────────────────────┘
```

### 2.3 Agent Loop (хуки)

```
session_start        → load/create PlanningState
before_prompt_build  → DynamicContextBuilder (Goal+Plan+Mem0+FAISS+Summaries+Events)
after_tool_call      → ToolIntelligence + memory update
agent_end            → ReflectionEngine + PlanningState update
session_end          → persist SessionState
```

### 2.4 Memory Spaces

| Space | Назначение | Хранение | Очистка |
|---|---|---|---|
| `global` | Общие знания | Mem0 + FAISS | Никогда |
| `user` | Предпочтения пользователя | Mem0 | По запросу |
| `project` | Проектный контекст | Mem0 + PlanningState | При удалении проекта |
| `session` | Текущая сессия | L3 + SQLite | При /new |
| `temporary` | Рабочая память | In-memory | При каждом шаге |

---

## 3. API Reference

### 3.1 HTTP API (порт 8765)

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/health` | — | `{"status":"ok"}` |
| GET | `/health/deep` | — | 7-layer healthcheck |
| GET | `/stats` | — | Статистика всех слоёв |
| POST | `/session/start` | `{session_id, project_id}` | Начало сессии |
| POST | `/session/end` | `{reason, extract_facts}` | Завершение |
| POST | `/turn/before` | `{query, strategy, token_budget}` | Контекст для LLM |
| POST | `/turn/after` | `{session_id, events, extract_facts}` | Запись событий |
| POST | `/compress` | `{end_step}` | Принудительная компрессия |
| GET | `/mem0/search?q=&limit=` | — | Поиск фактов + архива |
| GET | `/mem0/stats` | — | Статистика Mem0 |
| GET | `/mem0/entities` | — | Список сущностей |
| POST | `/mem0/add` | `{type, content, confidence}` | Добавить факт |

### 3.2 Cognitive API

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/cognitive/start` | `{goal, plan, space}` | Запуск задачи |
| POST | `/cognitive/resume` | — | Восстановление состояния |
| POST | `/cognitive/context` | `{query, token_budget}` | Динамический контекст |
| POST | `/cognitive/step` | `{response, tool_calls, tool_outputs}` | Запись шага + рефлексия |
| POST | `/cognitive/reflect` | `{response, tool_calls, tool_outputs}` | Только рефлексия |
| POST | `/cognitive/strategy` | — | Корректировка стратегии |
| GET | `/cognitive/state` | — | Полное состояние |

### 3.3 Feature Flags

| Флаг | Значение | Эффект |
|---|---|---|
| `HERMES_MEMORY_ENABLED=1` | ON | Память активна |
| `HERMES_MEMORY_ENABLED=0` | OFF | Полное отключение |
| `HERMES_COGNITIVE_RUNTIME=1` | ON | Cognitive Runtime активен |
| `HERMES_COGNITIVE_RUNTIME=0` | OFF | Только память, без планирования |

---

## 4. Паттерны проектирования

### 4.1 Append-Only Archive (L3)

**Паттерн:** Event Sourcing  
**Реализация:** JSONL файлы, только append, без update/delete.  
**Преимущества:** иммутабельность, полный аудит-трейл, восстановление перезаписью.

### 4.2 Context Assembly

**Паттерн:** Priority-based weighted assembly  
**Реализация:** 7 источников с приоритетами и весами, token budget как constraint.  
**Преимущества:** детерминированная сборка, предсказуемый размер prompt.

### 4.3 ADD-only Memory (Mem0)

**Паттерн:** CRDT-lite (merge by hash)  
**Реализация:** дубликаты мержатся повышением confidence, без перезаписи.  
**Преимущества:** нет потери данных, разрешение конфликтов без блокировок.

### 4.4 Graceful Degradation

**Паттерн:** Circuit Breaker (per-layer)  
**Реализация:** каждый слой независим, отказ одного не блокирует остальные.  
**Преимущества:** агент продолжает работу при отказе любого компонента памяти.

### 4.5 Reflection Loop

**Паттерн:** OODA (Observe-Orient-Decide-Act)  
**Реализация:** после каждого шага — анализ прогресса, детекция циклов, смена стратегии.  
**Преимущества:** агент сам понимает когда застрял, без внешнего мониторинга.

---

## 5. Хранилище

```
~/.openclaw/memory-store/
├── l3_archive/               # JSONL — иммутабельный архив
│   └── session-{id}.jsonl    # Один файл на сессию
├── metadata.db               # SQLite — метаданные
│   ├── sessions              # Сессии
│   ├── events                # События (индекс)
│   ├── summaries             # Компрессия
│   ├── artifacts             # Артефакты
│   └── vector_map            # FAISS ID → event
├── mem0.db                   # SQLite — долговременная память
│   ├── mem0_facts            # Факты (ADD-only)
│   ├── mem0_entities         # Сущности
│   └── mem0_relations        # Связи
├── faiss/                    # FAISS — векторный индекс
│   ├── faiss.index
│   └── faiss_id_map.json
└── cognitive/                # Cognitive Runtime
    └── planning_state.json   # Состояние планирования
```

---

## 6. Производительность (из тестов)

| Метрика | Значение | Цель |
|---|---|---|
| build_context | 8.3 ms avg | <50 ms |
| latency p50 | 6.7 ms | <30 ms |
| latency p99 | 20.5 ms | <50 ms |
| memory update | ~2 ms | <20 ms |
| Prompt tokens (500 шагов) | 359 avg | stable |
| Prompt growth | 1.1x | <3x |
| Storage (500 шагов) | 4.7 MB | <10 MB |

---

## 7. Зависимости

```
Python 3.12+
├── numpy (уже установлен)
├── faiss-cpu (уже установлен)
├── tiktoken (уже установлен)
└── sentence-transformers (опционально, качается)

Node.js 22+ (для плагина)
├── openclaw/plugin-sdk (встроен в Gateway)
└── Стандартный fetch API (встроен в Node 22+)

SQLite 3.x (встроен в Python)
```

---

## 8. Контакты и лицензия

- **Продукт:** Kettu Mem v0.2.0
- **Владелец:** Aurum Kettunen
- **Разработчик:** Аурум Вейкко Кеттунен
- **Статус:** Production Release, Stable
- **Лицензия:** MIT
