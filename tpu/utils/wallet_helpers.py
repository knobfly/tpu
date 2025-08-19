# modules/utils/wallet_helpers.py

import json
import os
import time
from datetime import datetime, timedelta

from core.live_config import config
from utils.logger import log_event
from utils.wallet_tracker import get_wallet_tracker_score

# === Constants ===
ALPHA_SCORE_THRESHOLD = 60  # Can be adjusted in config if needed
WALLET_HISTORY_DIR = config.get("wallet_history_dir", "/home/ubuntu/nyx/runtime/data/wallet_history")

# === Simulated Runtime Trade Memory ===
recent_wallet_trades = []  # Populated elsewhere at runtime


def get_recent_buys(since_seconds=30):
    cutoff = datetime.utcnow() - timedelta(seconds=since_seconds)
    return [(t["wallet"], t["token"]) for t in recent_wallet_trades if t["timestamp"] > cutoff]


def get_wallet_signal_bonus(token, recent_trades=None):
    recent = recent_trades or recent_wallet_trades
    matching = [t for t in recent if t["token"] == token]
    trust = sum(get_wallet_tracker_score(t["wallet"]) for t in matching)
    bonus = min(trust * 0.1, 3.0)
    return bonus


def get_all_tracked_wallets() -> list:
    if not os.path.exists(WALLET_HISTORY_DIR):
        return []
    return [
        filename.replace(".json", "")
        for filename in os.listdir(WALLET_HISTORY_DIR)
        if filename.endswith(".json")
    ]


def get_wallet_trade_history(wallet_name: str) -> list:
    filepath = os.path.join(WALLET_HISTORY_DIR, f"{wallet_name}.json")
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r") as f:
        return json.load(f)


def get_recent_smart_buys(minutes=3):
    cutoff = time.time() - minutes * 60
    return [
        t["token"]
        for t in recent_wallet_trades
        if t.get("timestamp", 0) > cutoff and is_alpha_wallet(t["wallet"])
    ]


def is_alpha_wallet(wallet_address: str) -> bool:
    trusted = set(config.get("trusted_wallets", []))
    if wallet_address in trusted:
        return True
    score = get_wallet_tracker_score(wallet_address)
    return score >= ALPHA_SCORE_THRESHOLD

def count_unique_buyers(token_context: dict) -> int:
    """
    Returns number of unique buyer wallets.
    """
    buyers = token_context.get("buyers", [])
    return len(set(buyers))
