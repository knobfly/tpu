# === /strategy_randomizer.py ===

import random


def add_behavior_noise(score: float, mode: str = "snipe") -> float:
    """
    Slightly alters the score to add unpredictability.
    - Mode can be 'snipe' or 'trade'.
    - Adds up to ±2 points of variation.

    For high scores (over 90), noise is reduced to avoid missing strong plays.
    """
    if score >= 90:
        jitter = random.uniform(-0.5, 0.5)
    else:
        jitter = random.uniform(-2.0, 2.0)

    noisy_score = score + jitter
    noisy_score = max(0, round(noisy_score, 2))
    return noisy_score


def random_behavior_shift(strategy: dict, risk_tolerance: float = 1.0) -> dict:
    """
    Randomizes minor config toggles within safe limits (confidence_boost, aggression).
    Only for minor stochasticity — not full strategy alteration.

    Example:
    - Slightly increase or decrease confidence_boost randomly within ±1
    - Occasionally change aggression between "cautious", "balanced", "risky"
    """
    if not strategy:
        return {}

    shift = random.choice([-1, 0, 1])
    strategy["confidence_boost"] = max(0, strategy.get("confidence_boost", 0) + shift)

    if random.random() < 0.1 * risk_tolerance:
        strategy["aggression"] = random.choice(["cautious", "balanced", "risky"])

    return strategy
