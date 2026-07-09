# Kettu Mem — Memory Evaluation Framework Specification

**Версия:** 0.1.0 «Memory First»  
**Дата:** 2026-07-09  
**Продукт:** Kettu Mem (Hermes MemoryManager)  

---

## 1. Назначение

Memory Evaluation Framework — независимая система измерения эффективности памяти агента. Оценивает **только** работу MemoryManager. Не оценивает LLM, Planning, Reflection или Tool Intelligence.

### Измеряемые подсистемы

- L3 Verbatim Archive
- SQLite Metadata Index
- FAISS Semantic Index
- Mem0 Store
- Context Builder
- Compression Engine
- Retrieval Pipeline
- Recovery

---

## 2. Главный показатель: MES

**MES** — Memory Efficiency Score (0–100).

### Компоненты

| # | Компонент | Вес | Max |
|---|---|---|---|
| 1 | Compression | 20% | 20 |
| 2 | Prompt Stability | 15% | 15 |
| 3 | Retrieval | 15% | 15 |
| 4 | Mem0 Quality | 10% | 10 |
| 5 | Archive Integrity | 10% | 10 |
| 6 | Context Builder | 10% | 10 |
| 7 | Semantic Index | 5% | 10 |
| 8 | Recovery | 10% | 10 |
| 9 | Memory Pollution | 5% | 10 |
| **Total** | | **100%** | **110** |

### Интерпретация

| MES | |
|---|---|
| 90–100 | 🏆 Exceptional |
| 75–89 | ✨ Excellent |
| 60–74 | ✅ Good |
| 40–59 | ⚠️ Fair |
| 20–39 | 🔴 Poor |
| 0–19 | 💀 Critical |

---

## 3. Детальные метрики

### 3.1 Compression

Измеряет:
- Raw History Tokens vs Prompt Tokens → Compression Ratio
- Compression Count — количество событий компрессии
- Average Summary Size — средний размер саммари
- Summary Compression Ratio — эффективность сжатия саммари
- Quality Degradation — деградация качества после компрессии

### 3.2 Prompt Stability

Строит кривую Prompt Size vs History Size на чекпоинтах:
- 10 шагов
- 50 шагов
- 100 шагов
- 300 шагов
- 500 шагов
- 1000 шагов

Критерий: рост prompt не должен быть линейным.

### 3.3 Retrieval

| Метрика | K=1 | K=3 | K=5 | K=10 |
|---|---|---|---|---|
| Recall | ✅ | ✅ | ✅ | ✅ |
| Precision | ✅ | — | ✅ | — |

Дополнительно: false retrieval, missed retrieval, irrelevant retrieval.

### 3.4 Mem0 Quality

- Facts Total / Used / Never Used
- Duplicate Facts / Contradictory Facts
- Stale Facts / Low Confidence Facts
- Memory Hit Rate

### 3.5 Archive Integrity

- Append-only проверка
- Отсутствие потери событий
- Корректность refs
- Целостность JSONL
- Скорость поиска

### 3.6 Context Builder

- Build latency
- Prompt utilisation (%)
- Contribution breakdown: Memory / Semantic / Recent / Summaries
- Отсутствие raw tool outputs
- Соблюдение token budget

### 3.7 Semantic Index

- Search latency / Rebuild latency
- Vector count
- Orphan vectors / Missing vectors
- Index consistency

### 3.8 Recovery

После restart проверяет:
- L3, SQLite, FAISS, Mem0
- Refs, Summaries
- All Recovered

### 3.9 Memory Pollution

- Duplicate entities/facts
- Obsolete summaries
- Unused facts / Temporary facts
- Garbage Ratio

---

## 4. Хранилище

```
~/.openclaw/memory-evaluation-store/
└── memory-eval.db    # SQLite (WAL mode)
    ├── memory_runs
    ├── compression_snapshots
    ├── prompt_snapshots
    ├── retrieval_snapshots
    ├── mem0_snapshots
    ├── archive_checks
    ├── context_snapshots
    ├── semantic_snapshots
    ├── recovery_logs
    ├── pollution_snapshots
    └── memory_metrics
```

---

## 5. CLI

```
hermes memory eval             Run full evaluation
hermes memory benchmark        Benchmark suite (5 scenarios)
hermes memory compare <a> <b>  Compare two runs
hermes memory doctor           Health check
hermes memory export-report    Export MES report (JSON)
hermes memory list             List recent runs
```

---

## 6. Структура модуля

```
src/evaluation/
├── memory_eval_store.py       # SQLite хранилище
├── memory_telemetry.py        # Сбор метрик из MemoryManager
├── memory_metrics_engine.py   # Расчёт 8 групп метрик
├── mes_calculator.py          # MES 0-100
├── memory_eval_framework.py   # Оркестратор + CLI
├── test_memory_evaluation.py  # Acceptance tests
```

---

## 7. Acceptance Tests: 4/4 PASSED

| Тест | Результат |
|---|---|
| Store Integrity | ✅ MES 54/100 |
| Benchmark Suite (5 сценариев) | ✅ Avg MES 85.6/100 |
| MES Report Format | ✅ MES 89.5/100 |
| CLI Compatibility | ✅ Doctor, List, Export |

---

## 8. Отличие от Evaluation Framework v1

| | Evaluation Framework v1 | Memory Evaluation Framework |
|---|---|---|
| **Область** | Весь агент | Только память |
| **Метрика** | HAES (0-100) | MES (0-100) |
| **Компонентов** | 9 | 9 (только память) |
| **Retrieval** | Recall@5, Precision@5 | Recall@1/3/5/10, Precision@1/5 |
| **Prompt** | avg, growth | Кривая: 10/50/100/300/500/1000 |
| **Mem0** | Hit rate | + duplicates, contradictions, stale |
| **Archive** | — | Append-only, JSONL, refs, поиск |
| **Хранилище** | `~/.openclaw/evaluation-store/` | `~/.openclaw/memory-evaluation-store/` |
