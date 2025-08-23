
import logging
from typing import Dict, Any
from scoring.dynamic_score_engine import dynamic_score_engine

def apply_strategy_logic(features: Dict[str, float], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Applies dynamic scoring and strategy context logic after base scoring.

    This includes:
    - Adaptive weights based on win/loss memory and market behavior
    - Fallback to safe mode on 3-loss streak
    - Boosting or suppressing weights for known devs, rugs, themes, etc.

    Input:
        features: {
            "wallet_score": float,
            "social_score": float,
            "chart_score": float,
            "token_age": float,
            ...
        }

        context: {
            "trusted_dev": bool,
            "lp_never_locked": bool,
            "burn_common": bool,
            "meta_theme": str | None,
            ...
        }

    Returns:
        {
            "final_score": float,
            "weights": Dict[str, float],
            "reasoning": List[str],
            "fallback_triggered": bool,
            "override_trace": List[str]
        }
    """
    result = dynamic_score_engine.score_token(features, context)

    fallback_triggered = dynamic_score_engine.loss_streak >= 3
    override_trace = []

    if fallback_triggered:
        override_trace.append("⛔ Loss streak ≥ 3 — fallback strategy activated (safe mode weights).")

    if context.get("trusted_dev"):
        override_trace.append("✅ Trusted dev detected — dev_trust weight boosted.")

    if context.get("lp_never_locked"):
        override_trace.append("⚠️ LP lock is rare — lp_locked weight reduced.")

    if context.get("burn_common"):
        override_trace.append("🔥 Everyone burns — burn penalty reduced.")

    if context.get("meta_theme"):
        override_trace.append(f"📊 Meta theme: {context['meta_theme']} — adjusted theme relevance.")

    return {
        "final_score": result["final_score"],
        "weights": result["weights"],
        "reasoning": result["reasoning"],
        "fallback_triggered": fallback_triggered,
        "override_trace": override_trace,
        "timestamp": result["timestamp"],
    }
