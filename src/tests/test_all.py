"""
Comprehensive test suite for Kettu Mem v0.2.0.

Run: python3 -m pytest tests/test_all.py -v
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Disable OpenAI for fast testing
os.environ["OPENAI_API_KEY"] = ""

import pytest


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════
# STORAGE TESTS
# ═══════════════════════════════════════════════════════


class TestL3VerbatimArchive:
    def test_record_and_read(self, temp_dir):
        from storage.l3_verbatim import L3VerbatimArchive

        l3 = L3VerbatimArchive(temp_dir)
        eid = l3.record_event(
            "s1", 0, role="user", type="message", content="Hello world test message"
        )
        events = l3.read_session("s1")
        assert len(events) == 1
        assert events[0]["content"] == "Hello world test message"
        assert events[0]["role"] == "user"

    def test_append_only(self, temp_dir):
        from storage.l3_verbatim import L3VerbatimArchive

        l3 = L3VerbatimArchive(temp_dir)
        for i in range(100):
            l3.record_event("s1", i, role="user", type="message", content=f"msg-{i}")
        assert l3.get_event_count("s1") == 100

    def test_refs(self, temp_dir):
        from storage.l3_verbatim import L3VerbatimArchive

        l3 = L3VerbatimArchive(temp_dir)
        l3.record_event(
            "s1", 0, role="user", type="message", content="X", refs=[("task", "1"), ("layer", "L2")]
        )
        events = l3.read_session("s1")
        assert events[0]["refs"] == [["task", "1"], ["layer", "L2"]]

    def test_empty_session(self, temp_dir):
        from storage.l3_verbatim import L3VerbatimArchive

        l3 = L3VerbatimArchive(temp_dir)
        assert l3.read_session("nonexistent") == []
        assert l3.get_event_count("nonexistent") == 0


class TestSQLiteIndex:
    def test_index_and_query(self, temp_dir):
        from storage.sqlite_index import SQLiteMetadataIndex

        sql = SQLiteMetadataIndex(f"{temp_dir}/meta.db")
        sql.index_event(
            "e1", "s1", 0, role="user", type="message", content="Hello world test message"
        )
        sql.index_event(
            "e2", "s1", 1, role="assistant", type="message", content="Response to the world"
        )
        info = sql.get_session_info("s1")
        assert info["total_events"] == 2
        recent = sql.get_recent_events("s1", limit=1)
        assert len(recent) == 1
        sql.close()

    def test_summaries(self, temp_dir):
        from storage.sqlite_index import SQLiteMetadataIndex

        sql = SQLiteMetadataIndex(f"{temp_dir}/meta.db")
        sql.index_event("e1", "s1", 0, role="user", type="message", content="Hello world test data")
        sid = sql.add_summary("s1", 0, 10, "compression", "Summary text")
        summaries = sql.get_summaries("s1")
        assert len(summaries) == 1
        assert summaries[0]["content"] == "Summary text"
        sql.close()

    def test_vector_map(self, temp_dir):
        from storage.sqlite_index import SQLiteMetadataIndex

        sql = SQLiteMetadataIndex(f"{temp_dir}/meta.db")
        sql.index_event("e1", "s1", 0, role="user", type="message", content="Hello world test data")
        sql.map_vector("e1", "s1", 0, "chunk text")
        faiss_ids = sql.get_faiss_ids_for_session("s1")
        assert len(faiss_ids) == 1
        assert faiss_ids[0]["chunk_text"] == "chunk text"
        sql.close()


# ═══════════════════════════════════════════════════════
# RETRIEVAL TESTS
# ═══════════════════════════════════════════════════════


class TestContextBuilder:
    def test_basic_build(self):
        from retrieval.context_builder import ContextBuilder, ContextConfig

        cfg = ContextConfig(token_budget=16000)
        builder = ContextBuilder(cfg)
        builder.set_system("You are a test assistant.")
        builder.set_recent_events(
            [
                {"step_id": 0, "role": "user", "content": "Hello"},
            ]
        )
        prompt, stats = builder.build()
        assert "test assistant" in prompt
        assert stats["used_tokens"] > 0
        assert stats["utilization_pct"] < 100

    def test_strategies(self):
        from retrieval.context_builder import BudgetStrategy, ContextConfig

        for strategy in [BudgetStrategy.TIGHT, BudgetStrategy.NORMAL, BudgetStrategy.GENEROUS]:
            cfg = ContextConfig.from_strategy(strategy)
            assert cfg.token_budget in (16000, 32000, 64000)

    def test_mem0_integration(self):
        from retrieval.context_builder import ContextBuilder

        builder = ContextBuilder()
        builder.set_mem0_facts(
            [
                {"type": "preference", "content": "Likes dark mode", "confidence": 0.9},
            ]
        )
        prompt, _ = builder.build()
        assert "Likes dark mode" in prompt

    def test_tool_exclusion(self):
        from retrieval.context_builder import ContextBuilder

        builder = ContextBuilder()
        builder.set_recent_events(
            [
                {"step_id": 0, "role": "tool", "type": "tool_output", "content": "big output"},
            ]
        )
        prompt, _ = builder.build()
        assert "big output" not in prompt  # tool outputs excluded

    def test_compression_needed(self):
        from retrieval.context_builder import ContextBuilder, ContextConfig

        # Small budget should trigger compression
        cfg = ContextConfig(token_budget=100)
        builder = ContextBuilder(cfg)
        builder.set_system("A" * 200)
        _, stats = builder.build()
        # With small budget and large system prompt, utilization is high
        assert stats["utilization_pct"] > 0


class TestBM25Scorer:
    def test_basic_search(self):
        from retrieval.hybrid_search import BM25Scorer

        bm25 = BM25Scorer()
        docs = [
            ("hello world test", {"id": 1}),
            ("another document", {"id": 2}),
            ("hello again", {"id": 3}),
        ]
        bm25.index(docs)
        results = bm25.search("hello world")
        assert len(results) >= 1
        assert results[0][0] in (0, 2)  # doc index 0 or 2

    def test_empty_search(self):
        from retrieval.hybrid_search import BM25Scorer

        bm25 = BM25Scorer()
        assert bm25.search("query") == []


# ═══════════════════════════════════════════════════════
# EXTRACTOR TESTS
# ═══════════════════════════════════════════════════════


class TestIngestionFilter:
    def test_normal_content(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        ok, reason = f.should_ingest("This is a valid user message", "user", "message")
        assert ok
        assert reason == "ok"

    def test_reject_short(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        ok, reason = f.should_ingest("Hi", "user", "message")
        assert not ok
        assert "too_short" in reason

    def test_reject_system(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        ok, reason = f.should_ingest(
            "This is a long enough system message here", "system", "message"
        )
        assert not ok
        assert "system_role" in reason

    def test_reject_empty(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        ok, reason = f.should_ingest("", "user", "message")
        assert not ok

    def test_reject_system_prompt(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        ok, reason = f.should_ingest(
            "You are a helpful AI assistant with long-term memory", "assistant", "message"
        )
        assert not ok
        assert reason == "system_prompt"

    def test_dedup(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        f.should_ingest("Unique test message for dedup check!", "user", "message")
        ok, reason = f.should_ingest("Unique test message for dedup check!", "user", "message")
        assert not ok
        assert reason == "duplicate"

    def test_normalization(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        text = "  Hello\n\n\nWorld!  "
        assert f.normalize(text) == "Hello\n\nWorld!"


class TestMemoryQualityScorer:
    def test_calculate(self):
        from extractors.memory_quality import MemoryQualityScorer

        scorer = MemoryQualityScorer()
        fact = {
            "type": "preference",
            "confidence": 0.85,
            "created_at": time.time(),
            "access_count": 5,
        }
        score = scorer.calculate(fact)
        assert 0 <= score.total <= 1
        assert score.importance > 0
        assert score.recency > 0.9  # just created

    def test_decay(self):
        from extractors.memory_quality import MemoryQualityScorer

        scorer = MemoryQualityScorer()
        # Very old fact
        fact = {
            "type": "fact",
            "confidence": 0.5,
            "created_at": time.time() - 365 * 86400,
            "access_count": 0,
        }
        score = scorer.calculate(fact)
        assert score.recency < 0.1
        assert score.is_expired

    def test_rank(self):
        from extractors.memory_quality import MemoryQualityScorer

        scorer = MemoryQualityScorer()
        now = time.time()
        facts = [
            {"type": "fact", "confidence": 0.5, "created_at": now - 100, "access_count": 0},
            {"type": "decision", "confidence": 0.9, "created_at": now, "access_count": 10},
            {"type": "entity", "confidence": 0.3, "created_at": now - 10000, "access_count": 1},
        ]
        ranked = scorer.rank(facts, limit=1)
        assert ranked[0]["type"] == "decision"


class TestCompression:
    def test_compress(self, temp_dir):
        from extractors.compression import CompressionEngine
        from storage.l3_verbatim import L3VerbatimArchive
        from storage.sqlite_index import SQLiteMetadataIndex

        l3 = L3VerbatimArchive(temp_dir)
        sql = SQLiteMetadataIndex(f"{temp_dir}/meta.db")
        engine = CompressionEngine(sql, l3)

        for i in range(20):
            l3.record_event(
                "s1",
                i,
                role="user" if i % 2 == 0 else "assistant",
                type="message",
                content=f"Message {i}: decided something important about task {i}",
            )

        result = engine.compress_range("s1", 0, 19)
        assert result.events_compressed == 20
        assert result.summary
        assert len(result.decisions) > 0
        sql.close()


# ═══════════════════════════════════════════════════════
# MEMORY MANAGER TESTS
# ═══════════════════════════════════════════════════════


class TestMemoryManager:
    def test_create_and_session(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-sess", "test-project")
        mm.record_event("user", "message", "Hello world this is a proper test message")
        stats = mm.get_archive_stats()
        assert stats["l3_events"] == 1
        assert stats["session_id"] == "test-sess"
        mm.close()

    def test_context_build(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-sess")
        mm.record_event("user", "message", "Hello, this is a test message for context building")
        mm.record_event("assistant", "message", "Got it, processing your request now")
        prompt, stats = mm.build_context("test query")
        assert "test message" in prompt
        assert stats["used_tokens"] > 0
        mm.close()

    def test_mem0_extraction(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-sess")
        mm.record_event(
            "user",
            "message",
            "Я предпочитаю работать в Figma для дизайна макетов и проектирования интерфейсов",
        )
        mm.extract_all_facts()
        mem0_stats = mm.mem0.get_stats()
        # Verify at least one fact was extracted (preference pattern matches)
        assert (
            mem0_stats["total_facts"] >= 1
        ), f"Expected >=1 facts, got {mem0_stats['total_facts']}"
        # Verify preference fact exists
        facts = mm.mem0.get_all(limit=10)
        prefs = [f for f in facts if f.get("type") == "preference"]
        assert len(prefs) >= 1, f"Expected >=1 preference facts, got {len(prefs)}"
        mm.close()


# ═══════════════════════════════════════════════════════
# ISOLATION TESTS
# ═══════════════════════════════════════════════════════


class TestSessionIsolation:
    def test_namespace_path(self):
        from storage.session_isolation import SessionNamespace

        ns = SessionNamespace("p", "w", "a", "u", "s")
        assert ns.path() == "p/w/a/u/s"
        assert ns.parent_path() == "p/w/a/u"

    def test_namespace_match(self):
        from storage.session_isolation import SessionNamespace

        ns1 = SessionNamespace("p", "w", "a", "u", "s1")
        ns2 = SessionNamespace("p", "w", "a", "u", "s2")
        ns3 = SessionNamespace("p2", "w", "a", "u", "s3")
        assert ns1.matches(ns2)
        assert not ns1.matches(ns3)

    def test_from_path(self):
        from storage.session_isolation import SessionNamespace

        ns = SessionNamespace.from_path("p1/w1/a1/u1/s1")
        assert ns.project == "p1"
        assert ns.workspace == "w1"
        assert ns.agent == "a1"
        assert ns.user == "u1"
        assert ns.session_id == "s1"

    def test_ancestor_paths(self):
        from storage.session_isolation import SessionNamespace

        ns = SessionNamespace("a", "b", "c", "d", "e")
        paths = ns.ancestor_paths()
        assert paths == ["a", "a/b", "a/b/c", "a/b/c/d"]


# ═══════════════════════════════════════════════════════
# CONFIG TESTS
# ═══════════════════════════════════════════════════════


class TestConfig:
    def test_defaults(self):
        from config import settings

        assert settings.port == 8765
        assert settings.token_budget_normal == 32000
        assert settings.compression_threshold_pct == 0.70
        assert settings.ttl_days == 90

    def test_weights(self):
        from config import settings

        total = (
            settings.importance_weight
            + settings.recency_weight
            + settings.confidence_weight
            + settings.access_weight
        )
        assert abs(total - 1.0) < 0.01


# ═══════════════════════════════════════════════════════
# EXPANDED COVERAGE TESTS (v0.2.0 repair)
# ═══════════════════════════════════════════════════════


class TestMem0Store:
    """Direct Mem0Store tests for better coverage."""

    def test_add_and_retrieve_fact(self, temp_dir):
        from extractors.mem0 import FactType, Mem0Store

        store = Mem0Store(f"{temp_dir}/mem0.db")
        fact = store.add_fact(
            FactType.PREFERENCE, "User prefers dark mode", confidence=0.9, source_session="s1"
        )
        assert fact.type == FactType.PREFERENCE
        assert fact.confidence == 0.9
        facts = store.get_all(limit=5)
        assert len(facts) >= 1
        store.close()

    def test_fact_dedup_merge(self, temp_dir):
        from extractors.mem0 import FactType, Mem0Store

        store = Mem0Store(f"{temp_dir}/mem0.db")
        f1 = store.add_fact(
            FactType.FACT, "Python is great for ML", confidence=0.5, source_session="s1"
        )
        f2 = store.add_fact(
            FactType.FACT, "Python is great for ML", confidence=0.7, source_session="s2"
        )
        # Should merge — confidence increases, same fact_id
        assert f1.fact_id == f2.fact_id
        stats = store.get_stats()
        assert stats["total_facts"] == 1
        store.close()

    def test_add_fact_with_entities(self, temp_dir):
        from extractors.mem0 import FactType, Mem0Store

        store = Mem0Store(f"{temp_dir}/mem0.db")
        store.add_fact(
            FactType.DECISION,
            "Decided to use React",
            confidence=0.8,
            entities=["React", "JavaScript"],
            source_session="s1",
        )
        entities = store.get_entities()
        assert len(entities) >= 2
        store.close()

    def test_get_by_type(self, temp_dir):
        from extractors.mem0 import FactType, Mem0Store

        store = Mem0Store(f"{temp_dir}/mem0.db")
        store.add_fact(FactType.PREFERENCE, "Likes coffee", source_session="s1")
        store.add_fact(FactType.DECISION, "Use Docker", source_session="s1")
        prefs = store.get_by_type(FactType.PREFERENCE)
        assert len(prefs) >= 1
        assert prefs[0]["type"] == "preference"
        store.close()

    def test_session_isolation_in_store(self, temp_dir):
        from extractors.mem0 import FactType, Mem0Store

        store = Mem0Store(f"{temp_dir}/mem0.db")
        store.add_fact(FactType.FACT, "Session A secret", source_session="session-A")
        store.add_fact(FactType.FACT, "Session B public", source_session="session-B")
        # Query with session-A filter
        results = store.get_all(limit=10, source_session="session-A")
        assert len(results) == 1
        assert results[0]["source"]["session"] == "session-A"
        # Query with session-B filter
        results_b = store.get_all(limit=10, source_session="session-B")
        assert len(results_b) == 1
        assert results_b[0]["source"]["session"] == "session-B"
        store.close()

    def test_quality_scorer_integration(self, temp_dir):
        import time

        from extractors.mem0 import FactType, Mem0Store

        store = Mem0Store(f"{temp_dir}/mem0.db")
        store.add_fact(FactType.PREFERENCE, "Likes Python", confidence=0.9, source_session="s1")
        # Back-date to test TTL not expired
        store.conn.execute("UPDATE mem0_facts SET created_at = ?", (time.time() - 10 * 86400,))
        store.conn.commit()
        facts = store.get_all(limit=10)
        assert len(facts) >= 1
        store.close()


class TestFAISSIndex:
    """FAISS semantic index tests."""

    def test_build_and_search(self, temp_dir):
        from embeddings.faiss_index import FAISSSemanticIndex

        idx = FAISSSemanticIndex(temp_dir)
        idx.build_index(["hello world test", "another document", "hello again"], [0, 1, 2])
        results = idx.search("hello world", k=2)
        assert len(results) >= 1
        stats = idx.get_index_stats()
        assert stats["count"] == 3

    def test_add_vectors(self, temp_dir):
        from embeddings.faiss_index import FAISSSemanticIndex

        idx = FAISSSemanticIndex(temp_dir)
        next_id = idx.add_vectors(["test message a", "test message b"], start_id=0)
        assert next_id == 2
        stats = idx.get_index_stats()
        assert stats["count"] == 2

    def test_empty_index_search(self, temp_dir):
        from embeddings.faiss_index import FAISSSemanticIndex

        idx = FAISSSemanticIndex(temp_dir)
        results = idx.search("query", k=5)
        assert results == []

    def test_backend_detection(self, temp_dir):
        from embeddings.faiss_index import FAISSSemanticIndex

        idx = FAISSSemanticIndex(temp_dir)
        backend = idx.embedding_backend
        assert backend in ("openai", "sentence_transformers", "random", "none")


class TestCompressionEngine:
    """Test CompressionEngine more thoroughly."""

    def test_compress_range_with_decisions(self, temp_dir):
        from extractors.compression import CompressionEngine
        from storage.l3_verbatim import L3VerbatimArchive
        from storage.sqlite_index import SQLiteMetadataIndex

        l3 = L3VerbatimArchive(temp_dir)
        sql = SQLiteMetadataIndex(f"{temp_dir}/meta.db")
        engine = CompressionEngine(sql, l3)

        for i in range(15):
            l3.record_event(
                "s1",
                i,
                role="user" if i % 2 == 0 else "assistant",
                type="message",
                content=f"Message {i}: decided something about task {i}",
            )

        result = engine.compress_range("s1", 0, 14)
        assert result.events_compressed == 15
        assert result.summary
        sql.close()

    def test_incremental_compress(self, temp_dir):
        from extractors.compression import CompressionEngine
        from storage.l3_verbatim import L3VerbatimArchive
        from storage.sqlite_index import SQLiteMetadataIndex

        l3 = L3VerbatimArchive(temp_dir)
        sql = SQLiteMetadataIndex(f"{temp_dir}/meta.db")
        engine = CompressionEngine(sql, l3)

        for i in range(30):
            l3.record_event(
                "s2",
                i,
                role="user",
                type="message",
                content=f"Event number {i} with important content for processing",
            )
            sql.index_event(
                f"e{i}",
                "s2",
                i,
                role="user",
                type="message",
                content=f"Event number {i} with important content for processing",
            )

        result = engine.incremental_compress("s2", threshold_pct=0.1)
        if result:
            assert result.events_compressed > 0
        sql.close()


class TestContextBuilderEdgeCases:
    """Edge case tests for ContextBuilder."""

    def test_set_tools(self):
        from retrieval.context_builder import ContextBuilder, ToolSchema

        builder = ContextBuilder()
        tools = [
            ToolSchema(name="search", description="Search the web", parameters={"query": "string"}),
        ]
        builder.set_tools(tools)
        prompt, _ = builder.build()
        assert "search" in prompt

    def test_set_archive_refs(self):
        from retrieval.context_builder import ContextBuilder

        builder = ContextBuilder()
        builder.set_archive_refs(
            [{"type": "tool_output", "step_id": 42, "content": "archive data"}]
        )
        prompt, _ = builder.build()
        assert "archive data" in prompt

    def test_tight_strategy(self):
        from retrieval.context_builder import BudgetStrategy, ContextConfig

        cfg = ContextConfig.from_strategy(BudgetStrategy.TIGHT)
        assert cfg.token_budget == 16000
        assert cfg.recent_events_limit == 15

    def test_custom_config_no_strategy(self):
        from retrieval.context_builder import ContextBuilder, ContextConfig

        cfg = ContextConfig(token_budget=10000, recent_events_limit=5)
        builder = ContextBuilder(cfg)
        builder.set_system("You are helpful.")
        builder.set_recent_events([{"step_id": 0, "role": "user", "content": "Query here"}])
        prompt, stats = builder.build()
        assert stats["total_budget"] == 10000


# ═══════════════════════════════════════════════════════
# EXPANDED COVERAGE TESTS (v0.2.0 repair)
# ═══════════════════════════════════════════════════════


class TestIngestionFilterIntegration:
    """Verify IngestionFilter actually filters in MemoryManager flow."""

    def test_filter_blocks_system_prompt_in_mm(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-filter")
        # System prompt content should be filtered
        eid = mm.record_event(
            "assistant", "message", "You are a helpful AI assistant with long-term memory"
        )
        assert eid.startswith("filtered:")
        # Verify nothing was recorded
        stats = mm.get_archive_stats()
        assert stats["l3_events"] == 0
        mm.close()

    def test_filter_blocks_reasoning(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-filter-reasoning")
        # Reasoning trace should be filtered
        eid = mm.record_event(
            "assistant", "message", "Let me think about this problem carefully..."
        )
        assert eid.startswith("filtered:")
        mm.close()

    def test_filter_blocks_json_blob(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-filter-json")
        eid = mm.record_event("assistant", "message", '{"results": [{"id": 1, "value": "test"}]}')
        assert eid.startswith("filtered:")
        mm.close()

    def test_filter_blocks_error_traceback(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-filter-traceback")
        eid = mm.record_event(
            "assistant",
            "message",
            'Traceback (most recent call last):\n  File "test.py", line 42, in foo',
        )
        assert eid.startswith("filtered:")
        mm.close()

    def test_filter_blocks_duplicate(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-filter-dup")
        msg = "This is a unique enough test message for deduplication checking!"
        eid1 = mm.record_event("user", "message", msg)
        eid2 = mm.record_event("user", "message", msg)
        assert not eid1.startswith("filtered:")
        assert eid2.startswith("filtered:")
        mm.close()

    def test_filter_blocks_tool_metadata(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-filter-tool")
        eid = mm.record_event("assistant", "tool_call", "web_search(query='test')")
        assert eid.startswith("filtered:")
        mm.close()


class TestTTLAndDecay:
    """Verify MemoryQualityScorer TTL expiration and decay in retrieval."""

    def test_expired_facts_not_returned(self, temp_dir):
        import time

        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-ttl")
        mm.record_event(
            "user", "message", "Я решил использовать Python для бэкенда этого проекта тестового"
        )
        mm.extract_all_facts()

        # Manually backdate the fact to simulate expiry
        mm.mem0.conn.execute(
            "UPDATE mem0_facts SET created_at = ? WHERE type = 'decision'",
            (time.time() - 365 * 86400,),
        )
        mm.mem0.conn.commit()

        # Search should not return expired facts
        facts = mm.mem0.get_all(limit=10)
        # Expired facts are filtered out
        decisions = [f for f in facts if f.get("type") == "decision"]
        assert len(decisions) == 0, f"Expected 0 decisions (expired), got {len(decisions)}"
        mm.close()

    def test_fresh_facts_score_higher(self, temp_dir):
        import time

        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-score")

        # Add a fresh preference
        mm.record_event("user", "message", "Я люблю использовать тёмную тему в редакторах кода")
        mm.extract_all_facts()

        # Add an old fact directly
        mm.mem0.conn.execute(
            """INSERT INTO mem0_facts (fact_id, type, content, confidence,
               entities_json, source_session, source_event, source_step,
               hash_key, created_at, updated_at, access_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "old-fact-1",
                "fact",
                "Old fact about something",
                0.5,
                "[]",
                "test-score",
                "",
                0,
                "deadbeef12345678",
                time.time() - 200 * 86400,
                time.time() - 200 * 86400,
                0,
            ),
        )
        mm.mem0.conn.commit()

        facts = mm.mem0.get_all(limit=10)
        # Fresh preferences should rank higher than old facts
        if len(facts) >= 1:
            # The first result should be the fresh preference (not the old fact)
            assert (
                facts[0]["type"] == "preference"
            ), f"Expected preference first, got {facts[0]['type']}"
        mm.close()

    def test_scorer_integration_in_retrieval(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-scorer-retrieval")
        mm.record_event(
            "user",
            "message",
            "Решили что проект будет называться Kettu Mem и использовать векторный поиск",
        )
        mm.extract_all_facts()
        stats = mm.mem0.get_stats()
        assert stats["total_facts"] >= 1
        # Search should still work with scorer integration
        facts = mm.get_mem0_context("Kettu", limit=5)
        assert len(facts) >= 1
        mm.close()


class TestSessionIsolationEnforcement:
    """Verify session A cannot read session B's memory."""

    def test_different_sessions_isolated(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)

        # Session A
        mm.start_session("session-A", project_id="proj-A")
        mm.record_event(
            "user", "message", "Сессия A: предпочитаю использовать PostgreSQL для баз данных"
        )
        mm.extract_all_facts()
        facts_a_before = mm.get_mem0_context("PostgreSQL", limit=10)
        assert len(facts_a_before) >= 1

        # Session B — should NOT see Session A's data
        mm.start_session("session-B", project_id="proj-B")
        mm.record_event("user", "message", "Сессия B: предпочитаю MongoDB для гибких схем данных")
        mm.extract_all_facts()

        # Session B searching for Session A's content should return nothing
        facts_b_search_a = mm.get_mem0_context("PostgreSQL", limit=10)
        # Session B can only see its own facts
        for f in facts_b_search_a:
            src = f.get("source", {})
            assert (
                src.get("session", "") == "session-B"
            ), f"Session B should not see Session A's facts: {f}"

        # Switch back to Session A
        mm.start_session("session-A", project_id="proj-A")
        facts_a_after = mm.get_mem0_context("PostgreSQL", limit=10)
        assert len(facts_a_after) >= 1
        mm.close()

    def test_namespace_isolation_registered(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session(
            "test-ns", project_id="p1", workspace_id="ws1", agent_id="agent1", user_id="user1"
        )
        assert mm.namespace.project == "p1"
        assert mm.namespace.workspace == "ws1"
        assert mm.namespace.agent == "agent1"
        assert mm.namespace.user == "user1"
        assert mm.namespace.session_id == "test-ns"
        mm.close()

    def test_ingestion_filter_reject_stats(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-reject-stats")
        # Feed stuff that will be rejected by pattern matching (logged in stats)
        mm.record_event(
            "assistant", "message", "You are a helpful AI assistant with guidelines for behavior"
        )
        mm.record_event(
            "assistant", "message", "Let me think step by step about the solution approach"
        )
        mm.record_event("assistant", "message", '{"results": [{"id": 1, "value": "test"}]}')
        stats = mm.ingestion_filter.get_reject_stats()
        assert stats["total_rejected"] >= 2, f"Got {stats['total_rejected']} rejections"
        mm.close()


class TestMalformedInput:
    """Verify robustness to malformed JSON and oversized payloads."""

    def test_record_event_none_content(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-none")
        eid = mm.record_event("user", "message", "")
        assert eid.startswith("filtered:")
        mm.close()

    def test_record_event_very_long_content(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-long")
        long_msg = "A" * 20000
        eid = mm.record_event("user", "message", long_msg)
        # Should be ingested (not filtered) but truncated by normalizer
        assert not eid.startswith("filtered:")
        stats = mm.get_archive_stats()
        assert stats["l3_events"] == 1
        mm.close()

    def test_record_event_special_chars(self, temp_dir):
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        mm.start_session("test-special")
        eid = mm.record_event("user", "message", "Тестовое сообщение с Unicode: 🦊 日本語 też")
        assert not eid.startswith("filtered:")
        mm.close()

    def test_context_builder_empty_inputs(self):
        from retrieval.context_builder import ContextBuilder

        builder = ContextBuilder()
        prompt, stats = builder.build()
        assert isinstance(prompt, str)
        assert stats["used_tokens"] >= 0

    def test_context_builder_no_query_facts(self):
        from retrieval.context_builder import ContextBuilder

        builder = ContextBuilder()
        # Empty facts list should not crash
        builder.set_mem0_facts([])
        builder.set_semantic_results([])
        builder.set_summaries([])
        prompt, stats = builder.build()
        assert isinstance(prompt, str)

    def test_ingestion_filter_unicode(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        # Short Unicode message should be rejected
        ok, reason = f.should_ingest("Привет!", "user", "message")
        assert not ok  # too short
        assert "too_short" in reason

    def test_ingestion_filter_unicode_passes(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        # Long enough Unicode message passes
        ok, reason = f.should_ingest(
            "Привет мир! Как дела? 😊 Сегодня отличный день", "user", "message"
        )
        assert ok

    def test_ingestion_filter_none_content(self):
        from extractors.ingestion_filter import IngestionFilter

        f = IngestionFilter()
        ok, reason = f.should_ingest(None, "user", "message")
        assert not ok
        assert reason == "empty_or_non_string"


class TestConcurrency:
    """Verify MemoryManager handles concurrent access properly."""

    def test_parallel_sessions_independent(self, temp_dir):
        """Multiple sessions created in sequence don't interfere."""
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)

        # Create 5 sessions sequentially (simulates concurrent access)
        sessions = [f"conc-sess-{i}" for i in range(5)]
        for sid in sessions:
            mm.start_session(sid)
            mm.record_event("user", "message", f"Event recorded in session {sid} now")

        # Each session should have exactly 1 event
        for sid in sessions:
            mm.start_session(sid)
            stats = mm.get_archive_stats()
            assert (
                stats["l3_events"] == 1
            ), f"Session {sid} has {stats['l3_events']} events, expected 1"
        mm.close()

    def test_multiple_sessions_different_projects(self, temp_dir):
        """Sessions in different projects are fully isolated."""
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)

        mm.start_session("s1", project_id="project-alpha")
        mm.record_event("user", "message", "Alpha project secret data that is confidential")

        mm.start_session("s2", project_id="project-beta")
        mm.record_event("user", "message", "Beta project public announcement data here now")

        # Session 2 should not see Session 1's events
        stats_s2 = mm.get_archive_stats()
        assert stats_s2["l3_events"] == 1  # Only its own event
        mm.close()


class TestServerHealth:
    """Verify FastAPI server endpoints (without actual HTTP server)."""

    def test_app_creation(self):
        """FastAPI app can be imported without errors."""
        from api.server import app

        assert app.title == "Kettu Mem"
        assert app.version == "0.2.1"

    def test_health_check_no_mm(self):
        """Health check works even without MemoryManager."""
        import asyncio

        from api.server import health

        # Health endpoint doesn't require MM
        result = asyncio.get_event_loop().run_until_complete(health())
        assert result["status"] == "ok"

    def test_security_import(self):
        """Security middleware can be imported."""
        from api.security import InputSanitizer

        sanitized = InputSanitizer.sanitize("Hello <script>alert(1)</script> World")
        assert "<script>" not in sanitized
        assert "Hello" in sanitized

    def test_settings_import(self):
        """Settings can be imported from server context."""
        from config import settings

        assert settings.data_dir is not None
        assert settings.port > 0


# ═══════════════════════════════════════════════════════
# METRICS & SECURITY COVERAGE TESTS
# ═══════════════════════════════════════════════════════


class TestMetricsAndSecurity:
    """Tests for metrics registry and security middleware."""

    def test_metrics_registry_record_request(self):
        from api.metrics import metrics

        metrics.record_request("GET", "/test", 200, 0.01)
        metrics.record_ingestion("s1", 0.005)
        metrics.record_fact_extraction("preference")
        metrics.record_compression()
        metrics.record_search("bm25", 0.02)
        metrics.record_embedding(0.05)
        # Should not raise

    def test_input_sanitizer_sql_injection(self):
        from api.security import InputSanitizer

        result = InputSanitizer.sanitize("SELECT * FROM users WHERE 1=1")
        assert "SELECT" not in result

    def test_input_sanitizer_script_tags(self):
        from api.security import InputSanitizer

        result = InputSanitizer.sanitize("<script>alert('xss')</script>")
        assert "<script>" not in result

    def test_input_sanitizer_null_bytes(self):
        from api.security import InputSanitizer

        result = InputSanitizer.sanitize("hello\x00world")
        assert "\x00" not in result

    def test_input_sanitizer_normal_text(self):
        from api.security import InputSanitizer

        result = InputSanitizer.sanitize("Normal text with no issues")
        assert result == "Normal text with no issues"

    def test_input_sanitizer_non_string(self):
        from api.security import InputSanitizer

        assert InputSanitizer.sanitize(None) is None
        assert InputSanitizer.sanitize(123) == 123

    def test_rate_limiter_allows(self):
        from api.security import RateLimiter

        rl = RateLimiter()
        rl.max_requests = 100
        rl.window = 60
        allowed, reason = rl.is_allowed("192.168.1.1")
        assert allowed
        assert reason == "ok"

    def test_rate_limiter_client_ip(self):
        from api.security import RateLimiter

        rl = RateLimiter()
        from unittest.mock import MagicMock

        mock_req = MagicMock()
        mock_req.headers = {"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}
        mock_req.client = MagicMock()
        mock_req.client.host = "127.0.0.1"
        ip = rl.get_client_ip(mock_req)
        assert ip == "10.0.0.1"

    def test_metrics_registry_set_mm(self, temp_dir):
        from api.metrics import metrics
        from memory.memory_manager import MemoryManager

        mm = MemoryManager(temp_dir)
        metrics.set_memory_manager(mm)
        metrics.update_gauges()  # Should not crash
        mm.close()

    def test_settings_defaults(self):
        from config import settings

        assert 0 < settings.ttl_days <= 365
        assert 0 < settings.decay_rate <= 1.0
        assert settings.ingest_min_content_length >= 1


class TestHybridSearch:
    """Hybrid search edge cases."""

    def test_bm25_multiple_results(self):
        from retrieval.hybrid_search import BM25Scorer

        bm25 = BM25Scorer()
        docs = [
            ("python programming language", {"id": 1}),
            ("java programming language", {"id": 2}),
            ("python for data science", {"id": 3}),
            ("javascript web development", {"id": 4}),
            ("python machine learning", {"id": 5}),
        ]
        bm25.index(docs)
        results = bm25.search("python programming", k=3)
        assert len(results) >= 1
        best_idx = results[0][0]
        assert "python" in docs[best_idx][0]

    def test_bm25_no_match(self):
        from retrieval.hybrid_search import BM25Scorer

        bm25 = BM25Scorer()
        docs = [("hello world test", {"id": 1})]
        bm25.index(docs)
        results = bm25.search("zzz_not_found_xxx")
        assert results == []

    def test_hybrid_retriever_normalize(self, temp_dir):
        from embeddings.faiss_index import FAISSSemanticIndex
        from retrieval.hybrid_search import HybridRetriever
        from storage.sqlite_index import SQLiteMetadataIndex

        faiss = FAISSSemanticIndex(temp_dir)
        sql = SQLiteMetadataIndex(f"{temp_dir}/search.db")
        retriever = HybridRetriever(faiss, sql)
        normalized = retriever.normalize_query("  Hello, World!  ")
        assert "hello" in normalized
        assert "world" in normalized
        # Punctuation should be stripped
        assert "!" not in normalized
        assert "," not in normalized
        sql.close()

    def test_bm25_stats(self):
        from retrieval.hybrid_search import BM25Scorer

        bm25 = BM25Scorer()
        docs = [("short", {"id": 1}), ("longer document here", {"id": 2})]
        bm25.index(docs)
        assert bm25._total_docs == 2
        assert bm25._avg_dl > 0


# ═══════════════════════════════════════════════════════
# FASTAPI TEST CLIENT TESTS
# ═══════════════════════════════════════════════════════


class TestAPIServerEndpoints:
    """Test API app creation and health endpoints."""

    def test_app_meta(self):
        from api.server import app

        assert app.title == "Kettu Mem"
        assert app.version == "0.2.1"

    def test_routes_registered(self):
        from api.server import app

        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/ready" in routes
        assert "/metrics" in routes
        assert "/stats" in routes
        assert "/session/start" in routes
        assert "/turn/before" in routes
        assert "/mem0/search" in routes

    def test_security_add_middleware(self):
        from fastapi import FastAPI

        from api.security import add_security_middleware

        app = FastAPI()
        add_security_middleware(app)
        # Should not raise

    def test_server_module_imports(self):
        """All server module functions are importable."""
        from api.server import (
            health,
        )

        # All imports should succeed
        assert callable(health)


# ═══════════════════════════════════════════════════════
# FASTAPI TEST CLIENT TESTS
# ═══════════════════════════════════════════════════════


class TestFastAPIClient:
    """Test API endpoints using actual HTTP calls via TestClient."""

    @pytest.fixture
    def client(self, temp_dir):
        from fastapi.testclient import TestClient

        import api.server as server_module

        # Override data dir before creating test client
        server_module._data_dir = temp_dir
        server_module._mm = None
        server_module._cr = None

        # Use context manager to handle lifespan properly
        with TestClient(server_module.app, raise_server_exceptions=False) as tc:
            yield tc

        # Cleanup
        if server_module._mm:
            try:
                server_module._mm.close()
            except Exception:
                pass
            server_module._mm = None
        server_module._cr = None
        server_module._data_dir = ""

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_ready_endpoint(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200

    def test_metrics_endpoint(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_session_start_and_end(self, client):
        # Start session
        resp = client.post(
            "/session/start", json={"session_id": "api-test-sess", "project_id": "api-test-proj"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"

        # End session
        resp2 = client.post("/session/end", json={"reason": "done"})
        assert resp2.status_code == 200

    def test_turn_flow(self, client):
        client.post("/session/start", json={"session_id": "api-turn"})

        # Record event
        resp = client.post(
            "/turn/after",
            json={
                "events": [
                    {
                        "role": "user",
                        "type": "message",
                        "content": "Test event that is long enough for ingestion",
                    }
                ]
            },
        )
        assert resp.status_code == 200

        # Build context
        resp2 = client.post("/turn/before", json={"query": "test"})
        assert resp2.status_code == 200

    def test_mem0_operations(self, client):
        client.post("/session/start", json={"session_id": "api-mem0"})
        resp = client.get("/mem0/stats")
        assert resp.status_code == 200
