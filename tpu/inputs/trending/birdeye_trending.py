import logging
from datetime import datetime

import aiohttp
from core.live_config import config
from cortex.chart_cortex import chart_cortex
from cortex.meta_cortex import meta_cortex
from cortex.txn_cortex import txn_cortex
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status

BIRDEYE_API_KEY = config.get("birdeye_api_key")
BASE_URL = (
    "https://public-api.birdeye.so/public"
    if not BIRDEYE_API_KEY
    else "https://public-api.birdeye.so/api/v1"
)
HEADERS = {"X-API-KEY": BIRDEYE_API_KEY} if BIRDEYE_API_KEY else {}
_seen_tokens = set()

# === Fetch with shared session ===
async def fetch_birdeye_trending(session):
    try:
        url = f"{BASE_URL}/defi/trending"
        async with session.get(url, headers=HEADERS, timeout=10) as resp:
            if resp.status != 200:
                raise Exception(f"Birdeye returned status {resp.status}")
            return await resp.json()
    except Exception as e:
        logging.warning(f"[Birdeye] Failed to fetch trending tokens: {e}")
        return {"error": str(e)}

# === Main scan logic ===
async def scan_birdeye_trending():
    update_status("birdeye_trending")
    log_event("🪶 Birdeye trending scanner started")

    try:
        async with aiohttp.ClientSession() as session:
            response = await fetch_birdeye_trending(session)
            results = response.get("data", [])

            for entry in results:
                address = entry.get("address")
                name = entry.get("name")
                symbol = entry.get("symbol")
                volume = entry.get("volume24hQuote", 0)
                change = entry.get("priceChange24hPct", 0)
                price = entry.get("price", 0)
                liq = entry.get("liquidityQuote", 0)

                if not address or address in _seen_tokens:
                    continue

                _seen_tokens.add(address)

                metadata = {
                    "price": price,
                    "liq": liq,
                    "volume": volume,
                    "change_pct": change,
                    "origin": "birdeye_trending",
                    "symbol": symbol,
                }

                score = chart_cortex.score(address, metadata=metadata)
                confidence = meta_cortex.assess_confidence(address, metadata, base_score=score)

                log_scanner_insight(address, "birdeye_trending", {
                    "symbol": symbol,
                    "score": score,
                    "confidence": confidence,
                    "price": price,
                    "volume": volume,
                    "change_24h_pct": change,
                    "liquidity": liq,
                })

                librarian.record_signal({
                    "token": address,
                    "source": "birdeye_trending",
                    "score": score,
                    "confidence": confidence,
                    "symbol": symbol,
                    "time": datetime.utcnow().isoformat()
                })

                log_event(f"📈 Birdeye trending: {symbol} | Score: {score:.2f} | Conf: {confidence:.2f} | Vol: {volume:.0f} | Chg: {change:.2f}%")

                if confidence >= config.get("birdeye_trending_threshold", 6.5):
                    txn_cortex.register_buy(address, metadata=metadata, score=score, origin="birdeye_trending")

    except Exception as e:
        log_event(f"[BirdeyeScanner] ❌ Error: {e}")
