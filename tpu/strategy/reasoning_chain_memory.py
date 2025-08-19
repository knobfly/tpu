import json
import os
from collections import Counter, defaultdict
from itertools import combinations

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_chains.json"
MAX_DEPTH = 3  # max number of tag combinations

def load_reasoning_chains():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_reasoning_chains(data):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def log_reasoning_chain(reasoning_tags: list[str], outcome: str):
    """
    Stores frequency of reasoning tag combinations and associated outcome.
    """
    data = load_reasoning_chains()
    norm_outcome = outcome.lower().strip()

    cleaned = [tag.lower().strip() for tag in reasoning_tags if tag]
    for r in range(2, MAX_DEPTH + 1):
        for combo in combinations(sorted(set(cleaned)), r):
            key = "|".join(combo)
            if key not in data:
                data[key] = {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0}
            if norm_outcome in data[key]:
                data[key][norm_outcome] += 1

    save_reasoning_chains(data)

def get_high_value_chains(min_profit=3, min_ratio=0.7):
    """
    Returns chains with high win ratios.
    """
    chains = load_reasoning_chains()
    winners = []

    for key, outcomes in chains.items():
        profit = outcomes.get("profit", 0) + outcomes.get("moon", 0) * 2
        bad = outcomes.get("loss", 0) + outcomes.get("rug", 0) * 2 + outcomes.get("dead", 0)
        total = profit + bad
        if total < min_profit:
            continue
        ratio = profit / total
        if ratio >= min_ratio:
            winners.append((key, round(ratio, 3), total))

    return sorted(winners, key=lambda x: (-x[1], -x[2]))

def get_risky_chains(min_loss=3, max_ratio=0.3):
    """
    Returns chains that are often followed by loss or rug.
    """
    chains = load_reasoning_chains()
    losers = []

    for key, outcomes in chains.items():
        profit = outcomes.get("profit", 0) + outcomes.get("moon", 0)
        bad = outcomes.get("loss", 0) + outcomes.get("rug", 0) + outcomes.get("dead", 0)
        total = profit + bad
        if total < min_loss:
            continue
        ratio = profit / total if total > 0 else 0
        if ratio <= max_ratio:
            losers.append((key, round(ratio, 3), total))

    return sorted(losers, key=lambda x: (x[1], -x[2]))
