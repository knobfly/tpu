# core/llm/sentiment_reason.py
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

# Optional OpenAI path (auto-disabled if no key)
try:
    from openai import AsyncOpenAI
    
    aclient = AsyncOpenAI(api_key=OPENAI_KEY)  # type: ignore
except Exception:  # library not installed
    openai = None  # type: ignore

from core.live_config import config
from utils.logger import log_event
from utils.service_status import update_status

# -----------------------
# Config
# -----------------------
OPENAI_KEY = (config.get("openai_api_key") or "").strip()
OPENAI_MODEL = config.get("openai_model", "gpt-4")
OPENAI_TEMPERATURE = float(config.get("openai_temperature", 0.6))

if openai and OPENAI_KEY:

# -----------------------
# Heuristic lexicons
# -----------------------
_BULL = ("🚀", "🔥", "📈", "ath", "moon", "pump", "profit", "green", "breakout", "surge")
_BEAR = ("⚠️", "❌", "📉", "rug", "dump", "scam", "rekt", "crash")

_SUSPICIOUS_HINTS = (
    "airdrop", "giveaway", "send", "private key", "seed", "dm me", "free",
    "double", "100x now", "guaranteed", "pump group"
)

# -----------------------
# Utilities
# -----------------------
def _format_chatter(posts: List[Dict[str, Any]] | List[str]) -> str:
    if not posts:
        return "No chatter found."
    lines: List[str] = []
    for p in posts[-6:]:
        if isinstance(p, str):
            txt = p.strip()
        else:
            txt = str(p.get("text", "")).strip()
        if txt:
            lines.append(f"- {txt[:200]}")
    return "\n".join(lines) if lines else "No clean posts."

def _extract_keywords(text: str, top_k: int = 12) -> List[str]:
    tickers = re.findall(r"\$([A-Za-z0-9_]{2,20})", text)
    tags    = re.findall(r"#([A-Za-z0-9_]{2,20})", text)
    words   = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,20}", text.lower())
    # de-dupe in order
    seen, out = set(), []
    for w in [*tickers, *tags, *words]:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            out.append(w)
        if len(out) >= top_k:
            break
    return out

def _heuristic_reason(text: str) -> Dict[str, Any]:
    """Always-available fast path. Returns canonical JSON shape."""
    t = (text or "").strip()
    tl = t.lower()

    # quick labels
    if any(k in tl for k in _BULL):
        reason = "bullish_emoji_or_phrase"
    elif any(k in tl for k in _BEAR):
        reason = "bearish_emoji_or_phrase"
    elif re.search(r"\$[A-Za-z0-9_]{2,20}", t):
        reason = "ticker_mention"
    else:
        reason = "neutral_signal"

    suspicious = any(h in tl for h in _SUSPICIOUS_HINTS)
    kws = _extract_keywords(t)
    summary = (
        "Bullish chatter with hype cues."
        if reason.startswith("bullish") else
        "Bearish chatter with risk cues."
        if reason.startswith("bearish") else
        "Ticker or neutral mention."
    )

    return {
        "reason": reason,
        "keywords": kws,
        "summary": summary,
        "suspicious": bool(suspicious),
        "model": "heuristic",
    }

async def _openai_chat_json(prompt: str) -> Optional[Dict[str, Any]]:
    """Call OpenAI; return parsed JSON or None on any failure."""
    if not (openai and OPENAI_KEY):
        return None
    try:
        update_status("sentiment_reason_extractor")
        # ChatCompletion legacy path kept for compatibility with your codebase
        resp = await aclient.chat.completions.create(model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=OPENAI_TEMPERATURE)
        content = resp.choices[0].message.content.strip()
        # Extract the first {...} block to be robust to stray text
        start = content.find("{")
        end   = content.rfind("}")
        if start == -1 or end == -1:
            return None
        return json.loads(content[start:end+1])
    except Exception as e:
        logging.warning(f"[SentimentReason/OpenAI] {e}")
        return None

# -----------------------
# Public API
# -----------------------
async def explain_trending_reason(token_data: Dict[str, Any]) -> str:
    """
    Optional richer explanation based on token metadata.
    Uses OpenAI if available, otherwise falls back to a brief heuristic line.
    """
    # Heuristic fallback (no network)
    def _heuristic_meta(td: Dict[str, Any]) -> str:
        text = json.dumps(td, ensure_ascii=False)
        kws = _extract_keywords(text, top_k=12)
        flags = []
        for k in ("elon", "ai", "meme", "pump", "influencer", "utility", "bot"):
            if k in text.lower():
                flags.append(k)
        hint = ", ".join(flags) if flags else "general interest"
        return f"Likely trending due to {hint}; keywords: {', '.join(kws[:6])}."

    if not (openai and OPENAI_KEY):
        logging.debug("[SentimentReason] OpenAI key missing; using heuristic metadata reason.")
        return _heuristic_meta(token_data or {})

    prompt = f"""
You are a crypto trend analyst AI. Given the token metadata and known attributes, explain why this token might be trending.

Metadata:
{json.dumps(token_data or {}, indent=2)}

Identify themes (meme, Elon, AI, pump.fun, influencer, utility) and suggest likely reasons.
Respond with 1 paragraph ONLY.
"""
    j = await _openai_chat_json(prompt)
    if isinstance(j, dict) and "summary" in j:
        # If the JSON came back in the same shape we use, prefer that
        return str(j.get("summary") or _heuristic_meta(token_data or {}))
    # If model responded with plain text rather than JSON, re-run a simple heuristic
    return _heuristic_meta(token_data or {})

async def extract_sentiment_reason(
    text: str,
    *,
    chatter: Optional[List[Dict[str, Any]] | List[str]] = None,
    price_data: Optional[Dict[str, Any]] = None,
    mode: str = "auto",   # "auto" | "heuristic" | "openai"
) -> Dict[str, Any]:
    """
    Canonical async API used everywhere.
    Returns a dict with keys: reason, keywords, summary, suspicious, model.
    """
    t = (text or "").strip()
    # Fast path: heuristic only
    if mode == "heuristic" or not (openai and OPENAI_KEY):
        return _heuristic_reason(t)

    if mode not in ("auto", "openai"):
        mode = "auto"

    # Build OpenAI prompt from chatter/price context
    prompt = f"""
You are a crypto sentiment analyst. Given recent chatter and optional price movement, explain *why* this text/token is trending.
Return JSON with keys: reason (one of hype, influencer, meme, utility, bot activity, scam, pumpfun, tokenomics, community, unknown),
keywords (array), summary (short), suspicious (boolean).

Text:
{t or "N/A"}

Chatter:
{_format_chatter(chatter or [])}

Price Data:
{json.dumps(price_data, indent=2) if price_data else 'N/A'}

Respond with valid JSON only.
"""
    j = await _openai_chat_json(prompt)
    if not isinstance(j, dict):
        # Fallback robustly to heuristic
        return _heuristic_reason(t)

    # Normalize fields & fill gaps
    out = {
        "reason": str(j.get("reason") or "unknown"),
        "keywords": list(j.get("keywords") or _extract_keywords(t)),
        "summary": str(j.get("summary") or "No summary provided."),
        "suspicious": bool(j.get("suspicious", False)),
        "model": "openai",
    }
    # Quick sanity: if keywords empty, backfill
    if not out["keywords"]:
        out["keywords"] = _extract_keywords(t)
    return out

def extract_sentiment_reason_sync(
    text: str,
    *,
    chatter: Optional[List[Dict[str, Any]] | List[str]] = None,
    price_data: Optional[Dict[str, Any]] = None,
    mode: str = "auto",
) -> Dict[str, Any]:
    """
    Sync shim for legacy call sites. Uses asyncio.run when safe; otherwise
    returns a heuristic result rather than deadlocking.
    """
    try:
        asyncio.get_running_loop()
        # A loop is running (likely in Telethon); avoid deadlock: heuristic only.
        logging.debug("[SentimentReason] sync called under running loop; returning heuristic.")
        return _heuristic_reason(text or "")
    except RuntimeError:
        return asyncio.run(extract_sentiment_reason(text, chatter=chatter, price_data=price_data, mode=mode))
