# core/llm/embedding_service.py
from __future__ import annotations

import asyncio
import hashlib
import logging
from functools import lru_cache
from typing import List, Optional

# Model config
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Lazy, threadsafe singleton
_model: Optional[object] = None
_model_lock = asyncio.Lock()


# ---------- internals ----------

def _load_st_model_sync() -> object:
    """
    Import SentenceTransformer lazily and load the model synchronously.
    Called inside a thread executor.
    """
    from sentence_transformers import SentenceTransformer  # lazy import
    return SentenceTransformer(_MODEL_NAME)

async def _ensure_model() -> Optional[object]:
    """
    Ensure a single shared ST model instance. Returns None on failure.
    """
    global _model
    async with _model_lock:
        if _model is not None:
            return _model
        try:
            loop = asyncio.get_running_loop()
            _model = await loop.run_in_executor(None, _load_st_model_sync)
        except Exception as e:
            logging.warning(f"[EmbeddingService] Failed to load model: {e}")
            _model = None
    return _model

@lru_cache(maxsize=100_000)
def _key(text: str) -> str:
    return (text or "").strip().lower()[:4096]

def _hash_vec(text: str, dim: int = 128) -> List[float]:
    """
    Deterministic 128-dim fallback using blake2b -> z-score normalize.
    Uses numpy if available; otherwise a pure-Python normalization.
    """
    payload = _key(text).encode("utf-8")
    h = hashlib.blake2b(payload, digest_size=32).digest()  # 32 bytes
    raw = (h * (dim // len(h) + 1))[:dim]  # repeat bytes to reach dim

    try:
        import numpy as np  # optional dependency
        arr = np.frombuffer(bytes(raw), dtype=np.uint8).astype("float32")
        arr = (arr - arr.mean()) / max(1e-6, arr.std())
        return arr.tolist()
    except Exception:
        # pure-Python z-score
        vals = [float(b) for b in raw]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
        std = var ** 0.5 or 1e-6
        return [(v - mean) / std for v in vals]


# ---------- public API ----------

async def embed_text(text: str) -> List[float]:
    """
    Return a dense embedding for `text`.
    - Tries SentenceTransformer (normalized) in a background thread.
    - Falls back to a deterministic 128-dim hash vector if ST is unavailable.
    - Returns [] for empty/whitespace input.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    try:
        model = await _ensure_model()
        if model is None:
            return _hash_vec(cleaned)

        # model.encode is sync; run it in a thread
        loop = asyncio.get_running_loop()
        vec = await loop.run_in_executor(
            None,
            lambda: model.encode([cleaned], normalize_embeddings=True)  # type: ignore[attr-defined]
        )
        return vec[0].tolist()
    except Exception as e:
        logging.warning(f"[EmbeddingService] embed_text failed: {e}")
        return _hash_vec(cleaned)


async def embed_batch(texts: List[str]) -> List[List[float]]:
    """
    Batch embedding with order preservation.
    - Empty/whitespace items map to [].
    - Uses a single encode call when ST is available; otherwise hash fallback.
    """
    try:
        # Short-circuit trivial case
        if not texts:
            return []

        model = await _ensure_model()
        cleaned = [(t or "").strip() for t in texts]

        if model is None:
            # Hash fallback per item (sync, fast)
            return [_hash_vec(t) if t else [] for t in cleaned]

        # Prepare list of non-empty strings for one shot encode
        idx_map = [i for i, t in enumerate(cleaned) if t]
        if not idx_map:
            return [[] for _ in texts]

        to_embed = [cleaned[i] for i in idx_map]
        loop = asyncio.get_running_loop()
        vecs = await loop.run_in_executor(
            None,
            lambda: model.encode(to_embed, normalize_embeddings=True)  # type: ignore[attr-defined]
        )

        # Stitch back to original order
        out: List[List[float]] = [[] for _ in texts]
        it = iter(vecs)
        for i in idx_map:
            out[i] = next(it).tolist()
        return out

    except Exception as e:
        logging.warning(f"[EmbeddingService] embed_batch failed: {e}")
        # best-effort fallback for entire batch
        return [await embed_text(t) for t in texts]
