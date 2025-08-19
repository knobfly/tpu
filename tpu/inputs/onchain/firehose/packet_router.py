import time
from collections import defaultdict

# Track liquidity events by token address
_token_liquidity_events = defaultdict(list)  # token -> [timestamps]
_token_last_liquidity = {}  # token -> last_seen_time


def record_liquidity_event(token_address: str, timestamp: float = None):
    """Called when a liquidity add/remove tx is seen for a token."""
    now = timestamp or time.time()
    _token_liquidity_events[token_address].append(now)
    _token_last_liquidity[token_address] = now


def get_last_liquidity_seen(token_address: str) -> float:
    """Returns timestamp of the last known liquidity event for a token."""
    return _token_last_liquidity.get(token_address, 0.0)


def get_liquidity_event_count(token_address: str, within_seconds: int = 3600) -> int:
    """Returns how many liquidity events happened within the time window."""
    now = time.time()
    recent = [
        ts for ts in _token_liquidity_events.get(token_address, [])
        if ts >= now - within_seconds
    ]
    return len(recent)


def reset_liquidity_tracking(token_address: str):
    """Wipe liquidity tracking for a given token (if delisted or dead)."""
    _token_liquidity_events.pop(token_address, None)
    _token_last_liquidity.pop(token_address, None)
