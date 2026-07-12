"""
Backward-compatible re-exports — canonical packages: storage/, embeddings/, retrieval/, extractors/.

Import from the new package names for new code:
  from storage import L3VerbatimArchive, SQLiteMetadataIndex, SessionNamespace, SessionIsolation
  from embeddings import FAISSSemanticIndex
  from retrieval import ContextBuilder, ContextConfig, BudgetStrategy, ToolSchema, HybridRetriever
  from extractors import Mem0Store, FactType, CompressionEngine, CognitiveRuntime, MemorySpace, IngestionFilter, MemoryQualityScorer

Imports from layers.* still work for backward compatibility.
"""

from embeddings.faiss_index import FAISSSemanticIndex
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
from retrieval.context_builder import (
    BudgetStrategy,
    ContextBuilder,
    ContextConfig,
    ToolSchema,
)
from retrieval.hybrid_search import BM25Scorer, HybridRetriever
from storage.l3_verbatim import L3VerbatimArchive
from storage.session_isolation import SessionIsolation, SessionNamespace
from storage.sqlite_index import SQLiteMetadataIndex
