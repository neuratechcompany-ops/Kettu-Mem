"""
FAISS Semantic Index — vector similarity search.

Embedding backends (auto-selected in order):
  1. OpenAI text-embedding-3-small (via API key)
  2. sentence-transformers (local all-MiniLM-L6-v2)
  3. Deterministic pseudo-random (fallback for spike/dev)

OpenAI key sources (checked in order):
  - OPENAI_API_KEY env var
  - ~/.openclaw/workspace/secrets/openai-key.txt

Architecture:
- SQLite vector_map: maps faiss_id → event_id, chunk_text
- FAISS index: stores vectors, returns by faiss_id
- L3 archive: full event content retrieved by event_id
"""
import json
import os
import time
import numpy as np
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)


def _load_openai_key() -> str:
    """Load OpenAI API key from env or secrets file."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return key
    secret_paths = [
        Path.home() / ".openclaw/workspace/secrets/openai-key.txt",
        Path.home() / "secrets/openai-key.txt",
        Path.home() / ".openclaw/secrets/openai-key.txt",
    ]
    for p in secret_paths:
        if p.exists():
            return p.read_text().strip()
    return ""


class FAISSSemanticIndex:
    """FAISS-based semantic search with multi-backend embedding."""

    # OpenAI model settings
    OPENAI_MODEL = "text-embedding-3-small"
    OPENAI_DIM = 1536  # full quality (Matryoshka 384 → 1536)

    def __init__(self, index_dir: str, model_name: str = "all-MiniLM-L6-v2"):
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self._model = None
        self._dim = 384  # default
        self._backend = "none"  # openai, sentence_transformers, random
        self._openai_client = None
        self._load_model()

    def _load_model(self):
        """Try loading best available embedding backend."""
        # 1. OpenAI
        openai_key = _load_openai_key()
        if openai_key:
            try:
                from openai import OpenAI
                self._openai_client = OpenAI(api_key=openai_key)
                # Verify with a small test
                test_resp = self._openai_client.embeddings.create(
                    model=self.OPENAI_MODEL,
                    input=["test"],
                    dimensions=self.OPENAI_DIM,
                )
                self._dim = len(test_resp.data[0].embedding)
                self._backend = "openai"
                self._model = True  # flag: backend ready
                logger.info("faiss_backend_loaded", backend="openai", model=self.OPENAI_MODEL, dim=self._dim)
                return
            except Exception as e:
                logger.warning("faiss_openai_unavailable", error=str(e))

        # 2. sentence-transformers (local)
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            self._dim = self._model.get_sentence_embedding_dimension()
            self._backend = "sentence_transformers"
            logger.info("faiss_backend_loaded", backend="sentence_transformers", model=self.model_name, dim=self._dim)
            return
        except (ImportError, Exception) as e:
            logger.warning("faiss_local_unavailable", error=str(e))

        # 3. Random fallback
        logger.warning("faiss_random_fallback", dim=self._dim)
        self._model = None
        self._backend = "random"

    @property
    def embedding_backend(self) -> str:
        return self._backend

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a list of texts into vectors."""
        if self._backend == "openai" and self._openai_client:
            return self._embed_openai(texts)
        elif self._backend == "sentence_transformers" and self._model:
            return self._model.encode(texts, show_progress_bar=False)
        else:
            return self._embed_random(texts)

    def _embed_openai(self, texts: list[str]) -> np.ndarray:
        """Embed using OpenAI API with Matryoshka reduced dimensions."""
        # Batch: OpenAI allows up to 2048 inputs per request
        all_embeddings = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = self._openai_client.embeddings.create(
                model=self.OPENAI_MODEL,
                input=batch,
                dimensions=self.OPENAI_DIM,
            )
            for item in resp.data:
                all_embeddings.append(item.embedding)

        vecs = np.array(all_embeddings, dtype=np.float32)
        # Normalize for cosine similarity
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return vecs / norms

    def _embed_random(self, texts: list[str]) -> np.ndarray:
        """Fallback: deterministic pseudo-random based on text hash."""
        vecs = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = hash(t) % (2**31)
            rng = np.random.RandomState(seed)
            vecs[i] = rng.randn(self._dim).astype(np.float32)
        # Normalize
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return vecs / norms

    def build_index(self, texts: list[str], ids: list[int]) -> str:
        """
        Build a FAISS index from texts and save atomically to disk.

        Writes to a temp file first, fsyncs, then atomically renames.
        This prevents corrupted index files on crash during write.

        Returns the index file path.
        """
        import faiss

        vectors = self.embed(texts).astype(np.float32)

        # Use IndexFlatIP for cosine similarity (since we normalize)
        index = faiss.IndexFlatIP(self._dim)
        index.add(vectors)

        # Atomic write: temp file → fsync → rename
        index_path = self.index_dir / "faiss.index"
        tmp_index_path = self.index_dir / "faiss.index.tmp"
        id_map_path = self.index_dir / "faiss_id_map.json"
        tmp_id_map_path = self.index_dir / "faiss_id_map.json.tmp"

        # Write index to temp
        faiss.write_index(index, str(tmp_index_path))
        with open(tmp_index_path, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp_index_path, index_path)

        # Write ID map to temp
        with open(tmp_id_map_path, "w") as f:
            json.dump({"ids": ids, "dim": self._dim, "count": len(ids)}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_id_map_path, id_map_path)

        logger.info("faiss_index_built", vectors=len(ids), dim=self._dim)
        return str(index_path)

    def load_index(self) -> tuple:
        """
        Load FAISS index and ID map.

        Returns (faiss_index, id_list) or (None, []) if missing/corrupted.
        Corrupted indices are detected and logged; callers should auto-rebuild.
        """
        import faiss

        index_path = self.index_dir / "faiss.index"
        id_map_path = self.index_dir / "faiss_id_map.json"

        if not index_path.exists():
            return None, []

        try:
            index = faiss.read_index(str(index_path))
            if not id_map_path.exists():
                logger.warning("faiss_id_map_missing", path=str(id_map_path))
                return None, []
            with open(id_map_path) as f:
                id_map = json.load(f)
            return index, id_map["ids"]
        except (RuntimeError, OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("faiss_index_corrupted",
                           path=str(index_path),
                           error=str(e))
            # Clean up corrupted files so a rebuild can proceed cleanly
            for p in (index_path, id_map_path):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            return None, []

    def is_index_healthy(self) -> bool:
        """Check if the FAISS index is present and loadable."""
        index, ids = self.load_index()
        return index is not None and len(ids) > 0

    def search(self, query: str, k: int = 10) -> list[dict]:
        """
        Search for k most similar chunks.

        Returns list of {faiss_id, score, chunk_text}.
        chunk_text must be resolved via SQLite vector_map.
        """
        import faiss

        index, ids = self.load_index()
        if index is None or len(ids) == 0:
            return []

        query_vec = self.embed([query]).astype(np.float32)
        scores, indices = index.search(query_vec, min(k, len(ids)))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(ids):
                continue
            results.append({
                "faiss_id": ids[idx],
                "score": float(score),
            })
        return results

    def add_vectors(self, texts: list[str], start_id: int) -> int:
        """
        Add new vectors to an existing index (or build from scratch).
        Uses atomic writes (temp file → fsync → rename).

        Returns the next available ID.
        """
        import faiss

        vectors = self.embed(texts).astype(np.float32)
        new_ids = list(range(start_id, start_id + len(texts)))

        index_path = self.index_dir / "faiss.index"
        tmp_index_path = self.index_dir / "faiss.index.tmp"
        id_map_path = self.index_dir / "faiss_id_map.json"
        tmp_id_map_path = self.index_dir / "faiss_id_map.json.tmp"

        if index_path.exists():
            existing_index = faiss.read_index(str(index_path))
            existing_index.add(vectors)
            faiss.write_index(existing_index, str(tmp_index_path))
            with open(tmp_index_path, "rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp_index_path, index_path)

            with open(id_map_path) as f:
                id_map = json.load(f)
            id_map["ids"].extend(new_ids)
            id_map["count"] = len(id_map["ids"])
        else:
            index = faiss.IndexFlatIP(self._dim)
            index.add(vectors)
            faiss.write_index(index, str(tmp_index_path))
            with open(tmp_index_path, "rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp_index_path, index_path)
            id_map = {"ids": new_ids, "dim": self._dim, "count": len(new_ids)}

        with open(tmp_id_map_path, "w") as f:
            json.dump(id_map, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_id_map_path, id_map_path)

        return start_id + len(texts)

    def get_index_stats(self) -> dict:
        """Return index statistics."""
        index, ids = self.load_index()
        if index is None:
            return {"exists": False, "count": 0}
        return {
            "exists": True,
            "count": index.ntotal,
            "dim": index.d,
        }
