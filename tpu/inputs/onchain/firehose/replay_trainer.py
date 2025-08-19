# modules/firehose/replay_trainer.py

import logging
import time

from inputs.meta_data.token_metadata import get_price_change_since
from inputs.onchain.firehose.firehose_replay_buffer import get_recent_events
from special.insight_logger import log_ai_insight
from strategy.strategy_memory import tag_token_result

MOON_THRESHOLD_X = 2.0  # 2x gain
MAX_LOOKBACK_SECONDS = 600  # 10 min

async def run_replay_trainer():
    try:
        missed = get_recent_events(lambda e: not e.get("sniped", False))

        for event in missed:
            token = event.get("token")
            if not token:
                continue

            # Skip known blacklisted or dust tokens
            if event.get("memory_blacklisted"):
                continue

            try:
                x_gain = await get_price_change_since(token, event["timestamp"])
                if x_gain >= MOON_THRESHOLD_X:
                    msg = f"😓 Missed moon: {token}  |  Gain: {x_gain:.2f}x"
                    log_ai_insight("Replay Trainer", {"token": token, "gain": x_gain})
                    tag_token_result(token, "missed_moon", x_gain)
            except Exception as err:
                logging.warning(f"[ReplayTrainer] Error analyzing {token}: {err}")

    except Exception as e:
        logging.error(f"[ReplayTrainer] Failed to run: {e}")
