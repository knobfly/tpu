# /x_alpha/wallet_reaction_watcher.py

from inputs.wallet.wallet_behavior_analyzer import get_wallet_clusters
from memory.wallet_memory_index import get_behavior
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import tag_token_result
from utils.logger import log_event

CONFIDENCE_THRESHOLD = 0.7

def check_wallet_response(token):
    """
    Analyze smart wallet behavior for the given token.
    Returns a float score (0.0 to 1.0) based on smart wallet ratio.
    Includes logging, learning, and insight hooks.
    """

    wallets = get_wallet_clusters(token)
    if not wallets:
        log_event(f"[WalletReaction] ❌ No wallet data found for {token}")
        return 0.0

    smart_entries = [w for w in wallets if w.get("score", 0) >= CONFIDENCE_THRESHOLD]
    confidence_score = len(smart_entries) / len(wallets)

    log_event(f"[WalletReaction] 📊 Token ${token}: {len(smart_entries)} smart out of {len(wallets)} → Score: {round(confidence_score, 2)}")

    # 🧠 Tagging + Cortex-compatible hooks
    if smart_entries:
        tag_token_result(token, "wallet_reacted")
        get_behavior([w["address"] for w in smart_entries], context=token)

    # 📰 Log to insight system
    log_scanner_insight(
        token=token,
        source="wallet_reaction",
        sentiment=round(confidence_score, 2),
        volume=len(smart_entries),
        result="wallet_reacted"
    )

    return round(confidence_score, 2)
