# === /strategy_deviation_logger.py ===

import json
import os
from datetime import datetime

BASELINE_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/baseline_scores.json"
DEVIATION_LOG = "/home/ubuntu/nyx/runtime/memory/strategy/strategy_deviation_log.json"
MAX_LOG = 300

def load_baselines():
    if not os.path.exists(BASELINE_FILE):
        return {}
    try:
        with open(BASELINE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_baselines(data):
    try:
        with open(BASELINE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def log_deviation(token_address: str, current_score: float):
    baselines = load_baselines()
    baseline_score = baselines.get(token_address)

    deviation_log = []
    if os.path.exists(DEVIATION_LOG):
        try:
            with open(DEVIATION_LOG, "r") as f:
                deviation_log = json.load(f)
        except Exception:
            deviation_log = []

    if baseline_score is None:
        # First entry
        baselines[token_address] = current_score
        save_baselines(baselines)
        return

    delta = round(current_score - baseline_score, 2)

    deviation_log.append({
        "token": token_address,
        "baseline": baseline_score,
        "current": current_score,
        "delta": delta,
        "timestamp": datetime.utcnow().isoformat()
    })

    if len(deviation_log) > MAX_LOG:
        deviation_log = deviation_log[-MAX_LOG:]

    try:
        with open(DEVIATION_LOG, "w") as f:
            json.dump(deviation_log, f, indent=2)
    except Exception:
        pass
