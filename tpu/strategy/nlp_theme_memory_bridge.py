# === sniper_bot/modules/strategy/nlp_theme_memory_bridge.py ===

import json
import os
from collections import defaultdict

from utils.logger import log_event

THEME_MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/nlp_theme_weights.json"

def load_theme_weights():
    if not os.path.exists(THEME_MEMORY_FILE):
        return defaultdict(lambda: {"profit": 0, "rug": 0, "loss": 0, "moon": 0, "dead": 0})
    try:
        with open(THEME_MEMORY_FILE, "r") as f:
            data = json.load(f)
            return defaultdict(lambda: {"profit": 0, "rug": 0, "loss": 0, "moon": 0, "dead": 0}, data)
    except Exception:
        return defaultdict(lambda: {"profit": 0, "rug": 0, "loss": 0, "moon": 0, "dead": 0})

def save_theme_weights(data):
    try:
        with open(THEME_MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def update_theme_weights(themes: list[str], outcome: str):
    """
    Updates memory based on NLP themes linked to trade outcomes.
    """
    if not outcome or not themes:
        return

    data = load_theme_weights()
    for theme in themes:
        theme = theme.lower().strip()
        if outcome in data[theme]:
            data[theme][outcome] += 1

    save_theme_weights(data)
    log_event(f"🧠 Updated NLP theme memory for {themes} -> {outcome}")

def get_theme_boost(themes: list[str]) -> tuple[int, list[str]]:
    """
    Calculates intuition score from NLP theme outcome patterns.
    """
    data = load_theme_weights()
    boost = 0
    reasons = []

    for theme in themes:
        scores = data.get(theme.lower().strip(), {})
        raw_score = (
            scores.get("profit", 0) * 2 +
            scores.get("moon", 0) * 3 -
            scores.get("rug", 0) * 4 -
            scores.get("loss", 0) * 2 -
            scores.get("dead", 0)
        )
        if raw_score != 0:
            capped = min(max(raw_score, -10), 10)
            boost += capped
            reasons.append(f"{theme}: {capped:+}")

    return boost, reasons
