# /utils/wallet_data.py

import json
import os

TRACKED_WALLETS_PATH = "/home/ubuntu/nyx/runtime/data/tracked_wallets.json"

def get_tracked_wallets():
    if not os.path.exists(TRACKED_WALLETS_PATH):
        return []
    try:
        with open(TRACKED_WALLETS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []
