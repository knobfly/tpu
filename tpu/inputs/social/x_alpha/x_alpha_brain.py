import json
import os
from datetime import datetime, timedelta
from typing import Dict, List

from core.live_config import config
from inputs.social.x_alpha.alpha_account_tracker import alpha_account_tracker
from inputs.social.x_alpha.x_signal_logger import log_x_signal
from inputs.wallet.wallet_behavior_analyzer import get_recent_wallet_activity
from strategy.strategy_memory import tag_token_result
from utils.logger import log_event

X_MENTIONS_LOG = "/home/ubuntu/nyx/runtime/logs/x_mentions.json"

class XAlphaBrain:
    def __init__(self):
        self.replies_enabled = config.get("x_autopost_enabled", True)
        self.quotes_enabled = config.get("x_quote_mode", True)

    def analyze_post(self, handle: str, token: str, tweet_text: str) -> str:
        try:
            wallet_score = get_recent_wallet_activity(token)
        except Exception as e:
            log_event(f"⚠️ Wallet activity fetch failed for ${token}: {e}")
            wallet_score = 0.0

        try:
            account_score = alpha_account_tracker.get_score(handle)
        except Exception as e:
            log_event(f"⚠️ Alpha account score fetch failed for @{handle}: {e}")
            account_score = 0

        log_event(f"🧠 X Alpha Brain — {handle} on ${token}: Wallet={wallet_score:.2f}, Account={account_score}")

        if wallet_score > 0.6 and account_score > 70:
            log_x_signal(token, handle, "buy", confidence="high")
            tag_token_result(token, "x_high_confidence")
            return "quote" if self.quotes_enabled else "watch"
        elif wallet_score > 0.3 or account_score > 50:
            log_x_signal(token, handle, "watch", confidence="medium")
            return "watch"
        else:
            log_x_signal(token, handle, "ignore", confidence="low")
            return "ignore"

def load_x_mentions_log() -> List[Dict]:
    if not os.path.exists(X_MENTIONS_LOG):
        return []
    try:
        with open(X_MENTIONS_LOG, "r") as f:
            return json.load(f)
    except Exception:
        return []

def get_recent_x_mentions(minutes: int = 30) -> List[Dict]:
    """
    Returns recent X (Twitter) token mentions within the last `minutes`.
    Each entry in the log should be a dict like: { "token": ..., "timestamp": ..., "text": ..., ... }
    """
    all_mentions = load_x_mentions_log()
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)

    recent = []
    for entry in all_mentions:
        try:
            ts = entry.get("timestamp")
            if not ts:
                continue
            ts_dt = datetime.fromisoformat(ts)
            if ts_dt >= cutoff:
                recent.append(entry)
        except Exception:
            continue

    return recent


x_alpha_brain = XAlphaBrain()
