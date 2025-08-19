# modules/token_reentry_monitor.py

import asyncio
import logging
from datetime import datetime

from core.live_config import config
from memory.trade_history import post_trade_strategy_feedback
from scoring.scoring_engine import score_token
from utils.dexscreener_adapter import get_dexscreener_summary
from utils.logger import log_event
from utils.token_utils import get_token_metadata


async def monitor_token_reentry_loop(token_data: dict, executor):
    """
    Background loop after initial snipe.
    Re-checks trade potential every cooldown interval.
    """

    address = token_data.get("address")
    if not address:
        return

    log_event(f"🔄 Starting re-entry monitor for {address}")
    previous_score = 0
    cooldown = config.get("recheck_delay", 3600)  # Default 1 hour
    threshold = config.get("min_trade_reentry_score", 60)

    try:
        while True:
            await asyncio.sleep(cooldown)

            metadata = get_token_metadata(address) or {}
            trade = score_token(address, metadata, mode="trade")
            trade_score = trade.get("score", 0)
            token_data["trade_score"] = trade_score

            # === Dead Token Filter ===
            dex = get_dexscreener_summary(address) or {}
            volume = dex.get("volume_5m", 0)
            liquidity = dex.get("liquidity", 0)
            token_data["dex_volume"] = volume
            token_data["dex_liquidity"] = liquidity

            if volume < 500 or liquidity < 300:
                log_event(f"💀 Re-entry aborted: {address} is dead. Volume={volume}, LP={liquidity}")
                token_data["result_reason"] = "dead_token"
                post_trade_strategy_feedback(token_data)
                break

            # === Drop-Off Detection ===
            if trade_score < previous_score:
                log_event(f"📉 Score falling: {address} dropped from {previous_score} → {trade_score}")
                token_data["result_reason"] = "lost_momentum"
                post_trade_strategy_feedback(token_data)
                break

            # === Re-entry Trigger ===
            if trade_score >= threshold:
                log_event(f"📈 Re-entry triggered for {address} — Score={trade_score:.2f}")
                await executor.buy_token(token_data)
                token_data["result_reason"] = "reentry_success"
                post_trade_strategy_feedback(token_data)
                break

            previous_score = trade_score
            log_event(f"⏳ Recheck: {address} holding at {trade_score:.2f}")

    except Exception as e:
        log_event(f"⚠️ Re-entry monitor error for {address}: {e}")
