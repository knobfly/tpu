# /wallet_memory_index.py

import json
import logging
import os
from datetime import datetime

from librarian.data_librarian import librarian
from utils.file_utils import safe_load_json, safe_save_json

WALLET_MEM_FILE = "/home/ubuntu/nyx/runtime/data/wallet_memory_index.json"
MEMORY_DIR = "/home/ubuntu/nyx/runtime/data/wallet_clusters"
os.makedirs(MEMORY_DIR, exist_ok=True)
MAX_CLUSTERS = 500


class WalletMemoryIndex:
    def __init__(self):
        self.memory = {}
        self.load()

    def load(self):
        if os.path.exists(WALLET_MEM_FILE):
            with open(WALLET_MEM_FILE, "r") as f:
                self.memory = json.load(f)

    def save(self):
        with open(WALLET_MEM_FILE, "w") as f:
            json.dump(self.memory, f, indent=2)

    def record(self, wallet, token, behavior, outcome):
        if wallet not in self.memory:
            self.memory[wallet] = []

        self.memory[wallet].append({
            "token": token,
            "behavior": behavior,
            "outcome": outcome
        })
        self.save()

    def get_behavior(self, wallet):
        return self.memory.get(wallet, [])

def update_wallet_cluster_memory(wallet_address: str, cluster_data: dict) -> None:
    """
    Update the cluster memory of a given wallet.
    Saves JSON logs and injects memory into the librarian index.
    """
    if not wallet_address or not isinstance(cluster_data, dict):
        logging.warning(f"[ClusterMemory] Skipping invalid update for wallet={wallet_address}")
        return

    # Normalize address
    wallet_address = wallet_address.lower()
    path = os.path.join(MEMORY_DIR, f"{wallet_address}.json")

    # Load existing memory if it exists
    existing_data = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                existing_data = json.load(f)
        except Exception as e:
            logging.error(f"[ClusterMemory] Failed to read memory for {wallet_address}: {e}")

    # Merge new cluster data
    updated = False
    for k, v in cluster_data.items():
        if k not in existing_data or existing_data[k] != v:
            existing_data[k] = v
            updated = True

    if updated:
        try:
            with open(path, "w") as f:
                json.dump(existing_data, f, indent=2)
            logging.info(f"[ClusterMemory] 🔁 Updated cluster memory for {wallet_address}")
        except Exception as e:
            logging.error(f"[ClusterMemory] ❌ Failed to save memory for {wallet_address}: {e}")

    # Inject into librarian for instant memory recall
    memory_blob = {
        "type": "wallet_cluster",
        "wallet": wallet_address,
        "timestamp": datetime.utcnow().isoformat(),
        "cluster_data": cluster_data
    }
    librarian.ingest(memory_blob)

def trim_wallet_memory() -> dict:
    """
    Trims the wallet cluster memory file down to the most recent MAX_CLUSTERS entries.
    Returns a dict with stats on what was removed.
    """
    try:
        data = safe_load_json(MEMORY_DIR, default={})
        total = len(data)
        if total <= MAX_CLUSTERS:
            return {"total": total, "trimmed": 0}

        trimmed_data = dict(list(data.items())[-MAX_CLUSTERS:])
        safe_save_json(MEMORY_DIR, trimmed_data)

        return {"total": total, "trimmed": total - MAX_CLUSTERS}

    except Exception as e:
        logging.warning(f"[WalletMemory] Failed to trim wallet memory: {e}")
        return {"error": str(e)}


wallet_memory_index = WalletMemoryIndex()
