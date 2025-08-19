# modules/x_alpha/alpha_account_tracker.py

import json
import os
import time

from utils.logger import log_event

TRACKER_FILE = "/home/ubuntu/nyx/runtime/data/alpha_accounts.json"
DECAY_HALF_LIFE_DAYS = 30  # After ~30 days, old posts have half the weight

class AlphaAccountTracker:
    def __init__(self):
        self.accounts = {}
        self.load()

    def load(self):
        if os.path.exists(TRACKER_FILE):
            with open(TRACKER_FILE, "r") as f:
                self.accounts = json.load(f)
        else:
            self.accounts = {}

    def save(self):
        with open(TRACKER_FILE, "w") as f:
            json.dump(self.accounts, f, indent=2)

    def register_post(self, handle, token, outcome):
        """Log new post result for scoring"""
        ts = time.time()
        if handle not in self.accounts:
            self.accounts[handle] = {"history": []}

        self.accounts[handle]["history"].append({
            "token": token,
            "outcome": outcome,
            "timestamp": ts
        })

        log_event(f"📊 Alpha tracker update: @{handle} → {outcome} on ${token}")
        self.save()

    def get_score(self, handle):
        """Returns decayed win rate % based on recent outcomes"""
        history = self.accounts.get(handle, {}).get("history", [])
        if not history:
            return 0

        now = time.time()
        win_weight = 0
        total_weight = 0

        for entry in history:
            age_days = (now - entry["timestamp"]) / 86400
            decay = 0.5 ** (age_days / DECAY_HALF_LIFE_DAYS)
            total_weight += decay

            if entry["outcome"] == "win":
                win_weight += decay

        if total_weight == 0:
            return 0

        return round(100 * win_weight / total_weight, 2)

    def get_accounts(self):
        return self.accounts

    def is_trusted(self, handle):
        """Simple heuristic for trusted handles (can be used for quoting)"""
        return self.get_score(handle) >= 65 and len(self.accounts.get(handle, {}).get("history", [])) >= 5

alpha_account_tracker = AlphaAccountTracker()
