"""
Mem0 — Long-term Memory Layer (ADD-only extraction v1).

Extracts and stores:
- Facts (declarative knowledge)
- Preferences (user likes/dislikes)
- Decisions (project-level conclusions)
- Entities (people, tools, projects, brands)
- Relationships (entity linking)

Design principles:
- ADD-only: never overwrite, always append
- Confidence scoring: each fact has a confidence [0..1]
- Source tracking: every fact links back to session/event
- Deduplication: merge similar facts, increase confidence
- SQLite for structured storage, FAISS for semantic retrieval
"""
import json
import time
import uuid
import hashlib
from dataclasses import dataclass, field
from enum import Enum

from extractors.memory_quality import MemoryQualityScorer


class FactType(Enum):
    PREFERENCE = "preference"  # user likes/dislikes
    DECISION = "decision"      # project decisions
    FACT = "fact"              # declarative knowledge
    ENTITY = "entity"          # named entity
    RELATION = "relation"      # entity relationship


@dataclass
class Mem0Fact:
    """A single long-term memory fact."""
    fact_id: str
    type: FactType
    content: str
    confidence: float = 1.0
    entities: list[str] = field(default_factory=list)
    source_session: str = ""
    source_event: str = ""
    source_step: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0
    hash_key: str = ""  # for dedup

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "type": self.type.value,
            "content": self.content,
            "confidence": self.confidence,
            "entities": self.entities,
            "source": {
                "session": self.source_session,
                "event": self.source_event,
                "step": self.source_step,
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
        }


class Mem0Store:
    """
    Long-term memory with ADD-only semantics.

    Usage:
        mem0 = Mem0Store(db_path, faiss_index)
        mem0.extract_facts(events, session_id)     # extract from session
        mem0.add_fact(FactType.PREFERENCE, "...")  # manual add
        facts = mem0.search("query")               # semantic search
        all_facts = mem0.get_all()                  # dump all
    """

    def __init__(self, db_path: str, faiss_index=None):
        import sqlite3
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.faiss = faiss_index
        self.scorer = MemoryQualityScorer()
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS mem0_facts (
            fact_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            entities_json TEXT DEFAULT '[]',
            source_session TEXT,
            source_event TEXT,
            source_step INTEGER,
            hash_key TEXT,
            created_at REAL,
            updated_at REAL,
            access_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS mem0_entities (
            entity_id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            type TEXT,
            first_seen_at REAL,
            last_seen_at REAL,
            mention_count INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS mem0_relations (
            relation_id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            source_session TEXT,
            created_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_mem0_type ON mem0_facts(type);
        CREATE INDEX IF NOT EXISTS idx_mem0_hash ON mem0_facts(hash_key);
        CREATE INDEX IF NOT EXISTS idx_mem0_source ON mem0_facts(source_session);
        CREATE INDEX IF NOT EXISTS idx_mem0_entity_name ON mem0_entities(name);
        """)

    def _make_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def add_fact(self, fact_type: FactType, content: str, *,
                 confidence: float = 1.0, entities: list[str] = None,
                 source_session: str = "", source_event: str = "",
                 source_step: int = 0) -> Mem0Fact:
        """
        Add a fact with ADD-only semantics.
        If a similar fact exists (same hash), increase confidence instead.
        """
        hash_key = self._make_hash(content)

        # Check for existing similar fact
        existing = self.conn.execute(
            "SELECT * FROM mem0_facts WHERE hash_key = ?", (hash_key,)
        ).fetchone()

        if existing:
            # Merge: increase confidence (capped at 1.0)
            new_conf = min(1.0, existing["confidence"] + confidence * 0.3)
            self.conn.execute(
                """UPDATE mem0_facts SET confidence = ?, updated_at = ?, access_count = access_count + 1
                   WHERE fact_id = ?""",
                (new_conf, time.time(), existing["fact_id"])
            )
            self.conn.commit()
            return self._row_to_fact(existing)

        # New fact
        fact_id = uuid.uuid4().hex[:12]
        now = time.time()
        ent_json = json.dumps(entities or [], ensure_ascii=False)

        self.conn.execute(
            """INSERT INTO mem0_facts (fact_id, type, content, confidence,
               entities_json, source_session, source_event, source_step,
               hash_key, created_at, updated_at, access_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (fact_id, fact_type.value, content, confidence, ent_json,
             source_session, source_event, source_step, hash_key, now, now)
        )

        # Register entities
        for ent in (entities or []):
            self._register_entity(ent, "derived")

        self.conn.commit()

        # Embed if FAISS available
        if self.faiss:
            try:
                self.faiss.add_vectors([content], self._next_faiss_id())
            except (ValueError, RuntimeError, OSError) as e:
                import structlog
                logger = structlog.get_logger("mem0")
                logger.warning("faiss_add_vectors_failed", error=str(e)[:200])

        return Mem0Fact(
            fact_id=fact_id, type=fact_type, content=content,
            confidence=confidence, entities=entities or [],
            source_session=source_session, source_event=source_event,
            source_step=source_step, created_at=now, hash_key=hash_key,
        )

    def _next_faiss_id(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as c FROM mem0_facts").fetchone()
        return row["c"]

    def _register_entity(self, name: str, entity_type: str = "unknown"):
        entity_id = self._make_hash(f"entity:{name}")
        now = time.time()
        self.conn.execute(
            """INSERT INTO mem0_entities (entity_id, name, type, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
               last_seen_at = ?, mention_count = mention_count + 1""",
            (entity_id, name, entity_type, now, now, now)
        )

    def _row_to_fact(self, row) -> Mem0Fact:
        entities = json.loads(row["entities_json"]) if row["entities_json"] else []
        return Mem0Fact(
            fact_id=row["fact_id"],
            type=FactType(row["type"]),
            content=row["content"],
            confidence=row["confidence"],
            entities=entities,
            source_session=row["source_session"] or "",
            source_event=row["source_event"] or "",
            source_step=row["source_step"] or 0,
            created_at=row["created_at"] or 0,
            updated_at=row["updated_at"] or 0,
            access_count=row["access_count"] or 0,
            hash_key=row["hash_key"] or "",
        )

    # ── Extraction ──────────────────────────────────────

    def extract_facts(self, events: list[dict], session_id: str) -> list[Mem0Fact]:
        """
        Extract facts from session events (ADD-only).

        Heuristics:
        - Preferences: "я люблю", "мне нравится", "я предпочитаю", "I prefer"
        - Decisions: same markers as CompressionEngine
        - Entities: CAPITALIZED terms, email-like mentions
        - Facts: assertive statements from assistant
        """
        extracted = []

        for evt in events:
            if evt["type"] != "message":
                continue

            content = evt["content"]
            step = evt["step_id"]
            event_id = evt["event_id"]
            role = evt["role"]

            # Extract preferences (only from user)
            if role == "user":
                for fact in self._extract_preferences(content, session_id, event_id, step):
                    f = self.add_fact(FactType.PREFERENCE, fact["content"],
                                      confidence=fact["confidence"],
                                      source_session=session_id,
                                      source_event=event_id, source_step=step)
                    extracted.append(f)

            # Extract decisions
            for fact in self._extract_decisions_from_text(content, session_id, event_id, step):
                f = self.add_fact(FactType.DECISION, fact["content"],
                                  confidence=fact["confidence"],
                                  source_session=session_id,
                                  source_event=event_id, source_step=step)
                extracted.append(f)

            # Extract entities
            entities = self._find_entities(content)
            for ent in entities:
                self.add_fact(FactType.ENTITY, f"Entity: {ent}", confidence=0.8,
                              entities=[ent],
                              source_session=session_id,
                              source_event=event_id, source_step=step)

        return extracted

    def _extract_preferences(self, text: str, session_id: str, event_id: str, step: int) -> list[dict]:
        """Extract user preferences from text."""
        prefs = []
        patterns = [
            (r"(?:я|мне)\s+(?:люблю|нравится|предпочитаю|важно|ценю)\s+(.{10,100}?)(?:[.!?]|$)", 0.85),
            (r"(?:я|мне)\s+(?:не люблю|не нравится|раздражает|бесит)\s+(.{10,100}?)(?:[.!?]|$)", 0.9),
            (r"I\s+(?:prefer|like|love|hate|dislike)\s+(.{10,100}?)(?:[.!?]|$)", 0.8),
            (r"(?:хочу|буду)\s+(?:чтобы|использовать|работать)\s+(.{10,100}?)(?:[.!?]|$)", 0.7),
        ]
        import re
        for pattern, conf in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for m in matches:
                pref_text = m.strip() if isinstance(m, str) else m[0].strip()
                if len(pref_text) > 10:
                    prefs.append({"content": f"Предпочитает: {pref_text}", "confidence": conf})
        return prefs[:3]

    def _extract_decisions_from_text(self, text: str, session_id: str, event_id: str, step: int) -> list[dict]:
        """Extract decisions from text."""
        import re
        decisions = []
        markers = [
            r"(?:решил[аи]?|решение|decided|decision)\s*:?\s*(.{10,200}?)(?:[.!]|$)",
            r"(?:согласован[оы]|утвержден[оы]|выбрал[аи]?\s+вариант|остановились\s+на)\s*(.{10,200}?)(?:[.!]|$)",
            r"(?:договорились|принято|agreed|final\s+decision)\s*:?\s*(.{10,200}?)(?:[.!]|$)",
        ]
        for pattern in markers:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for m in matches:
                d = m.strip() if isinstance(m, str) else m[0].strip()
                if len(d) > 10:
                    decisions.append({"content": d, "confidence": 0.8})
        return decisions[:2]

    def _find_entities(self, text: str) -> list[str]:
        """Find named entities in text (heuristic)."""
        import re
        entities = set()

        # Capitalized multi-word terms
        caps = re.findall(r'\b([A-ZА-Я][a-zа-я]+(?:\s+[A-ZА-Я][a-zа-я]+){1,3})\b', text)
        for c in caps:
            if len(c) > 3 and c.lower() not in ("давай", "привет", "окей"):
                entities.add(c)

        # @mentions
        mentions = re.findall(r'@(\w+)', text)
        entities.update(mentions)

        # Known brand/product patterns
        brands = ["AmoCRM", "Bitrix24", "Яндекс", "Google", "Telegram",
                   "Notion", "Figma", "Slack", "Jira", "Miro", "Excel"]
        for b in brands:
            if b.lower() in text.lower():
                entities.add(b)

        return list(entities)[:10]

    # ── Queries ─────────────────────────────────────────

    def get_by_type(self, fact_type: FactType, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM mem0_facts WHERE type = ? ORDER BY confidence DESC, access_count DESC LIMIT ?",
            (fact_type.value, limit)
        ).fetchall()
        return [self._row_to_fact(r).to_dict() for r in rows]

    def get_all(self, limit: int = 100, source_session: str = None) -> list[dict]:
        if source_session:
            rows = self.conn.execute(
                "SELECT * FROM mem0_facts WHERE source_session = ? ORDER BY updated_at DESC",
                (source_session,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM mem0_facts ORDER BY updated_at DESC"
            ).fetchall()
        facts = [self._row_to_fact(r) for r in rows]
        # Apply quality scoring and filter expired
        scored = self._score_and_filter(facts)
        return [s["fact"].to_dict() for s in scored[:limit]]

    def get_entities(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM mem0_entities ORDER BY mention_count DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]

    def search_text(self, query: str, limit: int = 10, source_session: str = None) -> list[dict]:
        """Word-level text search in facts (case-insensitive, Unicode-aware)."""
        q_words = query.lower().split()
        # Fetch all facts, filter in Python (Mem0 stores are small)
        if source_session:
            rows = self.conn.execute(
                "SELECT * FROM mem0_facts WHERE source_session = ? ORDER BY confidence DESC",
                (source_session,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM mem0_facts ORDER BY confidence DESC"
            ).fetchall()
        results = []
        for row in rows:
            content_lower = row["content"].lower()
            # All query words must appear in content
            if all(w in content_lower for w in q_words):
                results.append(self._row_to_fact(row))
                if len(results) >= limit * 3:  # fetch more for scoring
                    break
        # Apply quality scoring and filter expired
        scored = self._score_and_filter(results)
        return [s["fact"].to_dict() for s in scored[:limit]]

    def search_semantic(self, query: str, k: int = 10) -> list[dict]:
        """Semantic search via FAISS if available."""
        if not self.faiss:
            return self.search_text(query, k)
        results = self.faiss.search(query, k)
        facts = []
        for r in results:
            row = self.conn.execute(
                "SELECT * FROM mem0_facts WHERE fact_id = (SELECT fact_id FROM mem0_facts LIMIT 1 OFFSET ?)",
                (r.get("faiss_id", 0),)
            ).fetchone()
            if row:
                d = self._row_to_fact(row).to_dict()
                d["semantic_score"] = r.get("score", 0)
                facts.append(d)
        return facts

    def _score_and_filter(self, facts: list) -> list[dict]:
        """Apply quality scoring, filter expired, rank by final_score."""
        scored = []
        for fact in facts:
            fact_dict = fact.to_dict() if hasattr(fact, 'to_dict') else fact
            score = self.scorer.calculate(fact_dict)
            if score.is_expired:
                continue
            scored.append({
                "fact": fact,
                "score": score,
                "total": score.total,
            })
        # Sort by total score descending
        scored.sort(key=lambda s: s["total"], reverse=True)
        return scored

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) as c FROM mem0_facts").fetchone()["c"]
        by_type = {}
        for ft in FactType:
            c = self.conn.execute(
                "SELECT COUNT(*) as c FROM mem0_facts WHERE type = ?", (ft.value,)
            ).fetchone()["c"]
            if c > 0:
                by_type[ft.value] = c
        entities = self.conn.execute("SELECT COUNT(*) as c FROM mem0_entities").fetchone()["c"]
        return {
            "total_facts": total,
            "by_type": by_type,
            "total_entities": entities,
        }

    def close(self):
        self.conn.close()
