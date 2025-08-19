import json
import os

from memory.token_outcome_memory import get_token_outcome
from strategy.theme_profiler import profile_theme_keywords
from strategy.trait_weight_engine import get_trait_score

WEIGHT_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/outcome_weights.json"

def load_weights():
    if not os.path.exists(WEIGHT_FILE):
        return {}
    try:
        with open(WEIGHT_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_weights(data):
    try:
        with open(WEIGHT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except:
        pass

def update_outcome_weights(token_address: str, keywords: list[str], outcome: str):
    if not outcome or not token_address:
        return
    weights = load_weights()
    for k in keywords:
        k = k.lower().strip()
        if k not in weights:
            weights[k] = {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0}
        if outcome in weights[k]:
            weights[k][outcome] += 1
    save_weights(weights)

def get_outcome_weight_score(keywords: list[str]) -> tuple[int, list[str]]:
    """
    Returns outcome-weighted intuition modifier and reasons.
    Negative if traits lead to bad outcomes historically.
    """
    weights = load_weights()
    total_score = 0
    reasons = []

    for k in keywords:
        k = k.lower().strip()
        w = weights.get(k, {})
        score = (
            w.get("profit", 0) * 2 +
            w.get("moon", 0) * 3 -
            w.get("loss", 0) * 2 -
            w.get("rug", 0) * 4 -
            w.get("dead", 0)
        )
        if score > 0:
            total_score += min(score, 10)
            reasons.append(f"{k}: +{score}")
        elif score < 0:
            total_score += max(score, -10)
            reasons.append(f"{k}: {score}")

    return total_score, reasons
