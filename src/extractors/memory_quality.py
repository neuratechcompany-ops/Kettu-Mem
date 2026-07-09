"""
Memory Quality — memory scoring, TTL, decay, deduplication.

Memory Score = weighted sum:
  import_score * w_importance
+ recency_score * w_recency
+ confidence * w_confidence
+ access_score * w_access

Where:
- import_score: based on fact type (decision > preference > fact > entity)
- recency_score: exponential decay over time (configurable half-life)
- confidence: raw confidence from extraction [0..1]
- access_score: logarithmic boost from access_count

TTL: facts older than ttl_days are marked for archival/re-evaluation.

Usage:
  from extractors.memory_quality import MemoryQualityScorer
  scorer = MemoryQualityScorer()
  score = scorer.calculate(fact)
"""
import time
import math
from dataclasses import dataclass
from enum import Enum

from config import settings


class FactImportance(Enum):
    """Base importance for different fact types."""
    DECISION = 1.0
    PREFERENCE = 0.9
    FACT = 0.5
    RELATION = 0.6
    ENTITY = 0.3


# Map fact type strings to importance
_TYPE_IMPORTANCE = {
    "decision": FactImportance.DECISION.value,
    "preference": FactImportance.PREFERENCE.value,
    "fact": FactImportance.FACT.value,
    "relation": FactImportance.RELATION.value,
    "entity": FactImportance.ENTITY.value,
}


@dataclass
class MemoryScore:
    """Structured memory quality score."""
    total: float          # 0..1 composite score
    importance: float     # 0..1 type-based
    recency: float        # 0..1 time-decayed
    confidence: float     # 0..1 from extraction
    access: float         # 0..1 from usage
    is_expired: bool      # TTL exceeded
    days_until_expiry: int


class MemoryQualityScorer:
    """
    Compute memory quality scores for ranking and eviction.

    Weights from config:
      importance_weight (default 0.3)
      recency_weight     (default 0.3)
      confidence_weight  (default 0.2)
      access_weight      (default 0.2)
    """

    def __init__(self):
        self.w_imp = settings.importance_weight
        self.w_rec = settings.recency_weight
        self.w_con = settings.confidence_weight
        self.w_acc = settings.access_weight
        self.ttl_seconds = settings.ttl_days * 86400
        self.decay_rate = settings.decay_rate

    def calculate(self, fact: dict) -> MemoryScore:
        """
        Calculate composite memory score for a fact.

        Args:
            fact: {type, confidence, created_at, updated_at, access_count}

        Returns MemoryScore with total and breakdown.
        """
        now = time.time()

        # 1. Importance (type-based)
        fact_type = fact.get("type", "fact")
        importance = _TYPE_IMPORTANCE.get(fact_type, 0.5)

        # 2. Recency (exponential decay)
        created_at = fact.get("created_at", now)
        age_days = (now - created_at) / 86400.0
        # Half-life: ln(2) / decay_rate = ~13.5 days for decay_rate=0.95
        recency = self._decay_score(age_days)

        # 3. Confidence (raw)
        confidence = float(fact.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        # 4. Access score
        access_count = int(fact.get("access_count", 0))
        access = math.log(access_count + 1) / math.log(10 + 1)  # 0..1 range

        # Composite
        total = (
            importance * self.w_imp +
            recency * self.w_rec +
            confidence * self.w_con +
            access * self.w_acc
        )
        total = max(0.0, min(1.0, total))

        # TTL check
        age_seconds = now - created_at
        is_expired = age_seconds > self.ttl_seconds
        days_until = max(0, int((self.ttl_seconds - age_seconds) / 86400))

        return MemoryScore(
            total=round(total, 4),
            importance=round(importance, 4),
            recency=round(recency, 4),
            confidence=round(confidence, 4),
            access=round(access, 4),
            is_expired=is_expired,
            days_until_expiry=days_until,
        )

    def _decay_score(self, age_days: float) -> float:
        """Exponential decay: score = decay_rate ^ age_days."""
        if age_days <= 0:
            return 1.0
        return max(0.0, self.decay_rate ** age_days)

    def batch_score(self, facts: list[dict]) -> list[tuple[dict, MemoryScore]]:
        """Score multiple facts."""
        return [(f, self.calculate(f)) for f in facts]

    def rank(self, facts: list[dict], limit: int = 10) -> list[dict]:
        """Return top-N facts by memory score."""
        scored = self.batch_score(facts)
        scored.sort(key=lambda x: x[1].total, reverse=True)
        return [f for f, _s in scored[:limit]]

    def get_expired(self, facts: list[dict]) -> list[dict]:
        """Return facts that have exceeded TTL."""
        scored = self.batch_score(facts)
        return [f for f, s in scored if s.is_expired]
