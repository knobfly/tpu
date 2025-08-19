# modules/x_signal_logger.py

import json
import os
from datetime import datetime, timedelta

from special.insight_logger import log_ai_insight  # ✅ AI brain hook

# === Paths ===
SIGNAL_LOG_PATH = "/home/ubuntu/nyx/runtime/logs/x_signal_log.json"
ACTIVITY_LOG_PATH = "/home/ubuntu/nyx/runtime/x_logs/x_activity_log.json"

# === Limits ===
MAX_SIGNAL_LOGS = 1000
MAX_ACTIVITY_LOGS = 500

# === Log individual tweet/reply/follow/etc. ===
def log_x_action(action_type: str, data: dict):
    """
    Logs X activity (follows, posts, replies) to runtime.
    """
    try:
        os.makedirs(os.path.dirname(ACTIVITY_LOG_PATH), exist_ok=True)

        entry = {
            "type": action_type,  # follow | post | reply
            "data": data,
            "timestamp": datetime.utcnow().isoformat()
        }

        if os.path.exists(ACTIVITY_LOG_PATH):
            with open(ACTIVITY_LOG_PATH, "r") as f:
                logs = json.load(f)
        else:
            logs = []

        logs.append(entry)

        with open(ACTIVITY_LOG_PATH, "w") as f:
            json.dump(logs[-MAX_ACTIVITY_LOGS:], f, indent=2)
    except Exception as e:
        print(f"[XLogger] Failed to log X action: {e}")


# === Retrieve daily log summary ===
def get_x_activity_log(since=None):
    """
    Returns summary of follows, posts, replies in last 24h.
    """
    if not os.path.exists(ACTIVITY_LOG_PATH):
        return {"follows": [], "posts": [], "replies": []}

    try:
        with open(ACTIVITY_LOG_PATH, "r") as f:
            logs = json.load(f)
    except Exception:
        return {"follows": [], "posts": [], "replies": []}

    since = since or (datetime.utcnow() - timedelta(hours=24))
    counts = {"follows": [], "posts": [], "replies": []}

    for entry in logs:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts >= since:
                t = entry["type"]
                if t in counts:
                    counts[t].append(entry)
        except:
            continue

    return counts


# === Log detected token signal via X (used by AI brain/scanners) ===
def log_x_signal(token, source, action, confidence="unknown"):
    """
    Logs an X-based token signal (e.g., tweet mention, reply).
    """
    entry = {
        "time": datetime.utcnow().isoformat(),
        "token": token,
        "source": source,
        "action": action,
        "confidence": confidence
    }

    try:
        if os.path.exists(SIGNAL_LOG_PATH):
            with open(SIGNAL_LOG_PATH, "r") as f:
                existing = json.load(f)
        else:
            existing = []

        existing.append(entry)

        if len(existing) > MAX_SIGNAL_LOGS:
            existing = existing[-MAX_SIGNAL_LOGS:]

        with open(SIGNAL_LOG_PATH, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to log X signal: {e}")

    # Forward to AI insight engine
    try:
        log_ai_insight("x_signal_detected", entry)
    except Exception as e:
        print(f"⚠️ Failed to forward X signal to AI insight logger: {e}")
