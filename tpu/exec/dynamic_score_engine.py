import logging
from typing import Dict, Any
from datetime import datetime

from librarian.data_librarian import librarian

# === Config ===
DEFAULT_WEIGHTS = {
    "wallet_score": 1.0,
    "social_score": 1.0,
    "chart_score": 1.0,
    "token_age": 0.5,
    "dev_trust": 1.0,
    "lp_locked": 1.0,
    "burned": 0.8,
    "meta_theme": 0.7,
}

SAFE_MODE_WEIGHTS = {
    k: v * 0.7 for k, v in DEFAULT_WEIGHTS.items()
}

LOSS_THRESHOLD = 3

class DynamicScoreEngine:
    def __init__(self):
        self.loss_streak = 0
        self.weights = DEFAULT_WEIGHTS.copy()

    def reset_weights(self):
        self.weights = DEFAULT_WEIGHTS.copy()

    def regress_weights(self):
        logging.warning("[DynamicScore] Entering safe mode after 3 losses.")
        self.weights = SAFE_MODE_WEIGHTS.copy()

    def record_trade_result(self, result: str):
        if result == "win":
            self.loss_streak = 0
            self.weights = self._gradually_restore_weights()
        elif result == "loss":
            self.loss_streak += 1
            if self.loss_streak >= LOSS_THRESHOLD:
                self.regress_weights()

    def _gradually_restore_weights(self):
        # Slowly return to default weights
        new_weights = self.weights.copy()
        for k in new_weights:
            target = DEFAULT_WEIGHTS[k]
            diff = target - new_weights[k]
            new_weights[k] += diff * 0.3  # slow convergence
        return new_weights

    def adjust_weights_for_context(self, context: Dict[str, Any]):
        """
        Adapt weights based on live data trends:
        - Everyone burns: reduce burned weight
        - No LP locks: reduce lp_locked importance
        - Dev is trusted: boost dev_trust
        """
        if context.get("burn_common", False):
            self.weights["burned"] *= 0.8
        if context.get("lp_never_locked", False):
            self.weights["lp_locked"] *= 0.7
        if context.get("trusted_dev", False):
            self.weights["dev_trust"] *= 1.2
        if context.get("meta_theme"):
            self.weights["meta_theme"] += 0.2
        # normalize
        for k in self.weights:
            self.weights[k] = round(min(max(self.weights[k], 0.1), 2.0), 3)

    def score_token(self, features: Dict[str, float], context: Dict[str, Any] = {}) -> Dict[str, Any]:
        self.adjust_weights_for_context(context)

        final_score = 0
        reasoning = []
        for k, base_score in features.items():
            weight = self.weights.get(k, 1.0)
            contribution = base_score * weight
            final_score += contribution
            reasoning.append(f"{k}: {base_score:.2f} × {weight:.2f} = {contribution:.2f}")

        return {
            "final_score": round(final_score, 3),
            "reasoning": reasoning,
            "weights": self.weights.copy(),
            "timestamp": datetime.utcnow().isoformat()
        }


# === Singleton ===
dynamic_score_engine = DynamicScoreEngine()
