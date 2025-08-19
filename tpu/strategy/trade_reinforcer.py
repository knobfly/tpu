import logging

from memory.shared_runtime import shared_memory
from memory.token_memory_index import update_token_result
from strategy.outcome_predictor import update_actual_outcome
from strategy.reasoning_memory import update_reasoning_memory
from strategy.signal_pattern_tracker import log_signal_pattern
from strategy.theme_profiler import log_theme_result


def reinforce_trade_feedback(trade_data: dict):
    """
    trade_data example:
    {
        "token": "GARY",
        "token_address": "abc123...",
        "action": "snipe",
        "final_score": 42,
        "reasoning": ["celeb", "whale", "fresh deploy"],
        "themes": ["celeb", "bundle"],
        "signals": {
            "lp_status": "locked",
            "whales": True,
            "sniper_overlap": True,
            ...
        },
        "outcome": "rug"
    }
    """

    token = trade_data.get("token")
    token_address = trade_data.get("token_address")
    reasoning = trade_data.get("reasoning", [])
    outcome = trade_data.get("outcome")
    themes = trade_data.get("themes", [])
    signals = trade_data.get("signals", {})

    logging.info(f"[Reinforcer] 🧠 Updating feedback: {token} -> {outcome}")

    if outcome:
        # === Memory feedback ===
        update_reasoning_memory(reasoning, outcome)
        log_signal_pattern(signals, outcome)
        update_actual_outcome(token_address, outcome)

        if themes:
            log_theme_result(themes, outcome)

        # === Token performance flag ===
        update_token_result(token_address, outcome)

        # === Tag token in memory for fast lookup ===
        shared_memory["tokens"][token_address] = {
            **shared_memory["tokens"].get(token_address, {}),
            "last_outcome": outcome,
            "last_reasons": reasoning,
            "score": trade_data.get("final_score", 0),
        }
