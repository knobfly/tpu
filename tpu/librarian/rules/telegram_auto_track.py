# librarian/rules/telegram_auto_track.py
import logging
import time
from datetime import datetime

from utils.logger import log_event


def _now_iso():
    return datetime.utcnow().isoformat()

def _high_conf_candidates(cands, min_conf=0.9):
    return [c for c in (cands or [])
            if float(c.get("confidence", 0)) >= min_conf
            and c.get("kind") == "mint"]

def _subscribe_mint(mint: str, ttl_sec: int = 86400):
    try:
        from inputs.onchain.solana_stream_listener import add_mint_watch
        add_mint_watch(mint, ttl=ttl_sec)
        log_event(f"[TelegramRule] 📡 Tracking mint {mint} (ttl={ttl_sec}s)")
    except Exception as e:
        logging.warning(f"[TelegramRule] Failed mint subscribe {mint}: {e}")

def _record_memory(mint: str, tags: dict):
    try:
        from memory.token_memory_index import token_memory_index
        token_memory_index.record(mint, "telegram_source", tags)
    except Exception as e:
        logging.warning(f"[TelegramRule] token_memory_index record failed: {e}")

def run_on_memory_entry(entry: dict):
    """
    Called whenever a new memory item is stored.
    Entry format assumed:
    {
        "source": "telegram",
        "chat_id": "...",
        "chat_name": "...",
        "text": "...",
        "token_candidates": [ { "value": "...", "kind": "mint", "confidence": 0.95, "reason": "url_detect" } ],
        "topic_tags": [...]
    }
    """
    if entry.get("source") != "telegram":
        return

    cands = entry.get("token_candidates") or []
    hi = _high_conf_candidates(cands)

    if len(hi) == 1:
        mint = hi[0]["value"]
        _subscribe_mint(mint, ttl_sec=86400)
        _record_memory(mint, {
            "chat": entry.get("chat_name"),
            "chat_id": entry.get("chat_id"),
            "reason": hi[0].get("reason", "extracted_from_url"),
            "topic_tags": entry.get("topic_tags", []),
            "ts": _now_iso(),
        })
        log_event(f"[TelegramRule] ✅ Telegram → mint {mint} auto-tracked")
    elif len(cands) > 1:
        log_event(f"[TelegramRule] 📝 Multiple candidates in {entry.get('chat_name')}, skipping auto-subscribe")
    else:
        if entry.get("topic_tags"):
            log_event(f"[TelegramRule] (topic-only) {entry.get('chat_name')}: {entry.get('topic_tags')}")
