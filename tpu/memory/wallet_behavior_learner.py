#/wallet_behavior_learner.py

import json
import os
from datetime import datetime

WALLET_TIMING_FILE = "/home/ubuntu/nyx/runtime/data/wallet_entry_times.json"

class WalletBehaviorLearner:
    def __init__(self):
        self.entries = []
        self.load()

    def load(self):
        if os.path.exists(WALLET_TIMING_FILE):
            with open(WALLET_TIMING_FILE, "r") as f:
                self.entries = json.load(f)

    def save(self):
        with open(WALLET_TIMING_FILE, "w") as f:
            json.dump(self.entries, f, indent=2)

    def register_entry(self, token, wallet, time=None):
        entry = {
            "token": token,
            "wallet": wallet,
            "time": time or datetime.utcnow().isoformat()
        }
        self.entries.append(entry)
        self.save()

    def get_entries_for_token(self, token):
        return [e for e in self.entries if e["token"] == token]

get_wallet_entry_times = WalletBehaviorLearner().get_entries_for_token
wallet_behavior_learner = WalletBehaviorLearner()
