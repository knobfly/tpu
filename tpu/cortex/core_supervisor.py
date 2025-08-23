# cortex/core_supervisor.py
from __future__ import annotations
import logging
from typing import Dict, Any, Optional

# --- optional helpers (best-effort imports) -------------------------------
try:
    from inputs.onchain.onchain_listener import get_recent_transactions
except Exception:  # pragma: no cover
    get_recent_transactions = None  # type: ignore

try:
    from defense.liquidity_monitor import check_lp_add_event
except Exception:  # pragma: no cover
    check_lp_add_event = None  # type: ignore

try:
    from chart.pump_pattern_classifier import classify_pump_pattern
except Exception:  # pragma: no cover
    classify_pump_pattern = None  # type: ignore

try:
    from exec.real_time_wallet_trigger import detect_sniper_behavior
except Exception:  # pragma: no cover
    detect_sniper_behavior = None  # type: ignore

try:
    from inputs.onchain.firehose.rug_signature_engine import detect_rug_signature
except Exception:  # pragma: no cover
    detect_rug_signature = None  # type: ignore

try:
    from inputs.onchain.firehose.influence_mapper import get_influential_wallet_trigger
except Exception:  # pragma: no cover
    get_influential_wallet_trigger = None  # type: ignore

try:
    from inputs.onchain.firehose.event_classifier import classify_txn_events
except Exception:  # pragma: no cover
    classify_txn_events = None  # type: ignore

try:
    from inputs.onchain.firehose.nlp_event_summarizer import summarize_event_activity
except Exception:  # pragma: no cover
    summarize_event_activity = None  # type: ignore

try:
    from memory.token_memory_index import update_token_txn_memory
except Exception:  # pragma: no cover
    update_token_txn_memory = None  # type: ignore

# Ledger to persist subscores/final score
try:
    from core.token_ledger import set_scores
except Exception:  # pragma: no cover
    def set_scores(*args, **kwargs):  # type: ignore
        logging.info(f"[set_scores fallback] Called with args={args}, kwargs={kwargs}")

# -------------------------------------------------------------------------


class CoreSupervisor:
    """
    Blended supervisor:
      - Collects insights from chart/wallet/social/txn/meta/risk cortices
      - Uses rich txn pipeline (helpers injected to txn.analyze_transactions)
      - Writes sub-scores and final to token ledger
      - Provides identity/observe hooks + AI brain learning methods
    """

    def __init__(self, cortices: Dict[str, Any], *, ai_brain=None, llm_brain=None):
        self.chart = cortices.get("chart")
        self.wallet = cortices.get("wallet")
        self.social = cortices.get("social")
        self.txn = cortices.get("txn")
        self.meta = cortices.get("meta")
        self.risk = cortices.get("risk")
        self.strategy = cortices.get("strategy")
        self.ai_brain = ai_brain
        self.llm_brain = llm_brain
        self.cortex_modules = cortices  # used by observe_global_state()

    def broadcast_chart_signals(self, token_context: Dict[str, Any], chart_insights: Dict[str, Any]):
        """
        Broadcast chart signals and scores to all cortexes and AI/LLM brains, including strategy cortex.
        """
        for name, cortex in (self.cortex_modules or {}).items():
            if hasattr(cortex, "receive_chart_signal"):
                cortex.receive_chart_signal(token_context, chart_insights)
        if self.strategy and hasattr(self.strategy, "receive_chart_signal"):
            self.strategy.receive_chart_signal(token_context, chart_insights)
        if self.ai_brain and hasattr(self.ai_brain, "receive_chart_signal"):
            self.ai_brain.receive_chart_signal(token_context, chart_insights)
        if self.llm_brain and hasattr(self.llm_brain, "receive_chart_signal"):
            self.llm_brain.receive_chart_signal(token_context, chart_insights)

    def share_persona_context(self, persona_context: Dict[str, Any]):
        """
        Share persona context and feedback with all cortexes and brains, including strategy cortex.
        """
        for name, cortex in (self.cortex_modules or {}).items():
            if hasattr(cortex, "update_persona_context"):
                cortex.update_persona_context(persona_context)
        if self.strategy and hasattr(self.strategy, "update_persona_context"):
            self.strategy.update_persona_context(persona_context)
        if self.ai_brain and hasattr(self.ai_brain, "update_persona_context"):
            self.ai_brain.update_persona_context(persona_context)
        if self.llm_brain and hasattr(self.llm_brain, "update_persona_context"):
            self.llm_brain.update_persona_context(persona_context)

    def route_analytics_update(self, update: Dict[str, Any]):
        """
        Route analytics/state updates to all modules for unified decision-making, including strategy cortex.
        """
        for name, cortex in (self.cortex_modules or {}).items():
            if hasattr(cortex, "receive_analytics_update"):
                cortex.receive_analytics_update(update)
        if self.strategy and hasattr(self.strategy, "receive_analytics_update"):
            self.strategy.receive_analytics_update(update)
        if self.ai_brain and hasattr(self.ai_brain, "receive_analytics_update"):
            self.ai_brain.receive_analytics_update(update)
        if self.llm_brain and hasattr(self.llm_brain, "receive_analytics_update"):
            self.llm_brain.receive_analytics_update(update)

    def get_shared_feature_frame(self, token_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build and return a shared feature frame for cross-module analytics, including strategy cortex.
        """
        frame = {}
        if self.chart and hasattr(self.chart, "build_feature_frame"):
            frame = self.chart.build_feature_frame(token_context)
        # Allow other cortexes to contribute features
        for name, cortex in (self.cortex_modules or {}).items():
            if hasattr(cortex, "contribute_features"):
                frame.update(cortex.contribute_features(token_context))
        if self.strategy and hasattr(self.strategy, "contribute_features"):
            frame.update(self.strategy.contribute_features(token_context))
        return frame

    # --- internal ----------------------------------------------------------

    def _token_addr(self, ctx: Dict[str, Any]) -> Optional[str]:
        return ctx.get("token_address") or ctx.get("mint") or ctx.get("token")

    # --- public ------------------------------------------------------------

    def evaluate(self, token_context: Dict[str, Any]) -> Dict[str, Any]:
        token_addr = self._token_addr(token_context)

        # chart
        chart_insights = {}
        try:
            if self.chart:
                chart_insights = self.chart.analyze_token(token_context) or {}
        except Exception as e:
            logging.warning(f"[CoreSupervisor] chart analyze failed: {e}")

        # wallet
        wallet_insights = {}
        try:
            if self.wallet:
                wallet_insights = self.wallet.analyze_wallets(token_context) or {}
        except Exception as e:
            logging.warning(f"[CoreSupervisor] wallet analyze failed: {e}")

        # social
        social_insights = {}
        try:
            if self.social:
                social_insights = self.social.analyze_sentiment(token_context) or {}
        except Exception as e:
            logging.warning(f"[CoreSupervisor] social analyze failed: {e}")


        # strategy
        strategy_insights = {}
        try:
            if self.strategy:
                strategy_insights = self.strategy.analyze_strategy(token_context) or {}
        except Exception as e:
            logging.warning(f"[CoreSupervisor] strategy analyze failed: {e}")

        # ML predictions available in context
        ml_price = token_context.get('ml_price_pred')
        ml_rug = token_context.get('ml_rug_pred')
        ml_wallet = token_context.get('ml_wallet_pred')
        # Use ML predictions in scoring, risk, and analytics
        if ml_rug is not None and ml_rug > 0.7:
            token_context['risk_flags'] = token_context.get('risk_flags', []) + ['ml_rug_high']
        if ml_price is not None:
            token_context['ml_price_score'] = ml_price
        if ml_wallet is not None:
            token_context['ml_wallet_behavior'] = ml_wallet

        # txn
        txn_insights = {}
        try:
            if self.txn:
                if get_recent_transactions:
                    txns = get_recent_transactions(token_addr) if token_addr else []
                else:
                    txns = token_context.get("recent_txns", [])

                try:
                    txn_insights = self.txn.analyze_transactions(
                        token_data=token_context,
                        recent_txns=txns,
                        check_lp_add_event=check_lp_add_event,
                        detect_sniper_behavior=detect_sniper_behavior,
                        classify_pump_pattern=classify_pump_pattern,
                        detect_rug_signature=detect_rug_signature,
                        get_influential_wallet_trigger=get_influential_wallet_trigger,
                        classify_txn_events=classify_txn_events,
                        summarize_event_activity=summarize_event_activity,
                        update_token_txn_memory=update_token_txn_memory,
                    ) or {}
                except TypeError:
                    txn_insights = self.txn.analyze_transactions(token_context) or {}
        except Exception as e:
            logging.warning(f"[CoreSupervisor] txn analyze failed: {e}")

        # meta
        meta_insights = {}
        try:
            if self.meta:
                meta_insights = self.meta.analyze_meta(token_context) or {}
        except Exception as e:
            logging.warning(f"[CoreSupervisor] meta analyze failed: {e}")

        # risk
        risk_insights = {}
        try:
            if self.risk:
                risk_insights = self.risk.analyze_risk(token_context) or {}
        except Exception as e:
            logging.warning(f"[CoreSupervisor] risk analyze failed: {e}")

        # aggregate
        parts = {
            "chart":  chart_insights,
            "wallet": wallet_insights,
            "social": social_insights,
            "strategy": strategy_insights,
            "txn":    txn_insights,
            "meta":   meta_insights,
            "risk":   risk_insights,
        }
        agg = self._aggregate(parts)

        result = {
            "final_score": agg["total"],
            "action": agg["action"],
            "reasoning": agg["reasons"],
            "insights": parts,
        }

        # write to token ledger
        try:
            if token_addr:
                set_scores(
                    token_addr,
                    sub_scores=agg["sub_scores"],
                    final={"score": result["final_score"], "action": result["action"]},
                )
        except Exception as e:
            logging.warning(f"[CoreSupervisor] set_scores failed: {e}")

        return result

