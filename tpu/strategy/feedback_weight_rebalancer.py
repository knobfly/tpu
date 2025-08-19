import json
import os
from collections import defaultdict

from utils.file_manager import load_json_file, save_json_file

WEIGHT_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/feedback_weights.json"
MAX_HISTORY = 200

def load_feedback_weights():
    if not os.path.exists(WEIGHT_FILE):
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0})
    try:
        data = load_json_file(WEIGHT_FILE)
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0}, data)
    except:
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0})

def save_feedback_weights(data):
    save_json_file(WEIGHT_FILE, dict(data))

def reinforce_weights_from_outcome(reasoning: list[str], outcome: str):
    if not reasoning or not outcome:
        return
    weights = load_feedback_weights()
    for reason in reasoning:
        key = reason.lower().strip()
        if key not in weights:
            weights[key] = {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0}
        if outcome in weights[key]:
            weights[key][outcome] += 1
    save_feedback_weights(weights)

def score_feedback_weight(reason: str) -> int:
    weights = load_feedback_weights()
    reason = reason.lower().strip()
    data = weights.get(reason, {})
    return (
        data.get("profit", 0) * 2 +
        data.get("moon", 0) * 3 -
        data.get("loss", 0) * 2 -
        data.get("rug", 0) * 4 -
        data.get("dead", 0) * 1
    )

def apply_feedback_scores(reasons: list[str]) -> tuple[int, list[str]]:
    score = 0
    details = []
    for r in reasons:
        delta = score_feedback_weight(r)
        if delta != 0:
            score += delta
            details.append(f"{r}: {delta}")
    return score, details
