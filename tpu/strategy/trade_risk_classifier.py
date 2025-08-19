# === /trade_risk_classifier.py ===

from strategy.reinforced_trait_reweighter import load_reasoning_memory
from strategy.signal_pattern_tracker import load_signal_patterns


def classify_trade_risk(reasoning: list, signals: dict) -> str:
    """
    Assigns a risk level to the current trade based on:
    - Reasoning tag history
    - Signal pattern outcome memory
    - Number of positive vs negative memory hits

    Returns: one of ['high', 'medium', 'low', 'experimental', 'alpha-safe']
    """
    if not reasoning and not signals:
        return "experimental"

    tag_memory = load_reasoning_memory()
    signal_memory = load_signal_patterns()

    rug_weight = 0
    win_weight = 0

    for r in reasoning:
        tag = r.lower().strip()
        if tag in tag_memory:
            rug_weight += tag_memory[tag].get("rug", 0) * 1.5
            win_weight += tag_memory[tag].get("profit", 0) + tag_memory[tag].get("moon", 0)

    for k, v in signals.items():
        val = str(v).lower()
        mem = signal_memory.get(k, {}).get(val, {})
        rug_weight += mem.get("rug", 0) * 1.2
        win_weight += mem.get("profit", 0) + mem.get("moon", 0)

    total = rug_weight + win_weight
    if total == 0:
        return "experimental"

    rug_ratio = rug_weight / total

    if rug_ratio >= 0.7:
        return "high"
    elif rug_ratio >= 0.5:
        return "medium"
    elif rug_ratio <= 0.2 and win_weight >= 3:
        return "alpha-safe"
    elif rug_ratio <= 0.4:
        return "low"
    else:
        return "medium"
