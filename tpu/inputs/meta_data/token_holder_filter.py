# modules/token_holder_filter.py

import logging
from typing import Optional

from utils.token_utils import get_token_holder_distribution

# Thresholds and weights
MAX_WHALE_PERCENT = 5.0     # No holder should control more than 5%
MIN_UNIQUE_HOLDERS = 20     # Minimum unique holders to be considered safe
HOLDER_WEIGHT = 1.2         # Weight applied to final score boost or penalty

# === Main scoring function ===
def get_holder_distribution_score(token_address: str) -> float:
    try:
        dist = get_token_holder_distribution(token_address)
        if not dist:
            return 0.0

        unique_holders = dist.get("unique_holders", 0)
        top_holder_pct = dist.get("top_holder_pct", 100.0)

        score = 50

        # Reward distribution
        if unique_holders >= MIN_UNIQUE_HOLDERS:
            score += (unique_holders - MIN_UNIQUE_HOLDERS) * 0.5

        # Penalize whale control
        if top_holder_pct > MAX_WHALE_PERCENT:
            excess = top_holder_pct - MAX_WHALE_PERCENT
            score -= excess * 2

        return max(0.0, min(100.0, score * HOLDER_WEIGHT))

    except Exception as e:
        logging.warning(f"[HolderFilter] Error checking holders for {token_address}: {e}")
        return 0.0

# === Optional label ===
def label_holder_risk(token_address: str) -> Optional[str]:
    try:
        dist = get_token_holder_distribution(token_address)
        if not dist:
            return None

        top = dist.get("top_holder_pct", 100.0)
        if top > 25.0:
            return f"🐋 Whale risk: top holder {top:.1f}%"
        if top > 10.0:
            return f"⚠️ Centralized: top holder {top:.1f}%"
        return None
    except:
        return None

def get_holder_classification(holder_dist: dict) -> str:
    """
    Classify token based on holder distribution.

    Args:
        holder_dist (dict): Output from get_token_holder_distribution()

    Returns:
        str: One of ['whale-heavy', 'retail-heavy', 'sniper-heavy', 'balanced']
    """
    if not holder_dist:
        return "unknown"

    whales = holder_dist.get("whales", 0)
    retail = holder_dist.get("retail", 0)
    snipers = holder_dist.get("snipers", 0)
    top_10_share = holder_dist.get("top_10_share", 0)

    if whales >= 5 or top_10_share > 0.75:
        return "whale-heavy"
    if snipers >= 5:
        return "sniper-heavy"
    if retail > 20 and top_10_share < 0.5:
        return "retail-heavy"
    return "balanced"
