import json
import logging
import os
import time
from datetime import datetime, timedelta

OUTCOME_MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/token_outcomes.json"

# === Load/save JSON-based memory ===

def load_outcome_memory():
    if not os.path.exists(OUTCOME_MEMORY_FILE):
        return {}
    try:
        with open(OUTCOME_MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_outcome_memory(data):
    try:
        with open(OUTCOME_MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[OutcomeMemory] Failed to save: {e}")

# === In-memory token outcome store ===
_token_outcomes = load_outcome_memory()  # token_address -> dict

# === Logging functions ===

def log_token_outcome(token_address: str, outcome: str, details: dict = None):
    """
    Record outcome in memory and JSON log (e.g. rug, exit, win, loss).
    """
    record = {
        "outcome": outcome,
        "timestamp": datetime.utcnow().isoformat(),
        "details": details or {},
    }
    _token_outcomes[token_address] = record
    save_outcome_memory(_token_outcomes)

    # Append raw log for audit
    try:
        raw_payload = {
            "token": token_address,
            "timestamp": time.time(),
            "outcome": outcome,
            "details": details or {}
        }
        with open(OUTCOME_MEMORY_FILE, "a") as f:
            f.write(json.dumps(raw_payload) + "\n")
    except Exception as e:
        logging.warning(f"[OutcomeMemory] Failed to append raw: {e}")

def update_token_outcome(token_address: str, outcome: str):
    """
    Update in-memory + JSON storage with simplified string outcome only.
    """
    _token_outcomes[token_address] = {
        "outcome": outcome,
        "timestamp": datetime.utcnow().isoformat(),
        "details": {}
    }
    save_outcome_memory(_token_outcomes)

def get_token_outcome(token_address: str) -> dict:
    return _token_outcomes.get(token_address, {})

def get_recent_failed_trades(since_minutes: int = 120) -> list[dict]:
    """
    Return tokens that failed recently (rug/honeypot/etc).
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=since_minutes)
    failed = []

    for token, data in _token_outcomes.items():
        try:
            ts = datetime.fromisoformat(data["timestamp"])
            if ts < cutoff:
                continue
            if data["outcome"] in {"rug", "honeypot", "exit", "loss", "abandon"}:
                failed.append({
                    "token_address": token,
                    "outcome": data["outcome"],
                    "timestamp": ts,
                    "details": data.get("details", {})
                })
        except Exception:
            continue

    return failed
