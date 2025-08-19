# === /wallet_reinforcement_engine.py ===

import logging

from strategy.wallet_cluster_memory import update_wallet_outcome
from utils.logger import log_event

NEGATIVE_OUTCOMES = {"rug", "dead", "loss"}
POSITIVE_OUTCOMES = {"moon", "profit"}

def reinforce_wallets(trade_result: dict):
    """
    Reinforce memory of wallets involved in a trade outcome.
    
    Applies penalties or boosts to wallet clusters and cabals.
    """
    address = trade_result.get("token_address")
    outcome = trade_result.get("outcome")
    involved_wallets = trade_result.get("wallets", [])
    reasoning = trade_result.get("reasoning", [])
    
    if not address or not outcome or not involved_wallets:
        return

    tag = "cabal" if "cabal" in reasoning else "whale"

    for wallet in involved_wallets:
        result = update_wallet_outcome(wallet, outcome, tag)
        log_event(f"[WalletReinforce] {wallet} -> {outcome} via {tag}: {result}")
        logging.info(f"[WalletReinforce] Memory update for wallet {wallet}: {result}")
