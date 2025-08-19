import json
import os
from collections import Counter, defaultdict

SIGNAL_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/signal_patterns.json"

def load_signal_patterns():
    if not os.path.exists(SIGNAL_FILE):
        return {}
    try:
        with open(SIGNAL_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_signal_patterns(data):
    try:
        with open(SIGNAL_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def record_token_signals(token_address: str, outcome: str, signals: dict):
    """
    Called after evaluation to log signal traits that were present for this token.

    outcome: "profit", "loss", "rug", "dead", "moon"
    signals: {
        "lp_status": "locked",
        "creator": "9x9EXxx",
        "sniper_overlap": True,
        "whales": True,
        "bundle": False
    }
    """
    data = load_signal_patterns()
    for key, value in signals.items():
        str_val = str(value).lower()
        if key not in data:
            data[key] = {}
        if str_val not in data[key]:
            data[key][str_val] = {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0}
        if outcome in data[key][str_val]:
            data[key][str_val][outcome] += 1
    save_signal_patterns(data)

def get_signal_summary():
    """
    Returns a summary of win/loss/rug counts by signal trait.
    """
    return load_signal_patterns()
