import json
import os
from collections import defaultdict
from datetime import datetime

EVAL_LOG_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/evaluation_log.json"

def load_evaluation_log():
    if not os.path.exists(EVAL_LOG_FILE):
        return []
    try:
        with open(EVAL_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_evaluation_log(log_data):
    try:
        with open(EVAL_LOG_FILE, "w") as f:
            json.dump(log_data, f, indent=2)
    except Exception:
        pass

def log_evaluation(score: float, outcome: str, mode: str = "snipe"):
    """
    Log the score and result of an evaluation.
    Useful for learning optimal thresholds and visual heatmaps.
    """
    if score is None or outcome is None:
        return
    log = load_evaluation_log()
    log.append({
        "score": round(score, 2),
        "outcome": outcome,
        "mode": mode,
        "timestamp": datetime.utcnow().isoformat()
    })

    # Trim to last 5000
    if len(log) > 5000:
        log = log[-5000:]

    save_evaluation_log(log)

def get_score_distribution():
    """
    Returns a dict: {bucket: {"win": x, "loss": y, ...}}
    Buckets: 0–9, 10–19, ..., 90–99, 100
    """
    data = defaultdict(lambda: defaultdict(int))
    log = load_evaluation_log()
    for entry in log:
        score = int(entry["score"] // 10) * 10
        outcome = entry["outcome"]
        data[score][outcome] += 1
    return dict(data)
