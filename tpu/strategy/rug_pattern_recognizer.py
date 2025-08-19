# === sniper_bot/modules/strategy/rug_pattern_recognizer.py ===

import json
import os
from collections import defaultdict

from memory.token_outcome_memory import get_token_memory
from utils.logger import log_event

RUG_PATTERN_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/rug_patterns.json"

def load_rug_patterns():
    if not os.path.exists(RUG_PATTERN_FILE):
        return {}
    try:
        with open(RUG_PATTERN_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_rug_patterns(data):
    try:
        with open(RUG_PATTERN_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def update_rug_traits(traits: list[str], token_address: str):
    """
    Reinforces pattern memory if token rugged.
    Example traits: ["mint_function_found", "locked_lp=false", "same_creator_as_X", "suspicious_cluster"]
    """
    outcome = get_token_memory(token_address)
    if not traits or outcome != "rug":
        return

    data = load_rug_patterns()
    for trait in traits:
        trait = trait.lower().strip()
        data[trait] = data.get(trait, 0) + 1

    save_rug_patterns(data)
    log_event(f"💀 Rug pattern updated: {traits}")

def check_rug_risk(traits: list[str]) -> dict:
    """
    Calculates rug risk score based on pattern memory.
    """
    data = load_rug_patterns()
    score = 0
    risky = []

    for t in traits:
        count = data.get(t.lower().strip(), 0)
        if count >= 3:
            score += count
            risky.append(t)

    risk_level = "low"
    if score >= 15:
        risk_level = "extreme"
    elif score >= 8:
        risk_level = "high"
    elif score >= 4:
        risk_level = "moderate"

    return {
        "score": score,
        "level": risk_level,
        "risky_traits": risky
    }
