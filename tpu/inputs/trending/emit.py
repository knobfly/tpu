import asyncio
import logging
from typing import Any, Dict

from librarian.data_librarian import librarian
from utils.logger import log_event

_COOLDOWN = {}  # mint -> next_allowed_ts

def _cooldown_ok(mint: str, ttl_s: int) -> bool:
    import time
    now = time.time()
    nxt = _COOLDOWN.get(mint, 0)
    if now >= nxt:
        _COOLDOWN[mint] = now + ttl_s
        return True
    return False

async def emit_trend(evt: Dict[str, Any], token_cooldown_s: int = 600):
    mint = evt.get("mint")
    if not mint:
        return
    if not _cooldown_ok(mint, token_cooldown_s):
        return
    try:
        if hasattr(librarian, "ingest_trending_signal"):
            await librarian.ingest_trending_signal(evt)
        else:
            # Fallback: send as a generic event Nyx can learn from
            await librarian.ingest_stream_event({"type": "trending_signal", **evt})
        log_event(f"[Trending] {evt['source']} -> {mint} ({evt['reason']})")
    except Exception as e:
        logging.warning(f"[Trending] ingest failed: {e}")
