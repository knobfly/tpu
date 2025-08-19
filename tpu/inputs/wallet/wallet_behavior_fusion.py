import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

from inputs.onchain.firehose.wallet_insight import get_wallet_activity_from_firehose
from memory.wallet_cluster_memory import add_wallet_to_cluster, get_cluster
from strategy.reinforcement_tracker import get_wallet_result_score
from utils.logger import log_event
from utils.service_status import update_status

CACHE_FILE = "/home/ubuntu/nyx/runtime/logs/wallet_behavior_cache.json"
CACHE_EXPIRY_MINUTES = 15

_wallet_cache = {}


def _load_cache():
    global _wallet_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                _wallet_cache = json.load(f)
        except Exception as e:
            logging.warning(f"[WalletBehaviorFusion] Failed to load cache: {e}")
            _wallet_cache = {}
    else:
        _wallet_cache = {}


def _save_cache():
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(_wallet_cache, f, indent=2)
    except Exception as e:
        logging.warning(f"[WalletBehaviorFusion] Failed to save cache: {e}")


def _is_cache_valid(wallet: str) -> bool:
    entry = _wallet_cache.get(wallet, {})
    if not entry:
        return False
    ts = entry.get("timestamp")
    if not ts:
        return False
    try:
        ts_dt = datetime.fromisoformat(ts)
        return datetime.utcnow() - ts_dt < timedelta(minutes=CACHE_EXPIRY_MINUTES)
    except Exception:
        return False


def analyze_wallet_behavior(wallet: str) -> dict:
    """
    Fetch wallet behavior signals from Firehose and reinforcement history.
    Cache results to reduce repeated lookups.
    """
    update_status("wallet_behavior_fusion")

    if _is_cache_valid(wallet):
        return _wallet_cache[wallet]["data"]

    try:
        firehose_data = get_wallet_activity_from_firehose(wallet)
        reinforcement_score = get_wallet_result_score(wallet)
        cluster = get_cluster(wallet)

        signals = {
            "wallet": wallet,
            "tx_count": firehose_data.get("tx_count", 0),
            "avg_tx_size": firehose_data.get("avg_tx_size", 0),
            "reputation_score": reinforcement_score,
            "cluster": cluster or "none",
            "recent_tokens": firehose_data.get("recent_tokens", []),
            "timestamp": datetime.utcnow().isoformat()
        }

        # Add to cluster if behavior suggests grouping
        if firehose_data.get("is_sniper"):
            add_wallet_to_cluster("snipers", wallet)

        _wallet_cache[wallet] = {"data": signals, "timestamp": datetime.utcnow().isoformat()}
        _save_cache()

        return signals
    except Exception as e:
        logging.error(f"[WalletBehaviorFusion] Failed to analyze wallet {wallet}: {e}")
        return {}


def bulk_analyze_wallets(wallets: list[str]) -> dict:
    """
    Analyze multiple wallets and return a dict of wallet: behavior_data.
    """
    result = {}
    for w in wallets:
        result[w] = analyze_wallet_behavior(w)
    return result


def score_wallet_influence(wallets: list[str]) -> float:
    """
    Aggregate influence score of wallets (used for token scoring).
    Combines reputation, transaction frequency, and cluster presence.
    """
    total_score = 0
    count = 0
    for w in wallets:
        data = analyze_wallet_behavior(w)
        rep = data.get("reputation_score", 0)
        tx_count = data.get("tx_count", 0)
        total_score += rep + (0.1 * tx_count)
        count += 1

    return round(total_score / count, 2) if count else 0


def clear_wallet_cache():
    global _wallet_cache
    _wallet_cache = {}
    _save_cache()
    log_event("[WalletBehaviorFusion] Cache cleared.")
