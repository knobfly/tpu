import logging

from strategy.reasoning_memory import get_reasoning_score
from strategy.reinforcement_tracker import get_summary


def reweight_confidence(original_score: float, reasoning: list[str], mode: str = "snipe") -> float:
    """
    Adjusts confidence score using:
    - reinforcement outcome streaks (loss/win)
    - memory-weighted reasoning tag values

    Returns modified score.
    """
    bonus = 0
    penalty = 0

    # === 1. Apply memory weights
    for r in reasoning:
        weight = get_reasoning_score(r)
        if weight > 0:
            bonus += weight * 0.2
        elif weight < 0:
            penalty += abs(weight) * 0.3

    # === 2. Apply streak logic
    summary = get_summary()
    streak_type = summary["streak"]["type"]
    streak_count = summary["streak"]["count"]

    if streak_type == "loss" and streak_count >= 3:
        penalty += 5 + (streak_count - 3)  # Growing penalty
    elif streak_type in ["profit", "moon"] and streak_count >= 3:
        bonus += 3 + (streak_count - 3)  # Confidence boost
    elif streak_type == "rug":
        penalty += 10

    # === Final adjustment
    modified = original_score + bonus - penalty
    final_score = max(0, round(modified, 2))

    logging.info(f"[ConfidenceReweighter] Score {original_score} → {final_score} | bonus={bonus:.2f} penalty={penalty:.2f}")
    return final_score
