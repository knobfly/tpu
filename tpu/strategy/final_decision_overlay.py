from strategy.outcome_predictor import predict_outcome_from_signals
from strategy.reinforcement_tracker import get_summary


def finalize_action_decision(score: float, signals: dict, base_action: str) -> str:
    """
    Adjusts the raw action based on outcome prediction + recent streaks.

    - If rug rate is high → be cautious
    - If win streak → tolerate lower score for snipe
    - If loss streak → require higher score for snipe
    """

    # === Outcome Prediction Influence
    prediction = predict_outcome_from_signals(signals)
    rug_prob = prediction.get("rug", 0)
    moon_prob = prediction.get("moon", 0)

    # === Streak Adjustment
    streak = get_summary().get("streak", {})
    streak_type = streak.get("type")
    streak_count = streak.get("count", 0)

    # === Base
    action = base_action

    # === Rug Risk Override
    if rug_prob > 0.5 and score < 25:
        return "ignore"

    # === High Moon Odds
    if moon_prob > 0.4 and score >= 15:
        return "snipe"

    # === Streak-Based Modifiers
    if streak_type == "loss" and streak_count >= 3 and score < 30:
        return "ignore"
    if streak_type == "win" and streak_count >= 3 and score >= 15:
        return "snipe"

    return action
