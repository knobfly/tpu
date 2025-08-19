# === /self_adjustment_engine.py ===

from strategy.reasoning_memory import summarize_reasoning
from strategy.reinforcement_tracker import get_summary


def apply_self_adjustments(score: float, token_address: str) -> float:
    """
    Adjusts raw snipe or trade score based on recent reinforcement trends:
    - Adds momentum bonuses for win streaks
    - Applies caution penalties after rugs or losing streaks
    - Boosts patterns that historically led to profit
    """

    summary = get_summary()
    reasoning = summarize_reasoning(token_address)

    streak = summary.get("streak", {})
    streak_type = streak.get("type")
    streak_count = streak.get("count", 0)
    counts = summary.get("counts", {})

    adjusted = score

    # === Caution on recent rugs or heavy losses
    rug_count = counts.get("rug", 0)
    if rug_count >= 5:
        adjusted -= 5
    elif rug_count >= 2:
        adjusted -= 2

    if streak_type == "rug" and streak_count >= 2:
        adjusted -= 3

    # === Momentum bonus if on a win/profit streak
    if streak_type in ["win", "profit", "moon"]:
        adjusted += min(streak_count * 1.5, 6)  # cap the bonus

    # === Penalize if loss streak
    if streak_type == "loss" and streak_count >= 3:
        adjusted -= 4

    # === Reasoning reinforcement bonus
    good_tags = reasoning.get("win_keys", {})
    bad_tags = reasoning.get("fail_keys", {})
    net_boost = sum(good_tags.values()) - sum(bad_tags.values())
    adjusted += min(net_boost * 0.25, 5)

    return round(max(0, adjusted), 2)
