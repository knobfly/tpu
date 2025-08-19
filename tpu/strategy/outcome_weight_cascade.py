# === /outcome_weight_cascade.py ===

import json
import os
from collections import defaultdict

REASONING_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_weights.json"
PATTERN_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/signal_patterns.json"

def load_json(path, fallback):
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return fallback

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def apply_cascading_weights():
    """
    Reinforces related signal patterns based on high-performing reasoning tags.
    Useful for boosting traits that appear frequently in wins (e.g., 'whale_buyer').
    """
    reasoning = load_json(REASONING_FILE, {})
    patterns = load_json(PATTERN_FILE, {})

    for tag, outcomes in reasoning.items():
        profit_score = outcomes.get("profit", 0)
        rug_score = outcomes.get("rug", 0)
        moon_score = outcomes.get("moon", 0)
        loss_score = outcomes.get("loss", 0)
        dead_score = outcomes.get("dead", 0)

        net_score = (profit_score * 2 + moon_score * 3) - (rug_score * 4 + loss_score * 2 + dead_score)

        if abs(net_score) < 3:
            continue  # insignificant signal

        for trait_key in patterns:
            for val in patterns[trait_key]:
                related = patterns[trait_key][val]
                for outcome in related:
                    if tag in val.lower() or tag in trait_key.lower():
                        related[outcome] += int(net_score / 5)

    save_json(PATTERN_FILE, patterns)
