import json
import os
from collections import defaultdict
from statistics import mean

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/alpha_patterns.json"
MAX_HISTORY = 200

def load_alpha_patterns():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_alpha_patterns(data):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except:
        pass

def log_alpha_pattern(result: dict):
    """
    Logs a pattern from a completed trade result.

    result = {
        "token_address": "...",
        "reasoning": ["celeb", "whale", "chart"],
        "outcome": "profit",
        ...
    }
    """
    patterns = load_alpha_patterns()
    tags = result.get("reasoning", [])
    outcome = result.get("outcome")

    if not tags or not outcome:
        return

    key = "|".join(sorted(set(tags)))
    entry = patterns.get(key, {"count": 0, "wins": 0, "losses": 0, "moons": 0, "rugs": 0})

    entry["count"] += 1
    if outcome in ["profit", "win"]:
        entry["wins"] += 1
    elif outcome in ["loss"]:
        entry["losses"] += 1
    elif outcome in ["moon"]:
        entry["moons"] += 1
    elif outcome in ["rug", "dead"]:
        entry["rugs"] += 1

    patterns[key] = entry
    save_alpha_patterns(patterns)

def get_top_patterns(min_count=3):
    patterns = load_alpha_patterns()
    results = []

    for combo, stats in patterns.items():
        count = stats["count"]
        if count < min_count:
            continue

        win_rate = (stats["wins"] + stats["moons"] * 2) / count
        rug_rate = stats["rugs"] / count
        score = round(win_rate - rug_rate, 3)

        results.append((combo, score, count))

    return sorted(results, key=lambda x: -x[1])
