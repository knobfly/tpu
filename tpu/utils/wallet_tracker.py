# modules/wallet_tracker.py

import json
import logging
import os
from typing import Any, Dict, List, Optional

from utils.rpc_loader import get_active_rpc
from utils.service_status import update_status

TRACKER_FILE = "/home/ubuntu/nyx/runtime/data/tracked_wallets.json"
TAG_FILE_PATH = "/home/ubuntu/nyx/runtime/data/wallet_tags.json"
WALLET_RUG_PATH = "/home/ubuntu/nyx/runtime/data/wallet_rug_history.json"
TRADE_HISTORY_PATH = "/home/ubuntu/nyx/runtime/data/wallet_trade_history.json"

DEFAULT_REPUTATION = 50
MAX_REPUTATION = 100
MIN_REPUTATION = 0

# === Load Tracked Wallet Data ===
def _load() -> Dict[str, dict]:
    if not os.path.exists(TRACKER_FILE):
        return {}
    try:
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[WalletTracker] Load failed: {e}")
        return {}

# === Save Tracked Wallet Data ===
def _save(data: Dict[str, dict]):
    try:
        with open(TRACKER_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[WalletTracker] Save failed: {e}")

# Load wallet tags if they exist
def load_wallet_tags() -> dict:
    if os.path.exists(TAG_FILE_PATH):
        try:
            with open(TAG_FILE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

wallet_tags = load_wallet_tags()

def get_wallet_tag(wallet: str) -> Optional[str]:
    """
    Returns a known tag (e.g., "rugger", "alpha_sniper", "influencer") for a wallet address.
    """
    return wallet_tags.get(wallet.lower())

def tag_wallet(wallet: str, tag: str) -> None:
    """
    Add or update a tag for a wallet address and persist it.
    """
    wallet = wallet.lower()
    wallet_tags[wallet] = tag
    save_wallet_tags()

def save_wallet_tags() -> None:
    try:
        with open(TAG_FILE_PATH, "w") as f:
            json.dump(wallet_tags, f, indent=2)
    except Exception as e:
        print(f"❌ Failed to save wallet tags: {e}")

# Load wallet rug count (number of past rugs detected)
def get_wallet_rug_history(wallet: str) -> int:
    wallet = wallet.lower()
    if not os.path.exists(WALLET_RUG_PATH):
        return 0
    try:
        with open(WALLET_RUG_PATH, "r") as f:
            history = json.load(f)
        return int(history.get(wallet, 0))
    except Exception:
        return 0

# === Trade History Loader ===
def get_wallet_trade_history(wallet: str) -> Optional[Dict[str, Any]]:
    wallet = wallet.lower()
    if not os.path.exists(TRADE_HISTORY_PATH):
        return None
    try:
        with open(TRADE_HISTORY_PATH, "r") as f:
            data = json.load(f)
        return data.get(wallet)
    except Exception:
        return None

# === Register New Token Activity from Wallet ===
def register_wallet_activity(wallet: str, token: str, outcome: str = None):
    update_status("wallet_tracker")
    data = _load()
    wallet = wallet.lower()
    if wallet not in data:
        data[wallet] = {
            "tokens": [],
            "reputation": DEFAULT_REPUTATION,
            "history": {}
        }

    if token not in data[wallet]["tokens"]:
        data[wallet]["tokens"].append(token)

    if outcome:
        data[wallet]["history"][token] = outcome
        _adjust_reputation(data[wallet], outcome)

    _save(data)

# === Performance Score Calculator ===
def get_wallet_performance_score(wallet: str) -> float:
    """
    Simple weighted score based on trade count, profit, and rug count.
    Score = (profit in SOL) * 10 + trade_count * 1.5 - rug_count * 5
    """
    wallet = wallet.lower()
    history = get_wallet_trade_history(wallet) or {}
    rug_count = get_wallet_rug_history(wallet)

    trades = history.get("trades", 0)
    profit = history.get("total_profit_sol", 0)

    score = (profit * 10) + (trades * 1.5) - (rug_count * 5)
    return round(score, 2)

# === Record Trade Outcome for a Wallet ===
def record_trade_outcome(wallet: str, token: str, outcome: str):
    update_status("wallet_tracker")
    data = _load()
    wallet = wallet.lower()
    if wallet not in data:
        logging.warning(f"[WalletTracker] Cannot record outcome — wallet {wallet} not found.")
        return

    data[wallet]["history"][token] = outcome
    _adjust_reputation(data[wallet], outcome)
    _save(data)

# === Adjust Wallet Reputation Based on Outcome ===
def _adjust_reputation(wallet_data: dict, outcome: str):
    rep = wallet_data.get("reputation", DEFAULT_REPUTATION)
    if outcome == "win":
        rep = min(rep + 5, MAX_REPUTATION)
    elif outcome == "rug":
        rep = max(rep - 10, MIN_REPUTATION)
    elif outcome == "dead":
        rep = max(rep - 3, MIN_REPUTATION)
    else:
        rep = max(rep - 1, MIN_REPUTATION)
    wallet_data["reputation"] = rep

# === Get Reputation Score of a Wallet ===
def get_wallet_tracker_score(wallet: str) -> float:
    data = _load()
    wallet = wallet.lower()
    return float(data.get(wallet, {}).get("reputation", DEFAULT_REPUTATION))

# === Return All Tracked Wallets ===
def get_all_tracked_wallets() -> Dict[str, dict]:
    return _load()

# === Get Token History (win/loss/rug) of Wallet ===
def get_wallet_token_history(wallet: str) -> Dict[str, str]:
    data = _load()
    wallet = wallet.lower()
    return data.get(wallet, {}).get("history", {})

# === Get Tokens a Wallet Has Interacted With ===
def get_tokens_by_wallet(wallet: str) -> List[str]:
    data = _load()
    wallet = wallet.lower()
    return data.get(wallet, {}).get("tokens", [])

# === Utility: Has Wallet Seen This Token Before? ===
def has_wallet_seen_token(wallet: str, token: str) -> bool:
    data = _load()
    wallet = wallet.lower()
    return token in data.get(wallet, {}).get("tokens", [])

# === Fetch Wallet Token Activity (RPC version) ===
async def get_wallet_token_activity(wallet_address: str, limit: int = 25) -> list:
    """
    Fetches recent token activity for a wallet.
    Uses Solana RPC instead of Helius.
    """
    try:
        txns = await get_active_rpc(
            f"/addresses/{wallet_address}/transactions?limit={limit}",
            priority="normal"
        )
        activity = []
        for tx in txns:
            for event in tx.get("events", {}).get("tokenTransfers", []):
                token_address = event.get("mint")
                amount = float(event.get("amount", 0))
                side = "buy" if amount > 0 else "sell"
                timestamp = tx.get("timestamp", tx.get("blockTime"))
                activity.append({
                    "token": token_address,
                    "side": side,
                    "amount": abs(amount),
                    "timestamp": timestamp,
                    "tx": tx.get("signature"),
                })
        return activity

    except Exception as e:
        logging.warning(f"[WalletTracker] Failed to fetch activity for {wallet_address}: {e}")
        return []

# === Return recent smart wallet token buys (for overlap detection) ===
def get_recent_smart_buys(limit: int = 25) -> List[str]:
    from strategy.strategy_memory import get_tagged_tokens

    smart_tags = ["trusted_wallet", "wallet_cluster", "ai_snipe_overlap"]
    seen = set()
    results = []

    for tag in smart_tags:
        tokens = get_tagged_tokens(tag)
        for token in tokens:
            if token not in seen:
                seen.add(token)
                results.append(token)
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    return results

wallet_outcomes = {
    "get_wallet_tracker_score": get_wallet_tracker_score,
    "get_wallet_token_history": get_wallet_token_history,
    "get_all_tracked_wallets": get_all_tracked_wallets,
    "get_tokens_by_wallet": get_tokens_by_wallet,
    "has_wallet_seen_token": has_wallet_seen_token,
}

from librarian.data_librarian import librarian

librarian.register("wallet_tracker", wallet_outcomes)
