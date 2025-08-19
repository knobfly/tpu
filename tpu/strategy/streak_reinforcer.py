# /streak_reinforcer.py

import logging
import time
from typing import Dict

from strategy.reinforcement_tracker import get_streak_status
from utils.logger import log_event
from utils.service_status import update_status

update_status("streak_reinforcer")

# === Constants ===
LOSS_FREEZE_THRESHOLD = 3          # soft-freeze trading after N consecutive losses
WIN_HYPERBOOST_THRESHOLD = 4       # go aggressive after N consecutive wins
COOL_OFF_SECONDS = 900             # 15 min cool-off after freeze
BOOST_DECAY_SECONDS = 600          # 10 min hyperboost decay

# === Internal State ===
_last_freeze_ts = 0.0
_last_boost_ts = 0.0


def _now() -> float:
    return time.time()


def should_freeze_trading() -> bool:
    """
    Returns True if Nyx should soft-freeze trading due to a loss streak or active cool-off window.
    """
    global _last_freeze_ts
    s = get_streak_status()
    if s.get("loss_streak", 0) >= LOSS_FREEZE_THRESHOLD:
        _last_freeze_ts = _now()
        log_event(f"[StreakReinforcer] ⚠️ Loss streak hit {s['loss_streak']} → soft-freeze engaged")
        return True

    if _now() - _last_freeze_ts < COOL_OFF_SECONDS:
        return True

    return False


def should_aggressively_trade() -> bool:
    """
    Returns True if Nyx is on a hot streak and should momentarily trade more aggressively.
    """
    global _last_boost_ts
    s = get_streak_status()
    if s.get("win_streak", 0) >= WIN_HYPERBOOST_THRESHOLD:
        _last_boost_ts = _now()
        log_event(f"[StreakReinforcer] 🔥 Win streak hit {s['win_streak']} → hyperboost engaged")
        return True

    if _now() - _last_boost_ts < BOOST_DECAY_SECONDS:
        return True

    return False


def streak_adjust_tuning(tuning: Dict) -> Dict:
    """
    Apply streak-based overrides to the current tuning dict.
    Call this inside ai_self_tuner.tune_strategy() (or right after) to apply streak bias.
    """
    try:
        if should_freeze_trading():
            # Soften everything
            tuning["risk_bias"] = max(0.6, tuning.get("risk_bias", 1.0) * 0.8)
            tuning["score_boost"] = min(0.0, tuning.get("score_boost", 0.0) - 1.0)
            tuning["delay_factor"] = min(3.0, tuning.get("delay_factor", 1.0) * 1.5)

        elif should_aggressively_trade():
            # Turn it up slightly, but bounded
            tuning["risk_bias"] = min(2.0, tuning.get("risk_bias", 1.0) * 1.15)
            tuning["score_boost"] = min(5.0, tuning.get("score_boost", 0.0) + 0.75)
            tuning["delay_factor"] = max(0.6, tuning.get("delay_factor", 1.0) * 0.85)

        return tuning

    except Exception as e:
        logging.warning(f"[StreakReinforcer] Failed to adjust tuning: {e}")
        return tuning


def get_streak_report() -> str:
    """Human-readable snapshot for Telegram `/ai_insights` or `/brain_status`."""
    s = get_streak_status()
    freeze_left = max(0, COOL_OFF_SECONDS - int(_now() - _last_freeze_ts)) if _last_freeze_ts else 0
    boost_left  = max(0, BOOST_DECAY_SECONDS - int(_now() - _last_boost_ts)) if _last_boost_ts else 0

    return (
        "🎯 *Streak Status*\n"
        f"- Win streak: `{s.get('win_streak', 0)}`\n"
        f"- Loss streak: `{s.get('loss_streak', 0)}`\n"
        f"- Freeze active: `{'yes' if should_freeze_trading() else 'no'}` ({freeze_left}s left)\n"
        f"- Hyperboost active: `{'yes' if should_aggressively_trade() else 'no'}` ({boost_left}s left)\n"
    )
