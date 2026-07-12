"""
Retrieval layer — context assembly + hybrid search.

Exports:
  ContextBuilder — prompt assembly under token budget
  ContextConfig — assembly configuration
  BudgetStrategy — tight/normal/generous strategies
  ToolSchema — tool description schema
  HybridRetriever — BM25 + FAISS + RRF fusion search
  BM25Scorer — keyword search scorer
"""

from retrieval.context_builder import (
    BudgetStrategy,
    ContextBuilder,
    ContextConfig,
    ToolSchema,
)
from retrieval.hybrid_search import BM25Scorer, HybridRetriever

__all__ = [
    "ContextBuilder", "ContextConfig", "BudgetStrategy", "ToolSchema",
    "HybridRetriever", "BM25Scorer",
]
