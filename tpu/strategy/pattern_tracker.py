import json
import os
from collections import defaultdict

PATTERN_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/context_patterns.json"

def load_patterns():
    if not os.path.exists(PATTERN_FILE):
        return {}
    try:
        with open(PATTERN_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_patterns(data):
    try:
        with open(PATTERN_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def normalize_pattern(reason_list):
    return " | ".join(sorted(set(reason_list)))

def update_context_pattern(reason_list, outcome):
    """
    Track how often full reason clusters lead to outcomes.
    """
    patterns = load_patterns()
    key = normalize_pattern(reason_list)

    if key not in patterns:
        patterns[key] = {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0, "score": 0}

    patterns[key][outcome] = patterns[key].get(outcome, 0) + 1

    # Reinforcement weight
    score_delta = {
        "profit": 2,
        "moon": 4,
        "loss": -1,
        "rug": -4,
        "dead": -2
    }.get(outcome, 0)

    patterns[key]["score"] += score_delta
    save_patterns(patterns)

def get_pattern_score(reason_list):
    key = normalize_pattern(reason_list)
    patterns = load_patterns()
    return patterns.get(key, {}).get("score", 0)
