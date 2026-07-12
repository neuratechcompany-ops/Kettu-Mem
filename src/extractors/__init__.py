"""
Extractors layer — fact extraction, compression, cognitive runtime, quality.

Exports:
  Mem0Store — long-term memory (ADD-only extraction)
  FactType — preference/decision/fact/entity/relation types
  Mem0Fact — single memory fact dataclass
  CompressionEngine — rule-based event compression
  CompressionResult — compression output
  CognitiveRuntime — planning + reflection + tool intelligence
  MemorySpace — global/user/project/session/temporary
  StepOutcome — progress/stuck/loop/wrong_tool/strategy_change/complete/blocked
  IngestionFilter — pre-ingestion content filtering
  MemoryQualityScorer — memory scoring, TTL, decay
"""

from extractors.cognitive_runtime import (
    CognitiveRuntime,
    MemorySpace,
    PlanningState,
    PlanStep,
    ReflectionEngine,
    StepOutcome,
    ToolIntelligence,
)
from extractors.compression import CompressionEngine, CompressionResult
from extractors.ingestion_filter import IngestionFilter
from extractors.mem0 import FactType, Mem0Fact, Mem0Store
from extractors.memory_quality import MemoryQualityScorer, MemoryScore

__all__ = [
    "Mem0Store", "FactType", "Mem0Fact",
    "CompressionEngine", "CompressionResult",
    "CognitiveRuntime", "MemorySpace", "StepOutcome",
    "ReflectionEngine", "ToolIntelligence", "PlanStep", "PlanningState",
    "IngestionFilter", "MemoryQualityScorer", "MemoryScore",
]
