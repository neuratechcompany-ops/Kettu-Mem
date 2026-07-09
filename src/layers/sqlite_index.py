"""
Backward-compatible re-export shim — see storage/ package for canonical location.
"""
from storage.sqlite_index import SQLiteMetadataIndex

__all__ = ["SQLiteMetadataIndex"]
