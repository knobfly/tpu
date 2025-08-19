# === /wallet_risk_weighter.py ===

import logging

from memory.wallet_cluster_memory import load_wallet_memory


def score_wallet_risk(wallets: list[str]) -> tuple[int, list[str]]:
    """
    Calculates aggregate risk/reward score based on known wallet outcomes.
    
    Returns:
        (net_score, reasoning_list)
    """
    if not wallets:
        return 0, []

    memory = load_wallet_memory()
    score = 0
    reasoning = []

    for wallet in wallets:
        w = wallet.lower().strip()
        outcomes = memory.get(w, {})
        if not outcomes:
            continue

        # Score impact
        rug_score = -4 * outcomes.get("rug", 0)
        dead_score = -2 * outcomes.get("dead", 0)
        loss_score = -1 * outcomes.get("loss", 0)
        profit_score = 2 * outcomes.get("profit", 0)
        moon_score = 3 * outcomes.get("moon", 0)

        wallet_score = rug_score + dead_score + loss_score + profit_score + moon_score
        score += wallet_score

        if wallet_score != 0:
            reasoning.append(f"{wallet[:5]}...: {wallet_score}")

    logging.info(f"[WalletRisk] Composite wallet score: {score} from {len(wallets)} wallets")
    return score, reasoning
