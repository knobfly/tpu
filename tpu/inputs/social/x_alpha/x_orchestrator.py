# x_orchestrator.py (CLEANED + CONSOLIDATED)

from __future__ import annotations
import asyncio
import aiohttp
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from core.live_config import config, save_config
from inputs.social.twitter_api import fetch_recent_tweets
from inputs.social.x_alpha.alpha_post_manager import alpha_post_manager
from inputs.social.x_alpha.x_alpha_brain_adapter import analyze_post_compat
from inputs.social.x_alpha.x_autofollow import follow_user
from inputs.social.x_alpha.x_behavior_filter import check_backoff, is_english_text
from inputs.social.x_alpha.x_post_engine import post_quote
from librarian.data_librarian import librarian
from utils.logger import log_event
from utils.universal_input_validator import ensure_str
from inputs.social.x_alpha.x_gate import x_api_guard

# --- Constants & Filters ---
X_MIN_SPACING = float(config.get("x_min_spacing_sec", 5.0))
X_MAX_QUERY_CHARS = 480
BACKOFF_ON_FAILURE_SEC = 1800
MAX_TWEETS_PER_SCAN = 200

SEED_ACCOUNTS = {
    "aeyakovenko", "rajgokal", "solana",
    "solanafoundation", "jupiterexchange",
    "orca_so", "raydiumprotocol", "MagicEden"
}

# --- Memory keys ---
K_STATS = "x_influencer_stats"
K_TRUSTED = "x_trusted_handles"
K_WATCH_TOKS = "x_watch_tokens"
K_FAIL_TS = "x_last_fail_ts"

# --- Helpers ---
def _get_mem(key: str, default):
    try:
        val = librarian.recall(key)
        return default if val is None else val
    except Exception:
        return default

def _set_mem(key: str, value):
    try:
        librarian.remember(key, value)
    except Exception:
        pass

def _ensure_sets():
    stats = _get_mem(K_STATS, {})
    trusted = set(_get_mem(K_TRUSTED, []))
    watch = _get_mem(K_WATCH_TOKS, {"addresses": [], "tickers": []})
    if not isinstance(stats, dict): stats = {}
    if not isinstance(trusted, set): trusted = set(trusted)
    if not isinstance(watch, dict): watch = {"addresses": [], "tickers": []}
    watch.setdefault("addresses", []); watch.setdefault("tickers", [])
    return stats, trusted, watch

def _persist_trusted(trusted: set):
    _set_mem(K_TRUSTED, sorted(trusted))
    try:
        cfg = dict(config or {})
        cfg["x_trusted_handles"] = sorted(trusted)
        save_config(cfg)
    except Exception:
        pass

def extract_mentions(text: str) -> Dict[str, List[str]]:
    mint_re = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
    ticker_re = re.compile(r"(?<![A-Za-z0-9_])\$[A-Za-z0-9]{2,15}\b")
    mints = mint_re.findall(text or "")
    tickers = [t[1:] for t in ticker_re.findall(text or "")]
    return {"addresses": list(set(mints)), "tickers": list(set(tickers))}

def _build_query() -> str:
    _, trusted, watch = _ensure_sets()
    seed_terms = ["Solana", "Raydium", "Orca", "Jupiter", "token", "mint", "DEX", "AMM"]
    watch_tickers = [ensure_str(t).lstrip("$") for t in watch.get("tickers") or []]
    pool = seed_terms + sorted(list(trusted)) + watch_tickers
    deduped = list(dict.fromkeys(pool))
    query = " OR ".join(deduped)
    return f"({query}) -is:retweet -is:reply lang:en"[:X_MAX_QUERY_CHARS]

async def _record_signal(tweet: dict, parsed: dict, action: str):
    payload = {
        "id": ensure_str(tweet.get("id")),
        "ts": tweet.get("ts") or time.time(),
        "handle": ensure_str(tweet.get("user")),
        "text": ensure_str(tweet.get("text", "")),
        "addresses": parsed["addresses"],
        "tickers": parsed["tickers"],
        "followers": tweet.get("followers"),
        "retweets": tweet.get("retweets"),
        "likes": tweet.get("likes"),
        "source": "x_orchestrator",
        "action": action,
    }
    try:
        await librarian.record_signal({
            "type": "x_post",
            "token": parsed["addresses"][0] if parsed["addresses"] else None,
            "payload": payload,
            "timestamp": payload["ts"],
            "source": "x_orchestrator",
            "confidence": 0.7 if parsed["addresses"] or parsed["tickers"] else 0.3,
        })
    except Exception as e:
        logging.debug(f"[XOrchestrator] record_signal failed: {e}")

def _update_stats(handle: str, delta: int):
    stats, trusted, _ = _ensure_sets()
    rec = stats.get(handle) or {"score": 0, "posts": 0, "last_seen": 0}
    rec["score"] += int(delta)
    rec["posts"] += 1
    rec["last_seen"] = int(time.time())
    stats[handle] = rec
    _set_mem(K_STATS, stats)
    if rec["score"] >= 3 and handle not in trusted:
        trusted.add(handle); _persist_trusted(trusted)
        log_event(f"⭐ Promoted: @{handle} (score={rec['score']})")
    if rec["score"] <= -3 and handle in trusted:
        trusted.remove(handle); _persist_trusted(trusted)
        log_event(f"⚠️ Demoted: @{handle} (score={rec['score']})")

async def _maybe_autofollow(handle: str):
    if not config.get("x_autofollow_enabled", True): return
    try:
        await follow_user(handle)
        log_event(f"👤 Auto-followed @{handle}")
    except Exception as e:
        logging.warning(f"[XOrchestrator] Auto-follow failed: {e}")

# --- Core loop ---
async def run_x_orchestrator(poll_sec: int = 20):
    stats, trusted, _ = _ensure_sets()
    if not trusted:
        trusted.update(SEED_ACCOUNTS)
        _persist_trusted(trusted)
        log_event(f"🌱 Seeded trusted accounts: {', '.join(sorted(trusted))}")

    while True:
        try:
            if config.get("x_backoff_enabled", True) and check_backoff():
                log_event("⏳ Backoff active — skipping this cycle.")
                await asyncio.sleep(poll_sec)
                continue

            query = _build_query()
            async with x_api_guard(min_spacing=X_MIN_SPACING, cross_process=True, who="x_orchestrator"):
                async with aiohttp.ClientSession() as session:
                    tweets = await fetch_recent_tweets(
                        session=session,
                        bearer_token=config.get("twitter_bearer_token"),
                        keywords=query,
                        since_minutes=15,
                        limit=MAX_TWEETS_PER_SCAN,
                        page_size=100,
                    )

            for tw in (tweets or []):
                handle = ensure_str(tw.get("user") or "")
                text = ensure_str(tw.get("text", ""))
                if not handle or not text:
                    continue

                parsed = extract_mentions(text)
                token_key = (parsed["addresses"][0] if parsed["addresses"] else None) or (parsed["tickers"][0] if parsed["tickers"] else None)
                if token_key and alpha_post_manager.already_posted(token_key):
                    continue

                try:
                    action = analyze_post_compat(handle=handle, token=token_key, content=text)
                except Exception as e:
                    log_event(f"[XOrchestrator] analyze_post failed: {e}")
                    action = "ignore"

                if action == "quote" and config.get("x_quote_mode", True):
                    if config.get("x_english_only", False) and not is_english_text(text):
                        continue
                    try:
                        await post_quote(token_key or "", text)
                    except Exception as e:
                        _set_mem(K_FAIL_TS, int(time.time()))
                        log_event(f"❌ Quote failed: {e}")

                alpha_post_manager.register_post(token_key or handle, action, "auto")
                await _record_signal(tw, parsed, action)
                _update_stats(handle, 1 if action in ("quote", "watch") else -1)
                if action == "quote" and config.get("x_autofollow_enabled", True):
                    await _maybe_autofollow(handle)

        except Exception as e:
            log_event(f"[XOrchestrator] loop error: {e}")
            await asyncio.sleep(BACKOFF_ON_FAILURE_SEC)

        await asyncio.sleep(poll_sec)
