# modules/micro_strategy_tuner.py

import logging
from datetime import datetime
from typing import Any, Dict

from core.live_config import config
from inputs.meta_data.token_metadata import get_token_history
from special.ai_self_tuner import get_current_tuning, get_freeze_state, should_skip_trade
from strategy.strategy_memory import record_strategy_adjustment
from utils.logger import log_event
from utils.universal_input_validator import is_valid_token_address

# === Internal streak memory (kept for micro-level intuition) ===
win_streak = 0
loss_streak = 0
last_result_time = None
MAX_STREAK_MEMORY_MINUTES = 120


def reset_streaks():
    global win_streak, loss_streak, last_result_time
    win_streak = 0
    loss_streak = 0
    last_result_time = None


def register_trade_result(result: str):
    """
    Call this from your executor with the discrete result label:
    ["win", "tp_hit"] => win
    ["loss", "rug", "sl_hit", "early_exit"] => loss
    """
    global win_streak, loss_streak, last_result_time

    now = datetime.utcnow()
    if last_result_time and (now - last_result_time).total_seconds() > MAX_STREAK_MEMORY_MINUTES * 60:
        reset_streaks()

    last_result_time = now

    if result in ["win", "tp_hit", "profit"]:
        win_streak += 1
        loss_streak = 0
    elif result in ["loss", "rug", "sl_hit", "early_exit"]:
        loss_streak += 1
        win_streak = 0


def _apply_core_signals(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Your original Phase 2 heuristic logic (social/chart/wallet/bundle/theme/streak).
    Returns a dict with: aggression, confidence_boost, exit_mode, final_score, notes ...
    """
    score = float(context.get("meta_score", 0))
    token = context.get("token_name", "unknown")
    reasoning = []

    strategy = {
        "buy_delay": 1.0,
        "aggression": "balanced",
        "confidence_boost": 0,
        "exit_mode": "default",
        "final_score": score,
        "notes": []
    }

    # === Social signal
    social = float(context.get("social_score", 0))
    if social > 8:
        strategy["confidence_boost"] += 2
        reasoning.append("High social momentum")
    elif social > 5:
        strategy["confidence_boost"] += 1
        reasoning.append("Moderate social traction")

    # === Chart trend
    trend = context.get("chart_trend", "unknown")
    if trend == "up":
        strategy["aggression"] = "high"
        strategy["confidence_boost"] += 1
        reasoning.append("Chart shows upward trend")
    elif trend == "down":
        strategy["aggression"] = "low"
        strategy["confidence_boost"] -= 1
        strategy["exit_mode"] = "tight_stop"
        reasoning.append("Chart trending down - risk cautious")

    # === Wallet strength
    wallets = int(context.get("wallet_count", 0))
    if wallets >= 15:
        strategy["confidence_boost"] += 2
        reasoning.append("Strong wallet presence")
    elif wallets >= 8:
        strategy["confidence_boost"] += 1
        reasoning.append("Moderate wallet support")

    # === Bundle detection
    if context.get("bundle_detected", False):
        strategy["aggression"] = "high"
        strategy["confidence_boost"] += 2
        reasoning.append("Bundle launch detected")

    # === Meta themes
    themes = context.get("theme", []) or []
    if "celebrity" in themes:
        strategy["confidence_boost"] += 1
        reasoning.append("Celebrity-themed token")
    if "memecoin" in themes:
        strategy["confidence_boost"] += 1
        reasoning.append("Memecoin meta boost")

    # === Freeze on loss streak (local micro layer)
    global loss_streak, win_streak
    if loss_streak >= 3:
        strategy["confidence_boost"] -= 2
        strategy["aggression"] = "low"
        strategy["exit_mode"] = "ultra_safe"
        reasoning.append("Loss streak active - tuning down risk")
    elif win_streak >= 3:
        strategy["confidence_boost"] += 2
        reasoning.append("Win streak detected - reinforcing behavior")

    # === Historical pattern match
    if is_valid_token_address(token):
        try:
            token_history = get_token_history(token)
            if token_history and token_history.get("past_success", False):
                strategy["confidence_boost"] += 1
                reasoning.append("Token pattern matches previous win")
        except Exception as e:
            logging.warning(f"[StrategyTuner] History lookup failed for {token}: {e}")

    # === Final local scoring (pre Phase 3 globals)
    strategy["final_score"] = score + strategy["confidence_boost"]
    strategy["notes"] = reasoning
    return strategy


def tune_strategy_context(context: dict) -> dict:
    """
    Nyx's real-time per-trade intuition engine (Phase 3 aware).
    Integrates Phase 2 heuristics with Phase 3 global AI self tuner.

    Input context typical shape:
        {
            "token_name": str,
            "meta_score": float,
            "social_score": float,
            "chart_trend": str,
            "wallet_count": int,
            "bundle_detected": bool,
            "theme": list[str],
            "volatility": float (optional)  # pass if you have it
        }

    Returns:
        {
            ... (all original fields)
            "skip_trade": bool,
            "tuning_snapshot": dict,    # ai_self_tuner current values
            "freeze_state": dict,       # active freeze state
            "final_score": float        # after Phase 3 adjustments
        }
    """
    token = context.get("token_name", "unknown")

    # Check global freezes from ai_self_tuner
    freeze_active = should_skip_trade()
    freeze_state = get_freeze_state()

    # Run original micro-strategy scoring
    strategy = _apply_core_signals(context)

    # Attach Phase 3 tuning snapshot
    tuning = get_current_tuning()
    strategy["tuning_snapshot"] = tuning
    strategy["freeze_state"] = freeze_state
    strategy["skip_trade"] = False

    if freeze_active:
        strategy["skip_trade"] = True
        strategy["aggression"] = "frozen"
        strategy["exit_mode"] = "no_trade"
        strategy["notes"].append("Global freeze (ai_self_tuner) - skipping trade")
        # We still log it for visibility
        _safe_record(token, strategy)
        log_event(f"🧊 [MicroTuner] Trade skipped for {token} due to global freeze: {freeze_state}")
        return strategy

    # === Apply Phase 3 tuning to micro outcome
    # risk_bias scales the final score, score_boost is additive
    base_score = strategy["final_score"]
    final_score = (base_score * tuning.get("risk_bias", 1.0)) + tuning.get("score_boost", 0.0)

    # Apply volatility floor if provided
    vol_floor = tuning.get("volatility_floor", 0.0)
    vol = context.get("volatility")
    if vol is not None:
        try:
            vol = float(vol)
            if vol < vol_floor:
                # Below tolerance -> reduce aggression and penalize score
                final_score -= 1.0
                strategy["aggression"] = "low"
                strategy["notes"].append(f"Volatility below floor ({vol:.4f} < {vol_floor:.4f})")
        except Exception:
            pass

    # Delay factor moves buy_delay
    strategy["buy_delay"] *= tuning.get("delay_factor", 1.0)

    # Update final score and record
    strategy["final_score"] = final_score

    _safe_record(token, strategy)
    log_event(f"🧠 [MicroTuner] Strategy tuned for {token}: {strategy}")
    return strategy


def _safe_record(token: str, strategy: Dict[str, Any]):
    try:
        record_strategy_adjustment(token, strategy)
    except Exception as e:
        logging.warning(f"[MicroTuner] Failed to record strategy for {token}: {e}")
