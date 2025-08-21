from inputs.wallet.cabal_detector import detect_cabal_patterns
from inputs.wallet.dev_reputation import score_dev_reputation
from inputs.wallet.smart_wallet_personality import detect_wallet_personality
from inputs.wallet.wallet_alpha_sniper_overlap import check_for_overlap_trigger
from inputs.wallet.wallet_behavior_analyzer import analyze_wallet_behavior
from inputs.wallet.wallet_reputation_tracker import get_wallet_reputation_score
from memory.wallet_memory_index import update_wallet_cluster_memory
from strategy.reinforcement_tracker import get_wallet_result_score
from utils.wallet_data import get_tracked_wallets


class WalletCortex:
    # --- Supervisor Integration Hooks ---
    def receive_chart_signal(self, token_context: dict, chart_insights: dict):
        """
        Receive chart signals broadcast from supervisor or other modules.
        Can be used to update wallet scoring or trigger analytics.
        """
        # Example: log or adapt wallet scoring based on chart context
        pass

    def update_persona_context(self, persona_context: dict):
        """
        Receive persona context updates for adaptive wallet logic.
        """
        # Example: adapt wallet scoring or risk logic based on persona traits/mood
        pass

    def receive_analytics_update(self, update: dict):
        """
        Receive analytics/state updates for unified decision-making.
        """
        # Example: update internal state or trigger wallet analytics
        pass

    def contribute_features(self, token_context: dict) -> dict:
        """
        Contribute wallet-derived features for cross-module analytics.
        """
        insights = self.analyze_wallets(token_context)
        return {
            "wallet_score": insights.get("wallet_score", 0.0),
            "avg_reputation": insights.get("avg_reputation", 0.0),
            "whales_present": insights.get("whales_present", False),
        }
    def __init__(self, memory):
        self.memory = memory

    def analyze_wallets(self, token_data: dict) -> dict:
        token_address = token_data.get("token_address")
        wallet_list = token_data.get("buyers", [])
        mode = token_data.get("mode", "trade")

        if not token_address or not wallet_list:
            return {
                "wallet_activity": "unknown",
                "whales_present": False,
                "avg_reputation": 0,
                "overlap_snipers": 0,
                "wallet_score": 0,
                "cabal_flagged": False,
                "dev_score": 0,
                "personality_score": 0,
                "correlation_score": 0
            }

        total_score = 0
        behavior_score = 0
        avg_reputation = 0
        overlap_score = 0
        memory_boost = 0
        cabal_flag = False
        dev_score = 0
        personality_score = 0
        correlation_score = 0

        # === Wallet behavior
        behavior_report = analyze_wallet_behavior(wallet_list)
        activity_level = behavior_report.get("activity_level", "unknown")
        whale_detected = behavior_report.get("whale_detected", False)
        behavior_score = behavior_report.get("score", 0)
        total_score += behavior_score

        # === Trade mode → full analysis
        if mode == "trade":
            # Reputation
            try:
                reputation_scores = [get_wallet_reputation_score(w) for w in wallet_list[:5]]
                avg_reputation = sum(reputation_scores) / len(reputation_scores) if reputation_scores else 0
                total_score += avg_reputation
            except:
                avg_reputation = 0

            # Overlap
            try:
                overlap_data = check_for_overlap_trigger(wallet_list, token_address)
                overlap_score = overlap_data.get("score", 0)
                total_score += overlap_score
            except:
                overlap_score = 0

            # Reinforcement memory
            for wallet in wallet_list[:10]:
                memory_boost += get_wallet_result_score(wallet)
            memory_boost = memory_boost / len(wallet_list[:10]) if wallet_list[:10] else 0
            total_score += memory_boost

            # Cabal detection
            try:
                cabal = detect_cabal_patterns(wallet_list)
                if cabal.get("flagged"):
                    total_score -= 5
                    cabal_flag = True
            except:
                cabal_flag = False

            # Dev reputation
            try:
                dev_score = score_dev_reputation(wallet_list)
                total_score += dev_score
            except:
                dev_score = 0

            # Wallet personality
            try:
                personality_score = detect_wallet_personality(wallet_list)
                total_score += personality_score
            except:
                personality_score = 0

            # Smart wallet correlation
            try:
                correlation_score = get_tracked_wallets(wallet_list, token_address)
                total_score += correlation_score
            except:
                correlation_score = 0

        # === Snipe mode → skip slow ops

        # === Memory save
        try:
            update_wallet_cluster_memory(token_address, {
                "wallets": wallet_list,
                "activity": activity_level,
                "whale_present": whale_detected,
                "avg_reputation": avg_reputation,
                "overlap": overlap_score,
                "score": total_score
            })
        except:
            pass

        return {
            "wallet_activity": activity_level,
            "whales_present": whale_detected,
            "avg_reputation": avg_reputation,
            "overlap_snipers": overlap_score,
            "wallet_score": round(total_score, 2),
            "cabal_flagged": cabal_flag,
            "dev_score": dev_score,
            "personality_score": personality_score,
            "correlation_score": correlation_score
        }

