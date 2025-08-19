# modules/firehose/sniper_trigger.py

import logging

from exec.trade_executor import TradeExecutor
from inputs.onchain.firehose.alpha_poster import post_firehose_alpha
from inputs.onchain.firehose.firehose_trace_logger import write_trace
from scoring.snipe_score_engine import evaluate_snipe
from special.insight_logger import log_ai_insight
from strategy.strategy_memory import tag_token_result


async def try_sniper_trigger(event: dict) -> float | None:
    try:
        if event.get("event_type") not in ["lp_create", "swap"]:
            return None  # Ignore irrelevant types

        token = event.get("token") or "unknown"
        score = await evaluate_snipe(event)

        if score >= 75:
            log_ai_insight("🎯 Stream Snipe", {"token": token, "score": score})
            await TradeExecutor.execute_buy(token, reason="firehose_snipe", context=event)
            tag_token_result(token, "sniped_stream", score)
            await post_firehose_alpha(event, score)
        else:
            logging.debug(f"[SniperTrigger] Score {score} too low for {token}")

        write_trace(event, score=score, action="buy" if score >= 75 else "skipped")
        return score

    except Exception as e:
        logging.warning(f"[SniperTrigger] Failed to snipe: {e}")
        return None
