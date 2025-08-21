# x_feed_scanner.py

from __future__ import annotations
import asyncio
import json
import os
import time
from typing import Any, Dict, List, Set

import aiohttp
from core.live_config import config
from utils.logger import log_event
from utils.universal_input_validator import ensure_str
from inputs.social.twitter_api import fetch_recent_tweets
from librarian.data_librarian import librarian
from inputs.social.x_alpha.x_gate import x_api_guard
from inputs.social.x_alpha.x_trending import TrendingTerms
from utils.meta_keywords import add_keywords

# --- State ---
X_STATE_DIR = "/home/ubuntu/nyx/runtime/memory/x"
TRACKED_HANDLES_PATH = os.path.join(X_STATE_DIR, "tracked_handles.json")
TRACKED_TOKENS_PATH = os.path.join(X_STATE_DIR, "tracked_tokens.json")
X_MIN_SPACING = float(config.get("x_min_spacing_sec", 5.0))
X_MAX_TERMS = int(config.get("x_max_terms", 60))
X_MAX_HANDLES = int(config.get("x_max_handles", 300))
X_MAX_TOKENS = int(config.get("x_max_tokens", 500))
X_MAX_QUERY_CHARS = 480
X_TREND_HALFLIFE = int(config.get("x_trend_half_life_sec", 6 * 3600))

_trending = TrendingTerms(half_life_sec=X_TREND_HALFLIFE)

# --- File helpers ---
def _ensure_dirs():
    os.makedirs(X_STATE_DIR, exist_ok=True)

def _load_json(path: str, default: list) -> list:
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _save_json(path: str, data: list) -> None:
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        log_event(f"[XFeedScanner] Failed to save {path}")

def _get_tracked_handles() -> Set[str]:
    _ensure_dirs()
    return set(_load_json(TRACKED_HANDLES_PATH, []))

def _set_tracked_handles(handles: Set[str]):
    _ensure_dirs()
    _save_json(TRACKED_HANDLES_PATH, sorted(list(handles)[:X_MAX_HANDLES]))

def _get_tracked_tokens() -> Set[str]:
    _ensure_dirs()
    return set(_load_json(TRACKED_TOKENS_PATH, []))

def _set_tracked_tokens(tokens: Set[str]):
    _ensure_dirs()
    _save_json(TRACKED_TOKENS_PATH, sorted(list(tokens)[:X_MAX_TOKENS]))

def _dedupe_terms(terms: List[str]) -> List[str]:
    seen = set()
    out = []
    for t in terms:
        key = t.lstrip("$").lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out

def _pack_query(terms: List[str], base_filter: str = "", max_len: int = X_MAX_QUERY_CHARS) -> str:
    SAFETY = 16
    target = max(64, max_len - SAFETY)
    terms = _dedupe_terms(terms)
    chosen: List[str] = []
    for t in terms:
        candidate = f"({ ' OR '.join(chosen+[t]) })"
        if len(candidate.encode("utf-8")) <= target:
            chosen.append(t)
        else:
            break
    return f"({ ' OR '.join(chosen) })" if chosen else ""

def _extract_tokens(text: str) -> Set[str]:
    if not text:
        return set()
    out = set()
    parts = text.replace("\n", " ").split()
    for w in parts:
        if len(w) > 1 and w[0] == "$" and w[1:].isalnum():
            out.add(w.upper())
        if 30 <= len(w) <= 48 and w.isalnum():
            out.add(w)
    return out

def _extract_handle(tweet: dict[str, any]) -> str:
    user = tweet.get("user") or tweet.get("author") or tweet.get("handle")
    if isinstance(user, dict):
        return ensure_str(user.get("username") or user.get("screen_name") or user.get("handle"))
    return ensure_str(user)

async def fetch_x_mentions(token: str) -> list[str]:
    """
    Returns recent X posts that look like they mention this token.
    Keeps it simple to avoid circular imports: we call fetch_recent_tweets()
    and filter locally by text/token fields.
    """
    mentions: list[str] = []
    try:
        tweets = await fetch_recent_tweets()
    except Exception as e:
        log_event(f"[XFeed] fetch_x_mentions fetch failed: {e}")
        return mentions

    def send_alert(message: str):
        """Send a real-time alert (DM, push, etc.). Replace with actual DM logic."""
        log_event(f"[ALERT] {message}")
        # Example: send_self_dm(message) or push notification

    def update_keywords(scope: str, keywords: list[str]):
        """Admin function to update dynamic keywords for filtering."""
        from utils.meta_keywords import add_keywords
        add_keywords(scope=scope, keywords=keywords, source="admin", ref="manual_update")
    t = token.lower()
    for tw in tweets:
        text = ensure_str(tw.get("text", ""))
        tagged_token = ensure_str(tw.get("token", ""))
        if t and (t in text.lower() or t == tagged_token.lower() or f"${t}" in text.lower()):
            mentions.append(text)
    return mentions

# --- Core passive scanner ---
async def run_scan_x_feed():
    if not config.get("x_autopost_enabled", True):
        return

    tracked_handles = _get_tracked_handles()
    tracked_tokens = _get_tracked_tokens()
    base_pool = set(t.lstrip("$") for t in tracked_tokens if 1 <= len(t) <= 24)

    try:
        top_terms = _trending.top_terms(limit=X_MAX_TERMS, whitelist=base_pool)
    except Exception:
        top_terms = list(base_pool)[:X_MAX_TERMS]

    try:
        rule = _pack_query(top_terms)
        if not rule.strip():
            rule = _pack_query(list(base_pool)[:20])
    except Exception:
        rule = _pack_query(list(base_pool)[:20])

    tweets = []
    seen_ids = set()
    async with x_api_guard(min_spacing=X_MIN_SPACING, cross_process=True, who="x_feed_scanner"):
        try:
            async with aiohttp.ClientSession() as session:
                batch = await fetch_recent_tweets(
                    session=session,
                    bearer_token=config.get("twitter_bearer_token"),
                    keywords=rule,
                    since_minutes=15,
                    limit=200,
                    page_size=100,
                )
                for tw in batch:
                    tid = ensure_str(tw.get("id") or tw.get("tweet_id") or "")
                    if tid in seen_ids:
                        continue
                    seen_ids.add(tid)
                    tweets.append(tw)
        except Exception as e:
            log_event(f"❌ X API error in feed scanner: {e}")
            return

    new_handles = set()
    new_tokens = set()

    # --- Advanced analytics with dynamic keywords ---
    from special.insight_logger import log_insight
    from utils.meta_keywords import top_keywords
    from inputs.social.x_alpha.alpha_account_tracker import alpha_account_tracker
    # fallback static lists if no dynamic keywords found
    default_scam = ["rug", "scam", "exit", "pull", "hack", "exploit", "stolen", "drain"]
    default_launch = ["launch", "listing", "live", "pump", "moon", "entry", "new", "drop"]
    default_influencer = ["alpha", "whale", "team", "dev", "admin", "lead", "founder"]
    scam_keywords = [k for k, _ in top_keywords("x_scam", 20)] or default_scam
    launch_keywords = [k for k, _ in top_keywords("x_launch", 20)] or default_launch
    influencer_keywords = [k for k, _ in top_keywords("x_influencer", 20)] or default_influencer
    try:
        from strategy.sentiment_analyzer import analyze_sentiment
    except ImportError:
        def analyze_sentiment(texts):
            # fallback: neutral sentiment
            return {"score": 0, "label": "neutral"}

    for tw in tweets:
        text = ensure_str(tw.get("text", ""))
        handle = _extract_handle(tw)
        tokens = _extract_tokens(text)

    sentiment = analyze_sentiment([text]) if text else {"score": 0, "label": "neutral"}
    scam_detected = any(k in text.lower() for k in scam_keywords)
    launch_detected = any(k in text.lower() for k in launch_keywords)
    influencer_detected = any(k in text.lower() for k in influencer_keywords)
    influencer_score = alpha_account_tracker.get_score(handle) if handle else 0

        if handle:
            new_handles.add(handle)
        new_tokens.update(tokens)

        # --- Librarian tagging ---
        tags = []
        if scam_detected:
            tags.append("scam")
        if launch_detected:
            tags.append("launch")
        if influencer_detected or influencer_score > 70:
            tags.append("influencer")
        if sentiment["score"] > 0.5:
            tags.append("positive")
        elif sentiment["score"] < -0.5:
            tags.append("negative")

        try:
            await librarian.record_event("x_tweet_ingest", {
                "timestamp": time.time(),
                "handle": handle,
                "text": text,
                "token_hint": next(iter(tokens), None),
                "sentiment": sentiment,
                "tags": tags,
                "influencer_score": influencer_score
            })
        except Exception:
            pass

        # --- Insight logging ---
        log_insight("x_analytics", {
            "handle": handle,
            "text": text,
            "tokens": list(tokens),
            "sentiment": sentiment,
            "tags": tags,
            "influencer_score": influencer_score
        })

        for term in tokens:
            _trending.register_observation(term, usable=False, ts=time.time())

    if new_handles:
        merged = list(_get_tracked_handles() | new_handles)
        _set_tracked_handles(set(merged))
        log_event(f"➕ Tracked handles updated: {len(merged)}")

    if new_tokens:
        merged_t = list(_get_tracked_tokens() | new_tokens)
        _set_tracked_tokens(set(merged_t))
        log_event(f"➕ Tracked tokens updated: {len(merged_t)}")

    try:
        if hasattr(_trending, "_save"):
            _trending._save()
    except Exception:
        pass

    log_event("✅ X feed scan complete (passive mode)")

        # --- Automated daily/periodic summary and self-DM ---
        from special.insight_logger import generate_daily_summary
        if config.get("enable_telegram_talking", False):
            summary = generate_daily_summary()
            # send_self_dm(summary)  # Pseudo-code: replace with actual DM logic if available
            log_event(f"[SelfDM] X daily summary triggered: {summary}")
