#/strategy/rotation_suggestion_engine.py ===

import logging
from collections import defaultdict

from strategy.outcome_predictor import get_accuracy_summary
from strategy.reinforcement_weights import load_reasoning_memory
from strategy.signal_pattern_tracker import load_signal_patterns


def suggest_strategy_rotation() -> dict:
    """
    Suggests soft adjustments to the current strategy based on failure clusters,
    dominant failure signals, reasoning tag collapse, and outcome misprediction rates.

    Returns example:
    {
        "suggestions": [
            "🔄 Penalize wallet overlap signals (high rug rate)",
            "⚠️ Chart confidence declining — reweight chart score",
            "📉 Too many prediction errors — rerun reinforcement tuner"
        ],
        "confidence": 0.87
    }
    """
    suggestions = []
    confidence = 0.5

    # === Reasoning weight patterns
    memory = load_reasoning_memory()
    for tag, weights in memory.items():
        rug_rate = weights.get("rug", 0)
        profit_rate = weights.get("profit", 0)
        if rug_rate > profit_rate and rug_rate >= 3:
            suggestions.append(f"🔄 Penalize reasoning tag: '{tag}' (high rug rate)")

    # === Signal memory pattern traps
    signal_memory = load_signal_patterns()
    for key, submap in signal_memory.items():
        for val, outcome_map in submap.items():
            rug_count = outcome_map.get("rug", 0)
            win_count = outcome_map.get("profit", 0)
            if rug_count > win_count and rug_count >= 4:
                suggestions.append(f"⚠️ Signal: {key} = {val} → frequent rug outcome")

    # === Prediction accuracy stats
    accuracy = get_accuracy_summary()
    if accuracy["accuracy_percent"] < 60 and accuracy["total"] >= 20:
        suggestions.append("📉 Too many prediction errors — rerun reinforcement tuner")
        confidence += 0.15

    # === Confidence adjustment
    confidence += min(len(suggestions) * 0.05, 0.4)
    confidence = round(min(confidence, 1.0), 2)

    return {
        "suggestions": suggestions or ["✅ No immediate strategy concerns."],
        "confidence": confidence
    }
