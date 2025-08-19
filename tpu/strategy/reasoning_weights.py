import json
import os
from collections import defaultdict

WEIGHT_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_weights.json"

def load_weights():
    if not os.path.exists(WEIGHT_FILE):
        return {}
    try:
        with open(WEIGHT_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_weights(data):
    try:
        with open(WEIGHT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def update_reasoning_weights(reasoning_list, outcome):
    """
    Given a list of reasons and the result (profit, rug, etc), adjust weights.
    """
    weights = load_weights()

    impact = {
        "profit": 2,
        "moon": 3,
        "loss": -1,
        "rug": -3,
        "dead": -2
    }.get(outcome, 0)

    for reason in reasoning_list:
        if reason not in weights:
            weights[reason] = 0
        weights[reason] += impact

    save_weights(weights)

def get_reasoning_bias(reason: str) -> int:
    """
    Returns the score impact bias for a reasoning tag.
    """
    weights = load_weights()
    return weights.get(reason, 0)

def get_top_biases(n=10):
    weights = load_weights()
    return sorted(weights.items(), key=lambda x: x[1], reverse=True)[:n]
