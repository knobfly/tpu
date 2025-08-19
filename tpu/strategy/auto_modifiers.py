from strategy.compression_summary_engine import get_high_confidence_signals, get_risky_signals
from strategy.reasoning_memory import get_reasoning_score
from strategy.reinforcement_tracker import get_recent_streak


def get_auto_modifiers(reasoning: list[str], theme: list[str], base_delay=1.0) -> dict:
    """
    Adjusts aggression and delay based on known reasoning tags and recent streaks.

    Returns:
        {
            "aggression": "high" | "balanced" | "low",
            "delay": float
        }
    """
    # === Compute confidence score ===
    scores = [get_reasoning_score(r) for r in reasoning]
    avg_score = sum(scores) / len(scores) if scores else 0

    # === Apply delay modifier based on risk ===
    risky = set(get_risky_signals())
    confident = set(get_high_confidence_signals())

    delay = base_delay
    for r in reasoning:
        if r in risky:
            delay += 0.5
        if r in confident:
            delay -= 0.3

    # === Adjust further based on theme ===
    if "celeb" in theme or "dev mint" in theme:
        delay += 0.25
    if "community" in theme:
        delay -= 0.1

    # === Clamp delay
    delay = round(max(0.25, min(delay, 4.0)), 2)

    # === Adjust aggression ===
    if avg_score >= 10:
        aggression = "high"
    elif avg_score <= -5:
        aggression = "low"
    else:
        aggression = "balanced"

    # === Apply win/loss streak bias ===
    streak = get_recent_streak()
    if streak <= -3:
        aggression = "low"
        delay += 0.5
    elif streak >= 3:
        aggression = "high"
        delay -= 0.2

    return {
        "aggression": aggression,
        "delay": round(delay, 2)
    }
