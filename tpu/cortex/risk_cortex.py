import logging
from datetime import datetime, timedelta

from defense.auto_rug_blacklist import is_blacklisted_token
from defense.honeypot_scanner import is_honeypot
from defense.liquidity_monitor import is_liquidity_removed
from defense.liquidity_trend_watcher import detect_liquidity_drain
from defense.rug_wave_defender import detect_rug_wave
from inputs.wallet.wallet_behavior_analyzer import is_known_rugger
from inputs.wallet.wallet_cluster_analyzer import detect_wallet_traps
from librarian.data_librarian import librarian
from strategy.stop_snipe_defender import get_rug_rate
from utils.token_utils import get_lp_lock_status, has_contract_risks


class RiskCortex:
    # --- Supervisor Integration Hooks ---
    def receive_chart_signal(self, token_context: dict, chart_insights: dict):
        """
        Receive chart signals broadcast from supervisor or other modules.
        Can be used to update risk scoring or trigger analytics.
        """
        pass

    def update_persona_context(self, persona_context: dict):
        """
        Receive persona context updates for adaptive risk logic.
        """
        pass

    def receive_analytics_update(self, update: dict):
        """
        Receive analytics/state updates for unified decision-making.
        """
        pass

    def contribute_features(self, token_context: dict) -> dict:
        """
        Contribute risk-derived features for cross-module analytics.
        """
        insights = self.assess_token_risk(token_context.get("token_address", ""))
        return {
            "risk_score": insights.get("score", 0),
            "risk_flags": insights.get("flags", []),
        }
    def __init__(self, memory):
        self.last_risk_map = {}
        self.memory = memory

    def assess_token_risk(self, token: str) -> dict:
        """
        Evaluate all known risk dimensions for a token.
        Returns a structured risk dict: score, reasons, flags
        """
        score = 0
        reasons = []
        flags = []

        info = librarian.get_token_info(token)
        if not info:
            reasons.append("missing_metadata")
            score += 5

        if not get_lp_lock_status(token):
            flags.append("liquidity_unlocked")
            score += 7

        if has_contract_risks(token):
            flags.append("contract_flags")
            score += 8

        if is_known_rugger(info.get("creator")):
            flags.append("creator_rugger")
            score += 10

        if info.get("symbol", "").startswith("$"):
            reasons.append("$symbol")
            score += 1

        recent_rug_rate = get_rug_rate(30)
        if recent_rug_rate > 0.4:
            reasons.append("high_rug_environment")
            score += int(recent_rug_rate * 10)

        if info.get("age_minutes", 0) < 10:
            reasons.append("very_new")
            score += 1

        # 🧱 Integrated Defense Modules
        if is_blacklisted_token(token):
            flags.append("blacklist_match")
            score += 10

        if detect_rug_wave(token):
            flags.append("rug_wave_signal")
            score += 8

        if is_liquidity_removed(token):
            flags.append("liquidity_removed")
            score += 8

        if detect_liquidity_drain(token):
            flags.append("liquidity_drain_pattern")
            score += 6

        if is_honeypot(token):
            flags.append("honeypot_detected")
            score += 10

        if detect_wallet_traps(token):
            flags.append("wallet_trap_cluster")
            score += 7

        final = {
            "token": token,
            "score": score,
            "reasons": reasons,
            "flags": flags,
        }
        self.last_risk_map[token] = final
        return final

    def get_last_score(self, token: str) -> dict:
        return self.last_risk_map.get(token, {})



