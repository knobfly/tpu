import math

from strategy.evaluation_tracker import get_score_distribution


def analyze_heatmap(thresholds=None):
    """
    Analyzes the current score distribution and suggests threshold adjustments.
    Input thresholds:
        {
            "snipe": 20,
            "watch": 12
        }
    Returns analysis dict.
    """
    if thresholds is None:
        thresholds = {"snipe": 20, "watch": 12}

    distribution = get_score_distribution()
    summary = {
        "thresholds": thresholds,
        "score_ranges": {},
        "suggestions": []
    }

    for score_bucket, outcomes in distribution.items():
        total = sum(outcomes.values())
        if total == 0:
            continue
        win_rate = (
            (outcomes.get("profit", 0) + outcomes.get("moon", 0)) / total
        ) * 100

        summary["score_ranges"][score_bucket] = {
            "total": total,
            "outcomes": outcomes,
            "win_rate": round(win_rate, 2)
        }

        # === Threshold suggestions
        if score_bucket < thresholds["watch"] and win_rate > 40:
            summary["suggestions"].append(f"⬆️ Consider lowering watch threshold to include {score_bucket}+")
        if thresholds["watch"] <= score_bucket < thresholds["snipe"] and win_rate > 60:
            summary["suggestions"].append(f"⬆️ Consider lowering snipe threshold to include {score_bucket}+")
        if score_bucket >= thresholds["snipe"] and win_rate < 25:
            summary["suggestions"].append(f"⬇️ Consider raising snipe threshold to skip {score_bucket}")

    return summary


def get_heatmap_boost(price_data: dict) -> float:
    """
    Calculate a boost score based on recent heatmap zones.
    - Encourages momentum breakouts above resistance or strong support bounces.
    """
    if not price_data or len(price_data) < 5:
        return 0.0

    try:
        prices = [v["price"] for v in price_data.values() if "price" in v]
        if len(prices) < 5:
            return 0.0

        recent_price = prices[-1]
        support = min(prices)
        resistance = max(prices)

        range_span = resistance - support
        if range_span == 0:
            return 0.0

        # Normalize position of recent price between support/resistance (0.0 - 1.0)
        normalized_pos = (recent_price - support) / range_span

        # Boost logic:
        # - price near resistance -> possible breakout (score up to +0.5)
        # - price near support -> possible bounce (score up to +0.3)
        if normalized_pos >= 0.9:
            return 0.5  # breakout zone
        elif normalized_pos <= 0.1:
            return 0.3  # bounce zone
        else:
            return 0.0

    except Exception as e:
        return 0.0
