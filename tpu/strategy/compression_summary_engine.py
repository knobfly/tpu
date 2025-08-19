import json
import os
from collections import defaultdict
from statistics import mean

REASONING_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_weights.json"
COMPRESSION_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/compression_summary.json"

def load_reasoning_log():
    if not os.path.exists(REASONING_FILE):
        return {}
    try:
        with open(REASONING_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_compression_summary(data):
    try:
        with open(COMPRESSION_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except:
        pass

def compress_reasoning_weights():
    """
    Combines entropy analysis + top signal tags for scoring and outcome prediction.
    """
    raw = load_reasoning_log()
    compressed = {}
    summary = {
        "top_winners": [],
        "top_rugs": [],
        "top_moons": [],
        "dead_tags": [],
        "compressed": compressed
    }

    for tag, outcomes in raw.items():
        total = sum(outcomes.values())
        if total == 0:
            continue

        profit = outcomes.get("profit", 0)
        loss = outcomes.get("loss", 0)
        rug = outcomes.get("rug", 0)
        dead = outcomes.get("dead", 0)
        moon = outcomes.get("moon", 0)

        profit_score = profit + 3 * moon
        loss_score = loss + 2 * dead + 4 * rug
        win_ratio = round(profit_score / total, 3)
        loss_ratio = round(loss_score / total, 3)
        entropy = round(abs(profit_score - loss_score) / total, 3)

        compressed[tag] = {
            "total_signals": total,
            "profit_score": profit_score,
            "loss_score": loss_score,
            "win_ratio": win_ratio,
            "loss_ratio": loss_ratio,
            "entropy": entropy
        }

        win_quality = profit * 2 + moon * 3 - loss - rug * 2
        if win_quality > 5:
            summary["top_winners"].append((tag, win_quality))
        if rug >= 3:
            summary["top_rugs"].append((tag, rug))
        if moon >= 2:
            summary["top_moons"].append((tag, moon))
        if dead >= 2 and profit + moon == 0:
            summary["dead_tags"].append(tag)

    summary["top_winners"] = sorted(summary["top_winners"], key=lambda x: -x[1])[:10]
    summary["top_rugs"] = sorted(summary["top_rugs"], key=lambda x: -x[1])[:10]
    summary["top_moons"] = sorted(summary["top_moons"], key=lambda x: -x[1])[:10]

    save_compression_summary(summary)
    return summary

def get_high_confidence_signals(threshold=0.6):
    """
    Returns reasoning tags that are consistently profitable.
    """
    compressed = compress_reasoning_weights()["compressed"]
    return [k for k, v in compressed.items() if v["win_ratio"] >= threshold and v["entropy"] >= 0.5]

def get_risky_signals(threshold=0.6):
    """
    Returns reasoning tags that are consistently bad (rug/loss/dead).
    """
    compressed = compress_reasoning_weights()["compressed"]
    return [k for k, v in compressed.items() if v["loss_ratio"] >= threshold and v["entropy"] >= 0.5]
