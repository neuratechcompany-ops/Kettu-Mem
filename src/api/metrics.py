"""
Metrics — Prometheus-compatible metrics endpoint.

Exposes:
  GET /metrics  — Prometheus text format

Counters:
  kettu_requests_total{method, path, status}
  kettu_events_ingested_total{session_id}
  kettu_facts_extracted_total{type}
  kettu_compressions_total

Histograms:
  kettu_request_latency_seconds{method, path}
  kettu_ingestion_latency_seconds
  kettu_retrieval_latency_seconds
  kettu_embedding_latency_seconds

Gauges:
  kettu_active_sessions
  kettu_faiss_vectors
  kettu_mem0_facts_total
  kettu_l3_events_total
  kettu_memory_usage_bytes

Usage:
  from api.metrics import add_metrics_endpoint
  add_metrics_endpoint(app)
"""
import os
import time

import psutil
from fastapi import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

from config import settings

# ── Metrics Definitions ─────────────────────────────────

# Counters
requests_total = Counter(
    "kettu_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

events_ingested_total = Counter(
    "kettu_events_ingested_total",
    "Total events ingested",
    ["session_id"],
)

facts_extracted_total = Counter(
    "kettu_facts_extracted_total",
    "Total facts extracted",
    ["type"],
)

compressions_total = Counter(
    "kettu_compressions_total",
    "Total compressions triggered",
)

search_requests_total = Counter(
    "kettu_search_requests_total",
    "Total search requests",
    ["source"],  # bm25, faiss, hybrid
)

# Histograms
request_latency = Histogram(
    "kettu_request_latency_seconds",
    "Request latency in seconds",
    ["method", "path"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
)

ingestion_latency = Histogram(
    "kettu_ingestion_latency_seconds",
    "Event ingestion latency",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1],
)

retrieval_latency = Histogram(
    "kettu_retrieval_latency_seconds",
    "Context retrieval latency",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1],
)

embedding_latency = Histogram(
    "kettu_embedding_latency_seconds",
    "Embedding computation latency",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

# Gauges
active_sessions = Gauge(
    "kettu_active_sessions",
    "Number of active sessions",
)

faiss_vectors = Gauge(
    "kettu_faiss_vectors",
    "Number of vectors in FAISS index",
)

mem0_facts_total = Gauge(
    "kettu_mem0_facts_total",
    "Total Mem0 facts stored",
)

l3_events_total = Gauge(
    "kettu_l3_events_total",
    "Total L3 archive events",
)

memory_usage_bytes = Gauge(
    "kettu_memory_usage_bytes",
    "Process memory usage (RSS)",
)

# Info
build_info = Info(
    "kettu_build",
    "Kettu Mem build information",
)


# ── Metrics Registry ────────────────────────────────────

class MetricsRegistry:
    """
    Central metrics registry for Kettu Mem.

    Provides increment/observe/set methods and auto-updates gauges.
    """

    def __init__(self):
        self._mm = None  # MemoryManager reference (set by server)
        build_info.info({
            "version": "0.2.0",
            "config_port": str(settings.port),
            "embedding_backend": settings.embedding_backend,
            "token_budget_normal": str(settings.token_budget_normal),
        })

    def set_memory_manager(self, mm):
        """Bind MemoryManager for gauge updates."""
        self._mm = mm

    def record_request(self, method: str, path: str, status: int, latency_s: float):
        """Record an HTTP request."""
        requests_total.labels(method=method, path=path, status=str(status)).inc()
        request_latency.labels(method=method, path=path).observe(latency_s)

    def record_ingestion(self, session_id: str, latency_s: float):
        """Record event ingestion."""
        events_ingested_total.labels(session_id=session_id).inc()
        ingestion_latency.observe(latency_s)

    def record_fact_extraction(self, fact_type: str):
        """Record fact extraction."""
        facts_extracted_total.labels(type=fact_type).inc()

    def record_compression(self):
        """Record compression trigger."""
        compressions_total.inc()

    def record_search(self, source: str, latency_s: float):
        """Record search request."""
        search_requests_total.labels(source=source).inc()
        retrieval_latency.observe(latency_s)

    def record_embedding(self, latency_s: float):
        """Record embedding computation."""
        embedding_latency.observe(latency_s)

    def update_gauges(self):
        """Update gauge metrics from MemoryManager."""
        if not self._mm:
            return

        try:
            # Active sessions
            active_sessions.set(1 if self._mm._session_id else 0)

            # FAISS
            faiss_stats = self._mm.faiss.get_index_stats()
            faiss_vectors.set(faiss_stats.get("count", 0))

            # Mem0
            mem0_stats = self._mm.mem0.get_stats()
            mem0_facts_total.set(mem0_stats.get("total_facts", 0))

            # L3
            if self._mm._session_id:
                l3_events_total.set(self._mm.l3.get_event_count(self._mm._session_id))

            # Memory
            process = psutil.Process(os.getpid())
            memory_usage_bytes.set(process.memory_info().rss)
        except (AttributeError, psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass


# Singleton
metrics = MetricsRegistry()


# ── Metrics Middleware ──────────────────────────────────

class MetricsMiddleware:
    """
    ASGI middleware for automatic request metrics.

    Usage:
        app.add_middleware(MetricsMiddleware)
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.time()
        path = scope.get("path", "?")
        method = scope.get("method", "?")
        status_code = 0

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency_s = time.time() - start
            metrics.record_request(method, path, status_code, latency_s)


# ── Metrics endpoint ────────────────────────────────────

def add_metrics_endpoint(app):
    """Add Prometheus /metrics endpoint to FastAPI app."""

    @app.get("/metrics")
    async def metrics_endpoint():
        """Prometheus metrics endpoint."""
        metrics.update_gauges()
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )
