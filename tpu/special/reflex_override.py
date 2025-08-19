# === /reflex_override.py ===
import logging

from memory.reinforced_trait_reweighter import get_reinforced_trait_weights
from memory.token_outcome_memory import get_token_outcome_memory

OVERRIDE_THRESHOLD = 0.7  # confidence to trigger override

def evaluate_reflex_override(token_data: dict) -> dict:
    """
    Checks whether Nyx's memory strongly contradicts her current instinct.

    Returns:
        {
            "override": True/False,
            "reason": str,
            "adjustment": str (e.g. "downgrade", "avoid", "flag"),
        }
    """
    if not token_data:
        return {"override": False}

    reasoning = token_data.get("reasoning", [])
    token_addr = token_data.get("token_address", "")
    name = token_data.get("token", "unknown")

    trait_weights = get_reinforced_trait_weights()
    outcome_memory = get_token_outcome_memory()

    # === Memory signals token has strong rug pattern?
    rug_score = 0
    for trait in reasoning:
        weight = trait_weights.get(trait, {})
        rug_score += weight.get("rug", 0)

    past_outcome = outcome_memory.get(token_addr, {}).get("final_outcome", "")

    if rug_score >= OVERRIDE_THRESHOLD or past_outcome == "rug":
        logging.warning(f"[ReflexOverride] Overriding instinct for {name}: memory suggests rug")
        return {
            "override": True,
            "reason": "Rug traits detected or past rug memory",
            "adjustment": "avoid"
        }

    return {"override": False}
