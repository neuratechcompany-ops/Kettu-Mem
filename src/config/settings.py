"""
Kettu Mem Configuration — pydantic-settings based.

All magic numbers, paths, and tunables live here.
Configuration sources (priority order):
  1. Environment variables (KETTU_MEM_ prefix)
  2. .env file in project root
  3. kettu_mem.yaml config file
  4. Default values

Usage:
  from config import settings
  print(settings.data_dir)
"""
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_config_yaml() -> Optional[Path]:
    """Find kettu_mem.yaml in standard locations."""
    candidates = [
        Path.cwd() / "kettu_mem.yaml",
        Path.home() / ".config" / "kettu_mem" / "kettu_mem.yaml",
        Path(__file__).parent.parent.parent / "kettu_mem.yaml",  # project root
        Path.home() / ".openclaw" / "kettu_mem.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


class Settings(BaseSettings):
    """Kettu Mem configuration model."""

    model_config = SettingsConfigDict(
        env_prefix="KETTU_MEM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Paths ────────────────────────────────────────

    data_dir: str = str(Path.home() / ".openclaw" / "memory-store")
    """Root data directory for all storage."""

    # ── Server ───────────────────────────────────────

    host: str = "127.0.0.1"
    port: int = 8765
    workers: int = 1
    """HTTP server settings."""

    # ── Embeddings ───────────────────────────────────

    embedding_backend: str = "auto"
    """Embedding backend: auto, openai, sentence_transformers, random."""

    embedding_model: str = "text-embedding-3-small"
    """Model name for embeddings."""

    embedding_dim: int = 1536
    """Embedding dimension."""

    embedding_batch_size: int = 100
    """Batch size for API embedding calls."""

    openai_api_key: Optional[str] = None
    """OpenAI API key. Falls back to env OPENAI_API_KEY or secrets file."""

    openai_base_url: Optional[str] = None
    """OpenAI API base URL (for proxies)."""

    # ── Token Budget ─────────────────────────────────

    token_budget_tight: int = 16000
    """Token budget for tight strategy."""

    token_budget_normal: int = 32000
    """Token budget for normal strategy."""

    token_budget_generous: int = 64000
    """Token budget for generous strategy."""

    output_reserve_pct: float = 0.20
    """Percentage of token budget reserved for model output."""

    # ── Context Assembly ─────────────────────────────

    recent_events_limit_tight: int = 15
    recent_events_limit_normal: int = 30
    recent_events_limit_generous: int = 50

    max_semantic_chunks_tight: int = 5
    max_semantic_chunks_normal: int = 10
    max_semantic_chunks_generous: int = 15

    max_mem0_facts: int = 10
    max_summaries: int = 5
    max_summaries_tight: int = 3
    max_summaries_generous: int = 10

    # ── Compression ──────────────────────────────────

    compression_threshold_pct: float = 0.70
    """Auto-compress when context utilization exceeds this."""

    compression_min_events: int = 10
    """Minimum uncompressed events to trigger compression."""

    # ── Mem0 ─────────────────────────────────────────

    mem0_extract_batch_size: int = 10
    """Auto-extract Mem0 facts every N events."""

    mem0_auto_extract: bool = True
    """Enable auto-extraction of Mem0 facts."""

    mem0_confidence_threshold: float = 0.3
    """Minimum confidence for fact dedup merging."""

    # ── Ingestion ────────────────────────────────────

    ingest_min_content_length: int = 20
    """Minimum content length for FAISS embedding."""

    ingest_max_content_length: int = 500
    """Maximum content chunk for FAISS embedding."""

    ingest_dedup_enabled: bool = True
    """Enable chunk deduplication before embedding."""

    # ── Retrieval ────────────────────────────────────

    search_default_k: int = 10
    """Default number of semantic search results."""

    search_max_k: int = 100
    """Maximum number of search results."""

    bm25_weight: float = 0.4
    """BM25 weight in hybrid search fusion."""

    faiss_weight_retrieval: float = 0.6
    """FAISS weight in hybrid search fusion."""

    # ── Memory Quality ───────────────────────────────

    ttl_days: int = 90
    """Default TTL for memory facts in days."""

    decay_rate: float = 0.95
    """Daily decay factor for memory scores."""

    importance_weight: float = 0.3
    recency_weight: float = 0.3
    confidence_weight: float = 0.2
    access_weight: float = 0.2
    """Weights for memory_score calculation."""

    # ── Security ─────────────────────────────────────

    api_key: Optional[str] = None
    """API key for authentication."""

    rate_limit_requests: int = 100
    """Max requests per minute per client."""

    rate_limit_window: int = 60
    """Rate limit window in seconds."""

    # ── Logging ──────────────────────────────────────

    log_level: str = "INFO"
    """Logging level."""

    log_format: str = "json"
    """Log format: json, console, or structured."""

    # ── Session ──────────────────────────────────────

    default_project_id: str = "default"
    """Default project ID for sessions."""

    max_sessions: int = 1000
    """Maximum number of sessions to track."""

    # ── Cognitive ────────────────────────────────────

    cognitive_max_steps: int = 500
    """Maximum steps for cognitive tasks."""

    cognitive_reflection_window: int = 5
    """Number of recent reflections to consider for strategy adjustment."""


# Singleton instance
settings = Settings()
