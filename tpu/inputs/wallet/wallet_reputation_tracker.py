# /wallet_reputation_tracker.py
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

from core.live_config import config
from special.insight_logger import log_scanner_insight
from utils.logger import log_event

# === Memory Store ===
wallet_stats = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "last_seen": None,
    "reputation": 0.0,
    "traits": set(),
})

_wallet_reputation = {}
DECAY_DAYS = 10
WIN_BOOST = 3.5
LOSS_PENALTY = 2.0

REPUTATION_LOG = "/home/ubuntu/nyx/runtime/memory/wallets/wallet_rug_scores.json"

_wallet_rug_scores = {}  # address -> {score, last_seen}

def load_wallet_scores():
    global _wallet_rug_scores
    if os.path.exists(REPUTATION_LOG):
        try:
            with open(REPUTATION_LOG, "r") as f:
                _wallet_rug_scores = json.load(f)
        except Exception as e:
            logging.warning(f"[Reputation] Failed to load rug scores: {e}")
            _wallet_rug_scores = {}

def save_wallet_scores():
    try:
        with open(REPUTATION_LOG, "w") as f:
            json.dump(_wallet_rug_scores, f, indent=2)
    except Exception as e:
        logging.warning(f"[Reputation] Failed to save rug scores: {e}")

def update_wallet_rug_score(wallet: str, score_delta: float):
    now = time.time()
    score_entry = _wallet_rug_scores.get(wallet, {"score": 0.0, "last_seen": now})
    score_entry["score"] += score_delta
    score_entry["last_seen"] = now
    _wallet_rug_scores[wallet] = score_entry
    save_wallet_scores()

def get_wallet_rug_score(wallet: str) -> float:
    """
    Returns the cumulative rug score for a wallet.
    Higher values indicate more rug-prone behavior.
    """
    if wallet in _wallet_rug_scores:
        return _wallet_rug_scores[wallet]["score"]
    return 0.0

def get_recent_rug_wallets(since_minutes=1440) -> list[str]:
    cutoff = time.time() - (since_minutes * 60)
    return [w for w, v in _wallet_rug_scores.items() if v.get("last_seen", 0) >= cutoff]


# === Update reputation based on trade result ===
def record_wallet_trade(wallet: str, token: str, result: str):
    stats = wallet_stats[wallet]
    stats["last_seen"] = datetime.utcnow()

    if result == "win":
        stats["wins"] += 1
        stats["reputation"] += WIN_BOOST
        stats["traits"].add("profit_chaser")
    elif result == "loss":
        stats["losses"] += 1
        stats["reputation"] -= LOSS_PENALTY
        stats["traits"].add("paperhands")

    stats["reputation"] = max(-20.0, min(100.0, stats["reputation"]))
    log_event(f"[Reputation] {wallet[:6]}...: {result} → rep={stats['reputation']:.1f}")

    log_scanner_insight("wallet_reputation", {
        "wallet": wallet,
        "reputation": stats["reputation"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "traits": list(stats["traits"])
    })

def get_wallet_reputation_score(address: str) -> float:
    """
    Returns the reputation score (0.0 - 1.0) for a given wallet address.
    Defaults to 0.5 if unknown.
    """
    try:
        return _wallet_reputation.get(address, 0.5)
    except Exception as e:
        logging.warning(f"[WalletReputation] Failed to get reputation for {address}: {e}")
        return 0.5

def set_wallet_reputation(address: str, score: float):
    """
    Set/update the reputation score for a wallet address.
    """
    try:
        _wallet_reputation[address] = max(0.0, min(1.0, score))
        logging.info(f"[WalletReputation] {address} score set to {_wallet_reputation[address]:.2f}")
    except Exception as e:
        logging.warning(f"[WalletReputation] Failed to set reputation for {address}: {e}")

# === Lookup score for wallet ===
def get_wallet_reputation(wallet: str) -> float:
    return wallet_stats[wallet]["reputation"]

# === Classify wallet category for display/scoring ===
def classify_wallet(wallet: str) -> str:
    rep = get_wallet_reputation(wallet)
    if rep >= 20:
        return "trusted"
    elif rep <= -10:
        return "suspicious"
    return "neutral"

# === Decay old data ===
def decay_wallet_reputations():
    cutoff = datetime.utcnow() - timedelta(days=DECAY_DAYS)
    for wallet, stats in list(wallet_stats.items()):
        if stats["last_seen"] and stats["last_seen"] < cutoff:
            log_event(f"[Decay] Removing old wallet: {wallet[:6]}...")
            del wallet_stats[wallet]


# Auto-load on import
load_wallet_scores()
