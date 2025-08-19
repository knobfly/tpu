import json
import logging
import os
from datetime import datetime

RECENT_RESULTS_FILE = "/home/ubuntu/nyx/runtime/logs/recent_results.json"
MAX_RESULTS = 200


def _load_results():
    if not os.path.exists(RECENT_RESULTS_FILE):
        return []
    try:
        with open(RECENT_RESULTS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[RecentResultTracker] Failed to load: {e}")
        return []


def _save_results(results):
    try:
        with open(RECENT_RESULTS_FILE, "w") as f:
            json.dump(results, f, indent=2)
    except Exception as e:
        logging.warning(f"[RecentResultTracker] Failed to save: {e}")


def log_result(token: str, outcome: str, score: float):
    """
    Logs the result of a token's trade (win/loss/rug).
    """
    results = _load_results()
    entry = {
        "token": token,
        "outcome": outcome,
        "score": score,
        "timestamp": datetime.utcnow().isoformat()
    }
    results.append(entry)
    if len(results) > MAX_RESULTS:
        results = results[-MAX_RESULTS:]
    _save_results(results)


def get_recent_results(limit=20):
    return _load_results()[-limit:]
