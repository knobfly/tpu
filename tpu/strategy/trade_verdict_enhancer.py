from chart.heatmap_optimizer import analyze_heatmap
from strategy.reasoning_memory import summarize_reasoning
from strategy.reinforcement_tracker import get_summary


def enhance_verdict(token_address: str, score: float, reasoning: list, base_action: str) -> dict:
    """
    Refines the final trade action decision with historical context.
    Adds confidence level and optional hesitation/reinforcement tags.
    """
    verdict = {
        "action": base_action,
        "confidence": "medium",
        "adjustments": []
    }

    # === Score Range Suggestions
    heatmap = analyze_heatmap()
    score_bucket = int(score // 5) * 5
    score_range_data = heatmap["score_ranges"].get(score_bucket, {})

    if score_range_data.get("win_rate", 0) > 75 and base_action == "watch":
        verdict["action"] = "snipe"
        verdict["adjustments"].append("🔥 Upgraded to snipe based on high win rate")
    elif score_range_data.get("win_rate", 0) < 20 and base_action == "snipe":
        verdict["action"] = "watch"
        verdict["adjustments"].append("⚠️ Downgraded to watch due to poor performance")

    # === Reinforcement Trends
    recent = get_summary()
    streak = recent.get("streak", {})
    if streak.get("type") == "loss" and streak.get("count", 0) >= 3:
        verdict["confidence"] = "low"
        verdict["adjustments"].append("🧊 Cooling off — recent losses detected")
    elif streak.get("type") == "profit" and streak.get("count", 0) >= 3:
        verdict["confidence"] = "high"
        verdict["adjustments"].append("🚀 Confidence boost — winning streak")

    # === Reasoning Pattern Review
    patterns = summarize_reasoning(token_address)
    for reason in reasoning:
        if patterns["fail_keys"].get(reason, 0) > patterns["win_keys"].get(reason, 0):
            verdict["adjustments"].append(f"❗ Risk flag: '{reason}' usually loses")

    return verdict
