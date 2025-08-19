import logging

from memory.shared_runtime import shared_memory
from strategy.reinforcement_tracker import get_wallet_result_score
from utils.logger import log_event
from utils.service_status import update_status

update_status("wallet_insight")

def analyze_wallet_activity(wallet: str, lookback_minutes: int = 30) -> dict:
    """
    Analyze wallet behavior using real-time firehose transaction memory.
    - Counts buys/sells within the last X minutes.
    - Evaluates PnL reputation using reinforcement memory.
    - Detects if wallet is alpha/sniper wallet (fast entries at launches).
    """
    try:
        txs = shared_memory.get("recent_transactions", [])
        filtered = [tx for tx in txs if tx.get("signer") == wallet]

        recent_buys = [tx for tx in filtered if tx.get("action") == "buy"]
        recent_sells = [tx for tx in filtered if tx.get("action") == "sell"]

        # Reputation score from reinforcement tracker
        reputation = get_wallet_result_score(wallet)

        alpha_trades = [
            tx for tx in recent_buys if tx.get("time_since_launch", 9999) < 5
        ]

        return {
            "wallet": wallet,
            "recent_buys": len(recent_buys),
            "recent_sells": len(recent_sells),
            "alpha_trades": len(alpha_trades),
            "reputation_score": reputation,
            "last_activity": filtered[-1]["timestamp"] if filtered else None,
        }
    except Exception as e:
        logging.warning(f"[WalletInsight] Firehose wallet analysis failed for {wallet}: {e}")
        return {
            "wallet": wallet,
            "recent_buys": 0,
            "recent_sells": 0,
            "alpha_trades": 0,
            "reputation_score": 0,
            "last_activity": None,
        }

def is_known_influencer(wallet: str) -> bool:
    """
    Returns True if the wallet has a high reputation or alpha score.
    Customize this with your own rules.
    """
    profile = get_wallet_profile(wallet)
    return (
        profile.get("reputation_score", 0) >= 7
        or profile.get("alpha_trades", 0) >= 3
    )


def get_wallet_profile(wallet: str) -> dict:
    """
    Retrieves a full wallet profile from live firehose memory.
    Combines:
    - On-chain recent activity (buys, sells, snipes)
    - Reinforcement memory (win/loss/rug history)
    - Token interactions (last seen tokens)
    """
    try:
        txs = shared_memory.get("recent_transactions", [])
        tokens_interacted = [
            tx.get("token") for tx in txs if tx.get("signer") == wallet
        ]
        tokens_interacted = list(set(tokens_interacted[-10:]))

        base = analyze_wallet_activity(wallet)
        base["recent_tokens"] = tokens_interacted

        log_event(f"[WalletInsight] Profiled wallet {wallet}: {base}")
        return base
    except Exception as e:
        logging.warning(f"[WalletInsight] Failed to build profile for {wallet}: {e}")
        return {"wallet": wallet, "error": str(e)}

get_wallet_tags = get_wallet_profile
