# modules/strategy_decay.py

import json
import logging
import os
from datetime import datetime, timedelta

from utils.service_status import update_status

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/strategy_memory.json"
DECAY_INTERVAL_DAYS = 7
DECAY_FACTOR = 0.9  # Retain 90% of score per decay cycle

def decay_strategy_scores():
    if not os.path.exists(MEMORY_FILE):
        return

    try:
        with open(MEMORY_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        logging.warning(f"[StrategyDecay] Failed to load memory: {e}")
        return

    last_decay_file = "strategy_decay_timestamp.txt"
    now = datetime.utcnow()

    try:
        if os.path.exists(last_decay_file):
            with open(last_decay_file, "r") as f:
                last_decay = datetime.fromisoformat(f.read().strip())
            if now - last_decay < timedelta(days=DECAY_INTERVAL_DAYS):
                return
    except Exception:
        pass  # If broken, just continue

    # Apply decay
    for strat, stats in data.items():
        for key in ["wins", "losses", "rugs"]:
            stats[key] = int(stats.get(key, 0) * DECAY_FACTOR)

    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
        with open(last_decay_file, "w") as f:
            f.write(now.isoformat())
        logging.info("📉 Strategy performance decayed over time.")
        update_status("strategy_decay")
    except Exception as e:
        logging.warning(f"[StrategyDecay] Failed to save decayed memory: {e}")
