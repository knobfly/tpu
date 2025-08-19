from core.live_config import config
from strategy.self_verifier import verify_evaluation
from strategy.trait_weight_engine import get_trait_score
from utils.logger import log_event

SOFT_BLOCK_THRESHOLD = -5

def apply_final_overlays(evaluation: dict) -> dict:
    """
    Adjust final decision before execution.
    - Sanity check from self-verifier
    - Trait penalties
    - Global state (e.g., manual cooldowns or kill switch)
    - Final overrides
    """
    token = evaluation.get("token")
    reasoning = evaluation.get("reasoning", [])
    traits = evaluation.get("themes", [])
    original_score = evaluation.get("final_score", 0)

    # === Run verification layer
    verification = verify_evaluation(evaluation)
    evaluation["verification"] = verification
    if verification.get("confidence") == "low":
        evaluation["final_score"] -= 10
        evaluation["verification"]["overlay_penalty"] = "low confidence"

    # === Apply trait weight modifiers
    trait_penalty = sum(get_trait_score(trait) for trait in traits if get_trait_score(trait) < 0)
    if trait_penalty < SOFT_BLOCK_THRESHOLD:
        evaluation["final_score"] += trait_penalty  # reduce score

    # === Check for global kill or manual override
    if config.get("manual_kill") is True:
        log_event(f"🚨 Final kill switch active — skipping {token}")
        evaluation["action"] = "ignore"
        evaluation["kill_reason"] = "manual_kill"

    if evaluation["final_score"] <= 0 and evaluation["action"] != "ignore":
        evaluation["action"] = "ignore"
        evaluation["kill_reason"] = "final_score_zeroed"

    log_event(f"[DecisionOverlay] Adjusted score {original_score} → {evaluation['final_score']} for {token}")
    return evaluation
