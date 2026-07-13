"""
Backward-compatible re-export shim — see extractors/ package for canonical location.
"""

from extractors.mem0 import FactType, Mem0Fact, Mem0Store

__all__ = ["Mem0Store", "FactType", "Mem0Fact"]
