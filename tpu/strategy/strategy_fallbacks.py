# === sniper_bot/modules/strategy/strategy_fallbacks.py ===

from datetime import datetime

from defense.ai_sniper_intuition import get_intuition_confidence
from utils.logger import log_event
from utils.time_utils import get_token_age_minutes
from utils.wallet_helpers import count_unique_buyers


def apply_fallback_decision(token_context: dict, current_score: float, reasons: list) -> tuple:
    """
    If score is low or inconclusive, use fallback logic to decide action.
    Returns: (final_score, fallback_applied)
    """
    token_address = token_context.get("token_address")
    age = get_token_age_minutes(token_context)
    buyer_count = count_unique_buyers(token_context)
    intuition_level = get_intuition_confidence(token_address)

    fallback_score = 0
    fallback_applied = False

    if current_score < 10:
        # Boost if fresh and active
        if age < 5 and buyer_count >= 10:
            fallback_score += 5
            reasons.append("Fallback: fresh + buyers")
            fallback_applied = True

        # Boost if AI intuition is strong
        if intuition_level >= 7:
            fallback_score += 3
            reasons.append("Fallback: strong AI intuition")
            fallback_applied = True

        # Penalize if old + no traction
        if age > 20 and buyer_count < 5:
            fallback_score -= 5
            reasons.append("Fallback: aged + inactive")
            fallback_applied = True

        # Floor: never fall below 0 or above 20
        final_score = max(0, min(current_score + fallback_score, 20))
    else:
        final_score = current_score

    if fallback_applied:
        log_event(f"[Fallback] Applied fallback logic to {token_address} | ΔScore={final_score - current_score:.2f}")

    return final_score, fallback_applied
