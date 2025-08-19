import logging
from datetime import datetime

from scoring import snipe_score_engine, trade_score_engine
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import tag_token_result
from utils.logger import log_event


class SignalTradeFusion:
    """
    Fuses outputs from snipe_score_engine and trade_score_engine
    with additional metadata (wallet, social, meta tags).
    """

    def __init__(self):
        self.cache = {}

    def evaluate_token(self, token_context: dict, mode: str = "auto") -> dict:
        """
        Evaluate a token and decide which engine to use.
        mode: "snipe", "trade", or "auto"
        """
        token_address = token_context.get("token_address")
        if not token_address:
            return {"action": "ignore", "reason": "No token address provided"}

        # === Auto-detect mode based on token age ===
        if mode == "auto":
            age_min = token_context.get("age_minutes", 0)
            mode = "snipe" if age_min <= 10 else "trade"

        log_event(f"[Fusion] Evaluating {token_address} in mode={mode}")

        if mode == "snipe":
            result = snipe_score_engine.evaluate_snipe(token_context)
        else:
            result = trade_score_engine.evaluate_trade(token_context)

        # === Post-fusion scoring
        fusion_score = self._apply_meta_adjustments(result, token_context)
        result["fusion_score"] = round(fusion_score, 2)

        # Cache the result
        self.cache[token_address] = {
            "timestamp": datetime.utcnow().isoformat(),
            "result": result
        }

        log_scanner_insight("fusion", token_context, {"fusion_score": fusion_score})
        return result

    def _apply_meta_adjustments(self, result: dict, token_context: dict) -> float:
        """
        Adjusts score based on meta tags, wallet signals, and social activity.
        """
        score = result.get("final_score", 0)

        tags = token_context.get("meta_tags", [])
        if "whale" in tags:
            score += 5
        if "risk" in tags:
            score -= 3
        if "alpha" in tags:
            score += 8

        wallets = token_context.get("wallets", {})
        if wallets.get("whales_present", False):
            score += 2
        if wallets.get("overlap_snipers", 0) > 0:
            score += wallets.get("overlap_snipers", 0)

        result["final_score"] = max(0, min(score, 100))
        return result["final_score"]

    def get_cached_evaluation(self, token_address: str):
        return self.cache.get(token_address, None)

    def retag_token_from_fusion(self, token_address: str, action: str, score: float):
        """
        Tag token result based on fusion decision.
        """
        try:
            tag_token_result(token_address, f"fusion_{action}", score)
            log_event(f"[Fusion] {token_address} tagged as fusion_{action} with score={score}")
        except Exception as e:
            logging.warning(f"[Fusion] Failed to tag token {token_address}: {e}")
