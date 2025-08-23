import asyncio
import inspect
import logging
import re
from typing import Any, Dict, List, Optional

from core.llm.llm_brain import analyze_token_name
from core.llm.sentiment_reason import extract_sentiment_reason
from strategy.strategy_memory import update_meta_keywords
from utils.nlp_language_bridge import process_text


# --- async/sync tolerant caller ---
async def maybe_call(func, *args, **kwargs):
    try:
        if func is None:
            return None
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        res = func(*args, **kwargs)
        if inspect.isawaitable(res):
            return await res
        return res
    except Exception as e:
        logging.warning(f"[TGNLP] call failed: {e}")
        return None

# --- precompiled patterns ---
_MINT_RE = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')
_TICKER_RE = re.compile(r'\$(\w{2,10})')

# === use this in async contexts ===
async def decode_text_signal(raw_text: str) -> Dict[str, Any]:
    cleaned = process_text(raw_text or "")

    # tolerate async or sync implementations
    reason = await maybe_call(extract_sentiment_reason, cleaned)
    meta = await maybe_call(analyze_token_name, cleaned)

    # update keywords (tolerate async/sync just in case)
    try:
        _ = await maybe_call(update_meta_keywords, meta)
    except Exception as e:
        logging.warning(f"[nlp_decoder_engine] update_meta_keywords failed: {e}")

    tokens = _MINT_RE.findall(cleaned)
    tickers = _TICKER_RE.findall(cleaned)

    return {
        "cleaned": cleaned,
        "reason": reason,
        "mints": tokens,
        "tickers": tickers,
        "meta": meta,
    }
