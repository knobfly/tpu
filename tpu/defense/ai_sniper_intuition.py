# modules/ai_sniper_intuition.py

import logging
import re

from memory.meta_tag_tracker import get_meta_trend_boost
from memory.token_memory_index import get_recent_sniper_patterns
from utils.clean_text import clean_name
from utils.logger import log_event

# === Pattern-based intuition keywords ===
HIGH_CONF_KEYWORDS = [
    "elon", "doge", "pepe", "meme", "moon", "pump", "ai", "chatgpt", "gpt",
    "shiba", "ape", "stonk", "frog", "wizard", "sol"
]

RUGGY_KEYWORDS = [
    "airdrop", "presale", "fairlaunch", "rebase", "lottery", "ponzi", "giveaway"
]

# === Recent performance memory weight ===
RECENT_GAIN_BOOST = 5
RECENT_LOSS_PENALTY = -5


def apply_sniper_intuition(token_address: str, keywords: list[str]) -> tuple[int, str]:
    """
    Returns (boost, reason) based on name patterns, meta trends, and memory.
    """
    try:
        name_string = " ".join(keywords).lower()
        clean_string = clean_name(name_string)
        boost = 0
        reason = ""

        # === Pattern Match Boost ===
        for key in HIGH_CONF_KEYWORDS:
            if key in clean_string:
                boost += 3
                reason += f"Keyword match '{key}', "
        for key in RUGGY_KEYWORDS:
            if key in clean_string:
                boost -= 4
                reason += f"Ruggy keyword '{key}', "

        # === Meta Trend Boost ===
        meta_boost = get_meta_trend_boost(keywords)
        if meta_boost:
            boost += meta_boost
            reason += f"Meta-trend boost ({meta_boost}), "

        # === Recent Performance Memory ===
        sniper_patterns = get_recent_sniper_patterns(limit=50)
        if sniper_patterns.get("profitable_hits", 0) > sniper_patterns.get("losses", 0):
            boost += RECENT_GAIN_BOOST
            reason += "Recent snipes profitable, "
        elif sniper_patterns.get("losses", 0) > sniper_patterns.get("profitable_hits", 0):
            boost += RECENT_LOSS_PENALTY
            reason += "Recent snipes losing, "

        if reason.endswith(", "):
            reason = reason[:-2]

        if boost != 0:
            log_event(f"[Intuition] Token {token_address} intuition boost={boost} ({reason})")

        return boost, reason or "No intuition signal"

    except Exception as e:
        logging.warning(f"[Intuition] Failed for {token_address}: {e}")
        return 0, "intuition_error"
