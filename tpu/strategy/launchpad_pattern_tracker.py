# === sniper_bot/modules/strategy/launchpad_pattern_tracker.py ===

import json
import os
from collections import defaultdict

from memory.token_outcome_memory import get_token_outcome
from utils.logger import log_event

LAUNCHPAD_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/launchpad_patterns.json"

def load_launchpad_memory():
    if not os.path.exists(LAUNCHPAD_FILE):
        return {}
    try:
        with open(LAUNCHPAD_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_launchpad_memory(data):
    try:
        with open(LAUNCHPAD_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def update_launchpad_profile(launchpad: str, token_address: str):
    """
    Adds a token result to a launchpad's profile for trend memory.
    """
    if not launchpad:
        return

    outcome = get_token_outcome(token_address)
    if not outcome:
        return

    data = load_launchpad_memory()
    if launchpad not in data:
        data[launchpad] = defaultdict(int)

    data[launchpad][outcome] += 1
    save_launchpad_memory(data)
    log_event(f"🚀 Launchpad pattern updated: {launchpad} → {outcome}")

def get_launchpad_reputation(launchpad: str) -> dict:
    """
    Returns launchpad performance score and classification.
    """
    data = load_launchpad_memory()
    outcomes = data.get(launchpad, {})

    score = (
        outcomes.get("moon", 0) * 4 +
        outcomes.get("profit", 0) * 2 -
        outcomes.get("loss", 0) * 2 -
        outcomes.get("rug", 0) * 5 -
        outcomes.get("dead", 0) * 3
    )

    level = "neutral"
    if score >= 10:
        level = "strong"
    elif score <= -10:
        level = "risky"
    elif score <= -20:
        level = "blacklisted"

    return {
        "score": score,
        "level": level,
        "summary": outcomes
    }
