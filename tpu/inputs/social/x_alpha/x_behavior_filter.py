# /x_alpha/x_behavior_filter.py

import re
import time

from core.live_config import config

# Fallback whitelist if config doesn't provide one
DEFAULT_WHITELISTED_HANDLES = {
    "KateMillerGems", "Candy_Gems", "TonyDialgaX", "cryptodevorah",
    "RomuloNevesOf", "adanelguerrero", "GinoAssereto", "lourdesanchezok"
}

# === Backoff Settings ===
BACKOFF_LIMIT = 3
BACKOFF_DURATION = 60 * 60  # 1 hour
backoff_state = {
    "failures": 0,
    "disabled_until": 0
}


def is_safe_to_reply(handle: str, text: str) -> bool:
    """
    Filters out unwanted or suspicious tweets.
    Only allows replies to trusted users with real signal content.
    """
    whitelisted = set(config.get("x_whitelisted_handles", [])) or DEFAULT_WHITELISTED_HANDLES
    if handle not in whitelisted:
        return False

    text_lower = text.lower()
    if any(bad_word in text_lower for bad_word in ["airdrop", "retweet", "giveaway", "bot", "contest", "like and follow"]):
        return False
    if "$" not in text and "sol" not in text_lower:
        return False

    return True


def is_english_text(text: str) -> bool:
    """
    Simple heuristic to check if tweet is mostly English.
    """
    try:
        english_chars = len(re.findall(r"[a-zA-Z]", text))
        total_chars = len(text)
        if total_chars == 0:
            return False
        return english_chars / total_chars > 0.75
    except Exception:
        return False


def check_backoff() -> bool:
    """
    Returns True if X posting is currently disabled due to repeated failures.
    """
    return time.time() < backoff_state.get("disabled_until", 0)


def register_post_failure():
    """
    Logs a failed X post attempt and activates cooldown if too many occur.
    """
    backoff_state["failures"] += 1
    if backoff_state["failures"] >= BACKOFF_LIMIT:
        backoff_state["disabled_until"] = time.time() + BACKOFF_DURATION
        backoff_state["failures"] = 0
        from utils.logger import log_event
        log_event("⚠️ X posting disabled for 1 hour due to repeated failures")


def reset_backoff():
    """
    Clears the failure counter and cooldown.
    """
    backoff_state["failures"] = 0
    backoff_state["disabled_until"] = 0


