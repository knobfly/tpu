import logging
from datetime import datetime

from utils.logger import log_event


class WalletInfluenceTracker:
    def __init__(self):
        self.wallet_scores = {}

    def record_wallet_activity(self, wallet: str, event: str, impact: float = 1.0):
        """
        Record wallet activity such as early buys or volume spikes.
        """
        try:
            entry = self.wallet_scores.get(wallet, {"score": 0, "last_seen": None})
            entry["score"] += impact
            entry["last_seen"] = datetime.utcnow().isoformat()
            self.wallet_scores[wallet] = entry
            log_event(f"[WalletInfluence] {wallet}: {event} (impact={impact})")
        except Exception as e:
            logging.warning(f"[WalletInfluence] Failed to record wallet activity: {e}")

    def get_wallet_score(self, wallet: str) -> float:
        return self.wallet_scores.get(wallet, {}).get("score", 0.0)

    def decay_scores(self):
        """
        Decays all wallet influence scores over time to prevent stale bias.
        """
        try:
            for w in self.wallet_scores:
                self.wallet_scores[w]["score"] *= 0.95
        except Exception as e:
            logging.warning(f"[WalletInfluence] Decay error: {e}")
