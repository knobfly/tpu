import json
import os
from datetime import datetime

SNAPSHOT_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/trade_snapshots.json"
MAX_SNAPSHOTS = 500

def load_trade_snapshots():
    if not os.path.exists(SNAPSHOT_FILE):
        return []
    try:
        with open(SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_trade_snapshots(data):
    try:
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(data[-MAX_SNAPSHOTS:], f, indent=2)
    except Exception:
        pass

def record_trade_snapshot(result: dict):
    """
    Captures a full snapshot of a trade result for replay and learning.

    result = {
        "token": "LUNA",
        "token_address": "...",
        "final_score": 67.4,
        "reasoning": [...],
        "signals": {...},
        "themes": [...],
        "outcome": "profit",
        "action": "snipe",
        ...
    }
    """
    snapshot = {
        "token": result.get("token"),
        "address": result.get("token_address"),
        "score": result.get("final_score"),
        "reasoning": result.get("reasoning", []),
        "signals": result.get("signals", {}),
        "themes": result.get("themes", []),
        "outcome": result.get("outcome"),
        "action": result.get("action"),
        "timestamp": datetime.utcnow().isoformat()
    }

    data = load_trade_snapshots()
    data.append(snapshot)
    save_trade_snapshots(data)

def summarize_recent_patterns(n=100):
    """
    Returns statistics from the most recent N trade decisions.
    """
    snapshots = load_trade_snapshots()
    stats = {
        "profit": 0,
        "loss": 0,
        "rug": 0,
        "moon": 0,
        "dead": 0,
        "unknown": 0
    }

    for snap in snapshots[-n:]:
        outcome = snap.get("outcome", "unknown")
        stats[outcome] = stats.get(outcome, 0) + 1

    return stats
