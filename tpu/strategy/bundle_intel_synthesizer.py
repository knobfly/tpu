# === /bundle_intel_synthesizer.py ===

import json
import os
from collections import defaultdict

from memory.token_outcome_memory import get_token_outcome
from utils.logger import log_event

BUNDLE_MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/bundle_traits.json"

def load_bundle_memory():
    if not os.path.exists(BUNDLE_MEMORY_FILE):
        return {}
    try:
        with open(BUNDLE_MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_bundle_memory(data):
    try:
        with open(BUNDLE_MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def update_bundle_traits(bundle_id: str, token_address: str, traits: list[str]):
    """
    Associates traits and outcomes with a bundle/project group.
    """
    if not bundle_id or not traits:
        return

    outcome = get_token_outcome(token_address)
    if not outcome:
        return

    data = load_bundle_memory()
    if bundle_id not in data:
        data[bundle_id] = {}

    for trait in traits:
        trait = trait.strip().lower()
        if trait not in data[bundle_id]:
            data[bundle_id][trait] = defaultdict(int)
        data[bundle_id][trait][outcome] += 1

    save_bundle_memory(data)
    log_event(f"🔗 Bundle trait update: {bundle_id} → {traits} ({outcome})")

def get_bundle_risk(bundle_id: str) -> dict:
    """
    Scores bundle risk based on previous outcomes and traits.
    Returns:
        {
            "score": int,
            "level": "low" | "moderate" | "high" | "severe",
            "top_flags": [str, ...]
        }
    """
    data = load_bundle_memory()
    if bundle_id not in data:
        return {"score": 0, "level": "low", "top_flags": []}

    score = 0
    top_flags = []
    for trait, outcomes in data[bundle_id].items():
        trait_score = (
            outcomes.get("rug", 0) * 4 +
            outcomes.get("dead", 0) * 2 +
            outcomes.get("loss", 0) * 1 -
            outcomes.get("profit", 0) * 1 -
            outcomes.get("moon", 0) * 2
        )
        score += trait_score
        if trait_score >= 3:
            top_flags.append(f"{trait} (+{trait_score})")

    level = "low"
    if score > 15:
        level = "severe"
    elif score > 10:
        level = "high"
    elif score > 5:
        level = "moderate"

    return {
        "score": score,
        "level": level,
        "top_flags": top_flags[:5]
    }
