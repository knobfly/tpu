# /strategy/strategy_rotation_scheduler.py
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict

from core.live_config import config
from librarian.data_librarian import librarian
from special.insight_logger import log_ai_insight
from strategy.stop_snipe_defender import get_rug_rate
from strategy.strategy_memory import get_recent_performance
from utils.logger import log_event

try:
    from strategy.contextual_bandit import get_bandit_sync
except Exception:
    get_bandit_sync = None  # we’ll guard against this


# ---- Settings (all overridable via live_config) ------------------------------

ROTATION_INTERVAL = int(config.get("strategy_rotation_interval_sec", 300))  # 5m default

# Define UTC daytime window (e.g., 12..21 ~ 8am–5pm ET)
DAY_START_UTC = int(config.get("day_start_hour_utc", 12))
DAY_END_UTC   = int(config.get("day_end_hour_utc", 21))

# Bandit exploration levels
EPSILON_DAY   = float(config.get("bandit_epsilon_day", 0.00))
EPSILON_NIGHT = float(config.get("bandit_epsilon_night", 0.05))

# Optional soft bias at night (multiply mean_reward for listed arms)
# Example in live_config:
#   "bandit_night_arm_multipliers": {"aggro": 0.92, "balanced": 1.03, "scalper": 1.06}
NIGHT_ARM_MULTIPLIERS: Dict[str, float] = dict(config.get("bandit_night_arm_multipliers", {}) or {})

# Persist last selected “mode” (for dashboards / logs)
PERSIST_MODE = bool(config.get("persist_strategy_mode", True))

# Manual override window (same behavior/name as your original)
manual_override_until = None  # Timestamp for override expiration


# ---- Helpers ----------------------------------------------------------------

def _is_daytime_utc() -> bool:
    h = datetime.utcnow().hour
    return DAY_START_UTC <= h <= DAY_END_UTC


def _safe_tag_mode(mode: str):
    """
    Your original code used librarian.tag(...). If tag() doesn’t exist in your
    DataLibrarian, we fall back to remember(key, value).
    """
    try:
        tag = getattr(librarian, "tag", None)
        if callable(tag):
            tag("strategy_mode", mode)
            return
    except Exception:
        pass
    # fallback: key-value memory
    try:
        librarian.remember("strategy_mode", mode)
    except Exception:
        pass


def _apply_bandit_daynight(is_day: bool):
    """
    Nudge BanditManager based on time of day:
      - set epsilon (explore/exploit balance)
      - optionally apply small arm mean_reward multipliers at night
    """
    if get_bandit_sync is None:
        return

    try:
        bm = get_bandit_sync()
    except Exception as e:
        logging.debug(f"[StrategyScheduler] Bandit not ready yet: {e}")
        return

    # Set epsilon (only if changed)
    target_eps = EPSILON_DAY if is_day else EPSILON_NIGHT
    try:
        if getattr(bm, "epsilon", None) != target_eps:
            bm.epsilon = float(target_eps)
            log_event(f"🎚️ Bandit epsilon set → {bm.epsilon:.3f} ({'day' if is_day else 'night'})")
    except Exception as e:
        logging.debug(f"[StrategyScheduler] failed to set bandit epsilon: {e}")

    # Optional soft multipliers at night (nudge, not override)
    if not is_day and isinstance(NIGHT_ARM_MULTIPLIERS, dict) and NIGHT_ARM_MULTIPLIERS:
        try:
            for arm_name, mult in NIGHT_ARM_MULTIPLIERS.items():
                arm = bm.arms.get(arm_name)
                if not arm:
                    continue
                try:
                    arm.mean_reward *= float(mult)
                except Exception:
                    continue
            log_event(f"🌙 Applied night arm multipliers: {NIGHT_ARM_MULTIPLIERS}")
        except Exception as e:
            logging.debug(f"[StrategyScheduler] arm bias apply failed: {e}")


# ---- Public API (kept same names so main stays unchanged) --------------------

async def run_strategy_scheduler():
    """
    Periodic, lightweight scheduler.
    Keeps the function name identical so your main wiring stays the same.
    """
    log_event("🧠 Strategy rotation scheduler started.")
    while True:
        try:
            await evaluate_and_rotate()
        except Exception as e:
            logging.warning(f"⚠️ Strategy rotation error: {e}")
        await asyncio.sleep(max(30, ROTATION_INTERVAL))

async def evaluate_and_rotate():
    """
    New Day/Night policy:
      - Determine day/night from UTC hour
      - Update config["mode"] for observability only (Bandit makes real choices)
      - Nudge BanditManager epsilon (+ optional arm multipliers at night)
      - Preserve manual override behavior
    """
    global manual_override_until

    # Manual override (same behavior as before)
    if config.get("manual_mode_override"):
        if not manual_override_until:
            manual_override_until = datetime.utcnow() + timedelta(minutes=30)
        elif datetime.utcnow() > manual_override_until:
            config["manual_mode_override"] = False
            manual_override_until = None
            log_event("🔄 Manual strategy override expired.")
        # When overridden, don’t auto-rotate
        return

    is_day = _is_daytime_utc()
    new_mode = "Day" if is_day else "Night"  # informational label only

    # Nudge bandit based on time of day
    _apply_bandit_daynight(is_day=is_day)

    # Persist/announce mode changes for dashboards
    if new_mode != config.get("mode"):
        config["mode"] = new_mode
        if PERSIST_MODE:
            try:
                cfg = dict(config or {})
                save_config(cfg)
            except Exception:
                pass

        _safe_tag_mode(new_mode)
        log_event(f"🧠 Strategy rotated to: {new_mode}")

        try:
            log_ai_insight({
                "timestamp": time.time(),
                "module": "strategy_rotation_scheduler",
                "new_mode": new_mode,
                "utc_hour": datetime.utcnow().hour,
                "epsilon": EPSILON_DAY if is_day else EPSILON_NIGHT,
                "night_bias": NIGHT_ARM_MULTIPLIERS if not is_day else {},
            })
        except Exception:
            pass

def force_strategy_override(mode: str, duration_min: int = 30):
    """
    Manual override remains intact for your ops:
      - sets config["mode"] immediately
      - freezes auto-rotation for duration_min
    """
    global manual_override_until
    config["mode"] = mode
    config["manual_mode_override"] = True
    manual_override_until = datetime.utcnow() + timedelta(minutes=int(duration_min))
    log_event(f"🚨 Manual strategy override set to {mode} for {duration_min} minutes.")
    try:
        log_ai_insight({
            "timestamp": time.time(),
            "module": "strategy_rotation_scheduler",
            "override_mode": mode,
            "duration_min": duration_min
        })
    except Exception:
        pass

def _safe_rug_rate_norm(window_minutes: int = 10) -> float:
    """
    Call get_rug_rate with whatever signature it supports and normalize to 0..1.
    - If it returns a dict with 'rug_rate' (count) -> map to fraction (count/5 capped at 1.0)
    - If it returns a dict with 'risk_score' -> map to fraction (score/10)
    - If it returns a float already, use it.
    """
    try:
        # Preferred: explicit window kw
        res = get_rug_rate(window_minutes=window_minutes)  # type: ignore[arg-type]
    except TypeError:
        # Older signature may accept a token_context dict
        try:
            res = get_rug_rate({"token_address": None, "creator": None})  # type: ignore[arg-type]
        except Exception:
            res = 0.0
    except Exception:
        res = 0.0

    try:
        if isinstance(res, dict):
            if "risk_score" in res:
                # risk_score is 0..10 usually
                return max(0.0, min(1.0, float(res.get("risk_score", 0.0)) / 10.0))
            if "rug_rate" in res:
                # rug_rate looked like a count in your previous code; normalize by 5
                return max(0.0, min(1.0, float(res.get("rug_rate", 0.0)) / 5.0))
        # assume scalar
        return max(0.0, min(1.0, float(res)))
    except Exception:
        return 0.0

def _safe_recent_performance(window_minutes: int = 20) -> str:
    """
    Call get_recent_performance with either 'window_minutes' or 'window_min' and
    return one of {'winning','losing','flat'}.
    """
    try:
        perf = get_recent_performance(window_minutes=window_minutes)  # type: ignore[arg-type]
    except TypeError:
        try:
            perf = get_recent_performance(window_min=window_minutes)  # type: ignore[arg-type]
        except Exception:
            perf = None
    except Exception:
        perf = None

    if isinstance(perf, str):
        p = perf.lower()
        if p in ("winning", "losing", "flat"):
            return p
    # Fallback heuristics if function returns numeric ratio or None
    try:
        ratio = float(perf)
        if ratio > 0.02:
            return "winning"
        if ratio < -0.02:
            return "losing"
    except Exception:
        pass
    return "flat"


async def _annotate_strategy_mode(new_mode: str):
    """
    Prefer librarian.tag if available; otherwise fall back to record_event;
    otherwise no-op but keep a log for visibility.
    """
    try:
        tag_fn = getattr(librarian, "tag", None)
        if callable(tag_fn):
            tag_fn("strategy_mode", new_mode)
            return

        rec_fn = getattr(librarian, "record_event", None)
        if callable(rec_fn):
            await rec_fn("strategy_rotation", {
                "ts": time.time(),
                "mode": new_mode,
            })
            return
    except Exception as e:
        logging.debug(f"[StrategyScheduler] annotate fallback failed: {e}")

    # last resort: just log
    log_event(f"[Librarian.tag] strategy_mode={new_mode} (shim)")


