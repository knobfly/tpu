# /reverse_learning.py

import logging
from datetime import datetime, timedelta

from strategy.strategy_memory import tag_token_result
from utils.logger import log_event
from utils.service_status import update_status
from utils.universal_input_validator import is_valid_token_address

update_status("reverse_learning")

# === Runtime state ===
sold_tokens = {}  # token: {"time": datetime, "price": float}
reverse_learning_log = {}
wallet_learning = {}

def record_token_sell(token: str, price: float):
    if not is_valid_token_address(token):
        return
    sold_tokens[token] = {"time": datetime.utcnow(), "price": price}

async def evaluate_rebound(token: str, current_price: float):
    from core.ai_brain import ai_brain  # Safe import

    if not is_valid_token_address(token):
        return

    data = sold_tokens.get(token)
    if not data or current_price <= 0:
        return

    time_diff = (datetime.utcnow() - data["time"]).total_seconds()
    if time_diff > 600:
        return  # Only track rebounds within 10 minutes

    price_diff = current_price - data["price"]
    rebound_pct = (price_diff / data["price"]) * 100 if data["price"] > 0 else 0

    if rebound_pct >= 25:
        log_event(f"📈 Rebound detected: {token} gained {rebound_pct:.2f}% after sell.")
        tag_token_result(token, "early_exit")

        try:
            ai_brain.adjust_hold_bias(token, boost=True, context={
                "gain_pct": rebound_pct,
                "delay_sec": time_diff
            })

            keywords = ai_brain.get_keywords_for_token(token)
            reverse_learning_log[token] = {
                "token": token,
                "delay_sec": time_diff,
                "keywords": keywords,
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logging.warning(f"[ReverseLearning] AI adjustment failed: {e}")

def should_hold_longer(token: str) -> bool:
    from core.ai_brain import ai_brain
    if not is_valid_token_address(token):
        return False
    try:
        return ai_brain.get_token_tendency(token, "rebound_hold_bias", default=False)
    except Exception as e:
        logging.warning(f"[ReverseLearning] Hold check failed: {e}")
        return False

def get_reverse_learning_log(limit: int = 5):
    recent = list(reverse_learning_log.values())[-limit:]
    tally = {}

    for entry in reverse_learning_log.values():
        for kw in entry.get("keywords", []):
            tally[kw] = tally.get(kw, 0) + 1

    sorted_tally = dict(sorted(tally.items(), key=lambda x: x[1], reverse=True))  
    return {
        "recent": recent,
        "summary": sorted_tally
    }

def record_exit_result(token_address: str, outcome: str, pnl: float):
    if not is_valid_token_address(token_address):
        return
    try:
        from strategy.strategy_memory import record_result
        reason = f"exit_pnl={pnl:.2f}"
        tag = f"exit:{outcome}"

        record_result(token_address, tag=tag, reason=reason)
        log_event(f"[ReverseLearning] 🔄 Exit result recorded: {token_address} | {outcome} | {pnl:.2f}")
    except Exception as e:
        logging.warning(f"[ReverseLearning] Failed to record exit result: {e}")

def record_token_theme_outcome(theme: str, result: str):
    try:
        from strategy.strategy_memory import update_meta_keywords
        update_meta_keywords(theme, result)
    except Exception as e:
        logging.warning(f"[ReverseLearning] Failed to tag theme outcome: {e}")

def record_wallet_outcome(wallet: str, token: str, outcome: str):
    if not is_valid_token_address(token):
        return
    key = f"{wallet}:{token}"
    wallet_learning[key] = {
        "wallet": wallet,
        "token": token,
        "outcome": outcome,
        "timestamp": datetime.utcnow().isoformat()
    }
