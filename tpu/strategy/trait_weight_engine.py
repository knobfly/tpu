import json
import os
from collections import defaultdict

TRAIT_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/trait_weights.json"

def load_trait_weights():
    if not os.path.exists(TRAIT_FILE):
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "dead": 0, "moon": 0})
    try:
        with open(TRAIT_FILE, "r") as f:
            data = json.load(f)
            return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "dead": 0, "moon": 0}, data)
    except Exception:
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "dead": 0, "moon": 0})

def save_trait_weights(data):
    try:
        with open(TRAIT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def update_trait_weights(traits: list[str], outcome: str):
    """
    Reinforces trait weights based on outcome.
    Example traits: ["pepe", "elon", "vibe", "locked_lp"]
    """
    if not outcome or not traits:
        return

    data = load_trait_weights()
    for trait in traits:
        trait = trait.lower().strip()
        if outcome in data[trait]:
            data[trait][outcome] += 1
    save_trait_weights(data)

def get_trait_score(trait: str) -> int:
    """
    Calculates weighted intuition score for a trait based on past outcomes.
    """
    data = load_trait_weights()
    trait = trait.lower().strip()
    if trait not in data:
        return 0
    scores = data[trait]
    return (
        scores.get("profit", 0) * 2 +
        scores.get("moon", 0) * 3 -
        scores.get("loss", 0) * 2 -
        scores.get("rug", 0) * 4 -
        scores.get("dead", 0) * 1
    )

def apply_trait_scores(keywords: list[str]) -> tuple[int, list[str]]:
    """
    Returns total trait-based intuition boost and reasons.
    """
    boost = 0
    reasons = []
    for k in keywords:
        score = get_trait_score(k)
        if score > 0:
            boost += min(score, 10)  # cap per trait
            reasons.append(f"{k}: +{score}")
        elif score < 0:
            boost += max(score, -5)  # negative weight
            reasons.append(f"{k}: {score}")
    return boost, reasons
