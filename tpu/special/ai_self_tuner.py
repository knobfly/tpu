# modules/ai_self_tuner.py
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from strategy.reinforcement_tracker import track_outcome_feedback  # assumed present in your tree
from strategy.strategy_memory import tag_token_result, update_strategy_signal
from strategy.streak_reinforcer import streak_adjust_tuning  # assumed present in your tree
from utils.logger import log_event
from utils.service_status import update_status

update_status("ai_self_tuner")

# === Files & constants ===
TUNE_FILE = "/home/ubuntu/nyx/runtime/logs/ai_tuner_state.json"
HISTORY_FILE = "/home/ubuntu/nyx/runtime/logs/ai_tuner_history.jsonl"
TUNE_COOLDOWN = 15  # seconds between adaptive tuning applications

# Freeze logic
SOFT_FREEZE_AFTER_LOSSES = 3
HARD_FREEZE_AFTER_LOSSES = 6
SOFT_FREEZE_DURATION_SEC = 15 * 60
HARD_FREEZE_DURATION_SEC = 60 * 60

# Default param deltas
DEFAULT_TUNING = {
    "risk_bias": 1.0,       # risk scaling coeff (1.0 neutral)
    "delay_factor": 1.0,    # buy delay multiplier (1.0 neutral)
    "score_boost": 0.0,     # additive score boost (final score += score_boost)
    "volatility_floor": 0.0 # minimum volatility filter to accept trades
}

RISK_ADJUST_STEP = 0.05
SCORE_ADJUST_STEP = 0.5

# Bandit arms (micro-strategy presets)
BANDIT_ARMS = {
    "conservative": {"risk_bias": 0.85, "delay_factor": 1.20, "score_boost": -1.0, "volatility_floor": 0.10},
    "balanced":     {"risk_bias": 1.00, "delay_factor": 1.00, "score_boost":  0.0, "volatility_floor": 0.05},
    "aggressive":   {"risk_bias": 1.30, "delay_factor": 0.80, "score_boost":  1.5, "volatility_floor": 0.00},
}

# Globals
_last_tune_time: float = 0.0
_state: Dict[str, Any] = {
    "current": DEFAULT_TUNING.copy(),
    "soft_freeze": False,
    "hard_freeze": False,
    "soft_freeze_until": 0.0,
    "hard_freeze_until": 0.0,
    "streak": {"wins": 0, "losses": 0},
    "bandit": {
        "arms": {arm: {"n": 0, "sum_reward": 0.0, "avg_reward": 0.0} for arm in BANDIT_ARMS.keys()},
        "last_arm": None
    },
    "last_updated": 0.0,
}

# -------------------------
# Persistence
# -------------------------
def _ensure_logs_dir():
    d = os.path.dirname(TUNE_FILE)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def load_tune_state():
    global _state
    _ensure_logs_dir()
    if os.path.exists(TUNE_FILE):
        try:
            with open(TUNE_FILE, "r") as f:
                _state = json.load(f)
            # ensure we have any new keys
            _state.setdefault("current", DEFAULT_TUNING.copy())
            _state.setdefault("soft_freeze", False)
            _state.setdefault("hard_freeze", False)
            _state.setdefault("soft_freeze_until", 0.0)
            _state.setdefault("hard_freeze_until", 0.0)
            _state.setdefault("streak", {"wins": 0, "losses": 0})
            _state.setdefault("bandit", {"arms": {}, "last_arm": None})
            if "arms" not in _state["bandit"]:
                _state["bandit"]["arms"] = {}
            # ensure all arms exist
            for arm in BANDIT_ARMS.keys():
                _state["bandit"]["arms"].setdefault(arm, {"n": 0, "sum_reward": 0.0, "avg_reward": 0.0})
        except Exception as e:
            logging.warning(f"[Tuner] Failed to load tuning state: {e}")

def save_tune_state():
    try:
        with open(TUNE_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logging.warning(f"[Tuner] Failed to save tuning state: {e}")

def _append_history(entry: Dict[str, Any]):
    try:
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logging.warning(f"[Tuner] Failed to write history: {e}")

# -------------------------
# Bandit logic (UCB1)
# -------------------------
def _ucb1_select_arm() -> str:
    """
    Select the arm using UCB1.
    If an arm hasn't been tried, try it first.
    """
    arms = _state["bandit"]["arms"]
    total = sum(v["n"] for v in arms.values())
    # try unplayed arms first
    for name, stats in arms.items():
        if stats["n"] == 0:
            return name
    # all played at least once -> compute UCB
    log_n = math.log(total)
    best_arm = None
    best_ucb = -1e9
    for name, stats in arms.items():
        n = stats["n"]
        avg = stats["avg_reward"]
        ucb = avg + math.sqrt((2 * log_n) / n)
        if ucb > best_ucb:
            best_ucb = ucb
            best_arm = name
    return best_arm

def _bandit_update(arm: str, reward: float):
    """
    reward expected in [0,1]. >0.5 is positive.
    """
    arm_stats = _state["bandit"]["arms"][arm]
    arm_stats["n"] += 1
    arm_stats["sum_reward"] += reward
    arm_stats["avg_reward"] = arm_stats["sum_reward"] / arm_stats["n"]

# -------------------------
# Core Adjustments
# -------------------------
def _normalize_reward(context: Dict[str, Any]) -> float:
    """
    Convert context (final_score, pnl, etc) into [0,1] reward.
    """
    pnl_pct = context.get("pnl_pct")
    if pnl_pct is not None:
        # clamp pnl to [-100, 300] and map to [0,1]
        pnl_pct = max(-100.0, min(300.0, float(pnl_pct)))
        return (pnl_pct + 100.0) / 400.0  # -100 -> 0.0, +300 -> 1.0
    # fallback to final_score
    fs = float(context.get("final_score", 0))
    # assume score ~[0,100]
    fs = max(0.0, min(100.0, fs))
    return fs / 100.0

def _apply_outcome_trends(outcomes: Dict[str, int]):
    wins = outcomes.get("wins", 0)
    losses = outcomes.get("losses", 0)

    if losses > wins:
        _state["current"]["risk_bias"] = max(0.5, _state["current"]["risk_bias"] - RISK_ADJUST_STEP)
        _state["current"]["score_boost"] = max(-5.0, _state["current"]["score_boost"] - SCORE_ADJUST_STEP)
    elif wins > losses:
        _state["current"]["risk_bias"] = min(2.0, _state["current"]["risk_bias"] + RISK_ADJUST_STEP)

def _update_streaks(outcome: str):
    if outcome in ("win", "profit"):
        _state["streak"]["wins"] += 1
        _state["streak"]["losses"] = 0
    else:
        _state["streak"]["losses"] += 1
        _state["streak"]["wins"] = 0

def _maybe_apply_freeze():
    now = time.time()
    # decay freezes if expired
    if _state["soft_freeze"] and now >= _state["soft_freeze_until"]:
        _state["soft_freeze"] = False
    if _state["hard_freeze"] and now >= _state["hard_freeze_until"]:
        _state["hard_freeze"] = False

    losses = _state["streak"]["losses"]
    if losses >= HARD_FREEZE_AFTER_LOSSES and not _state["hard_freeze"]:
        _state["hard_freeze"] = True
        _state["hard_freeze_until"] = now + HARD_FREEZE_DURATION_SEC
        log_event(f"[Tuner] HARD FREEZE engaged for {HARD_FREEZE_DURATION_SEC/60:.0f}m (losses={losses})")
    elif losses >= SOFT_FREEZE_AFTER_LOSSES and not _state["soft_freeze"]:
        _state["soft_freeze"] = True
        _state["soft_freeze_until"] = now + SOFT_FREEZE_DURATION_SEC
        log_event(f"[Tuner] SOFT FREEZE engaged for {SOFT_FREEZE_DURATION_SEC/60:.0f}m (losses={losses})")

def _apply_bandit_layer(context: Dict[str, Any]):
    """
    Select a bandit arm, merge its params over the current tuning.
    """
    arm = _ucb1_select_arm()
    _state["bandit"]["last_arm"] = arm
    params = BANDIT_ARMS[arm]
    for k, v in params.items():
        _state["current"][k] = v
    # reward will be updated later in tune_strategy() with the outcome
    return arm

def _finalize_and_log(token: str, final_score: float):
    _state["last_updated"] = time.time()
    update_strategy_signal(token, final_score, _state["current"])
    tag_token_result(token, "tuned", final_score)
    save_tune_state()

# -------------------------
# Public API
# -------------------------
def get_current_tuning() -> Dict[str, Any]:
    return _state["current"]

def get_freeze_state() -> Dict[str, Any]:
    return {
        "soft_freeze": _state["soft_freeze"],
        "hard_freeze": _state["hard_freeze"],
        "soft_freeze_until": _state["soft_freeze_until"],
        "hard_freeze_until": _state["hard_freeze_until"]
    }

def should_skip_trade() -> bool:
    """
    Quick helper: return True if a freeze is active.
    """
    _maybe_apply_freeze()
    return _state["soft_freeze"] or _state["hard_freeze"]

def tune_strategy(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point. Call after each evaluation (or after confirming outcome if available).
    context SHOULD include:
      - token_name
      - final_score
      - outcome ("win"/"loss"/"profit"/"stop"...)   -> optional but highly recommended
      - pnl_pct (optional)
      - volatility (optional)
    """
    global _last_tune_time
    now_ts = time.time()
    if now_ts - _last_tune_time < TUNE_COOLDOWN:
        return _state["current"]

    _last_tune_time = now_ts
    token = context.get("token_name", "unknown")
    final_score = float(context.get("final_score", 0.0))
    outcome = context.get("outcome", None)

    try:
        # Streak & freeze logic
        if outcome:
            _update_streaks(outcome)
        _maybe_apply_freeze()

        # Pull global outcome stats (e.g., last X trades wins/losses)
        outcomes = track_outcome_feedback()
        _apply_outcome_trends(outcomes)

        # Streak tuner (external module)
        _state["current"] = streak_adjust_tuning(_state["current"])

        # Bandit layer (select arm + apply)
        chosen_arm = _apply_bandit_layer(context)

        # Volatility-based risk modulation (optional)
        vol = context.get("volatility")
        if vol is not None:
            try:
                vol = float(vol)
                # higher volatility -> reduce delay_factor (enter earlier) but lower score_boost
                if vol > 0.15:
                    _state["current"]["delay_factor"] = max(0.7, _state["current"]["delay_factor"] - 0.05)
                    _state["current"]["score_boost"] = max(-5.0, _state["current"]["score_boost"] - 0.2)
                elif vol < 0.05:
                    _state["current"]["risk_bias"] = min(2.0, _state["current"]["risk_bias"] + 0.03)
            except Exception:
                pass

        # Update bandit reward using the current context (if we actually exited or have PnL/score)
        reward = _normalize_reward(context)
        if chosen_arm:
            _bandit_update(chosen_arm, reward)

        # Save, log, broadcast
        _finalize_and_log(token, final_score)

        entry = {
            "ts": datetime.utcnow().isoformat(),
            "token": token,
            "final_score": final_score,
            "outcome": outcome,
            "reward": reward,
            "chosen_arm": chosen_arm,
            "tuning": _state["current"].copy(),
            "streak": _state["streak"].copy(),
            "freeze": get_freeze_state(),
        }
        _append_history(entry)

        log_event(f"[Tuner] [{token}] tuned={_state['current']} arm={chosen_arm} outcome={outcome} streak={_state['streak']}")
        return _state["current"]

    except Exception as e:
        logging.warning(f"[Tuner] Failed tuning for {token}: {e}")
        return _state["current"]

def learn_from_result(token: str, outcome: dict):
    """
    Store trade outcomes and adjust AI scoring/tuning logic based on results.

    Args:
        token (str): Token address or symbol.
        outcome (dict): Contains keys like:
                        { 'action': 'buy/sell',
                          'profit': float,
                          'confidence': float,
                          'timestamp': str, ... }
    """
    try:
        # Ensure timestamp
        if "timestamp" not in outcome:
            outcome["timestamp"] = datetime.utcnow().isoformat()

        # Log for AI tuning
        logging.info(f"[AI Self Tuner] Learning from {token}: {outcome}")

        # (Extend this to persist results or trigger strategy adjustments)
        # Example: Save to memory logs
        try:
            from strategy.strategy_memory import record_result
            record_result({
                "token": token,
                "result": outcome
            })
        except Exception as e:
            logging.debug(f"[AI Self Tuner] Could not record to strategy memory: {e}")
    except Exception as e:
        logging.error(f"[AI Self Tuner] learn_from_result failed: {e}")

# -------------------------
# Daily/self-learning hooks
# -------------------------
async def daily_learning_cycle(summary: Optional[Dict[str, Any]] = None):
    """
    Optional hook to be scheduled daily:
      - aggregates history
      - soft-resets arms if needed
      - can trigger an LLM reflection (lazy import)
    """
    try:
        # Reset arm stats if they are stale / to avoid overfitting
        for arm, s in _state["bandit"]["arms"].items():
            if s["n"] > 0:
                s["n"] = max(1, int(s["n"] * 0.5))
                s["sum_reward"] *= 0.5
                s["avg_reward"] = s["sum_reward"] / s["n"]

        save_tune_state()

        # Optional: invoke LLM reflection if you want
        try:
            from core.llm.llm_brain import init_llm_brain
            llm = init_llm_brain()
            context = {
                "tuning": _state["current"],
                "streak": _state["streak"],
                "bandit_arms": _state["bandit"]["arms"],
                "summary": summary or {}
            }
            reflection = await llm.reflect({"phase": "daily_learning_cycle", "context": context})
            if reflection:
                log_event(f"[Tuner] Daily reflection stored.")
        except Exception as e:
            logging.warning(f"[Tuner] LLM reflection failed: {e}")

    except Exception as e:
        logging.error(f"[Tuner] daily_learning_cycle failed: {e}")


# Init
load_tune_state()
