import json
import os
from collections import defaultdict

from utils.logger import log_event

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/nlp_fusion_weights.json"

def load_nlp_weights():
    if not os.path.exists(MEMORY_FILE):
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0})
    try:
        with open(MEMORY_FILE, "r") as f:
            data = json.load(f)
            return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0}, data)
    except:
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0})

def save_nlp_weights(data):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log_event(f"⚠️ Failed to save NLP weights: {e}")

def reinforce_nlp_outcomes(phrases: list[str], outcome: str):
    if not phrases or not outcome:
        return

    weights = load_nlp_weights()
    for phrase in phrases:
        phrase = phrase.lower().strip()
        if outcome in weights[phrase]:
            weights[phrase][outcome] += 1
    save_nlp_weights(weights)

def get_nlp_phrase_score(phrase: str) -> int:
    data = load_nlp_weights()
    phrase = phrase.lower().strip()
    if phrase not in data:
        return 0
    stats = data[phrase]
    return (
        stats.get("profit", 0) * 2 +
        stats.get("moon", 0) * 3 -
        stats.get("loss", 0) * 2 -
        stats.get("rug", 0) * 4
    )

def apply_nlp_fusion_boost(phrases: list[str]) -> tuple[int, list[str]]:
    total = 0
    reasons = []
    for p in phrases:
        score = get_nlp_phrase_score(p)
        if score > 0:
            total += min(score, 10)
            reasons.append(f"{p}: +{score}")
        elif score < 0:
            reasons.append(f"{p}: {score}")
    return total, reasons
