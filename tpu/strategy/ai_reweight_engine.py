import logging

from strategy.reinforcement_tracker import get_token_score
from strategy.strategy_signal_memory import get_reasoning_weights


def apply_reasoning_weights(score: float, reasoning: list[str], token_address: str) -> float:
    """
    Adjusts the score dynamically based on historical performance of similar reasoning.
    If a certain reason tends to lead to losses/rugs, it gets penalized over time.
    """
    try:
        weights = get_reasoning_weights(token_address)
        reputation = get_token_score(token_address)

        adjusted_score = score
        penalty = 0

        for reason in reasoning:
            if reason in weights:
                reason_weight = weights[reason]
                if reputation < 0 and reason_weight > 2:
                    penalty_value = min(reason_weight * 0.5, 5)
                    adjusted_score -= penalty_value
                    penalty += penalty_value

        if penalty > 0:
            logging.info(f"[ReweightEngine] ⚖️ Adjusted score by -{penalty} due to poor reasoning trends for {token_address}")

        return round(max(0, adjusted_score), 2)

    except Exception as e:
        logging.warning(f"[ReweightEngine] Failed to apply reasoning weights: {e}")
        return score
