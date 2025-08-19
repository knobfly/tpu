# === /reasoning_memory.py ===

import json
import os
from collections import defaultdict
from typing import Any, Dict

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_memory.json"

# === Unified loader using nested defaultdicts for tag + token-based structure ===
def load_reasoning_memory() -> Dict[str, Any]:
    if not os.path.exists(MEMORY_FILE):
        return {
            "tags": defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "dead": 0, "moon": 0}),
            "tokens": {}
        }
    try:
        with open(MEMORY_FILE, "r") as f:
            data = json.load(f)
            return {
                "tags": defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "dead": 0, "moon": 0}, data.get("tags", {})),
                "tokens": data.get("tokens", {})
            }
    except Exception:
        return {
            "tags": defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "dead": 0, "moon": 0}),
            "tokens": {}
        }

def save_reasoning_memory(data: Dict[str, Any]):
    try:
        # Convert defaultdicts back to dict before saving
        tags = dict(data.get("tags", {}))
        tokens = data.get("tokens", {})
        with open(MEMORY_FILE, "w") as f:
            json.dump({"tags": tags, "tokens": tokens}, f, indent=2)
    except Exception:
        pass

# === LOG TOKEN-LEVEL FULL REASONING CONTEXT ===
def log_reasoning(token_address: str, outcome: str, reasoning: list):
    """
    Logs full context reasons for a token's outcome.
    outcome = 'win', 'loss', 'rug', etc.
    reasoning = [
        {"chart_score": 7, "details": {...}},
        {"wallet_score": 4, "details": {...}},
        ...
    ]
    """
    data = load_reasoning_memory()
    record = data["tokens"].get(token_address, {"history": []})

    record["history"].append({
        "outcome": outcome,
        "reasons": reasoning
    })

    # Limit history size
    if len(record["history"]) > 25:
        record["history"] = record["history"][-25:]

    data["tokens"][token_address] = record
    save_reasoning_memory(data)

# === SUMMARIZE REASONING BY FREQUENCY OF WIN/FAIL ===
def summarize_reasoning(token_address: str) -> Dict[str, Any]:
    data = load_reasoning_memory()
    record = data["tokens"].get(token_address, {})
    history = record.get("history", [])

    summary = {
        "total": len(history),
        "win_keys": {},
        "fail_keys": {}
    }

    for entry in history:
        outcome = entry.get("outcome", "")
        reasons = entry.get("reasons", [])

        for r in reasons:
            for key, val in r.items():
                if key == "details":
                    continue
                bucket = "win_keys" if outcome in ["win", "profit", "moon"] else "fail_keys"
                summary[bucket][key] = summary[bucket].get(key, 0) + 1

    return summary

# === TAG-WISE GLOBAL REINFORCEMENT TRACKING ===
def reinforce_reasoning_tags(reasoning_tags: list, outcome: str):
    """
    Updates reinforcement weights for each simple reasoning tag based on outcome.
    Valid outcomes: 'profit', 'loss', 'rug', 'dead', 'moon'
    """
    if not outcome or not reasoning_tags:
        return

    data = load_reasoning_memory()
    tag_data = data["tags"]

    for reason in reasoning_tags:
        reason = reason.lower().strip()
        if outcome in tag_data[reason]:
            tag_data[reason][outcome] += 1

    data["tags"] = tag_data
    save_reasoning_memory(data)

# === CALCULATE SCORE FOR INDIVIDUAL TAGS ===
def get_reasoning_score(reason: str) -> int:
    data = load_reasoning_memory()
    tag_data = data["tags"]
    reason = reason.lower().strip()

    if reason not in tag_data:
        return 0

    counts = tag_data[reason]
    score = (
        counts.get("profit", 0) * 2 +
        counts.get("moon", 0) * 3 -
        counts.get("loss", 0) * 1 -
        counts.get("rug", 0) * 4 -
        counts.get("dead", 0) * 2
    )
    return score
