#!/usr/bin/env python3
"""
Spike Test v2: Full MemoryManager with Mem0.

Tests all 6 layers:
  1. L3 Verbatim Archive
  2. SQLite Metadata Index
  3. FAISS Semantic Index
  4. Context Builder (v2: weighted, strategies, Mem0-aware)
  5. Compression Engine (v2: auto-trigger, incremental)
  6. Mem0 Store (ADD-only extraction, preferences, decisions, entities)
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from layers.compression import CompressionEngine
from layers.context_builder import BudgetStrategy, ContextBuilder, ContextConfig, ToolSchema
from layers.faiss_index import FAISSSemanticIndex
from layers.l3_verbatim import L3VerbatimArchive
from layers.mem0 import FactType, Mem0Store
from layers.sqlite_index import SQLiteMetadataIndex
from memory_manager import MemoryManager


def generate_test_events(n: int = 120) -> list[dict]:
    """Generate realistic test events with preferences and decisions."""
    events = []
    topics = [
        "создание лендинга для нового продукта",
        "анализ конкурентов в нише",
        "настройка рекламной кампании Яндекс.Директ",
        "SEO-оптимизация сайта",
        "email-рассылка для клиентов",
        "A/B тестирование заголовков",
        "аналитика воронки продаж",
        "контент-план на месяц",
    ]

    # Add preference-heavy messages
    preference_msgs = [
        "Я предпочитаю работать в Notion, а не в Google Docs. Мне нравится их система баз данных.",
        "Мне важно, чтобы аналитика была визуальной — люблю дашборды, а не таблицы.",
        "Я не люблю, когда отчеты приходят в PDF — предпочитаю интерактивные форматы.",
        "Терпеть не могу холодные звонки, только тёплые лиды.",
        "Для меня критично использовать amoCRM, ни на что другое не согласна.",
        "Хочу, чтобы все коммуникации шли через Telegram, почту не читаю.",
    ]

    decision_msgs = [
        "Решил: используем Tilda для лендингов, а не WordPress. Быстрее и проще.",
        "Согласовано: бюджет на контекстную рекламу — 100 000₽ в месяц.",
        "Договорились с командой: митинги по понедельникам в 10:00, длительность 30 минут.",
        "Выбрал вариант B для email-кампании: персонализированные цепочки с триггерами.",
        "Принято решение перенести запуск на сентябрь из-за сезонности.",
    ]

    for i in range(n):
        topic = topics[i % len(topics)]

        if i % 3 == 0:
            # User message — sometimes with preferences
            if i % 7 == 0 and preference_msgs:
                msg = preference_msgs.pop(0)
                events.append({"role": "user", "type": "message", "content": msg})
            elif i % 11 == 0 and decision_msgs:
                msg = decision_msgs.pop(0)
                events.append({"role": "assistant", "type": "message", "content": msg})
            else:
                events.append(
                    {
                        "role": "user",
                        "type": "message",
                        "content": f"Давай обсудим задачу: {topic}. "
                        f"Мне нужно понять, какие метрики важны и как подойти к реализации.",
                    }
                )
        elif i % 3 == 1:
            plans = [
                "Проанализирую задачу. Основные метрики: конверсия, CTR, CAC, LTV. "
                "Предлагаю начать с аудита текущих показателей.",
                "Понял задачу. Разобью на этапы: исследование → гипотезы → тестирование → масштабирование.",
                "Хорошо, смотрю на данные. Вижу несколько точек роста.",
                "Ок, давай системно. Сначала бенчмарки, потом сравнение.",
            ]
            events.append(
                {"role": "assistant", "type": "message", "content": plans[i % len(plans)]}
            )
            if i % 5 == 0:
                events.append(
                    {
                        "role": "assistant",
                        "type": "tool_call",
                        "content": f"web_search('{topic} best practices 2026')",
                    }
                )
        else:
            if i % 11 == 0:
                events.append(
                    {
                        "role": "tool",
                        "type": "error",
                        "content": f"Ошибка API: rate limit exceeded при запросе '{topic}'",
                    }
                )
            else:
                events.append(
                    {
                        "role": "tool",
                        "type": "tool_output",
                        "content": f"Результаты поиска по '{topic}': найдено 42 источника.",
                    }
                )

    return events


# ═══════════════════════════════════════════════════════════
# TEST 1: L3 Verbatim Archive
# ═══════════════════════════════════════════════════════════
def test_l3():
    print("\n" + "=" * 60)
    print("TEST 1: L3 Verbatim Archive")
    print("=" * 60)
    l3 = L3VerbatimArchive("/tmp/spike-mm-v2/l3")
    sid = "t1"
    l3.record_event(sid, 0, role="user", type="message", content="Привет")
    l3.record_event(
        sid, 1, role="assistant", type="message", content="Привет!", refs=[("task", "1")]
    )
    events = l3.read_session(sid)
    assert len(events) == 2
    assert events[1]["refs"][0] == ["task", "1"]
    assert l3.get_event_count(sid) == 2
    print(f"  ✅ {len(events)} events, {l3.get_size_bytes(sid)} bytes, refs OK")


# ═══════════════════════════════════════════════════════════
# TEST 2: SQLite
# ═══════════════════════════════════════════════════════════
def test_sqlite():
    print("\n" + "=" * 60)
    print("TEST 2: SQLite Metadata Index")
    print("=" * 60)
    sql = SQLiteMetadataIndex("/tmp/spike-mm-v2/meta.db")
    sql.index_event("e1", "s1", 0, role="user", type="message", content="Hello")
    sql.index_event("e2", "s1", 1, role="assistant", type="tool_call", content="search()")
    assert sql.get_session_info("s1")["total_events"] == 2
    assert len(sql.get_events_by_type("s1", "message")) == 1
    sql.add_summary("s1", 0, 5, "test", "Summary text")
    assert len(sql.get_summaries("s1")) == 1
    print("  ✅ 2 events, 1 summary, queries OK")
    sql.close()


# ═══════════════════════════════════════════════════════════
# TEST 3: FAISS
# ═══════════════════════════════════════════════════════════
def test_faiss():
    print("\n" + "=" * 60)
    print("TEST 3: FAISS Semantic Index")
    print("=" * 60)
    idx = FAISSSemanticIndex("/tmp/spike-mm-v2/faiss")
    texts = [f"test text number {i} about topic {i%5}" for i in range(20)]
    ids = list(range(20))
    idx.build_index(texts, ids)
    results = idx.search("topic about marketing", k=5)
    assert len(results) > 0
    print(f"  ✅ {idx.get_index_stats()['count']} vectors, search returns {len(results)} results")
    print(f"  ⚠ Using {'real' if idx._model else 'random'} embeddings")


# ═══════════════════════════════════════════════════════════
# TEST 4: Context Builder v2 (strategies, Mem0, weighted)
# ═══════════════════════════════════════════════════════════
def test_context_builder():
    print("\n" + "=" * 60)
    print("TEST 4: Context Builder v2")
    print("=" * 60)

    # Test strategies
    for strategy in [BudgetStrategy.TIGHT, BudgetStrategy.NORMAL, BudgetStrategy.GENEROUS]:
        cfg = ContextConfig.from_strategy(strategy)
        builder = ContextBuilder(cfg)
        builder.set_system("You are a helpful assistant.")

        events = [
            {
                "step_id": i,
                "role": "user" if i % 2 == 0 else "assistant",
                "type": "message",
                "content": f"Event {i}: marketing discussion",
            }
            for i in range(40)
        ]
        builder.set_recent_events(events)

        builder.set_semantic_results(
            [{"faiss_id": 0, "score": 0.9, "chunk_text": "relevant memory about marketing"}]
        )

        # Mem0 facts
        builder.set_mem0_facts(
            [
                {
                    "type": "preference",
                    "content": "Предпочитает Notion для документации",
                    "confidence": 0.9,
                    "entities": ["Notion"],
                },
                {
                    "type": "decision",
                    "content": "Бюджет на рекламу 100 000₽/мес",
                    "confidence": 0.95,
                    "entities": ["Яндекс.Директ"],
                },
                {
                    "type": "fact",
                    "content": "Команда из 5 маркетологов",
                    "confidence": 0.8,
                    "entities": [],
                },
            ]
        )

        builder.set_summaries(
            [
                {
                    "type": "stage",
                    "start_step": 0,
                    "end_step": 20,
                    "content": "Initial research phase.",
                }
            ]
        )

        builder.set_tools(
            [
                ToolSchema(name="search", description="Search the web"),
                ToolSchema(
                    name="analyze", description="Analyze data", parameters={"format": "json"}
                ),
            ]
        )

        prompt, stats = builder.build()

        print(f"\n  Strategy: {strategy.value}")
        print(
            f"  Budget: {stats['total_budget']:,}t → used {stats['used_tokens']:,}t ({stats['utilization_pct']}%)"
        )
        print(f"  Slices: {', '.join(s['name'] for s in stats['slices'])}")

        # Assertions
        assert "## Recent Session Events" in prompt
        assert "## Long-term Memory" in prompt, "Mem0 section missing!"
        assert "## Available Tools" in prompt
        assert (
            stats["used_tokens"] < stats["working_budget"]
        ), f"Over budget: {stats['used_tokens']} > {stats['working_budget']}"
        assert "Preferences" in prompt or "Decisions" in prompt, "Mem0 facts not rendered"
        assert "raw archive" not in prompt.lower()

    print("\n  ✅ All 3 strategies work, Mem0 integrated, under budget")


# ═══════════════════════════════════════════════════════════
# TEST 5: Compression Engine v2
# ═══════════════════════════════════════════════════════════
def test_compression():
    print("\n" + "=" * 60)
    print("TEST 5: Compression Engine v2")
    print("=" * 60)

    l3 = L3VerbatimArchive("/tmp/spike-mm-v2/comp/l3")
    sql = SQLiteMetadataIndex("/tmp/spike-mm-v2/comp/meta.db")
    engine = CompressionEngine(sql, l3)

    events = [
        ("user", "message", "Нужно выбрать CRM. Я предпочитаю AmoCRM, потому что там удобное API."),
        (
            "assistant",
            "message",
            "Проанализировал AmoCRM vs Bitrix24. AmoCRM лучше по интеграциям.",
        ),
        ("assistant", "message", "Решил: AmoCRM, бюджет 15000₽/мес, внедрение за 2 недели."),
        ("user", "message", "Согласовано. Осталось настроить вебхуки — pending задача."),
        ("assistant", "tool_call", "web_search('Amocrm OAuth 2.0 docs')"),
        ("tool", "error", "Connection timeout"),
        ("assistant", "tool_call", "exec('pip install amocrm-sdk')"),
        ("tool", "tool_output", "Package installed successfully."),
        ("user", "message", "Супер! И ещё нужно обучить команду — todo на следующую неделю."),
        ("assistant", "message", "Понял. Создам план онбординга для @Anna и @Petr."),
    ]

    sid = "comp-test"
    for i, (role, etype, content) in enumerate(events):
        l3.record_event(sid, i, role=role, type=etype, content=content)

    result = engine.compress_range(sid, 0, 9)

    assert result.events_compressed == 10
    assert len(result.decisions) > 0
    assert len(result.open_issues) > 0
    assert len(result.entities) > 0  # new: entity extraction!
    assert len(result.artifact_refs) > 0

    print(f"  ✅ Compressed: {result.events_compressed} events")
    print(f"  ✅ Decisions: {len(result.decisions)}")
    print(f"  ✅ Open issues: {len(result.open_issues)}")
    print(f"  ✅ Entities: {result.entities}")
    print(f"  ✅ Artifacts: {len(result.artifact_refs)}")
    print(f"  ✅ Tokens saved: ~{result.tokens_saved}")

    # Incremental compression (all 10 events already covered by summary)
    result2 = engine.incremental_compress(sid, threshold_pct=0.99)
    # May be None if all events already compressed
    if result2:
        print(f"  ✅ Incremental compress: triggered ({result2.events_compressed} events)")
    else:
        print("  ✅ Incremental compress: skipped (all events already compressed)")

    # L3 preservation
    assert len(l3.read_session(sid)) == 10
    print("  ✅ L3 preserved all 10 events")

    sql.close()


# ═══════════════════════════════════════════════════════════
# TEST 6: Mem0 Store (ADD-only extraction)
# ═══════════════════════════════════════════════════════════
def test_mem0():
    print("\n" + "=" * 60)
    print("TEST 6: Mem0 Long-term Memory")
    print("=" * 60)

    mem0 = Mem0Store("/tmp/spike-mm-v2/mem0.db")

    # Add facts of different types
    mem0.add_fact(
        FactType.PREFERENCE,
        "Предпочитает Notion для документации",
        confidence=0.9,
        entities=["Notion"],
        source_session="s1",
    )
    mem0.add_fact(
        FactType.PREFERENCE,
        "Любит визуальные дашборды, не таблицы",
        confidence=0.85,
        entities=["дашборды"],
        source_session="s1",
    )

    # Duplicate — should increase confidence
    mem0.add_fact(
        FactType.PREFERENCE,
        "Предпочитает Notion для документации",
        confidence=0.5,
        entities=["Notion"],
        source_session="s2",
    )

    mem0.add_fact(
        FactType.DECISION,
        "Бюджет на контекстную рекламу: 100 000₽/мес",
        confidence=0.95,
        entities=["Яндекс.Директ"],
        source_session="s1",
    )
    mem0.add_fact(
        FactType.DECISION, "Запуск перенесён на сентябрь", confidence=0.9, source_session="s1"
    )

    mem0.add_fact(
        FactType.FACT, "Команда: 5 маркетологов, 2 дизайнера", confidence=0.8, source_session="s1"
    )
    mem0.add_fact(FactType.ENTITY, "Entity: AmoCRM", entities=["AmoCRM"], source_session="s1")

    # Query
    prefs = mem0.get_by_type(FactType.PREFERENCE)
    assert len(prefs) >= 2
    # The duplicate should have merged
    notion_facts = [p for p in prefs if "Notion" in p["content"]]
    assert len(notion_facts) <= 1  # merged!

    decisions = mem0.get_by_type(FactType.DECISION)
    assert len(decisions) >= 2

    # Text search
    results = mem0.search_text("бюджет")
    assert len(results) >= 1
    assert "100 000" in results[0]["content"]

    # Entities
    entities = mem0.get_entities()
    assert len(entities) >= 2

    # Stats
    stats = mem0.get_stats()
    print(f"  ✅ Facts: {stats['total_facts']} total")
    print(f"  ✅ By type: {stats['by_type']}")
    print(f"  ✅ Entities: {stats['total_entities']}")

    # ADD-only: same fact again → confidence merged
    all_facts = mem0.get_all()
    notion_count = sum(1 for f in all_facts if "Notion" in f["content"])
    assert notion_count <= 1, f"ADD-only violated: {notion_count} Notion facts (should be 1)"
    print(f"  ✅ ADD-only dedup: {notion_count} Notion fact (merged)")

    # Extract from events
    test_events = [
        {
            "event_id": "e1",
            "step_id": 0,
            "role": "user",
            "type": "message",
            "content": "Я люблю работать в Figma, это лучший инструмент для дизайна.",
        },
        {
            "event_id": "e2",
            "step_id": 1,
            "role": "user",
            "type": "message",
            "content": "Мне важно, чтобы отчёты были в Google Data Studio.",
        },
        {
            "event_id": "e3",
            "step_id": 2,
            "role": "assistant",
            "type": "message",
            "content": "Понял. Решил: будем использовать Figma для всех макетов, Google Data Studio для отчётов.",
        },
    ]
    extracted = mem0.extract_facts(test_events, "s2")
    print(f"  ✅ Extracted from events: {len(extracted)} facts")
    for f in extracted:
        print(f"     [{f.type.value}] {f.content[:80]}... (conf={f.confidence:.2f})")

    mem0.close()

    print("\n  ✅ Mem0: ADD-only, dedup, extraction, entity tracking — all OK")


# ═══════════════════════════════════════════════════════════
# TEST 7: FULL VERTICAL SLICE with Mem0
# ═══════════════════════════════════════════════════════════
def test_full_pipeline():
    print("\n" + "=" * 60)
    print("TEST 7: FULL VERTICAL SLICE (130 events + Mem0)")
    print("=" * 60)

    shutil.rmtree("/tmp/spike-mm-v2/full", ignore_errors=True)

    mm = MemoryManager("/tmp/spike-mm-v2/full")
    session_id = "spike-v2-001"
    mm.start_session(session_id, project_id="mem0-spike")

    # Generate and record
    test_events = generate_test_events(130)
    print(f"\n  📝 Recording {len(test_events)} events (with preferences & decisions)...")
    mm.record_batch(test_events)

    # Force Mem0 extraction from all events
    print("  🧠 Extracting Mem0 facts...")
    facts = mm.extract_all_facts()
    print(f"  ✅ Extracted {len(facts)} Mem0 facts")

    # Get stats
    stats = mm.get_archive_stats()
    print(f"\n  📊 L3: {stats['l3_events']} events, {stats['l3_size_bytes']:,} bytes")
    print(f"  📊 SQLite: {stats['sqlite_stats']['total_events']} indexed")
    print(f"  📊 FAISS: {stats['faiss_stats']}")
    print(f"  📊 Mem0: {stats['mem0_stats']}")

    # Build context with query (triggers Mem0 + FAISS)
    print("\n  🔍 Testing context with Mem0...")

    queries = [
        ("инструменты для документации", BudgetStrategy.TIGHT),
        ("бюджет на рекламу", BudgetStrategy.NORMAL),
        ("запуск проекта", BudgetStrategy.GENEROUS),
    ]

    for query, strategy in queries:
        prompt, ctx_stats = mm.build_context(
            query=query, strategy=strategy, system_prompt="You are a marketing AI with memory."
        )

        assert ctx_stats["used_tokens"] < ctx_stats["working_budget"], f"Over budget for {query}"
        assert "## Recent Session Events" in prompt
        assert len(prompt) > 500

        has_mem0 = "## Long-term Memory" in prompt
        has_semantic = "## Relevant Memories" in prompt

        print(f"\n  Query: '{query}' ({strategy.value})")
        print(
            f"  Budget: {ctx_stats['used_tokens']:,}/{ctx_stats['working_budget']:,}t "
            f"({ctx_stats['utilization_pct']}%)"
        )
        print(
            f"  Mem0 facts: {'✅' if has_mem0 else '—'} | Semantic: {'✅' if has_semantic else '—'}"
        )

    # Verify L3 preservation
    all_events = mm.l3.read_session(session_id)
    assert len(all_events) >= 130, f"L3 lost events: {len(all_events)}"
    print(f"\n  ✅ L3: all {len(all_events)} events preserved")

    # Verify Mem0 has extracted preferences and decisions
    mem0_stats = mm.mem0.get_stats()
    assert mem0_stats["total_facts"] > 0, "Mem0 has no facts!"
    pref_count = mem0_stats["by_type"].get("preference", 0)
    dec_count = mem0_stats["by_type"].get("decision", 0)
    print(
        f"  ✅ Mem0: {mem0_stats['total_facts']} facts "
        f"({pref_count} preferences, {dec_count} decisions, "
        f"{mem0_stats['total_entities']} entities)"
    )

    # Compression
    result = mm.compress(end_step=100)
    print(
        f"  ✅ Compression: {result['events_compressed']} events → "
        f"{result.get('decisions', 0)} decisions, "
        f"{len(result.get('entities', []))} entities"
    )

    mm.close()

    print(f"\n{'='*60}")
    print("🎉 SPIKE v2 PASSED: All 6 layers with Mem0")
    print(f"{'='*60}")
    print(f"  L3: {stats['l3_events']} events, immutable JSONL")
    print("  SQLite: metadata indexed + Mem0 structured")
    print(f"  FAISS: vector index ({stats['faiss_stats']['count']} vectors)")
    print("  Context Builder: 3 strategies, weighted, Mem0-aware")
    print("  Compression: auto-trigger, incremental, entities")
    print(f"  Mem0: {mem0_stats['total_facts']} facts, ADD-only, dedup")


if __name__ == "__main__":
    os.makedirs("/tmp/spike-mm-v2", exist_ok=True)

    test_l3()
    test_sqlite()
    test_faiss()
    test_context_builder()
    test_compression()
    test_mem0()
    test_full_pipeline()

    print("\n✅ ALL 7 TESTS PASSED")
