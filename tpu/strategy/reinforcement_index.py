import json
import logging
import os

REINFORCEMENT_FILE = "/home/ubuntu/nyx/runtime/logs/reinforcement_index.json"


def _load_index():
    if not os.path.exists(REINFORCEMENT_FILE):
        return {}
    try:
        with open(REINFORCEMENT_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[ReinforcementIndex] Failed to load: {e}")
        return {}


def _save_index(data):
    try:
        with open(REINFORCEMENT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[ReinforcementIndex] Failed to save: {e}")


def update_index(token: str, outcome: str, score: float):
    """
    Updates reinforcement stats for a token.
    """
    data = _load_index()
    record = data.get(token, {"wins": 0, "losses": 0, "last_score": 0})

    if outcome == "win":
        record["wins"] += 1
    elif outcome in ["loss", "rug"]:
        record["losses"] += 1

    record["last_score"] = score
    data[token] = record
    _save_index(data)


def get_index(token: str):
    return _load_index().get(token, {"wins": 0, "losses": 0, "last_score": 0})
