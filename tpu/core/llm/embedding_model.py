# core/llm/embedding_model.py
from __future__ import annotations
import os, logging, asyncio, hashlib
from typing import List, Union, Optional

import numpy as np
from core.live_config import config

# ----------------------------
# Config
# ----------------------------
_EMBED_MODEL = config.get("openai_embed_model", "text-embedding-3-small")
_TARGET_DIM  = int(config.get("embed_dim", 768))  # keep stable output size

# ----------------------------
# Lazy OpenAI client
# ----------------------------
_client = None
def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from openai import OpenAI
        api_key = config.get("openai_api_key") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        _client = OpenAI(api_key=api_key)
        return _client
    except Exception as e:
        logging.debug(f"[EmbeddingModel] OpenAI client unavailable: {e}")
        return None

# ----------------------------
# Sentence-Transformers fallback
# ----------------------------
_ST_MODEL = None  # None = not tried, False = unavailable, else model

def _get_st_model():
    global _ST_MODEL
    if _ST_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            # small, fast model
            _ST_MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        except Exception as e:
            logging.debug(f"[EmbeddingModel] ST unavailable: {e}")
            _ST_MODEL = False
    return _ST_MODEL

# ----------------------------
# Hash fallback (deterministic)
# ----------------------------
def _hash_vec_np(text: str, dim: int) -> np.ndarray:
    payload = (text or "").strip().lower().encode("utf-8")
    h = hashlib.blake2b(payload, digest_size=32).digest()
    raw = (h * (dim // len(h) + 1))[:dim]
    arr = np.frombuffer(bytes(raw), dtype=np.uint8).astype("float32")
    # normalize
    if arr.size:
        arr = (arr - arr.mean()) / max(1e-6, arr.std())
    return arr

def _fit_dim(vec: np.ndarray, dim: int) -> np.ndarray:
    """Pad/truncate to dim so callers can rely on fixed size."""
    if vec.shape[0] == dim:
        return vec
    if vec.shape[0] > dim:
        return vec[:dim]
    out = np.zeros(dim, dtype=vec.dtype)
    out[: vec.shape[0]] = vec
    return out

# ----------------------------
# Core embedding helpers
# ----------------------------
def _embed_one(text: str, *, model: Optional[str] = None) -> np.ndarray:
    cleaned = (text or "").strip()
    if not cleaned:
        return np.zeros(_TARGET_DIM, dtype="float32")

    # 1) OpenAI
    client = _get_client()
    if client:
        try:
            resp = client.embeddings.create(model=model or _EMBED_MODEL, input=cleaned)
            vec = np.array(resp.data[0].embedding, dtype="float32")
            return _fit_dim(vec, _TARGET_DIM)
        except Exception as e:
            logging.warning(f"[EmbeddingModel] OpenAI embed failed; falling back: {e}")

    # 2) Sentence-Transformers
    st = _get_st_model()
    if st not in (None, False):
        try:
            vec = st.encode(cleaned)
            vec = np.array(vec, dtype="float32")
            return _fit_dim(vec, _TARGET_DIM)
        except Exception as e:
            logging.debug(f"[EmbeddingModel] ST encode failed; falling back: {e}")

    # 3) Hash fallback
    return _fit_dim(_hash_vec_np(cleaned, _TARGET_DIM), _TARGET_DIM)

# ----------------------------
# Public API (sync)
# ----------------------------
def embed_text(text: Union[str, List[str]], *, model: Optional[str] = None) -> Union[np.ndarray, List[np.ndarray]]:
    """
    If `text` is str -> returns np.ndarray of shape (_TARGET_DIM,)
    If `text` is List[str] -> returns List[np.ndarray] each of shape (_TARGET_DIM,)
    """
    if isinstance(text, str):
        return _embed_one(text, model=model)
    if not text:
        return [np.zeros(_TARGET_DIM, dtype="float32")]
    return [_embed_one(t, model=model) for t in text]

def embed_batch(texts: List[str], *, model: Optional[str] = None) -> List[np.ndarray]:
    return embed_text(texts, model=model)  # type: ignore[return-value]

# ----------------------------
# Public API (async)
# ----------------------------
async def aembed_text(text: Union[str, List[str]], *, model: Optional[str] = None) -> Union[np.ndarray, List[np.ndarray]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_text, text, model)
