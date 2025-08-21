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
    log_event("🌍 Trend Router: starting autonomous trending scan")

    global _seen, _trending_results
    _seen = set()
    _trending_results = []

    try:
        sources = []

        # === Internal autonomous trending ===
        # 1. Get top tokens by activity, volume, and mentions from librarian memory
        top_activity = librarian.query_by_genre("activity", limit=30)
        top_volume = librarian.query_by_genre("volume", limit=30)
        top_mentions = librarian.query_by_topic("mention", limit=30)

        # 2. Aggregate unique tokens
        token_set = set()
        for entry in top_activity + top_volume + top_mentions:
            token = entry.get("token")
            if token:
                token_set.add(token)

        # 3. Score each token using cortexes (via CoreSupervisor)
        from cortex.core_supervisor import CoreSupervisor
        from cortex.chart_cortex import ChartCortex
        from cortex.wallet_cortex import WalletCortex
        from cortex.social_cortex import SocialCortex
        from cortex.meta_cortex import MetaCortex
        from cortex.risk_cortex import RiskCortex
        from cortex.txn_cortex import TxnCortex

        # Setup cortexes with librarian memory
        cortices = {
            "chart": ChartCortex(librarian),
            "wallet": WalletCortex(librarian),
            "social": SocialCortex(librarian),
            "meta": MetaCortex(librarian),
            "risk": RiskCortex(librarian),
            "txn": TxnCortex(librarian),
        }
        supervisor = CoreSupervisor(cortices)

        # 4. Build token context and score
        for token in token_set:
            # Build context from librarian
            context = await librarian.build_context(token)
            result = supervisor.evaluate(context)
            score = result.get("final_score", 0)
            action = result.get("action", "unknown")
            reasoning = result.get("reasoning", [])
            meta = context
            _trending_results.append({
                "address": token,
                "symbol": meta.get("symbol", "???"),
                "source": "internal_trending",
                "score": score,
                "action": action,
                "reasoning": reasoning,
                "meta": meta
            })
            log_event(f"🔥 Trending: {meta.get('symbol', '???')} [internal] | Score: {score} | Action: {action}")

        # === External sources as backup ===
        try:
            birdeye = await fetch_birdeye_trending()
            data = birdeye.get("data", [])
            if data:
                sources.append(("birdeye_trending", data))
            else:
                log_event("⚠️ Birdeye unavailable, falling back to others")

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
            for source_name, token_list in sources:
                for token in token_list:
                    await score_and_log_token(token, source_name)
        except Exception as e:
            logging.warning(f"[TrendRouter] External trending error: {e}")

        # Save results
        librarian.save_trending_results(_trending_results)
        librarian.set_timestamp("last_trending_check", time.time())

    except Exception as e:
        logging.warning(f"[TrendRouter] Trending scan error: {e}")
