import json
import os
from collections import defaultdict
from datetime import datetime

SIGNAL_MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/signal_history.json"
MAX_HISTORY_PER_TOKEN = 50

def load_signal_memory():
    if not os.path.exists(SIGNAL_MEMORY_FILE):
        return {}
    try:
        with open(SIGNAL_MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_signal_memory(data):
    try:
        with open(SIGNAL_MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def record_signal(token_address: str, score: float, reasoning: list[str], mode: str):
    """
    Logs a signal score with associated reasoning for a token.
    Used for pattern learning, streak tracking, and weighting.
    """
    memory = load_signal_memory()
    history = memory.get(token_address, [])

    history.append({
        "score": score,
        "reasoning": reasoning,
        "mode": mode,
        "timestamp": datetime.utcnow().isoformat()
    })

    # Limit history per token
    memory[token_address] = history[-MAX_HISTORY_PER_TOKEN:]
    save_signal_memory(memory)

def get_reasoning_weights(token_address: str) -> dict:
    """
    Returns weighted frequency of reasoning used in decisions.
    """
    memory = load_signal_memory()
    history = memory.get(token_address, [])

    weights = defaultdict(int)
    for entry in history:
        for reason in entry.get("reasoning", []):
            weights[reason] += 1

    return dict(weights)

def get_token_signal_streak(token_address: str) -> dict:
    """
    Returns the most recent mode-based streak for this token.
    """
    memory = load_signal_memory()
    history = memory.get(token_address, [])

    if not history:
        return {"mode": None, "count": 0}

    last_mode = history[-1]["mode"]
    count = 0

    for entry in reversed(history):
        if entry["mode"] == last_mode:
            count += 1
        else:
            break

    return {"mode": last_mode, "count": count}
