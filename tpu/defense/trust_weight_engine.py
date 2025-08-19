import json
import os

from utils.logger import log_event

TRAIT_FILE = "/home/ubuntu/nyx/runtime/memory/trait_weights.json"
HISTORY_LIMIT = 200

def load_trait_weights():
    if not os.path.exists(TRAIT_FILE):
        return {}
    try:
        with open(TRAIT_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_trait_weights(data):
    try:
        with open(TRAIT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def update_trait_performance(traits: list, outcome: str):
    """
    Given a list of traits and the trade outcome,
    update long-term weight memory to reinforce or penalize.
    """
    weights = load_trait_weights()

    for trait in traits:
        if trait not in weights:
            weights[trait] = {"score": 0, "history": []}

        history = weights[trait]["history"]
        score = weights[trait]["score"]

        delta = 0
        if outcome in ("profit", "moon"):
            delta = +1
        elif outcome in ("loss", "dead"):
            delta = -1
        elif outcome == "rug":
            delta = -3

        weights[trait]["score"] += delta
        history.append({"outcome": outcome, "delta": delta})
        history[:] = history[-HISTORY_LIMIT:]

        log_event(f"[TraitReinforce] {trait} → {delta} ({outcome})")

    save_trait_weights(weights)

def get_trait_score(trait: str) -> int:
    weights = load_trait_weights()
    return weights.get(trait, {}).get("score", 0)
