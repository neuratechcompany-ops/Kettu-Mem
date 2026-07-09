"""
Embeddings layer — vector encoding + FAISS index.

Exports:
  FAISSSemanticIndex — multi-backend embeddings (OpenAI, sentence-transformers, random fallback)
"""

from embeddings.faiss_index import FAISSSemanticIndex

__all__ = ["FAISSSemanticIndex"]
