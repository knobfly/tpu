# === /reinforcement_pattern_diff.py ===

import json
import os
from collections import defaultdict

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_weights.json"
WINDOW_SIZE = 25

def load_reasoning_weights():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def get_tag_diff_summary() -> dict:
    """
    Compares each tag's profit vs rug trend over time.
    Outputs tags where performance has changed significantly.

    Returns:
        {
            "degrading": ["overlap_snipers", "whale_buyer"],
            "strengthening": ["lp_locked", "early_buy_window"]
        }
    """
    data = load_reasoning_weights()
    degrading = []
    strengthening = []

    for tag, stats in data.items():
        profit = stats.get("profit", 0)
        rug = stats.get("rug", 0)
        moon = stats.get("moon", 0)
        loss = stats.get("loss", 0)
        dead = stats.get("dead", 0)

        total = profit + rug + moon + loss + dead
        if total < WINDOW_SIZE:
            continue  # not enough data yet

        good = profit + moon
        bad = rug + loss + dead
        if good + bad == 0:
            continue

        ratio = good / (good + bad)

        # If recent rug rate > 70% with enough volume
        if bad > good and ratio < 0.3 and bad >= 5:
            degrading.append(tag)
        elif good > bad and ratio > 0.7 and good >= 5:
            strengthening.append(tag)

    return {
        "degrading": sorted(degrading),
        "strengthening": sorted(strengthening)
    }
