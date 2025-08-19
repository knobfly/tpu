# /x_alpha/alpha_post_manager.py

import json
import os
from datetime import datetime, timedelta

TRACK_FILE = "/home/ubuntu/nyx/runtime/data/recent_alpha_posts.json"
MAX_TRACKED_POSTS = 150

class AlphaPostManager:
    def __init__(self):
        self.posts = []
        self.load()

    def load(self):
        if os.path.exists(TRACK_FILE):
            with open(TRACK_FILE, "r") as f:
                try:
                    self.posts = json.load(f)
                except json.JSONDecodeError:
                    self.posts = []
        else:
            self.posts = []

    def save(self):
        # Limit number of posts stored to avoid unbounded growth
        self.posts = self.posts[-MAX_TRACKED_POSTS:]
        with open(TRACK_FILE, "w") as f:
            json.dump(self.posts, f, indent=2)

    def already_posted(self, token: str, within_minutes: int = 120) -> bool:
        """Check if token was posted about recently (within timeframe)."""
        cutoff = datetime.utcnow() - timedelta(minutes=within_minutes)
        for post in reversed(self.posts):
            if post["token"] == token:
                try:
                    post_time = datetime.fromisoformat(post["time"])
                    if post_time >= cutoff:
                        return True
                except Exception:
                    continue
        return False

    def register_post(self, token: str, action: str, confidence: str):
        entry = {
            "time": datetime.utcnow().isoformat(),
            "token": token,
            "action": action,
            "confidence": confidence
        }
        self.posts.append(entry)
        self.save()

    def recent_tokens(self, limit=10):
        """Get recent tokens posted about (latest N)."""
        return list({p["token"] for p in self.posts[-limit:]})

    def get_all_posts(self):
        return self.posts

alpha_post_manager = AlphaPostManager()
