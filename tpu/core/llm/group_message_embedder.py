from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

# -----------------------------
# Config: where to append JSONL
# -----------------------------
# Env override wins; defaults to your original library path
EMBED_LOG = os.environ.get(
    "NYX_EMBED_LOG",
    "/home/ubuntu/nyx/runtime/library/chats/group_embeds.jsonl",
)

# ---------------------------------------
# Optional local sentence-transformer Fallback
# ---------------------------------------
_ST_MODEL = None

def _get_sentence_model():
    global _ST_MODEL
    if _ST_MODEL is None:
        from sentence_transformers import SentenceTransformer  # lazy import
        _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _ST_MODEL

# -----------------------------
# Helpers
# -----------------------------
def _clean_text(text: Optional[str]) -> str:
    return (text or "").strip()

def _mk_id(cleaned: str) -> str:
    return hashlib.sha256(cleaned.lower().encode()).hexdigest()[:16] if cleaned else ""

def _token_count(cleaned: str) -> int:
    return len(cleaned.split()) if cleaned else 0

def _append_jsonl(path: str, rec: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

# ---------------------------------------------------
# Embedding backend resolver (async-friendly)
# Order: NyxLLMBrain.embed_message -> embedding_service.embed_text -> ST model
# ---------------------------------------------------
async def _embed_text(text: str) -> List[float]:
    cleaned = _clean_text(text)

    # 1) Try NyxLLMBrain if available
    try:
        from core.llm.llm_brain import NyxLLMBrain  # type: ignore
        brain = getattr(NyxLLMBrain, "instance", None) or NyxLLMBrain.load()
        if hasattr(brain, "embed_message"):
            rec = brain.embed_message(cleaned, source="tg::group")
            if isinstance(rec, dict) and "embedding" in rec:
                return list(rec["embedding"])  # type: ignore
            if isinstance(rec, list):
                return list(rec)
    except Exception as e:
        logging.debug(f"[GroupEmbedder] NyxLLMBrain unavailable/failed: {e}")

    # 2) Try async embedding service
    try:
        from core.llm.embedding_service import embed_text as svc_embed_text  # type: ignore
        vec = await svc_embed_text(cleaned)
        return list(vec or [])
    except Exception as e:
        logging.debug(f"[GroupEmbedder] embedding_service fallback failed: {e}")

    # 3) Fallback to SentenceTransformer (run off-thread)
    try:
        loop = asyncio.get_running_loop()
        model = _get_sentence_model()
        vec = await loop.run_in_executor(None, lambda: model.encode(cleaned).tolist() if cleaned else [])
        return list(vec or [])
    except Exception as e:
        logging.warning(f"[GroupEmbedder] ST fallback failed: {e}")
        return []

# ---------------------------------------------------
# Sync helpers for batch/offline use (kept from your original)
# ---------------------------------------------------

def embed_message(
    message: Optional[str] = None,
    *,
    text: Optional[str] = None,
    source: Optional[str] = None
) -> Dict[str, Any]:
    """
    Sync embed for standalone/batch contexts.
    Accepts either `message` or `text` (back-compat).
    Uses the local SentenceTransformer via _get_sentence_model().
    """
    cleaned = (text if text is not None else message) or ""
    cleaned = cleaned.strip()

    model = _get_sentence_model()
    vec = model.encode(cleaned).tolist() if cleaned else []

    return {
        "id": hashlib.sha256(cleaned.lower().encode()).hexdigest()[:16] if cleaned else "",
        "tokens": len(cleaned.split()) if cleaned else 0,
        "text": cleaned,
        "embedding": vec,
        "source": source or "tg::group",
        "ts": datetime.utcnow().isoformat(),
        "dim": len(vec),
    }

def batch_embed(messages: List[str]) -> List[Dict[str, Any]]:
    return [embed_message(msg) for msg in messages]

# ---------------------------------------------------
# Canonical async entrypoint for TG listeners
# ---------------------------------------------------
async def embed_group_message(
    text: str,
    group_name: str,
    *,
    timestamp: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    sentiment: Optional[float] = None,
    sender: Optional[str] = None,
    wallets: Optional[List[str]] = None,
    symbol: Optional[str] = None,   # token context (optional)
    mint: Optional[str] = None,     # token context (optional)
    **kwargs: Any,                   # tolerate extra args from various callers
) -> Dict[str, Any]:
    """
    Canonical embed for Telegram group messages.
    - Accepts timestamp and any extra kwargs safely.
    - Writes a jsonl record to EMBED_LOG (append-only).
    - Returns a structured dict including embedding, id, token count.
    """
    try:
        cleaned = _clean_text(text)
        ts = timestamp or datetime.utcnow().isoformat()
        vec = await _embed_text(cleaned)

        rec: Dict[str, Any] = {
            "ts": ts,
            "id": _mk_id(cleaned),
            "tokens": _token_count(cleaned),
            "group": group_name,
            "sender": sender,
            "text": cleaned,
            "keywords": keywords or [],
            "sentiment": float(sentiment) if sentiment is not None else None,
            "wallets": wallets or [],
            "symbol": symbol,
            "mint": mint,
            "dim": len(vec),
            "embedding": vec,
        }

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _append_jsonl, EMBED_LOG, rec)
        return rec
    except Exception as e:
        logging.warning(f"[GroupEmbedder] failed: {e}")
        return {}

# ---------------------------------------------------
# Back-compat async wrapper (old signature support)
# ---------------------------------------------------
async def embed_group_message_legacy(
    group: str,
    message: str,
    *,
    symbol: Optional[str] = None,
    mint: Optional[str] = None,
    timestamp: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Legacy-compatible wrapper so existing callers don't break.
    Supports the old order: (group, message, timestamp=...).
    """
    return await embed_group_message(
        text=message,
        group_name=group,
        timestamp=timestamp,
        symbol=symbol,
        mint=mint,
        keywords=kwargs.get("keywords"),
        sentiment=kwargs.get("sentiment"),
        sender=kwargs.get("sender"),
        wallets=kwargs.get("wallets"),
        **{k: v for k, v in kwargs.items() if k not in {"keywords", "sentiment", "sender", "wallets"}}
    )
