# inputs/social/sentiment_scanner.py
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Iterable, Union

from core.live_config import config
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import tag_token_result, update_meta_keywords
from utils.logger import log_event
from utils.service_status import update_status
from utils.social_sources import extract_token_mentions, fetch_recent_posts
from librarian.data_librarian import librarian  # <- make sure this module is available

# --------- config / cadence ----------
SENTIMENT_INTERVAL = int(config.get("sentiment_scan_interval_sec", 60))  # seconds
SENTIMENT_WINDOW_MIN = int(config.get("sentiment_window_min", 5))         # minutes

# --------- runtime state ----------
_seen_post_ids: set[str] = set()
_sentiment_scores: Dict[str, float] = {}
_sentiment_cache: List[Dict[str, Any]] = []  # rolling window of recent observations

# --------- lexicons / heuristics ----------
_POS = {
    "🚀","🔥","📈","ath","moon","pump","profit","green","breakout","surge",
    "bull","bullish","buy","entry","sending","send it","printing","rocketing",
    "undervalued","100x","strong","gains","love","meta","trending","viral",
    "community","degen","solana","alpha","win","rocket",
}
_NEG = {
    "⚠️","❌","📉","rug","dump","scam","rekt","crash","bear","bearish","exit",
    "dead","sell","shit","honeypot","no liquidity","slow","fail","broken",
    "scared","zero","avoid","warning","delay","stuck","blacklist","mute","bot",
}

# --------- helpers ----------
def _parse_iso(ts: Optional[str]) -> datetime:
    if not ts or not isinstance(ts, str):
        return datetime.utcnow()
    try:
        if ts.endswith("Z"):
            ts = ts[:-1]
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.utcnow()

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,20}")

def _extract_keywords(text: str, top_k: int = 12) -> List[str]:
    """
    Grab $TICKERS, #hashtags, and words (>=3 chars), de-dupe in order.
    """
    if not text:
        return []
    tickers = re.findall(r"\$[A-Za-z0-9_]{2,20}", text)
    tags    = re.findall(r"#[A-Za-z0-9_]{2,20}", text)
    words   = _WORD_RE.findall(text.lower())

    seen, out = set(), []
    for tok in [*tickers, *tags, *words]:
        tl = tok.lower()
        if tl in seen:
            continue
        seen.add(tl)
        out.append(tok)
        if len(out) >= top_k:
            break
    return out

def _fallback_sentiment(text: str) -> float:
    """
    Simple bullish/bearish heuristic -> score in [0,1] where 0.5 is neutral.
    """
    tl = (text or "").lower()
    bull = any(k in tl for k in _POS)
    bear = any(k in tl for k in _NEG)
    if bull and not bear:
        return 0.8
    if bear and not bull:
        return 0.2
    return 0.5

def get_sentiment_score(text: str) -> tuple[int, list[str]]:
    """
    Legacy-style integer score (sum of hits) + matched keywords (for logging).
    Clamped to [-3, +3].
    """
    tl = (text or "").lower()
    score = 0
    matched: List[str] = []
    for w in _POS:
        if w in tl:
            score += 1
            matched.append(w)
    for w in _NEG:
        if w in tl:
            score -= 1
            matched.append(w)
    score = max(-3, min(3, score))
    return score, matched

def extract_keywords_and_sentiment(text: str) -> Tuple[List[str], float]:
    if not text:
        return [], 0.5
    keywords = _extract_keywords(text)
    score_f = _fallback_sentiment(text)
    return keywords, score_f

def get_token_sentiment(token: str) -> float:
    return float(_sentiment_scores.get(token, 0.0))

def record_sentiment(token: str, sentiment: float, source: str, *, keep: int = 50) -> None:
    _sentiment_cache.append({
        "token": token,
        "sentiment": float(sentiment),
        "source": source,
        "ts": datetime.utcnow().isoformat()
    })
    if len(_sentiment_cache) > keep:
        _sentiment_cache.pop(0)

def get_sentiment_report() -> str:
    if not _sentiment_cache:
        return "🧠 No sentiment data available yet."
    avg = sum(item["sentiment"] for item in _sentiment_cache) / len(_sentiment_cache)
    top = sorted(_sentiment_cache, key=lambda x: x["sentiment"], reverse=True)[:3]
    lines = [f"- `{t['token']}` ({t['sentiment']:+.2f}) via {t['source']}" for t in top]
    return (
        f"📊 *Sentiment Report*\n"
        f"- Avg Sentiment: `{avg:.2f}`\n"
        f"- Top Tokens:\n" + "\n".join(lines)
    )

# --------- librarian bridge ----------
async def _maybe_await(x: Union[None, Any]) -> None:
    if asyncio.iscoroutine(x):
        await x

async def _send_to_librarian(event: Dict[str, Any]) -> None:
    """
    Prefer a rich social ingest if present; otherwise fall back to record_signal.
    """
    try:
        if hasattr(librarian, "ingest_social_event"):
            await _maybe_await(librarian.ingest_social_event(event))
            return
    except Exception as e:
        logging.warning(f"[SentimentScanner] librarian.ingest_social_event error: {e}")

    try:
        # minimal fallback
        await _maybe_await(librarian.record_signal({
            "source": event.get("source", "sentiment_scanner"),
            "signature": event.get("id"),
            "program_id": None,
            "slot": None,
            "wallets": [],
            "tokens": event.get("tokens", []),
            "timestamp": event.get("timestamp"),
            "logs": event.get("raw") or event.get("text", ""),
        }))
    except Exception as e:
        logging.warning(f"[SentimentScanner] librarian.record_signal error: {e}")

def _first_contract_like(mentions: Iterable[str]) -> Optional[str]:
    """
    Heuristic: treat base58-ish 32–44 char strings as contract/mint candidates.
    """
    for m in mentions:
        s = m.strip()
        if 32 <= len(s) <= 44 and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", s):
            return s
    return None

def _first_ticker(mentions: Iterable[str]) -> Optional[str]:
    for m in mentions:
        if m.startswith("$") and 2 <= len(m) <= 20:
            return m[1:].upper()
    return None

# --------- core scanning logic ----------
async def scan_posts_for_sentiment() -> None:
    """
    Pulls recent posts, extracts token mentions, updates per-token sentiment totals,
    logs insights, updates meta keywords, and forwards full context to librarian.
    """
    global _seen_post_ids, _sentiment_scores

    update_status("sentiment_scanner")

    try:
        posts = await fetch_recent_posts()
    except Exception as e:
        logging.warning(f"[SentimentScanner] ⚠️ Failed to fetch posts: {e}")
        return

    if not posts:
        logging.info("[SentimentScanner] 💤 No posts fetched.")
        return

    recent_cutoff = datetime.utcnow() - timedelta(minutes=SENTIMENT_WINDOW_MIN)
    processed_posts = 0
    tagged_tokens = 0

    for post in posts:
        post_id = str(post.get("id") or "")
        if not post_id or post_id in _seen_post_ids:
            continue
        _seen_post_ids.add(post_id)

        post_time = _parse_iso(post.get("timestamp"))
        if post_time < recent_cutoff:
            continue

        platform = (post.get("platform") or "unknown").lower()
        author = str(post.get("author") or "unknown")

        content = " ".join([
            str(post.get("text", "")),
            str(post.get("title", "")),
            str(post.get("url", "")),
            author,
            " ".join(post.get("tags", []) or []),
        ]).strip()

        if not content:
            continue

        # mentions from source (if provided) OR extracted
        src_mentions = post.get("mentions") or []
        mentions = src_mentions or extract_token_mentions(content)
        if not mentions:
            continue

        processed_posts += 1

        i_score, matched_keywords = get_sentiment_score(content)
        f_score = _fallback_sentiment(content)

        # structured keywords (tickers/hashtags/words)
        extracted_keywords = _extract_keywords(content, top_k=16)
        # union the two sets for librarian/meta
        all_keywords = list(dict.fromkeys([*matched_keywords, *extracted_keywords]))

        # choose a canonical contract/ticker if present
        contract = _first_contract_like(mentions)
        ticker = _first_ticker(mentions)

        # per-mention accounting
        for token in mentions:
            _sentiment_scores[token] = _sentiment_scores.get(token, 0.0) + float(i_score)
            tagged_tokens += 1

            src = platform
            log_event(f"🧠 Sentiment: {token} scored {i_score:+} from {src}")
            try:
                tag_token_result(token, "sentiment")
            except Exception:
                pass

            try:
                log_scanner_insight(
                    token=token,
                    source="sentiment_scanner",
                    sentiment=float(i_score),
                    volume=0,
                    result="scanned",
                    meta_words=all_keywords,
                )
            except Exception:
                pass

            record_sentiment(token, f_score, src)
            try:
                # persist meta keywords for this token/ticker
                update_meta_keywords(token, all_keywords)
            except Exception:
                pass

        # forward one rich event per post into librarian
        try:
            await _send_to_librarian({
                "id": post_id,
                "time": post_time.isoformat(),
                "timestamp": post_time.isoformat(),
                "source": f"social/{platform}",
                "kind": "social_post",
                "author": author,
                "url": post.get("url"),
                "text": content,
                "raw": post,
                "tokens": mentions,
                "keywords": all_keywords,
                "sentiment": {
                    "float": f_score,
                    "int": i_score,
                    "matched": matched_keywords,
                },
                # optional hints for librarian’s profiler:
                "contract": contract,           # prefer this if present
                "token_name": ticker,           # else use a ticker symbol
                "people": [author] + [t for t in all_keywords if t.startswith("@")],
                "hashtags": [t for t in all_keywords if t.startswith("#")],
                "platform": platform,
            })
        except Exception as e:
            logging.warning(f"[SentimentScanner] librarian dispatch error: {e}")

    logging.info(f"[SentimentScanner] ✅ {processed_posts} new posts | {tagged_tokens} token mentions tagged")

# --------- runner ----------
async def start_sentiment_scanner() -> None:
    log_event("🧠 Sentiment scanner started.")
    while True:
        try:
            await scan_posts_for_sentiment()
        except Exception as e:
            logging.error(f"[SentimentScanner] ❌ Main loop error: {e}")
        await asyncio.sleep(SENTIMENT_INTERVAL)
