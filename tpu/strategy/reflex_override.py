import logging


def check_reflex_override(token_data: dict) -> dict:
    """
    Emergency override handler for rare but dangerous edge cases.
    Returns a dict like {"override": True, "reason": "Fake LP lock"} or {"override": False}
    """

    token_address = token_data.get("token_address", "")
    metadata = token_data.get("metadata", {})
    insights = token_data.get("insights", {})

    override_reasons = []
    triggered = False

    # === Fake LP Lock detection (e.g., lock_time is in the past)
    if insights.get("lp_status") == "locked" and insights.get("lp_lock_expires", 0) < 300:
        override_reasons.append("Fake LP lock")
        triggered = True

    # === Suspicious wallet swarm (too many early buyers)
    buyers = insights.get("wallet", {}).get("buyers", 0)
    if buyers > 100:
        override_reasons.append("Suspicious wallet swarm")
        triggered = True

    # === Extreme chart spike (honeypot signature)
    if insights.get("chart", {}).get("chart_score", 0) >= 18 and insights.get("txn", {}).get("sniper_pressure", 0) <= 1:
        override_reasons.append("Fake volume spike")
        triggered = True

    if triggered:
        logging.warning(f"[ReflexOverride] 🚨 Emergency override triggered: {override_reasons}")
        return {"override": True, "reason": " | ".join(override_reasons)}

    return {"override": False}
