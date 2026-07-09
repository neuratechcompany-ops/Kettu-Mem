"""
Backward-compatible re-exports — canonical packages: storage/, embeddings/, retrieval/, extractors/.

Import from the new package names for new code:
  from storage import L3VerbatimArchive, SQLiteMetadataIndex, SessionNamespace, SessionIsolation
  from embeddings import FAISSSemanticIndex
  from retrieval import ContextBuilder, ContextConfig, BudgetStrategy, ToolSchema, HybridRetriever
  from extractors import Mem0Store, FactType, CompressionEngine, CognitiveRuntime, MemorySpace, IngestionFilter, MemoryQualityScorer

Imports from layers.* still work for backward compatibility.
"""
from storage.l3_verbatim import L3VerbatimArchive
from storage.sqlite_index import SQLiteMetadataIndex
from storage.session_isolation import SessionNamespace, SessionIsolation
from embeddings.faiss_index import FAISSSemanticIndex
from retrieval.context_builder import (
    ContextBuilder, ContextConfig, BudgetStrategy, ToolSchema,
)
from retrieval.hybrid_search import HybridRetriever, BM25Scorer
from extractors.compression import CompressionEngine, CompressionResult
from extractors.mem0 import Mem0Store, FactType, Mem0Fact
from extractors.cognitive_runtime import (
    CognitiveRuntime, MemorySpace, StepOutcome,
    ReflectionEngine, ToolIntelligence, PlanStep, PlanningState,
)
from extractors.ingestion_filter import IngestionFilter
from extractors.memory_quality import MemoryQualityScorer, MemoryScore
