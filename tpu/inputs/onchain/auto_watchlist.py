import logging
from typing import Dict

from librarian.data_librarian import librarian

TRUST_SCORE_THRESHOLD = 5.0
MIN_LIVE_HOLDERS = 2

def should_watch_token(context: Dict) -> bool:
    dev_profile = context.get("dev_profile", {})
    tags = dev_profile.get("tags", [])
    score = dev_profile.get("score", 0)

    if "junkfarmer" in tags:
        return False

    if score < TRUST_SCORE_THRESHOLD:
        return False

    top_wallets = context.get("top_wallets", [])
    if len(top_wallets) < MIN_LIVE_HOLDERS:
        return False

    return True

def add_to_watchlist(mint: str, context: Dict):
    if should_watch_token(context):
        logging.info(f"[AutoWatchlist] ✅ Added to watchlist: {mint}")
        librarian.add_to_watchlist(mint, context)
    else:
        logging.info(f"[AutoWatchlist] ❌ Rejected from watchlist: {mint}")
