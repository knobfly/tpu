import json
import logging
import os
from datetime import datetime

from utils.service_status import update_status

KEYWORD_FILE = "/home/ubuntu/nyx/runtime/logs/ai_keywords.json"
DECAY_LOG_FILE = "/home/ubuntu/nyx/runtime/logs/keyword_decay_log.json"
DECAY_THRESHOLD = 0  # Remove keywords with count <= this

_decay_log = []

def prune_stale_keywords():
    global _decay_log
    update_status("auto_refiner")

    if not os.path.exists(KEYWORD_FILE):
        logging.info("[AutoRefiner] No keyword file found.")
        return

    try:
        with open(KEYWORD_FILE, "r") as f:
            keywords = json.load(f)
    except Exception as e:
        logging.warning(f"[AutoRefiner] Failed to load keyword file: {e}")
        return

    removed = []
    for word in list(keywords):
        if keywords[word].get("count", 0) <= DECAY_THRESHOLD:
            removed.append(word)
            del keywords[word]

    if removed:
        logging.info(f"🧹 Pruned stale keywords: {removed}")
        _decay_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "removed": removed
        })

        try:
            with open(DECAY_LOG_FILE, "w") as f:
                json.dump(_decay_log[-20:], f, indent=2)
        except Exception as e:
            logging.warning(f"[AutoRefiner] Failed to save decay log: {e}")
    else:
        logging.info("[AutoRefiner] ✅ No keywords pruned this cycle")

    try:
        with open(KEYWORD_FILE, "w") as f:
            json.dump(keywords, f, indent=2)
    except Exception as e:
        logging.warning(f"[AutoRefiner] Failed to write keywords file: {e}")

def get_keyword_decay_log():
    global _decay_log
    if not _decay_log and os.path.exists(DECAY_LOG_FILE):
        try:
            with open(DECAY_LOG_FILE, "r") as f:
                _decay_log = json.load(f)
        except Exception as e:
            logging.warning(f"[AutoRefiner] Failed to load decay log: {e}")
            _decay_log = []
    return _decay_log

