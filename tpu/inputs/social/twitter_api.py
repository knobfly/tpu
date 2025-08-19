import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from core.live_config import config
from utils.logger import log_event
from utils.x_quota import XQuota
from utils.x_since import get_since_id, set_since_id

TWITTER_BEARER_TOKEN = config.get("twitter_bearer_token")
TWITTER_ACCOUNTS = config.get("twitter_accounts", [])
DEFAULT_POSITIVE = "(crypto OR solana OR memecoin OR token OR raydium)"
DEFAULT_LANG = "lang:en"
DEFAULT_NEGATIVES = "-is:retweet -is:reply"
BASE_URL = "https://api.twitter.com/2"
POST_URL = f"{BASE_URL}/tweets"
REPLY_URL = f"{BASE_URL}/tweets"
SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

SEEN_TWEET_IDS = set()

HEADERS = {
    "Authorization": f"Bearer {TWITTER_BEARER_TOKEN}",
    "Content-Type": "application/json"
}

def _bearer() -> str:
    tok = config.get("twitter_bearer_token")
    if not tok:
        raise RuntimeError("twitter_bearer_token missing in config.json")
    return tok

def _headers() -> Dict[str,str]:
    return {"Authorization": f"Bearer {_bearer()}"}

def _mk_query(handles: List[str], keywords: List[str]) -> List[str]:
    parts = []
    if handles:
        parts.append("(" + " OR ".join([f"from:{h.lstrip('@')}" for h in handles]) + ")")
    if keywords:
        parts.append("(" + " OR ".join([f'"{k}"' if " " in k else k for k in keywords]) + ")")
    base = " ".join(parts).strip()
    base = f"({base}) -is:retweet -is:reply" if base else "-is:retweet -is:reply"
    return [base]

def _normalize(resp: Dict[str,Any]) -> List[Dict[str,Any]]:
    users = {u["id"]:u for u in resp.get("includes",{}).get("users",[])}
    out = []
    for t in resp.get("data", []):
        u = users.get(t.get("author_id"), {})
        out.append({
            "id": t.get("id"),
            "text": t.get("text",""),
            "lang": t.get("lang"),
            "user": u.get("username",""),
            "created_at": t.get("created_at"),
            "token": None  # your scanner will extract mints/keywords
        })
    return out

def _build_query(base_query: Optional[str], keywords: Optional[str], negatives: str = "-is:retweet -is:reply") -> str:
    q = " ".join(filter(None, [base_query, keywords, negatives, "lang:en"])).strip()
    # Ensure at least one positive clause (X rejects pure negatives)
    if not base_query and not keywords:
        q = "(crypto OR solana OR token OR raydium) " + q
    return q

async def _fetch_page(session: aiohttp.ClientSession, bearer: str, q: str, *, since_id: Optional[str], max_results: int = 100) -> Dict[str, Any]:
    params = {
        "query": q,
        "max_results": max(10, min(100, int(max_results or 100))),
        "tweet.fields": "created_at,lang,public_metrics,author_id,conversation_id,entities",
    }
    if since_id:
        params["since_id"] = since_id
    headers = {"Authorization": f"Bearer {bearer}"}
    async with session.get(SEARCH_URL, params=params, headers=headers, timeout=15) as r:
        txt = await r.text()
        if r.status != 200:
            raise RuntimeError(f"X API error {r.status}: {txt}")
        return await r.json()

async def fetch_recent_tweets(
    session: aiohttp.ClientSession,
    *,
    bearer_token: str,
    base_query: Optional[str] = None,
    keywords: Optional[str] = None,     # backward-compat
    since_minutes: Optional[int] = None,
    since_id: Optional[str] = None,
    limit: int = 100,
    page_size: int = 100,
) -> List[Dict[str, Any]]:
    q = _build_query(base_query, keywords)
    results: List[Dict[str, Any]] = []

    # Optional: since_minutes -> start_time (you can add if needed)
    fetched = 0
    next_since_id = since_id
    while fetched < max(10, int(limit or 100)):
        page = await _fetch_page(session, bearer_token, q, since_id=next_since_id, max_results=page_size or 100)
        data = page.get("data", [])
        if not data:
            break
        results.extend(data)
        fetched += len(data)
        next_since_id = data[0]["id"]  # newest first
        if fetched >= limit:
            break
        await asyncio.sleep(0)
    return results

# === Quote Post to X ===
async def post_quote(text: str):
    payload = { "text": text }

    async with aiohttp.ClientSession() as session:
        async with session.post(POST_URL, json=payload, headers=HEADERS) as response:
            if response.status == 201:
                log_event("✅ Quote post sent to X.")
                return await response.json()
            else:
                error = await response.text()
                log_event(f"❌ Failed to post quote: {response.status} — {error}")
                raise Exception(f"Quote post failed: {error}")


# === Reply Post to X ===
async def post_reply(handle: str, text: str):
    tweet_id = await get_latest_tweet_id(handle)
    if not tweet_id:
        log_event(f"⚠️ Could not fetch latest tweet ID for @{handle}")
        return

    payload = {
        "text": text,
        "reply": {
            "in_reply_to_tweet_id": tweet_id
        }
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(REPLY_URL, json=payload, headers=HEADERS) as response:
            if response.status == 201:
                log_event(f"✅ Reply sent to @{handle}")
                return await response.json()
            else:
                error = await response.text()
                log_event(f"❌ Failed to send reply: {response.status} — {error}")
                raise Exception(f"Reply post failed: {error}")


# === Utility: Get Latest Tweet ID for a Handle ===
async def get_latest_tweet_id(handle: str):
    async with aiohttp.ClientSession() as session:
        url = f"{BASE_URL}/users/by/username/{handle}"
        async with session.get(url, headers=HEADERS) as res:
            if res.status != 200:
                return None
            data = await res.json()
            user_id = data.get("data", {}).get("id")

        tweets_url = f"{BASE_URL}/users/{user_id}/tweets"
        async with session.get(tweets_url, headers=HEADERS) as res:
            if res.status != 200:
                return None
            data = await res.json()
            tweets = data.get("data", [])
            return tweets[0].get("id") if tweets else None

