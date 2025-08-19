import json
import os
from collections import Counter, deque
from datetime import datetime, timedelta

SUMMARY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/recent_results.json"
MAX_HISTORY = 100

def load_recent_results():
    if not os.path.exists(SUMMARY_FILE):
        return deque(maxlen=MAX_HISTORY)

    try:
        with open(SUMMARY_FILE, "r") as f:
            data = json.load(f)
            return deque(data, maxlen=MAX_HISTORY)
    except Exception:
        return deque(maxlen=MAX_HISTORY)

def save_recent_results(results):
    try:
        with open(SUMMARY_FILE, "w") as f:
            json.dump(list(results), f, indent=2)
    except Exception:
        pass

def log_result(token_address: str, outcome: str):
    """
    Logs a new result (e.g., profit, rug, moon, loss).
    """
    results = load_recent_results()
    results.append({
        "token": token_address,
        "outcome": outcome,
        "timestamp": datetime.utcnow().isoformat()
    })
    save_recent_results(results)

def get_summary():
    """
    Returns a snapshot of recent reinforcement performance:
        - win/loss/rug ratios
        - current streak
        - trend direction
    """
    results = load_recent_results()
    outcomes = [r["outcome"] for r in results]

    counts = Counter(outcomes)
    total = sum(counts.values())
    last_result = outcomes[-1] if outcomes else None

    # === Streaks
    streak_type = None
    streak_count = 0
    for outcome in reversed(outcomes):
        if streak_type is None:
            streak_type = outcome
            streak_count = 1
        elif outcome == streak_type:
            streak_count += 1
        else:
            break

    return {
        "total": total,
        "counts": dict(counts),
        "last": last_result,
        "streak": {
            "type": streak_type,
            "count": streak_count
        }
    }
