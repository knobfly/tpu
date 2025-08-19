import json
import logging
import os
import time
from typing import Dict, List

FAILURE_FILE = "/home/ubuntu/nyx/runtime/logs/recent_failures.json"
MAX_ENTRIES = 50  # Rolling log

def log_failed_trade(token_address: str, reason: str, tx_hash: str = "", strategy: str = ""):
    """
    Log a failed trade to the rolling failure file.
    """
    if not os.path.exists("logs"):
        os.makedirs("logs")

    entry = {
        "token": token_address,
        "reason": reason,
        "tx": tx_hash,
        "strategy": strategy,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    }

    data = []
    try:
        if os.path.exists(FAILURE_FILE):
            with open(FAILURE_FILE, "r") as f:
                data = json.load(f)
    except Exception as e:
        logging.warning(f"[FailureLog] Could not load previous failures: {e}")

    data.append(entry)
    data = data[-MAX_ENTRIES:]  # Keep only latest N

    try:
        with open(FAILURE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"[FailureLog] Failed to save: {e}")


def get_recent_failures(limit=10) -> List[Dict]:
    """
    Retrieve the most recent failed trades for reporting.
    """
    try:
        if not os.path.exists(FAILURE_FILE):
            return []
        with open(FAILURE_FILE, "r") as f:
            return json.load(f)[-limit:]
    except Exception as e:
        logging.error(f"[FailureLog] Failed to load recent failures: {e}")
        return []
