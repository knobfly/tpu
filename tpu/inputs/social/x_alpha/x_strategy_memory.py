# /x_alpha/x_strategy_memory.py

import json
import os
from datetime import datetime, timedelta

MEMORY_FILE = "/home/ubuntu/nyx/runtime/data/x_strategy_memory.json"
DECAY_DAYS = 30  # Optional: auto decay entries older than 30 days

class XStrategyMemory:
    def __init__(self):
        self.memory = {}
        self.load()

    def load(self):
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r") as f:
                self.memory = json.load(f)
        else:
            self.memory = {}

    def save(self):
        with open(MEMORY_FILE, "w") as f:
            json.dump(self.memory, f, indent=2)

    def register_outcome(self, token, outcome):
        now = datetime.utcnow().isoformat()
        if token not in self.memory:
            self.memory[token] = {"wins": 0, "losses": 0, "last_seen": now}

        if outcome == "win":
            self.memory[token]["wins"] += 1
        elif outcome == "loss":
            self.memory[token]["losses"] += 1

        self.memory[token]["last_seen"] = now
        self.save()

    def get_score(self, token):
        data = self.memory.get(token, {})
        total = data.get("wins", 0) + data.get("losses", 0)
        if total == 0:
            return 0.5
        return round(data["wins"] / total, 4)

    def get_all_memory(self):
        return self.memory

    def decay_old_entries(self):
        """Optional: forget tokens not seen in DECAY_DAYS."""
        cutoff = datetime.utcnow() - timedelta(days=DECAY_DAYS)
        cleaned = {
            token: data for token, data in self.memory.items()
            if datetime.fromisoformat(data.get("last_seen", "1900-01-01T00:00:00")) > cutoff
        }
        if len(cleaned) < len(self.memory):
            self.memory = cleaned
            self.save()

x_strategy_memory = XStrategyMemory()
