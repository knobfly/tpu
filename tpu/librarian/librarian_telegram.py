# librarian_telegram.py
# Telegram user profile management for DataLibrarian

import time
import logging
from typing import Dict, Any, Optional, Set

class LibrarianTelegram:
    def __init__(self):
        self.user_profiles: Dict[str, Dict[str, Any]] = {}

    def ingest_user_profile(self, user_id: str, profile: Dict[str, Any]):
        if not user_id:
            return
        existing = self.user_profiles.get(user_id, {})
        merged = {**existing, **profile}
        merged["last_updated"] = time.time()
        self.user_profiles[user_id] = merged
        logging.info(f"[LibrarianTelegram] Ingested profile for Telegram user {user_id}")

    def update_activity(self, user_id: str, activity: Dict[str, Any]):
        if not user_id:
            return
        profile = self.user_profiles.setdefault(user_id, {})
        profile.setdefault("activity", []).append(activity)
        profile["last_active"] = time.time()

    def score_user(self, user_id: str) -> float:
        profile = self.user_profiles.get(user_id, {})
        score = 0.0
        # Example scoring: reliability, alpha signals, risk
        score += float(profile.get("reliability", 0))
        score += float(profile.get("alpha_signals", 0)) * 2
        score -= float(profile.get("risk_flags", 0)) * 1.5
        return max(score, 0.0)

    def get_user_profile(self, user_id: str) -> Dict[str, Any]:
        return self.user_profiles.get(user_id, {})

    def has_seen_user(self, user_id: str) -> bool:
        return user_id in self.user_profiles

