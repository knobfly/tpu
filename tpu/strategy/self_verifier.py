# === /self_verifier.py ===

from memory.token_outcome_memory import get_token_outcome
from utils.logger import log_event

RUGGED_OUTCOMES = {"rug", "dead", "loss"}

def verify_token_entry(token_address: str, final_score: float, min_score: float = 50) -> dict:
    """
    Verifies that the token is not previously rugged, dead, or invalid before scoring.
    Returns:
        {
            "allowed": True/False,
            "reason": "rugged before" / "score too low" / ...
        }
    """
    outcome = get_token_outcome(token_address)
    if outcome in RUGGED_OUTCOMES:
        log_event(f"🛑 Blocked token re-entry: {token_address} was previously {outcome}")
        return {"allowed": False, "reason": f"token was previously {outcome}"}

    if final_score < min_score:
        log_event(f"⚠️ Token score {final_score} < min {min_score}: {token_address}")
        return {"allowed": False, "reason": "score too low"}

    return {"allowed": True, "reason": "verified"}


def verify_evaluation(output: dict) -> dict:
    """
    Runs sanity checks on the final evaluation output to catch contradictions or red flags.
    Adds a 'verification' field to indicate confidence level or error state.
    """
    score = output.get("final_score", 0)
    action = output.get("action", "ignore")
    reasoning = output.get("reasoning", [])
    strategy = output.get("strategy", {})

    result = {
        "confidence": "normal",
        "issues": []
    }

    # === Basic sanity check
    if score == 0 and action != "ignore":
        result["confidence"] = "low"
        result["issues"].append("Score 0 but action is not ignore")

    if score > 80 and action == "ignore":
        result["confidence"] = "low"
        result["issues"].append("High score but ignored")

    if "blacklisted" in reasoning and action != "ignore":
        result["confidence"] = "low"
        result["issues"].append("Blacklisted but not ignored")

    if strategy.get("confidence_boost", 0) > 5 and score < 10:
        result["confidence"] = "low"
        result["issues"].append("Strategy boost high, but score still low")

    # === Final override
    if result["issues"]:
        result["status"] = "⚠️ Verification failed"
    else:
        result["status"] = "✅ Verified"

    return result
