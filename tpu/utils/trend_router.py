import asyncio
import logging
import time

from inputs.trending.alt_trending_sources import fetch_alt_trending
from inputs.trending.birdeye_trending import fetch_birdeye_trending
from inputs.trending.external_trending_sources import (
    fetch_dexscreener_trending,
    fetch_gecko_terminal_trending,
)
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status

_seen = set()
_trending_results = []


async def score_and_log_token(entry: dict, source: str):
    address = entry.get("address")
    if not address or address in _seen:
        return

    _seen.add(address)
    symbol = entry.get("symbol", "?")

    meta = {
        "price": entry.get("price"),
        "liq": entry.get("liquidity") or entry.get("liq"),
        "volume": entry.get("volume"),
        "change_pct": entry.get("change_24h_pct") or entry.get("change"),
    }

    score, reason = librarian.score_token_from_signal(
        token_address=address,
        source=source,
        symbol=symbol,
        metadata=meta,
    )

    _trending_results.append({
        "address": address,
        "symbol": symbol,
        "source": source,
        "score": score,
        "meta": meta,
        "reason": reason
    })

    log_event(f"🔥 Trending: {symbol} [{source}] | Score: {score}")
    log_scanner_insight(address, source, {
        "symbol": symbol,
        "score": score,
        "reason": reason,
        **meta
    })


async def scan_all_trending():
    update_status("trend_router")
    log_event("🌍 Trend Router: starting multi-source trending scan")

    global _seen, _trending_results
    _seen = set()
    _trending_results = []

    try:
        sources = []

        # ✅ Birdeye preferred
        birdeye = await fetch_birdeye_trending()
        data = birdeye.get("data", [])
        if data:
            sources.append(("birdeye_trending", data))
        else:
            log_event("⚠️ Birdeye unavailable, falling back to others")

        # ✅ Others as backup
        tasks = [
            fetch_dexscreener_trending(),
            fetch_gecko_terminal_trending(),
            fetch_alt_trending()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (name, result) in zip(["dexscreener", "geckoterminal", "alt_trending"], results):
            if isinstance(result, Exception):
                logging.warning(f"[TrendRouter] Error in {name}: {result}")
                continue
            sources.append((name, result))

        # 🚀 Score everything
        for source_name, token_list in sources:
            for token in token_list:
                await score_and_log_token(token, source_name)

        librarian.save_trending_results(_trending_results)
        librarian.set_timestamp("last_trending_check", time.time())

    except Exception as e:
        logging.warning(f"[TrendRouter] Trending scan error: {e}")
