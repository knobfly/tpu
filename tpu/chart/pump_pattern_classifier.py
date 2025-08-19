# chart/pump_pattern_classifier.py

import logging
from statistics import mean

from librarian.data_librarian import librarian
from special.insight_logger import log_scoring_insight
from utils.token_utils import get_token_price_history


def classify_pump_pattern(token: str) -> str:
    """
    Analyze price history and return a pattern classification:
    - 'early_moon', 'late_moon', 'flat', 'dump', or 'unknown'
    """
    history = get_token_price_history(token)
    if not history or len(history) < 5:
        return "unknown"

    prices = [p["price"] for p in history]
    deltas = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    avg_change = mean(deltas)

    if all(x > 0 for x in deltas[:3]) and prices[-1] > prices[0] * 2:
        return "early_moon"
    elif prices[-1] > prices[0] * 1.5 and deltas[-1] > 0:
        return "late_moon"
    elif abs(avg_change) < 0.01:
        return "flat"
    elif deltas[-1] < 0 and prices[-1] < prices[0] * 0.5:
        return "dump"
    else:
        return "unknown"


def score_token_pump_pattern(token: str) -> float:
    """
    Scores a token based on its detected pump/dump pattern.
    """
    pattern = classify_pump_pattern(token)

    base_score = {
        "early_moon": 1.0,
        "late_moon": 0.7,
        "flat": 0.2,
        "dump": -0.8,
        "unknown": 0.0,
    }.get(pattern, 0.0)

    # Log pattern and scoring
    log_scoring_insight(token, {"pump_pattern": pattern}, base_score, decision="pattern_classified")

    # Tag token via librarian
    try:
        librarian.tag_token(token, f"pattern_{pattern}")
    except Exception as e:
        logging.debug(f"[PumpPattern] librarian.tag_token failed: {e}")

    # Add to memory if risky
    if pattern == "dump":
        librarian.log_result({
            "type": "pump_pattern",
            "mint": token,
            "score": base_score,
            "tags": ["rug_risk"],
            "pattern": pattern,
        })

    return base_score


def detect_pump_signals(price_data: dict) -> dict:
    """
    Detect basic pump-like behavior in the price chart.
    Input: price_data = dict of {timestamp: {"price": float, "volume": float}}
    Output: dict of detected patterns and their confidence scores.
    """
    if not price_data or len(price_data) < 5:
        return {"pump_detected": False, "score": 0.0}

    try:
        prices = [v["price"] for v in price_data.values() if "price" in v]
        volumes = [v["volume"] for v in price_data.values() if "volume" in v]

        if len(prices) < 5 or len(volumes) < 5:
            return {"pump_detected": False, "score": 0.0}

        pct_change = (prices[-1] - prices[0]) / prices[0] * 100
        volume_spike = volumes[-1] > (sum(volumes[:-1]) / len(volumes[:-1])) * 2.5

        pump_score = 0.0
        if pct_change > 50:
            pump_score += 0.5
        if volume_spike:
            pump_score += 0.5

        return {
            "pump_detected": pump_score >= 0.5,
            "score": round(pump_score, 2),
            "pct_change": round(pct_change, 2),
            "volume_spike": volume_spike
        }

    except Exception as e:
        return {"pump_detected": False, "score": 0.0, "error": str(e)}
