"""
Storage layer — immutable archive + metadata indexing + session isolation.

Exports:
  L3VerbatimArchive — append-only JSONL event log
  SQLiteMetadataIndex — fast relational queries
  SessionNamespace — hierarchical namespace for session isolation
  SessionIsolation — cross-session management
"""

from storage.l3_verbatim import L3VerbatimArchive
from storage.session_isolation import SessionIsolation, SessionNamespace
from storage.sqlite_index import SQLiteMetadataIndex

__all__ = ["L3VerbatimArchive", "SQLiteMetadataIndex", "SessionNamespace", "SessionIsolation"]
