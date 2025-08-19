# modules/firehose/firehose_cache.py

import logging
import time
from collections import defaultdict, deque
from typing import Any, Dict, List

# Structure: {wallet_address: deque([event_dict, ...])}
_recent_wallet_events: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
_wallet_balances: Dict[str, float] = {}  # Latest known SOL balance per wallet

CACHE_EXPIRY_SECONDS = 120  # Ignore events older than this


# === Aliases for compatibility ===

def get_wallet_balance_from_firehose(wallet: str) -> float:
    """
    Compatibility alias for get_cached_balance().
    """
    return get_cached_balance(wallet)

def get_recent_txns_from_firehose(wallet: str, limit: int = 10) -> list:
    """
    Compatibility alias for get_recent_wallet_events().
    """
    return get_recent_wallet_events(wallet, limit)


def update_wallet_event(wallet: str, event: dict):
    """
    Called by the stream_router or firehose_listener to track wallet activity in-memory.
    """
    if not wallet or not isinstance(event, dict):
        return

    event["_ts"] = time.time()
    _recent_wallet_events[wallet].appendleft(event)

    # Update balance if present
    if "sol_balance" in event:
        _wallet_balances[wallet] = event["sol_balance"]


def get_recent_wallet_events(wallet: str, limit: int = 10) -> List[dict]:
    """
    Return recent events for a wallet from firehose memory cache.
    """
    now = time.time()
    events = _recent_wallet_events.get(wallet, [])
    return [e for e in list(events)[:limit] if now - e.get("_ts", 0) <= CACHE_EXPIRY_SECONDS]


def get_cached_balance(wallet: str) -> float:
    """
    Return the most recent known balance for a wallet, or -1 if not available.
    """
    return _wallet_balances.get(wallet, -1)


def clear_wallet_cache(wallet: str):
    """
    Clear memory cache for a specific wallet.
    """
    _recent_wallet_events.pop(wallet, None)
    _wallet_balances.pop(wallet, None)


def clear_all_cache():
    """
    Clear all in-memory firehose wallet tracking.
    """
    _recent_wallet_events.clear()
    _wallet_balances.clear()


def debug_wallet_cache() -> Dict[str, Any]:
    """
    Return basic stats for debug purposes.
    """
    return {
        "total_wallets_tracked": len(_recent_wallet_events),
        "total_balances": len(_wallet_balances),
        "sample_wallets": list(_recent_wallet_events.keys())[:5],
    }
