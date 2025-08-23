import json
import os
from collections import defaultdict
from datetime import datetime

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/chart_pattern_memory.json"
MAX_HISTORY = 1000

def load_chart_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_chart_memory(data):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[chart_pattern_memory] Failed to save chart memory: {e}")

def log_chart_pattern(token_address: str, pattern: str, outcome: str):
    """
    Reinforce memory that pattern X led to outcome Y.
    """
    data = load_chart_memory()
    if pattern not in data:
        data[pattern] = {
            "profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0,
            "last_seen": datetime.utcnow().isoformat(),
            "samples": []
        }

    data[pattern][outcome] = data[pattern].get(outcome, 0) + 1
    data[pattern]["last_seen"] = datetime.utcnow().isoformat()
    data[pattern]["samples"].append(token_address)

    if len(data[pattern]["samples"]) > MAX_HISTORY:
        data[pattern]["samples"] = data[pattern]["samples"][-MAX_HISTORY:]

    save_chart_memory(data)

def get_pattern_score(pattern: str) -> dict:
    """
    Return confidence score of a pattern based on past memory.
    """
    data = load_chart_memory()
    pattern_data = data.get(pattern)
    if not pattern_data:
        return {"score": 0, "total": 0, "breakdown": {}}

    score = (
        pattern_data.get("profit", 0) * 2 +
        pattern_data.get("moon", 0) * 3 -
        pattern_data.get("loss", 0) * 2 -
        pattern_data.get("rug", 0) * 4 -
        pattern_data.get("dead", 0)
    )
    total = sum(pattern_data.get(k, 0) for k in ["profit", "loss", "rug", "moon", "dead"])

    return {
        "score": score,
        "total": total,
        "breakdown": {
            "profit": pattern_data.get("profit", 0),
            "loss": pattern_data.get("loss", 0),
            "rug": pattern_data.get("rug", 0),
            "moon": pattern_data.get("moon", 0),
            "dead": pattern_data.get("dead", 0)
        }
    }

def get_top_patterns(limit=10):
    """
    Returns the most profitable historically seen chart patterns.
    """
    data = load_chart_memory()
    patterns = []
    for p, v in data.items():
        score = (
            v.get("profit", 0) * 2 +
            v.get("moon", 0) * 3 -
            v.get("loss", 0) * 2 -
            v.get("rug", 0) * 4 -
            v.get("dead", 0)
        )
        patterns.append((p, score, v.get("last_seen")))

    patterns.sort(key=lambda x: x[1], reverse=True)
    return patterns[:limit]
