# /wallet_alpha_sniper_overlap.py

import asyncio
import logging

from core.live_config import config
from exec.trade_executor import TradeExecutor
from special.insight_logger import log_scanner_insight

# === Settings ===
SCORE_THRESHOLD = 7.0
TRUSTED_WALLETS = set(config.get("trusted_wallets", []))

# token -> {"ai_score": float, "wallets": set(wallets)}
overlap_cache = {}


def record_ai_score(token: str, score: float):
    if token not in overlap_cache:
        overlap_cache[token] = {"ai_score": 0, "wallets": set()}
    overlap_cache[token]["ai_score"] = score


def record_wallet_signal(token: str, wallet: str):
    if wallet not in TRUSTED_WALLETS:
        return
    if token not in overlap_cache:
        overlap_cache[token] = {"ai_score": 0, "wallets": set()}
    overlap_cache[token]["wallets"].add(wallet)


async def check_for_overlap_trigger(token: str):
    data = overlap_cache.get(token, {})
    score = data.get("ai_score", 0)
    wallets = data.get("wallets", set())

    if score >= SCORE_THRESHOLD and len(wallets) >= 2:
        logging.info(f"[AlphaOverlap] 🎯 Triggering overlap buy for {token}")

        log_scanner_insight(
            token=token,
            source="wallet_alpha_overlap",
            sentiment=0.95,
            volume=len(wallets),
            result="ultra_confidence"
        )

        try:
            executor = TradeExecutor()
            await executor.buy_token(
                token,
                base_amount=0.2,
                override_filters=True,
                scanner_source="wallet_alpha_overlap"
            )
        except Exception as e:
            logging.warning(f"[AlphaOverlap] Buy error: {e}")


# === Background Loop ===
async def run_alpha_sniper_overlap():
    logging.info("[AlphaOverlap] 🔁 Loop started.")
    while True:
        for token in list(overlap_cache.keys()):
            await check_for_overlap_trigger(token)
        await asyncio.sleep(30)
