import json
import os
from datetime import datetime

HISTORY_FILE = "/home/ubuntu/nyx/runtime/memory/token_history.json"
os.makedirs("memory", exist_ok=True)

# === Load Token History ===
def load_token_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

# === Save Token History ===
def save_token_history(history: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

# === Add Token Event ===
def record_token_event(token_address: str, event_type: str, result: str = "", notes: str = ""):
    history = load_token_history()
    now = datetime.utcnow().isoformat()

    if token_address not in history:
        history[token_address] = []

    history[token_address].append({
        "time": now,
        "event": event_type,
        "result": result,
        "notes": notes
    })

    save_token_history(history)

# === Query History ===
def get_token_history(token_address: str) -> list:
    history = load_token_history()
    return history.get(token_address, [])
