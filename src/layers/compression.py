"""
Backward-compatible re-export shim — see extractors/ package for canonical location.
"""

from extractors.compression import CompressionEngine, CompressionResult

__all__ = ["CompressionEngine", "CompressionResult"]
