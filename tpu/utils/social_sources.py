import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiohttp
from aiogram import Bot
from core.live_config import config
from utils.logger import log_event

# === Config & Globals ===
TELEGRAM_GROUP_IDS = [-1002139845283, -1002142398499]
TELEGRAM_BOT_TOKEN = config.get("telegram_token", "")
telegram_bot = None
if TELEGRAM_BOT_TOKEN and ":" in TELEGRAM_BOT_TOKEN:
    telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
else:
    logging.warning("⚠️ Telegram bot token missing or invalid.")

# Keep your terminology (constants) the same:
GECKOTERMINAL_RECENT = "https://api.geckoterminal.com/api/v2/search/trending"
DEXSCREENER_TRENDING = "https://api.dexscreener.com/latest/dex/pairs/solana"
REDDIT_URLS = [
    "https://www.reddit.com/r/solana/new.json?limit=25",
    "https://www.reddit.com/r/cryptomoonshots/new.json?limit=25",
]
YOUTUBE_API_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id=UCnxrdFPXJMeHru_b4Q_vTPQ"
WEIBO_RSS = "https://rsshub.app/weibo/user/1195242865"
SNIPERWATCH_BACKUP_POSTS = "https://api.sniperwatch.io/social/recent_posts"

_telegram_seen_ids = set()

# === Helpers ===
MENTION_RE = re.compile(r"\$[A-Z]{2,8}|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b|#[a-zA-Z0-9_]+")
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "nyx-bot/1.0 (+telemetry only)",
}

async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout_s: float = 12.0,
    retries: int = 2,
    log_name: str = "",
) -> Tuple[Optional[dict], Optional[int]]:
    """GET json with small retry/backoff. Returns (json_or_none, status_or_none)."""
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)

    for attempt in range(retries + 1):
        try:
            async with session.get(url, params=params, headers=h, timeout=timeout_s) as resp:
                status = resp.status
                if status == 200:
                    ctype = resp.headers.get("Content-Type", "")
                    if "json" in ctype:
                        return await resp.json(), status
                    # try text then json-load
                    txt = await resp.text()
                    try:
                        import json as _json
                        return _json.loads(txt), status
                    except Exception:
                        logging.warning(f"⚠️ {log_name or url} unexpected content-type: {ctype}")
                        return None, status
                else:
                    logging.warning(f"⚠️ {log_name or url} status {status}")
        except Exception as e:
            logging.warning(f"⚠️ {log_name or url} fetch error: {e}")

        if attempt < retries:
            await asyncio.sleep(0.6 * (attempt + 1))

    return None, None

def extract_token_mentions(text: str) -> list:
    try:
        return MENTION_RE.findall(text or "")
    except Exception as e:
        logging.warning(f"⚠️ Mention extraction error: {e}")
        return []

def _now_iso() -> str:
    return datetime.utcnow().isoformat()

def _post(platform: str, id_: str, text: str, author: str = "system") -> Dict:
    return {
        "id": id_ or "",
        "timestamp": _now_iso(),
        "text": text or "",
        "author": author or "system",
        "platform": platform,
        "mentions": extract_token_mentions(text or ""),
    }

# === Telegram (simple polling) ===
async def fetch_telegram_group_posts(limit=20) -> List[Dict]:
    if not telegram_bot:
        return []
    try:
        # NOTE: get_updates polling only works if bot is added to groups *and* privacy is set accordingly.
        updates = await telegram_bot.get_updates(offset=-1)
        posts: List[Dict] = []
        for u in updates:
            msg = getattr(u, "message", None)
            if not msg or not getattr(msg, "chat", None):
                continue
            if msg.chat.id not in TELEGRAM_GROUP_IDS:
                continue
            if msg.message_id in _telegram_seen_ids:
                continue
            _telegram_seen_ids.add(msg.message_id)
            txt = (msg.text or "").strip()
            author = (msg.from_user.username if msg.from_user else None) or "unknown"
            posts.append(_post("telegram", str(msg.message_id), txt, author=author))
        return posts[-limit:]
    except Exception as e:
        logging.warning(f"⚠️ Telegram fetch error: {e}")
        return []

# === Reddit (best-effort without OAuth; may 403) ===
async def fetch_reddit_posts(session: aiohttp.ClientSession) -> List[Dict]:
    out: List[Dict] = []
    headers = {
        "User-Agent": "nyx-bot/1.0 (script; best-effort)",
        "Accept": "application/json",
    }
    for url in REDDIT_URLS:
        data, status = await _fetch_json(session, url, headers=headers, log_name="reddit")
        if not data or status != 200:
            continue
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            text = f"{p.get('title', '')} {p.get('selftext', '')}".strip()
            out.append(
                _post(
                    "reddit",
                    p.get("id", ""),
                    text,
                    author=p.get("author", "unknown"),
                )
            )
    return out

# === GeckoTerminal (primary -> fallback pattern) ===
async def fetch_gecko_trending(session: aiohttp.ClientSession) -> List[Dict]:
    """
    Strategy:
      1) Try your GECKOTERMINAL_RECENT first.
      2) If not 200, try a Solana-focused fallback path that commonly exists in GT APIs.
    Parses names/symbols when available; stores basic info consistently.
    """
    # primary
    data, status = await _fetch_json(session, GECKOTERMINAL_RECENT, log_name="geckoterminal/trending")
    items: List[Dict] = []
    try:
        if data and status == 200:
            for t in data.get("data", []):
                attrs = t.get("attributes", {})
                name = attrs.get("name") or attrs.get("symbol") or t.get("id", "")
                items.append(_post("geckoterminal", t.get("id", ""), name, author="system"))
            return items
    except Exception as e:
        logging.warning(f"⚠️ GeckoTerminal parse error (primary): {e}")

    # fallback(s) — harmless if 404
    fallbacks = [
        # popular alternative for “trending pools” style feeds (path may vary by API revision)
        ("https://api.geckoterminal.com/api/v2/networks/solana/trending_pools", "geckoterminal/trending_pools"),
    ]
    for url, lname in fallbacks:
        data2, status2 = await _fetch_json(session, url, log_name=lname)
        if not data2 or status2 != 200:
            continue
        try:
            for node in data2.get("data", []):
                attrs = node.get("attributes", {})
                nm = attrs.get("name") or attrs.get("pool_name") or node.get("id", "")
                items.append(_post("geckoterminal", node.get("id", ""), nm, author="system"))
            if items:
                return items
        except Exception as e:
            logging.warning(f"⚠️ GeckoTerminal parse error (fallback): {e}")

    return []

# === Dexscreener (primary -> fallback) ===
async def fetch_dexscreener_trending(session: aiohttp.ClientSession) -> List[Dict]:
    """
    Strategy:
      1) Try your DEXSCREENER_TRENDING first (may 404).
      2) Fallback to the documented /latest/dex/search?q=... (broad query to surface Solana pairs).
    """
    out: List[Dict] = []

    # primary
    data, status = await _fetch_json(session, DEXSCREENER_TRENDING, log_name="dexscreener/pairs")
    try:
        if data and status == 200:
            for p in (data.get("pairs", []) or [])[:30]:
                name = (p.get("baseToken", {}) or {}).get("name") or (p.get("baseToken", {}) or {}).get("symbol") or ""
                out.append(_post("dexscreener", p.get("pairAddress", "unknown"), name, author="dex"))
            if out:
                return out
    except Exception as e:
        logging.warning(f"⚠️ Dexscreener parse error (primary): {e}")

    # fallback: search endpoint (broad query to surface SOL pairs)
    try:
        search_url = "https://api.dexscreener.com/latest/dex/search"
        data2, status2 = await _fetch_json(session, search_url, params={"q": "solana"}, log_name="dexscreener/search")
        if data2 and status2 == 200:
            for p in (data2.get("pairs", []) or [])[:30]:
                name = (p.get("baseToken", {}) or {}).get("name") or (p.get("baseToken", {}) or {}).get("symbol") or ""
                out.append(_post("dexscreener", p.get("pairAddress", "unknown"), name, author="dex"))
    except Exception as e:
        logging.warning(f"⚠️ Dexscreener parse error (search): {e}")

    return out

# === Weibo (RSS via RSSHub; best-effort text scrape) ===
async def fetch_weibo_rss(session: aiohttp.ClientSession) -> List[Dict]:
    data_text: Optional[str] = None
    try:
        async with session.get(WEIBO_RSS, headers={"Accept": "text/xml,*/*"}, timeout=12) as resp:
            if resp.status != 200:
                logging.warning(f"⚠️ Weibo RSS fetch status {resp.status}")
                return []
            data_text = await resp.text()
    except Exception as e:
        logging.warning(f"⚠️ Weibo fetch error: {e}")
        return []

    try:
        titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", data_text or "")
        items: List[Dict] = []
        # skip the first <title> which is usually the feed title
        for i, t in enumerate(titles[1:], start=1):
            items.append(_post("weibo", f"weibo_{i}", t, author="weibo"))
        return items
    except Exception as e:
        logging.warning(f"⚠️ Weibo parse error: {e}")
        return []

# === Sniperwatch.io (if up) ===
async def fetch_sniperwatch_backup(session: aiohttp.ClientSession) -> List[Dict]:
    data, status = await _fetch_json(session, SNIPERWATCH_BACKUP_POSTS, log_name="sniperwatch")
    if not data or status != 200:
        return []
    out: List[Dict] = []
    try:
        for post in data:
            out.append(
                _post(
                    post.get("platform", "sniperwatch"),
                    str(post.get("id", "")),
                    post.get("text", ""),
                    author=post.get("author", "unknown"),
                )
            )
    except Exception as e:
        logging.warning(f"⚠️ Sniperwatch parse error: {e}")
    return out

# === Unified Aggregator ===
async def fetch_recent_posts() -> List[Dict]:
    """
    Runs all sources concurrently; never throws.
    Each item includes .mentions extracted from text.
    """
    results: List[Dict] = []
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        connector = aiohttp.TCPConnector(limit=32, ssl=False)  # ssl=False helps on some hosts that flake
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            tasks = [
                fetch_telegram_group_posts(),                # aiogram Bot polling
                fetch_reddit_posts(session),                 # best-effort without OAuth
                fetch_gecko_trending(session),               # GT primary -> fallback
                fetch_dexscreener_trending(session),         # DS primary -> fallback
                fetch_weibo_rss(session),                    # RSSHub best-effort
                fetch_sniperwatch_backup(session),           # backup feed
            ]
            all_results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in all_results:
                if isinstance(res, Exception):
                    logging.warning(f"⚠️ Aggregator subtask error: {res}")
                    continue
                results.extend(res or [])
    except Exception as e:
        logging.warning(f"❌ Social sources fetch error: {e}")

    # optional: light de-dupe by (platform,id)
    seen = set()
    deduped: List[Dict] = []
    for r in results:
        key = (r.get("platform"), r.get("id"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped
