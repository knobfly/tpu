import json
import logging
import os

TRACK_FILE = "/home/ubuntu/nyx/runtime/logs/group_tracker.json"

def _load():
    if not os.path.exists(TRACK_FILE):
        return {}
    try:
        with open(TRACK_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[GroupTracker] Failed to load: {e}")
        return {}

def _save(data: dict):
    try:
        with open(TRACK_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[GroupTracker] Failed to save: {e}")

def track_group(group_name: str, token: str):
    data = _load()
    if group_name not in data:
        data[group_name] = []
    if token not in data[group_name]:
        data[group_name].append(token)
    _save(data)

def get_groups_for_token(token: str) -> list:
    data = _load()
    return [g for g, tokens in data.items() if token in tokens]
