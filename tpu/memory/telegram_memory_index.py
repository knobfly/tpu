# memory/telegram_memory_index.py

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List

TELEGRAM_CHAT_LOG = "/home/ubuntu/nyx/runtime/logs/telegram_chat.json"  # adjust path if needed

def load_telegram_chat_log() -> List[Dict]:
    if not os.path.exists(TELEGRAM_CHAT_LOG):
        return []
    try:
        with open(TELEGRAM_CHAT_LOG, "r") as f:
            return json.load(f)
    except Exception:
        return []

def get_recent_group_messages(minutes: int = 30) -> List[Dict]:
    """
    Returns a list of recent group messages within the last `minutes`.
    Each message is a dict like { "text": ..., "timestamp": ..., "group": ... }
    """
    all_messages = load_telegram_chat_log()
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)

    recent = []
    for msg in all_messages:
        try:
            ts = msg.get("timestamp")
            if not ts:
                continue
            msg_time = datetime.fromisoformat(ts)
            if msg_time >= cutoff:
                recent.append(msg)
        except Exception:
            continue

    return recent
