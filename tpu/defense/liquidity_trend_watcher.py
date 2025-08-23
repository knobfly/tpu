import json
import logging
import os
import time
from typing import Any, Dict, Optional
from core.live_config import config as live_config
from utils.token_utils import get_token_liquidity_data

LP_FILE = "/home/ubuntu/nyx/runtime/data/lp_trends.json"

LIQUIDITY_TREND_WINDOW = 5 * 60  # 5 minutes
LIQUIDITY_DROP_THRESHOLD = 0.35  # 35% drop = suspicious

_liquidity_cache = {}  # token -> {timestamp, liquidity}

def load_trends() -> Dict[str, Any]:
    if not os.path.exists(LP_FILE):
        return {}
    with open(LP_FILE, "r") as f:
        return json.load(f)

def save_trends(data: Dict[str, Any]):
    with open(LP_FILE, "w") as f:
        json.dump(data, f, indent=2)

def record_liquidity(token_address: str, current_lp: float):
    """
    Save a timestamped LP amount for a token.
    """
    trends = load_trends()
    now = int(time.time())

    history = trends.get(token_address, [])
    history.append({"time": now, "lp": current_lp})

    # Keep only last 6 entries (~6 minutes if checked every 60s)
    trends[token_address] = history[-6:]
    save_trends(trends)

def get_trend_score(token_address: str) -> Optional[int]:
    """
    Returns a score based on LP trend:
    +1 if LP grew, 0 if stable, -2 if dropping
    """
    trends = load_trends()
    history = trends.get(token_address, [])
    if len(history) < 2:
        return 0  # Not enough data

    lp_start = history[0]["lp"]
    lp_end = history[-1]["lp"]

    if lp_end > lp_start * 1.2:
        return +1  # Healthy growth
    elif lp_end < lp_start * 0.8:
        return -2  # Big drop — possible LP drain
    else:
        return 0  # Stable

def detect_sudden_drain(token_address: str) -> bool:
    """
    True if LP dropped by >50% between last 2 points.
    """
    trends = load_trends()
    history = trends.get(token_address, [])
    if len(history) < 2:
        return False

    lp_now = history[-1]["lp"]
    lp_prev = history[-2]["lp"]
    return lp_now < lp_prev * 0.5

def detect_liquidity_drain(token_address: str) -> bool:
    """
    Detects if a token is undergoing a rapid liquidity drain.
    Returns True if a significant drop is detected within a short window.
    """
    if not live_config.get("sniper_defender_enabled", True):
        return False

    now = time.time()
    try:
        liq = get_token_liquidity_data(token_address)
        if not liq or liq <= 0:
            return False

        # Store if first time
        if token_address not in _liquidity_cache:
            _liquidity_cache[token_address] = {
                "timestamp": now,
                "liquidity": liq
            }
            return False

        prev = _liquidity_cache[token_address]
        age = now - prev["timestamp"]

        if age > LIQUIDITY_TREND_WINDOW:
            # Too old, reset tracking
            _liquidity_cache[token_address] = {
                "timestamp": now,
                "liquidity": liq
            }
            return False

        drop_ratio = (prev["liquidity"] - liq) / prev["liquidity"]
        if drop_ratio >= LIQUIDITY_DROP_THRESHOLD:
            logging.warning(f"[LiquidityDrain] ⚠️ {token_address} dropped {drop_ratio*100:.1f}% in {age:.0f}s")
            return True

        return False
    except Exception as e:
        logging.error(f"[LiquidityDrain] Error checking {token_address}: {e}")
        return False
