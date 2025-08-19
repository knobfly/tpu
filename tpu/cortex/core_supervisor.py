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
        pass

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
        self.ai_brain = ai_brain
        self.llm_brain = llm_brain
        self.cortex_modules = cortices  # used by observe_global_state()

    # --- internal ----------------------------------------------------------

    def _token_addr(self, ctx: Dict[str, Any]) -> Optional[str]:
        return ctx.get("token_address") or ctx.get("mint") or ctx.get("token")

    def _aggregate(self, parts: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        parts: dict with keys: chart/wallet/social/txn/meta/risk -> insight dict
        each insight dict may include its <name>_score numeric key
        """
        mapping = {
            "chart":  "chart_score",
            "wallet": "wallet_score",
            "social": "social_score",
            "txn":    "txn_score",
            "meta":   "meta_score",
            "risk":   "risk_score",
        }
        total = 0.0
        reasons = []
        sub_scores = {}
        for name, blob in parts.items():
            key = mapping[name]
            s = float(blob.get(key, 0.0) or 0.0)
            sub_scores[key] = s
            if s > 0:
                reasons.append({"source": key, "score": s, "details": blob})
                total += s

        if total >= 25:
            action = "buy"
        elif total >= 15:
            action = "watch"
        else:
            action = "ignore"

        return {"total": round(total, 2), "action": action, "reasons": reasons, "sub_scores": sub_scores}

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

        # txn (rich path if available)
        txn_insights = {}
        try:
            if self.txn:
                if get_recent_transactions:
                    txns = get_recent_transactions(token_addr) if token_addr else []
                else:
                    txns = token_context.get("recent_txns", [])

                # prefer extended signature if cortex supports it
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
                    # fall back to simpler signature
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

    def get_identity_profile(self) -> Dict[str, Any]:
        if getattr(self, "ai_brain", None) and hasattr(self.ai_brain, "identity_profile"):
            return self.ai_brain.identity_profile
        return {
            "name": "Nyx",
            "role": "Unknown",
            "chain": "Unknown",
            "mission": "Identity profile unavailable.",
            "drive": "N/A",
            "soul": "⚠️ Missing identity profile.",
        }

    def observe_global_state(self) -> Dict[str, Any]:
        try:
            observations = {}
            for name, cortex in (self.cortex_modules or {}).items():
                if hasattr(cortex, "observe_state"):
                    observations[name] = cortex.observe_state()
            return observations
        except Exception as e:
            logging.warning(f"[CoreSupervisor] Global state observation failed: {e}")
            return {}

    # learning hooks
    def learn(self, signal: Dict[str, Any]) -> None:
        try:
            if self.ai_brain and hasattr(self.ai_brain, "learn"):
                self.ai_brain.learn(signal)
        except Exception as e:
            logging.debug(f"[CoreSupervisor] learn hook failed: {e}")

    def log_token_feedback(self, token: str, outcome: str) -> None:
        try:
            if self.ai_brain and hasattr(self.ai_brain, "log_token_feedback"):
                self.ai_brain.log_token_feedback(token, outcome)
        except Exception as e:
            logging.debug(f"[CoreSupervisor] feedback hook failed: {e}")

