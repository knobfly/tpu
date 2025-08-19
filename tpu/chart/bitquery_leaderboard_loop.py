import asyncio
import logging

from chart.bitquery_analytics import detect_volume_spike, get_top_gainers_losers
from exec.trade_executor import TradeExecutor
from inputs.meta_data.token_metadata import parse_token_metadata
from scoring.snipe_score_engine import evaluate_snipe
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status
from utils.universal_input_validator import is_valid_token_address

CHECK_INTERVAL = 180  # seconds
CONFIDENCE_THRESHOLD = 7.0

async def run_bitquery_leaderboard_loop():
    update_status("bitquery_leaderboard")
    log_event("📊 Bitquery Gainers/Spike Monitor started.")

    while True:
        try:
            gainers = await get_top_gainers_losers(chain="solana", metric="volume", limit=12)

            for sym, data in gainers:
                token = data.get("address")
                volume = data.get("volume")

                if not token or not is_valid_token_address(token):
                    continue

                spike = await detect_volume_spike(token)
                if not spike:
                    continue

                try:
                    metadata = await parse_token_metadata(token)
                except Exception as e:
                    logging.warning(f"[Bitquery] ⚠️ Failed to fetch metadata for {token}: {e}")
                    metadata = {}

                try:
                    snipe_result = await evaluate_snipe(token)
                    score = snipe_result.get("score", 0)
                    confidence = snipe_result.get("sentiment", 0)
                except Exception as e:
                    logging.warning(f"[Bitquery] ⚠️ AI scoring error for {token}: {e}")
                    continue

                log_event(f"🚀 Spike: {sym} | {token} | {spike['multiplier']:.2f}x | Vol: {spike['spike_volume']:.2f}")
                log_scanner_insight(
                    token=token,
                    source="bitquery_leaderboard",
                    sentiment=confidence,
                    volume=spike["spike_volume"],
                    result="leaderboard_spike",
                    tags=[
                        f"symbol:{sym}",
                        f"spike:{spike['multiplier']:.2f}x",
                        f"volume:{spike['spike_volume']:.2f}"
                    ]
                )

                if confidence >= CONFIDENCE_THRESHOLD:
                    await TradeExecutor.buy_token(
                        token=token,
                        score=score,
                        source="bitquery",
                        metadata=metadata
                    )

        except Exception as e:
            logging.error(f"[Bitquery Leaderboard Loop] ❌ Loop error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)

