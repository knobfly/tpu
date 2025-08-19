# === /memory_trim_guardian.py ===

import json
import logging
import os
from datetime import datetime, timedelta

from memory.strategy_memory_index import trim_strategy_memory
from memory.token_memory_index import trim_token_memory
from memory.wallet_memory_index import trim_wallet_memory

MEMORY_PATHS = {
    "score_logs": "/home/ubuntu/nyx/runtime/memory/strategy/score_logs.json",
    "wallet_clusters": "/home/ubuntu/nyx/runtime/memory/wallets/wallet_clusters.json",
    "token_history": "/home/ubuntu/nyx/runtime/memory/token/token_history.json",
    "reasoning_weights": "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_weights.json",
    "reasoning_memory": "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_memory.json",
    "signal_patterns": "/home/ubuntu/nyx/runtime/memory/strategy/signal_patterns.json",
    "outcome_predictions": "/home/ubuntu/nyx/runtime/memory/strategy/outcome_predictions.json"
}

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[MemoryTrim] Failed to load {path}: {e}")
        return {}

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[MemoryTrim] Failed to save {path}: {e}")

def trim_score_logs(path, max_entries=3000):
    data = load_json(path)
    if isinstance(data, list) and len(data) > max_entries:
        data = data[-max_entries:]
        save_json(path, data)
        logging.info(f"[MemoryTrim] Trimmed score_logs to {max_entries} entries.")

def trim_dict_memory(path, max_keys=500):
    data = load_json(path)
    if isinstance(data, dict) and len(data) > max_keys:
        trimmed = dict(list(data.items())[-max_keys:])
        save_json(path, trimmed)
        logging.info(f"[MemoryTrim] Trimmed {path} to {max_keys} keys.")

def trim_reasoning_weights(path, min_total=2):
    data = load_json(path)
    if isinstance(data, dict):
        cleaned = {
            k: v for k, v in data.items()
            if sum(v.values()) >= min_total
        }
        if len(cleaned) != len(data):
            save_json(path, cleaned)
            logging.info(f"[MemoryTrim] Pruned low-signal reasoning weights.")

def run_memory_cleanup():
    logging.info("🧹 Running smart memory trim...")  
    trim_score_logs(MEMORY_PATHS["score_logs"])
    trim_dict_memory(MEMORY_PATHS["wallet_clusters"], max_keys=500)
    trim_dict_memory(MEMORY_PATHS["token_history"], max_keys=1000)
    trim_reasoning_weights(MEMORY_PATHS["reasoning_weights"])
    trim_dict_memory(MEMORY_PATHS["reasoning_memory"], max_keys=300)
    trim_dict_memory(MEMORY_PATHS["signal_patterns"], max_keys=300)
    trim_score_logs(MEMORY_PATHS["outcome_predictions"], max_entries=250)
    logging.info("✅ Memory trim complete.")

def trigger_trim_check():
    """
    Perform a memory trim sweep across both runtime JSON and in-memory stores.
    Called periodically or manually (e.g., via Telegram).
    """
    logging.info("[MemoryTrim] 🚿 Triggering full memory trim sweep...")

    run_memory_cleanup()
    token_stats = trim_token_memory()
    wallet_stats = trim_wallet_memory()
    strategy_stats = trim_strategy_memory()

    logging.info(f"[MemoryTrim] 🧠 Token Memory: {token_stats}")
    logging.info(f"[MemoryTrim] 👛 Wallet Memory: {wallet_stats}")
    logging.info(f"[MemoryTrim] 🎯 Strategy Memory: {strategy_stats}")

    return {
        "token": token_stats,
        "wallet": wallet_stats,
        "strategy": strategy_stats,
    }
