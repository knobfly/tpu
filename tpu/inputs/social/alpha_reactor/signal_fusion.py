import logging
from datetime import datetime, timedelta

from memory.signal_memory_index import get_recent_signal_activity
from utils.logger import log_event
from utils.service_status import update_status

update_status("alpha_signal_fusion")

_signal_cache = []

def process_alpha_signals(source: str, data: dict):
    """
    Unified signal fusion entry point.
    source: "telegram" or "x"
    data: {token, sentiment, volume, wallets, confidence}
    """
    try:
        signal = {
            "timestamp": datetime.utcnow().isoformat(),
            "source": source,
            "token": data.get("token"),
            "sentiment": data.get("sentiment", 0.0),
            "wallets": data.get("wallets", []),
            "volume": data.get("volume", 0),
            "confidence": round(data.get("confidence", 0.0), 2),
        }
        _signal_cache.append(signal)
        log_event(f"[AlphaFusion] {source.upper()} → {signal['token']} | sentiment={signal['sentiment']} | conf={signal['confidence']}")
        if len(_signal_cache) > 100:
            _signal_cache.pop(0)
    except Exception as e:
        logging.warning(f"[AlphaFusion] Failed to process alpha signal: {e}")

def get_recent_signals(limit=10):
    return _signal_cache[-limit:]

def get_signal_reactivity_score(token_address: str, window_minutes: int = 60) -> int:
    """
    Returns a reactivity score (0–100) for how much social signal activity 
    occurred around this token in the given time window.

    Combines Telegram mentions, X posts, and influencer overlap.
    """
    try:
        signal_data = get_recent_signal_activity(token_address, minutes=window_minutes)
        if not signal_data:
            return 0

        tg_mentions = signal_data.get("telegram_mentions", 0)
        x_mentions = signal_data.get("x_mentions", 0)
        influencer_flags = signal_data.get("influencer_overlap", 0)

        score = (
            min(tg_mentions * 2, 30) +
            min(x_mentions * 2, 30) +
            min(influencer_flags * 10, 40)
        )

        return min(score, 100)

    except Exception as e:
        logging.warning(f"[SignalFusion] Failed reactivity score for {token_address}: {e}")
        return 0
