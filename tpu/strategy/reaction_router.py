import logging

from strategy.trade_reinforcer import reinforce_trade_feedback


def handle_trade_result(result: dict):
    """
    Routes a completed trade result to reinforcement, memory, and learning.

    result = {
        "token": "LUNA",
        "token_address": "...",
        "final_score": 48,
        "reasoning": ["celeb", "whale", "chart"],
        "themes": ["celeb"],
        "signals": {
            "whales": True,
            ...
        },
        "outcome": "rug",   # If known, otherwise inferred
        "action": "snipe",
        ...
    }
    """

    outcome = result.get("outcome")
    if not outcome:
        # === Infer outcome from score, behavior, or stop-loss
        score = result.get("final_score", 0)
        pnl = result.get("pnl", 0)
        if pnl < -50 or "honeypot" in result.get("reasoning", []):
            result["outcome"] = "rug"
        elif pnl < 0:
            result["outcome"] = "loss"
        elif pnl > 100:
            result["outcome"] = "moon"
        elif pnl > 0:
            result["outcome"] = "profit"
        else:
            result["outcome"] = "dead"

    logging.info(f"[ReactionRouter] Routing outcome for {result.get('token')}: {result['outcome']}")
    reinforce_trade_feedback(result)
