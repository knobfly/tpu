import json
import os

from utils.logger import log_event

MEMORY_PATHS = [
    "/home/ubuntu/nyx/runtime/memory/strategy/score_memory.json",
    "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_weights.json",
    "/home/ubuntu/nyx/runtime/memory/strategy/outcome_predictions.json",
    "/home/ubuntu/nyx/runtime/memory/mentions.json",
    "/home/ubuntu/nyx/runtime/memory/tokens.json",
    "/home/ubuntu/nyx/runtime/memory/wallets.json",
    "/home/ubuntu/nyx/runtime/memory/groups.json",
    "/home/ubuntu/nyx/runtime/memory/insights.json"
]

def validate_json_file(path: str) -> bool:
    if not os.path.exists(path):
        log_event(f"⚠️ Missing memory file: {path}")
        return False
    try:
        with open(path, "r") as f:
            json.load(f)
        return True
    except Exception as e:
        log_event(f"❌ Corrupted memory file: {path} — {e}")
        return False

def audit_memory_integrity() -> dict:
    report = {}
    for file in MEMORY_PATHS:
        valid = validate_json_file(file)
        report[file] = "✅" if valid else "❌"
    return report
