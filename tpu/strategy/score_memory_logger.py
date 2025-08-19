# === sniper_bot/modules/strategy/score_memory_logger.py ===

import json
import os
from collections import defaultdict
from datetime import datetime

SCORE_LOG_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/score_memory.json"
MAX_ENTRIES = 50  # per token

def load_score_memory():
    if not os.path.exists(SCORE_LOG_FILE):
        return {}
    try:
        with open(SCORE_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_score_memory(memory):
    try:
        with open(SCORE_LOG_FILE, "w") as f:
            json.dump(memory, f, indent=2)
    except Exception:
        pass

def log_score_event(token_address: str, score: float, action: str, reasoning: list, mode="trade"):
    memory = load_score_memory()
    history = memory.get(token_address, [])

    history.append({
        "timestamp": datetime.utcnow().isoformat(),
        "score": score,
        "action": action,
        "mode": mode,
        "reasoning": reasoning
    })

    # Cap size
    if len(history) > MAX_ENTRIES:
        history = history[-MAX_ENTRIES:]

    memory[token_address] = history
    save_score_memory(memory)

def get_score_history(token_address: str):
    memory = load_score_memory()
    return memory.get(token_address, [])
