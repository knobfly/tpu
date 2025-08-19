import logging
from typing import Dict


def self_check_decision(score: float, action: str, reasoning: list, mode: str) -> Dict[str, str]:
    """
    Performs a lightweight internal reasoning sanity check.

    Flags contradictions or unclear decisions.

    Returns:
        {
            "status": "ok" | "warning" | "fail",
            "message": str
        }
    """

    if score < 10 and action != "ignore":
        return {
            "status": "warning",
            "message": f"⚠️ Score low ({score}) but action={action}. Verify confidence."
        }

    if "honeypot" in " ".join(reasoning).lower() and action == "snipe":
        return {
            "status": "fail",
            "message": "❌ Honeypot concern detected, but snipe action recommended."
        }

    if mode == "trade" and score > 80 and "low buyers" in " ".join(reasoning).lower():
        return {
            "status": "warning",
            "message": "⚠️ High score but flagged low demand. Double-check momentum."
        }

    if len(reasoning) < 2:
        return {
            "status": "warning",
            "message": "⚠️ Weak reasoning: not enough justification for decision."
        }

    return {
        "status": "ok",
        "message": "✅ Decision passed self-check."
    }
