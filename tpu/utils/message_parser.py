import logging
import re
from typing import Any, Dict, List, Optional, Union

from core.llm.sentiment_reason import extract_sentiment_reason, extract_sentiment_reason_sync
from utils.nlp_language_bridge import process_text


def _extract_keywords(text: str, max_k: int = 20) -> List[str]:
    """
    Extracts a mix of plain words, tickers ($XXX), and hashtags (#tag)
    of length >= 3. Deduplicates while preserving first-seen order.
    """
    tickers = re.findall(r"\$[A-Za-z0-9_]{2,20}", text)
    hashtags = re.findall(r"#[A-Za-z0-9_]{2,20}", text)
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text)

    seen, out = set(), []
    for tok in tickers + hashtags + words:
        t = tok.lower()
        if t not in seen:
            seen.add(t)
            out.append(tok)
        if len(out) >= max_k:
            break
    return out

async def parse_message_for_alpha(
    msg: Union[str, Dict[str, Any]],
    *,
    chatter: Optional[List[Dict[str, Any]]] = None,
    price_data: Optional[Dict[str, Any]] = None,
    force_mode: str = "auto",  # "auto", "heuristic", "openai"
) -> Dict[str, Any]:
    """
    Process a raw message into cleaned text, keywords, and sentiment reason JSON.
    
    Args:
        msg: raw text or dict containing a 'text' key
        chatter: optional list of related chatter posts (for context to LLM)
        price_data: optional dict with price/volume data
        force_mode: "auto" (prefer OpenAI if key set), "heuristic", or "openai"
    """
    try:
        raw_text = msg if isinstance(msg, str) else str(msg.get("text", ""))
        cleaned = process_text(raw_text)
    except Exception as e:
        logging.warning(f"[AlphaParser] Failed to process text: {e}")
        cleaned = str(msg) if not isinstance(msg, str) else msg

    try:
        # Always await async extractor
        reason_data = await extract_sentiment_reason(
            cleaned,
            chatter=chatter,
            price_data=price_data,
            mode=force_mode
        )
    except Exception as e:
        logging.warning(f"[AlphaParser] Sentiment extraction failed: {e}")
        # Fall back to heuristic sync call
        reason_data = extract_sentiment_reason_sync(cleaned, chatter=chatter, price_data=price_data, mode="heuristic")

    keywords = _extract_keywords(cleaned)

    return {
        "cleaned": cleaned,
        "sentiment_reason": reason_data,  # dict from unified module
        "keywords": keywords,
    }
