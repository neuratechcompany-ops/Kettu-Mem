"""
Retrieval Pipeline — hybrid search with BM25 + FAISS + RRF fusion.

Pipeline:
  1. Query normalization
  2. BM25 keyword search (on L3 archive or text chunks)
  3. FAISS semantic search
  4. Reciprocal Rank Fusion (RRF) — merge results
  5. Re-ranker (score-based)
  6. Context assembly

Usage:
  from retrieval.hybrid_search import HybridRetriever
  retriever = HybridRetriever(faiss_index, sqlite_index)
  results = retriever.search("query text", k=10)
"""
import re
import time
from collections import defaultdict

from config import settings


class BM25Scorer:
    """
    Minimal BM25 implementation for keyword search.

    Uses:
    - k1 = 1.5 (term frequency saturation)
    - b = 0.75 (length normalization)
    - avg_dl computed from corpus
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._documents: list[tuple[str, dict]] = []  # [(text, metadata), ...]
        self._avg_dl: float = 0
        self._term_index: dict[str, dict[int, int]] = defaultdict(dict)  # term → {doc_idx: freq}
        self._doc_freq: dict[str, int] = defaultdict(int)  # term → document frequency
        self._total_docs: int = 0

    def index(self, documents: list[tuple[str, dict]]):
        """Index a list of (text, metadata) tuples."""
        self._documents = documents
        self._term_index.clear()
        self._doc_freq.clear()
        self._total_docs = len(documents)

        total_length = 0
        for idx, (text, _meta) in enumerate(documents):
            tokens = self._tokenize(text)
            total_length += len(tokens)
            seen_terms = set()
            for token in tokens:
                self._term_index[token][idx] = self._term_index[token].get(idx, 0) + 1
                if token not in seen_terms:
                    self._doc_freq[token] = self._doc_freq.get(token, 0) + 1
                    seen_terms.add(token)

        self._avg_dl = total_length / max(self._total_docs, 1)

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenizer: lowercase, split on non-alphanumeric."""
        return re.findall(r'\w+', text.lower())

    def search(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        """Search and return [(doc_idx, bm25_score), ...]."""
        query_tokens = self._tokenize(query)
        if not query_tokens or self._total_docs == 0:
            return []

        scores = {}
        for token in set(query_tokens):
            df = self._doc_freq.get(token, 0)
            if df == 0:
                continue
            idf = max(0, (self._total_docs - df + 0.5) / (df + 0.5))
            idf += 1  # smooth

            for doc_idx, tf in self._term_index.get(token, {}).items():
                doc_len = len(self._tokenize(self._documents[doc_idx][0]))
                norm = 1 - self.b + self.b * (doc_len / max(self._avg_dl, 1))
                score = idf * (tf * (self.k1 + 1)) / (tf + self.k1 * norm)
                scores[doc_idx] = scores.get(doc_idx, 0) + score

        # Sort by score descending
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_scores[:k]


class HybridRetriever:
    """
    Hybrid search: BM25 + FAISS → RRF fusion → re-rank.

    Usage:
        retriever = HybridRetriever(faiss_index, sqlite_index)
        results = retriever.search("query", k=10)
    """

    def __init__(self, faiss_index, sqlite_index):
        self.faiss = faiss_index
        self.sqlite = sqlite_index
        self.bm25 = BM25Scorer()
        self._bm25_indexed: bool = False

    def _ensure_bm25_index(self):
        """Build BM25 index from all events if not done."""
        if self._bm25_indexed:
            return

        documents = []
        # Load from SQLite
        rows = self.sqlite.conn.execute(
            "SELECT event_id, content_preview, role, type FROM events ORDER BY step_id"
        ).fetchall()
        for r in rows:
            content = r["content_preview"] or ""
            if len(content) >= settings.ingest_min_content_length:
                documents.append((
                    content,
                    {
                        "event_id": r["event_id"],
                        "role": r["role"],
                        "type": r["type"],
                    }
                ))

        self.bm25.index(documents)
        self._bm25_indexed = True

    def normalize_query(self, query: str) -> str:
        """Normalize query before search."""
        query = query.strip().lower()
        # Remove excessive punctuation
        query = re.sub(r'[^\w\s]', ' ', query)
        # Collapse whitespace
        query = re.sub(r'\s+', ' ', query)
        return query

    def search(self, query: str, k: int = None,
               bm25_weight: float = None,
               faiss_weight: float = None) -> list[dict]:
        """
        Hybrid search with RRF fusion.

        Returns list of {event_id, score, bm25_rank, faiss_rank, content_preview, role, type}.
        """
        k = k or settings.search_default_k
        bm25_weight = bm25_weight if bm25_weight is not None else settings.bm25_weight
        faiss_weight = faiss_weight if faiss_weight is not None else settings.faiss_weight_retrieval

        query = self.normalize_query(query)
        t0 = time.time()

        # 1. BM25 keyword search
        self._ensure_bm25_index()
        bm25_results = self.bm25.search(query, k=min(k * 3, 50))
        bm25_ranks = {doc_idx: rank + 1 for rank, (doc_idx, _) in enumerate(bm25_results)}

        # 2. FAISS semantic search
        faiss_results = self.faiss.search(query, k=min(k * 3, 50))
        faiss_ranks = {}
        for rank, r in enumerate(faiss_results):
            faiss_id = r["faiss_id"]
            # Resolve faiss_id to event
            rows = self.sqlite.conn.execute(
                "SELECT event_id, chunk_text FROM vector_map WHERE faiss_id = ?",
                (faiss_id,)
            ).fetchall()
            for vr in rows:
                # Find corresponding BM25 doc_idx
                for doc_idx, (_text, meta) in enumerate(self.bm25._documents):
                    if meta["event_id"] == vr["event_id"]:
                        faiss_ranks[doc_idx] = rank + 1
                        break

        # 3. RRF (Reciprocal Rank Fusion)
        rrf_scores = defaultdict(float)
        r = 60  # RRF constant

        for doc_idx, rank in bm25_ranks.items():
            rrf_scores[doc_idx] += bm25_weight * (1.0 / (r + rank))

        for doc_idx, rank in faiss_ranks.items():
            rrf_scores[doc_idx] += faiss_weight * (1.0 / (r + rank))

        # Sort by RRF score
        sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        sorted_docs = sorted_docs[:k]

        # 4. Re-rank by score
        results = []
        for doc_idx, score in sorted_docs:
            if doc_idx < len(self.bm25._documents):
                _text, meta = self.bm25._documents[doc_idx]
                results.append({
                    "event_id": meta.get("event_id", ""),
                    "score": round(score, 4),
                    "bm25_rank": bm25_ranks.get(doc_idx, -1),
                    "faiss_rank": faiss_ranks.get(doc_idx, -1),
                    "content_preview": _text[:200],
                    "role": meta.get("role", "?"),
                    "type": meta.get("type", "?"),
                })

        total_time_ms = (time.time() - t0) * 1000
        # Attach timing to results (debug)
        if results:
            results[0]["_search_time_ms"] = round(total_time_ms, 1)
            results[0]["_bm25_hits"] = len(bm25_results)
            results[0]["_faiss_hits"] = len(faiss_results)

        return results

    def get_stats(self) -> dict:
        """Get retriever statistics."""
        return {
            "bm25_docs": self.bm25._total_docs if self._bm25_indexed else 0,
            "bm25_avg_dl": round(self.bm25._avg_dl, 1) if self._bm25_indexed else 0,
            "faiss_available": self.faiss.get_index_stats().get("exists", False),
        }
