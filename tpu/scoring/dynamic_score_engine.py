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
        Adapt weights based on all logical context:
        - Everyone burns: reduce burned weight
        - No LP locks: reduce lp_locked importance
        - Dev is trusted: boost dev_trust
        - Blacklisted: set all weights to minimum
        - High market volatility: reduce all weights
        - Recent loss streak: regress weights
        - ML predictions: if high rug risk, reduce all weights
        """
        if context.get("burn_common", False):
            self.weights["burned"] *= 0.8
        if context.get("lp_never_locked", False):
            self.weights["lp_locked"] *= 0.7
        if context.get("trusted_dev", False):
            self.weights["dev_trust"] *= 1.2
        if context.get("meta_theme"):
            self.weights["meta_theme"] += 0.2
        if context.get("blacklisted", False):
            for k in self.weights:
                self.weights[k] = 0.1
        if context.get("market_volatility", 0) > 0.8:
            for k in self.weights:
                self.weights[k] *= 0.7
        if context.get("loss_streak", 0) >= LOSS_THRESHOLD:
            self.regress_weights()
        ml_rug_pred = context.get("ml_rug_pred")
        if ml_rug_pred is not None and float(ml_rug_pred) > 0.7:
            for k in self.weights:
                self.weights[k] *= 0.5
        # normalize
        for k in self.weights:
            self.weights[k] = round(min(max(self.weights[k], 0.1), 2.0), 3)

    def score_token(self, features: Dict[str, float], context: Dict[str, Any] = {}) -> Dict[str, Any]:
        self.adjust_weights_for_context(context)

        final_score = 0
        reasoning = []
        # --- ML prediction blending ---
        ml_price_pred = context.get("ml_price_pred")
        ml_rug_pred = context.get("ml_rug_pred")
        ml_wallet_pred = context.get("ml_wallet_pred")

        # Blend ML predictions into scoring
        ml_boost = 0.0
        if ml_price_pred is not None:
            ml_boost += float(ml_price_pred) * 2.0  # price prediction weighted
            reasoning.append(f"ml_price_pred: {ml_price_pred:.2f} x 2.0 = {float(ml_price_pred)*2.0:.2f}")
        if ml_rug_pred is not None:
            # Assume rug_pred is a risk score (0=safe, 1=risky), so subtract
            ml_boost -= float(ml_rug_pred) * 3.0
            reasoning.append(f"ml_rug_pred: {ml_rug_pred:.2f} x -3.0 = {-float(ml_rug_pred)*3.0:.2f}")
        if ml_wallet_pred is not None:
            ml_boost += float(ml_wallet_pred) * 1.5
            reasoning.append(f"ml_wallet_pred: {ml_wallet_pred:.2f} x 1.5 = {float(ml_wallet_pred)*1.5:.2f}")

        # Score features as before
        if features:
            for k, base_score in features.items():
                weight = self.weights.get(k, 1.0)
                contribution = base_score * weight
                final_score += contribution
                reasoning.append(f"{k}: {base_score:.2f} × {weight:.2f} = {contribution:.2f}")
        else:
            # If no features, use ML predictions alone
            final_score = ml_boost
            reasoning.append("No features available, using ML predictions only.")

        # Add ML boost if features were present
        if features:
            final_score += ml_boost
            reasoning.append(f"ML blended boost: {ml_boost:.2f}")

        # Guarantee a score is always produced
        if final_score is None:
            final_score = 0.0
            reasoning.append("No score produced, defaulting to 0.")

        return {
            "final_score": round(final_score, 3),
            "reasoning": reasoning,
            "weights": self.weights.copy(),
            "timestamp": datetime.utcnow().isoformat(),
            "ml_boost": ml_boost,
        }


# === Singleton ===
dynamic_score_engine = DynamicScoreEngine()
