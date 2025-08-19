import logging
import os

from utils.file_utils import safe_load_json, safe_save_json

BASE_PATH = "/home/ubuntu/nyx/runtime/memory/strategy/"
MEMORY_FILES = {
    "reasoning_weights": "reasoning_weights.json",
    "reasoning_memory": "reasoning_memory.json",
    "signal_patterns": "signal_patterns.json",
    "outcome_predictions": "outcome_predictions.json",
}

LIMITS = {
    "reasoning_weights": 1000,
    "reasoning_memory": 300,
    "signal_patterns": 300,
    "outcome_predictions": 250,
}

def trim_strategy_memory() -> dict:
    """
    Trims each strategy memory file to a safe max size.
    Returns stats on trimming results.
    """
    stats = {}

    for key, filename in MEMORY_FILES.items():
        path = os.path.join(BASE_PATH, filename)
        data = safe_load_json(path, default={})
        max_items = LIMITS.get(key, 500)

        if isinstance(data, dict) and len(data) > max_items:
            trimmed = dict(list(data.items())[-max_items:])
            safe_save_json(path, trimmed)
            stats[key] = {"total": len(data), "trimmed": len(data) - max_items}
        elif isinstance(data, list) and len(data) > max_items:
            trimmed = data[-max_items:]
            safe_save_json(path, trimmed)
            stats[key] = {"total": len(data), "trimmed": len(data) - max_items}
        else:
            stats[key] = {"total": len(data), "trimmed": 0}

    return stats
