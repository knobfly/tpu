def assess_token_risk(token_context: dict) -> str:
    """
    Returns a risk level: low / medium / high / extreme

    Based on:
    - LP unlock
    - Bundle presence
    - Sniper-only wallets
    - Honeypot similarity
    - Repeated creator
    - Lack of organic buyers
    """
    score = 0
    meta = token_context.get("metadata", {})
    insights = token_context.get("insights", {})
    wallets = insights.get("wallet", {})
    txn = insights.get("txn", {})

    if token_context.get("lp_status") == "unlocked":
        score += 2

    if token_context.get("signals", {}).get("bundle"):
        score += 3

    if wallets.get("overlap_snipers", 0) >= 3:
        score += 2

    if wallets.get("whales_present") and not wallets.get("organic_buyer_ratio", 1) > 0.4:
        score += 2

    if txn.get("honeypot_similarity", 0) >= 10:
        score += 3

    if meta.get("creator", "").startswith("9x9e") or meta.get("creator_reputation") == "low":
        score += 1

    # === Risk grade logic
    if score >= 8:
        return "extreme"
    elif score >= 5:
        return "high"
    elif score >= 3:
        return "medium"
    return "low"
