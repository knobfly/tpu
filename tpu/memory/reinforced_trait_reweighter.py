# === /reinforced_trait_reweighter.py ===
import json
import os
from collections import defaultdict

REINFORCED_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/reinforced_trait_weights.json"

def load_reinforced_trait_weights():
    if not os.path.exists(REINFORCED_FILE):
        return {}
    try:
        with open(REINFORCED_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_reinforced_trait_weights(data):
    try:
        with open(REINFORCED_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except:
        pass

def update_reinforced_trait_weights(reasoning_tags: list, outcome: str):
    """
    Updates long-term outcome weights for traits like 'whale', 'celeb', 'bundle', etc.
    """
    data = load_reinforced_trait_weights()

    for tag in reasoning_tags:
        if tag not in data:
            data[tag] = defaultdict(int)
        data[tag][outcome] = data[tag].get(outcome, 0) + 1

    save_reinforced_trait_weights(data)

def get_reinforced_trait_weights():
    return load_reinforced_trait_weights()

# memory/reinforced_trait_reweighter.py

def get_trait_confidence_boost(traits: dict) -> float:
    """
    Applies a boost score based on presence and quality of known high-confidence traits.
    Used in reinforcement learning to reward good trades.

    Args:
        traits (dict): Token trait dictionary, e.g. {'anti_mev': True, 'meta_tag': 'ai', 'launch_type': 'fair', ...}

    Returns:
        float: Boost score, capped between 0 and 20.
    """
    boost_score = 0
    trait_weights = {
        "anti_mev": 5,
        "verified_dev": 5,
        "launch_type": {"fair": 4, "stealth": 2},
        "meta_tag": {"ai": 5, "infra": 4, "narrative": 3},
        "has_website": 2,
        "is_multisig": 2,
        "audit_passed": 3,
    }

    for key, weight in trait_weights.items():
        val = traits.get(key)

        if isinstance(weight, dict):
            if val in weight:
                boost_score += weight[val]
        elif val:
            boost_score += weight

    return min(boost_score, 20)
