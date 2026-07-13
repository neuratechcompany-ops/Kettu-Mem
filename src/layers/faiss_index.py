"""
Backward-compatible re-export shim — see embeddings/ package for canonical location.
"""

from embeddings.faiss_index import FAISSSemanticIndex

__all__ = ["FAISSSemanticIndex"]
