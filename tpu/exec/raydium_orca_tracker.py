import asyncio
import logging
from typing import List

import aiohttp
from defense.race_protection import check_sandwich_risk
from scoring.snipe_score_engine import evaluate_snipe
from special.insight_logger import log_scanner_insight
from strategy.stop_snipe_defender import activate_stop_snipes
from exec.trade_executor import TradeExecutor
from utils.logger import log_event
from inputs.meta_data.token_metadata import parse_token_metadata

CHECK_INTERVAL = 45
CONFIDENCE_THRESHOLD = 7.0

RAYDIUM_URL = "https://api.raydium.io/pairs"
ORCA_URL = "https://api.orca.so/pools"

class RaydiumOrcaTracker:
    def __init__(self):
        self.seen = set()

    async def run(self):
        log_event("🌊 Raydium/Orca Tracker running...")
        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    try:
                        await self.scan_dex_pools(session)
                    except Exception as e:
                        logging.error(f"[RaydiumOrca] Error: {e}")
                    await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            logging.error(f"[RaydiumOrca] Session error: {e}")

    async def scan_dex_pools(self, session):
        try:
            raydium_tokens = await fetch_raydium_tokens(session)
            orca_tokens = await fetch_orca_tokens(session)
            tokens = list(set(raydium_tokens + orca_tokens))

            for token_address in tokens:
                if token_address in self.seen:
                    continue
                self.seen.add(token_address)

                metadata = await parse_token_metadata(token_address)
                if not metadata:
                    continue

                snipe_result = await evaluate_snipe(token_address)
                score = snipe_result.get("score", 0)
                confidence = snipe_result.get("sentiment", 0)

                log_event(f"🌐 DEX Tracker: {token_address} | Score: {score:.2f} | AI: {confidence:.2f}")
                log_scanner_insight(
                    token=token_address,
                    source="dex_tracker",
                    sentiment=confidence,
                    volume=0,
                    result="dex_tracker_match",
                    tags=[f"score:{score:.2f}"]
                )

                if confidence >= CONFIDENCE_THRESHOLD:
                    if check_sandwich_risk(token_address):
                        log_event(f"🚫 Skipped {token_address} (sandwich risk)")
                        continue

                    await TradeExecutor.buy_token(
                        token=token_address,
                        score=score,
                        source="dex_tracker",
                        metadata=metadata
                    )

        except Exception as e:
            logging.warning(f"[RaydiumOrca] Scan failed: {e}")

# === Shared session for both DEX fetchers ===
async def fetch_raydium_tokens(session) -> List[str]:
    try:
        async with session.get(RAYDIUM_URL, timeout=10) as resp:
            data = await resp.json()
            tokens = [pair["baseMint"] for pair in data if "baseMint" in pair]
            return list(set(tokens))
    except Exception as e:
        logging.error(f"[Raydium] Fetch error: {e}")
        return []

async def fetch_orca_tokens(session) -> List[str]:
    try:
        async with session.get(ORCA_URL, timeout=10) as resp:
            data = await resp.json()
            tokens = [pool["mint"] for pool in data.get("pools", {}).values() if "mint" in pool]
            return list(set(tokens))
    except Exception as e:
        logging.error(f"[Orca] Fetch error: {e}")
        return []
