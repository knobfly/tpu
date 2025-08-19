import json
import os
from collections import defaultdict

from utils.file_manager import load_json_file, save_json_file

INFLUENCE_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/influence_map.json"

def load_influence_map():
    if not os.path.exists(INFLUENCE_FILE):
        return defaultdict(lambda: {"tokens": [], "tags": [], "mentions": 0})
    try:
        data = load_json_file(INFLUENCE_FILE)
        return defaultdict(lambda: {"tokens": [], "tags": [], "mentions": 0}, data)
    except:
        return defaultdict(lambda: {"tokens": [], "tags": [], "mentions": 0})

def save_influence_map(data):
    save_json_file(INFLUENCE_FILE, dict(data))

def update_influence(wallet: str, token: str, tags: list[str]):
    if not wallet or not token:
        return
    influence = load_influence_map()
    entry = influence[wallet]
    if token not in entry["tokens"]:
        entry["tokens"].append(token)
    for tag in tags:
        tag = tag.lower()
        if tag not in entry["tags"]:
            entry["tags"].append(tag)
    entry["mentions"] += 1
    influence[wallet] = entry
    save_influence_map(influence)

def score_influence(wallets: list[str]) -> tuple[int, list[str]]:
    influence = load_influence_map()
    score = 0
    reasons = []

    for w in wallets:
        entry = influence.get(w, {})
        if not entry:
            continue
        if entry["mentions"] > 10:
            score += 5
            reasons.append(f"{w[:6]}... influence cluster")
        if "moon" in entry.get("tags", []):
            score += 3
            reasons.append(f"{w[:6]}... tied to moons")
        if "rug" in entry.get("tags", []):
            score -= 5
            reasons.append(f"{w[:6]}... tied to rugs")

    return score, reasons
