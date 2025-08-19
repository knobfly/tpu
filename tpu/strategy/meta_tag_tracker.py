import json
import os
from collections import defaultdict

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/meta_tag_performance.json"
META_TAGS = ["rotation", "bundle", "whale", "launchpad", "community", "celeb", "dev mint"]

def load_meta_tags():
    if not os.path.exists(MEMORY_FILE):
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0})
    try:
        with open(MEMORY_FILE, "r") as f:
            data = json.load(f)
            return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0}, data)
    except:
        return defaultdict(lambda: {"profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0})

def save_meta_tags(data):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except:
        pass

def log_meta_performance(tags: list[str], outcome: str):
    data = load_meta_tags()
    for tag in tags:
        if tag in META_TAGS and outcome in data[tag]:
            data[tag][outcome] += 1
    save_meta_tags(data)

def get_meta_tag_summary():
    data = load_meta_tags()
    summary = {}
    for tag, counts in data.items():
        total = sum(counts.values())
        score = (
            counts["profit"] * 2 +
            counts["moon"] * 3 -
            counts["loss"] -
            counts["rug"] * 2 -
            counts["dead"]
        )
        avg_score = round(score / total, 2) if total else 0
        summary[tag] = {
            "total": total,
            "average_score": avg_score,
            "raw": counts
        }
    return summary

def get_favored_meta_tags(threshold=1.0):
    summary = get_meta_tag_summary()
    return [tag for tag, stats in summary.items() if stats["average_score"] >= threshold]

def get_avoided_meta_tags(threshold=-1.0):
    summary = get_meta_tag_summary()
    return [tag for tag, stats in summary.items() if stats["average_score"] <= threshold]
