# === /meta_tag_tracker.py ===
import json
import os
from collections import defaultdict
from datetime import datetime

FILE = "/home/ubuntu/nyx/runtime/memory/meta_tag_performance.json"
MAX_HISTORY = 500

def load_meta_tag_log():
    if not os.path.exists(FILE):
        return []
    try:
        with open(FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_meta_tag_log(data):
    try:
        with open(FILE, "w") as f:
            json.dump(data[-MAX_HISTORY:], f, indent=2)
    except:
        pass

def log_meta_tag_outcomes(tags: list, outcome: str):
    if not tags or not outcome:
        return
    history = load_meta_tag_log()
    for tag in tags:
        history.append({
            "tag": tag,
            "outcome": outcome,
            "timestamp": datetime.utcnow().isoformat()
        })
    save_meta_tag_log(history)

def summarize_meta_tag_performance():
    history = load_meta_tag_log()
    outcomes_by_tag = defaultdict(lambda: defaultdict(int))

    for entry in history:
        tag = entry["tag"]
        outcome = entry["outcome"]
        outcomes_by_tag[tag][outcome] += 1

    summary = {}
    for tag, outcomes in outcomes_by_tag.items():
        total = sum(outcomes.values())
        wins = outcomes.get("profit", 0) + 3 * outcomes.get("moon", 0)
        losses = outcomes.get("loss", 0) + 2 * outcomes.get("rug", 0) + outcomes.get("dead", 0)

        win_ratio = round(wins / total, 3) if total else 0
        loss_ratio = round(losses / total, 3) if total else 0
        bias = "favor" if win_ratio > loss_ratio + 0.2 else "avoid" if loss_ratio > win_ratio + 0.2 else "neutral"

        summary[tag] = {
            "win_ratio": win_ratio,
            "loss_ratio": loss_ratio,
            "bias": bias,
            "count": total
        }

    return summary

def get_current_meta_bias():
    return summarize_meta_tag_performance()
