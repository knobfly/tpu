import json
import os
from datetime import datetime, timedelta

SIGNAL_LOG_PATH = "/home/ubuntu/nyx/runtime/logs/signal_logs.jsonl"

def parse_timestamp(ts: str) -> datetime:
    return datetime.fromisoformat(ts) if "T" in ts else datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

def get_recent_signal_activity(token_address: str, minutes: int = 60) -> dict:
    """
    Scans recent social signal logs and aggregates activity for a specific token.
    Returns counts of mentions across telegram, x, and influencer tags.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    result = {
        "telegram_mentions": 0,
        "x_mentions": 0,
        "influencer_overlap": 0,
    }

    if not os.path.exists(SIGNAL_LOG_PATH):
        return result

    try:
        with open(SIGNAL_LOG_PATH, "r") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    ts = parse_timestamp(data.get("timestamp", ""))
                    if ts < cutoff:
                        continue

                    if data.get("token") != token_address:
                        continue

                    source = data.get("source", "").lower()
                    if "telegram" in source:
                        result["telegram_mentions"] += 1
                    elif "x_post" in source:
                        result["x_mentions"] += 1
                    if data.get("influencer_tagged"):
                        result["influencer_overlap"] += 1

                except Exception:
                    continue
    except Exception:
        pass

    return result
