import json
import os
from collections import defaultdict

from utils.logger import log_event

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/reasoning_weights.json"
ADJUSTMENT_LOG = "/home/ubuntu/nyx/runtime/memory/score_adjustments.json"

def load_reasoning_data():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_reasoning_data(data):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def log_adjustment(tag, delta, reason):
    if not os.path.exists(ADJUSTMENT_LOG):
        adjustments = []
    else:
        try:
            with open(ADJUSTMENT_LOG, "r") as f:
                adjustments = json.load(f)
        except Exception:
            adjustments = []

    adjustments.append({
        "tag": tag,
        "delta": delta,
        "reason": reason
    })

    with open(ADJUSTMENT_LOG, "w") as f:
        json.dump(adjustments[-200:], f, indent=2)  # keep last 200

def adjust_score_weights():
    """
    Adjusts reasoning weights based on performance.
    Tags with poor performance get negative weight nudges.
    High performers get small boosts.
    """
    data = load_reasoning_data()
    changes = 0

    for tag, outcomes in data.items():
        profit = outcomes.get("profit", 0)
        moon = outcomes.get("moon", 0)
        rug = outcomes.get("rug", 0)
        dead = outcomes.get("dead", 0)
        loss = outcomes.get("loss", 0)

        total = profit + moon + rug + dead + loss
        if total < 3:
            continue  # not enough data to judge

        win_score = profit + 3 * moon
        loss_score = rug * 4 + dead * 2 + loss

        if loss_score > win_score and loss_score > 5:
            # Penalize tag
            data[tag]["penalty"] = data[tag].get("penalty", 0) + 1
            log_event(f"🔻 Tag penalty: {tag} marked as underperforming")
            log_adjustment(tag, -1, "underperformance")
            changes += 1
        elif win_score > loss_score and win_score > 5:
            # Reward tag
            data[tag]["boost"] = data[tag].get("boost", 0) + 1
            log_event(f"🔺 Tag boost: {tag} marked as positive performer")
            log_adjustment(tag, +1, "strong performance")
            changes += 1

    if changes > 0:
        save_reasoning_data(data)
