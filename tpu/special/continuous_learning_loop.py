# /continuous_learning_loop.py
import asyncio
import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional

from inputs.meta_data.token_meta_learner import TokenMetaLearner
from librarian.data_librarian import librarian
from utils.crash_guardian import beat, register_module, wrap_safe_loop
from utils.logger import log_event
from utils.service_status import update_status

# --- Optional / soft deps (guarded) ---
try:
    from strategy.contextual_bandit import get_bandit_sync
except Exception:  # pragma: no cover
    get_bandit_sync = lambda: None

try:
    from librarian.feature_store import get_feature_store_sync
except Exception:  # pragma: no cover
    get_feature_store_sync = lambda: None

try:
    # your existing ai brain (used for extra context + signals)
    from core.ai_brain import ai_brain
except Exception:  # pragma: no cover
    ai_brain = None

try:
    # strategy memory + reinforcement
    from strategy.strategy_memory import calculate_total_win_rate
    from strategy.strategy_memory import record_result as strategy_record_result
    from strategy.strategy_memory import tag_token_result as strategy_tag_token_result
    from strategy.strategy_memory import update_strategy_signal as strategy_update_signal
except Exception:  # pragma: no cover
    strategy_record_result = None
    strategy_tag_token_result = None
    strategy_update_signal = None
    calculate_total_win_rate = lambda: 0.0

try:
    # if you still have a reinforcement tracker module
    from strategy.reinforcement_tracker import log_trade_feedback
except Exception:  # pragma: no cover
    log_trade_feedback = None


# --------------------------------------
TRADE_HISTORY_PATH = "/home/ubuntu/nyx/runtime/data/trade_history.json"
CHECK_INTERVAL_SEC = 15
MODULE_NAME = "continuous_learning"

# Cache last processed trade timestamp/signature to avoid double-learning
_state_path = "/home/ubuntu/nyx/runtime/learning/continuous_learning_state.json"


meta_learner = TokenMetaLearner()

def _ensure_dirs():
    os.makedirs("/home/ubuntu/nyx/runtime/learning", exist_ok=True)


def _load_state() -> Dict[str, Any]:
    _ensure_dirs()
    state = librarian.load_json_file(_state_path) or {}
    return {
        "last_ts": state.get("last_ts", 0.0),
        "last_sig": state.get("last_sig"),
    }


def _save_state(state: Dict[str, Any]):
    librarian.save_json_file(_state_path, state)


def _load_trade_history() -> List[Dict[str, Any]]:
    if os.path.exists(TRADE_HISTORY_PATH):
        try:
            with open(TRADE_HISTORY_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"[ContinuousLearning] Failed to read trade history: {e}")
            return []
    return []


def _select_new_trades(trades: List[Dict[str, Any]], last_ts: float, last_sig: Optional[str]) -> List[Dict[str, Any]]:
    """
    Very lenient: any trade with ts > last_ts is new.
    If timestamps equal, we optionally use signature ordering (if present).
    """
    new = []
    for t in trades:
        ts = t.get("timestamp") or t.get("time") or 0
        try:
            ts = float(ts)
        except Exception:
            ts = 0.0
        if ts > last_ts:
            new.append(t)
        elif ts == last_ts:
            sig = t.get("signature")
            if sig and sig != last_sig:
                new.append(t)
    # sort by ts, then signature to be deterministic
    new.sort(key=lambda x: (float(x.get("timestamp", x.get("time", 0))), str(x.get("signature", ""))))
    return new


def _shape_reward(pnl_pct: float, outcome: str) -> float:
    """ Reward shaping: scale PnL into (0..1.5) and clamp. Rugs → strong negative.
    """
    if outcome == "rug":
        return 0.0
    if pnl_pct is None:
        return 0.5  # neutral-ish
    # map: -100% => 0  …  +100% => 1.0  … +300% => 1.3 etc (capped)
    shaped = max(0.0, min(1.5, 0.5 + (pnl_pct / 200.0)))
    return shaped


def _build_context_features(trade: Dict[str, Any]) -> Dict[str, float]:
    """
    Extract a consistent, small context vector from trade metadata to feed the bandit.
    Extend freely as your feature_store grows.
    """
    ctx = {}
    ctx["pnl_pct"] = float(trade.get("pnl_pct") or 0.0)
    ctx["hold_time_s"] = float(trade.get("hold_time_s") or 0.0)
    ctx["volume_usd"] = float(trade.get("volume_usd") or 0.0)
    ctx["liquidity_usd"] = float(trade.get("liquidity_usd") or 0.0)
    ctx["pump_score"] = float(trade.get("pump_score") or 0.0)
    ctx["sentiment"] = float(trade.get("sentiment", {}).get("composite", 0.0)) if isinstance(trade.get("sentiment"), dict) else 0.0
    ctx["meta_overlap"] = float(trade.get("meta_overlap") or 0.0)
    ctx["age_s"] = float(trade.get("age_s") or 0.0)
    return ctx


async def _process_trade(trade: Dict[str, Any], bandit, feature_store):
    """
    Core learning step per trade.
    """
    token = trade.get("token") or trade.get("mint")
    strategy = trade.get("strategy", "unknown")
    outcome = trade.get("outcome", "unknown")  # "win" / "loss" / "rug" / ...
    pnl_pct = trade.get("pnl_pct")
    try:
        pnl_pct = float(pnl_pct) if pnl_pct is not None else None
    except Exception:
        pnl_pct = None

    reward = _shape_reward(pnl_pct, outcome)
    context = _build_context_features(trade)

    # === 1) Strategy memory update
    if strategy_record_result:
        strategy_record_result(strategy, outcome)

    if strategy_update_signal:
        strategy_update_signal(token or "unknown", reward, tuner={"auto": True, "from": MODULE_NAME})

    if strategy_tag_token_result and token:
        tag = "win" if outcome == "win" else ("rug" if outcome == "rug" else "loss")
        strategy_tag_token_result(token, tag, score=reward)

    # === 2) Reinforcement tracker (optional)
    if log_trade_feedback and token:
        try:
            log_trade_feedback({
                "token": token,
                "score": reward,
                "action": trade.get("action", "buy"),
                "result": outcome,
                "reasoning": trade.get("reasoning", []),
                "timestamp": trade.get("timestamp", time.time())
            })
        except Exception as e:
            logging.warning(f"[ContinuousLearning] Reinforcement logging failed: {e}")

    # === 3) Bandit update
    if bandit:
        try:
            # we treat each strategy as an "arm"
            bandit.update(arm=strategy, context=context, reward=reward)
        except Exception as e:
            logging.warning(f"[ContinuousLearning] Bandit update failed: {e}")

    # === 4) Feature store
    if feature_store:
        try:
            fs_row = {
                "ts": trade.get("timestamp", time.time()),
                "strategy": strategy,
                "token": token,
                "outcome": outcome,
                "reward": reward,
                **context
            }
            feature_store.append("trades", fs_row)  # store in "trades" table
        except Exception as e:
            logging.warning(f"[ContinuousLearning] Feature store append failed: {e}")

    # === 5) AI brain feedback hooks (optional, no-op if not present)
    if ai_brain:
        try:
            ai_brain.learn_from_trade(trade=trade, reward=reward, context=context)
        except Exception as e:
            logging.debug(f"[ContinuousLearning] ai_brain.learn_from_trade not implemented or failed: {e}")


async def _learning_pass(state: Dict[str, Any]):
    """
    One pass: scan new trades, update all systems, and persist state.
    """
    trades = _load_trade_history()
    new_trades = _select_new_trades(trades, state["last_ts"], state["last_sig"])

    if not new_trades:
        return state  # nothing to do

    bandit = None
    feature_store = None
    try:
        bandit = get_bandit_sync()
    except Exception:
        bandit = None
    try:
        feature_store = get_feature_store_sync()
    except Exception:
        feature_store = None

    for t in new_trades:
        await _process_trade(t, bandit, feature_store)

    # Move state forward to last processed trade
    last = new_trades[-1]
    try:
        last_ts = float(last.get("timestamp") or last.get("time") or time.time())
    except Exception:
        last_ts = time.time()

    last_sig = last.get("signature")
    state["last_ts"] = last_ts
    state["last_sig"] = last_sig

    _save_state(state)
    return state


async def run_continuous_learning():
    """
    Public entry: run this in your brain_loops in main.py
    """
    # Let CrashGuardian know we exist
    update_status(MODULE_NAME)
    register_module(MODULE_NAME, wrap_safe_loop(MODULE_NAME, _run_loop))

    # If called directly (without CrashGuardian wrap), still run:
    await _run_loop()


async def _run_loop():
    """
    Internal loop, wrapped with CrashGuardian via wrap_safe_loop in run_continuous_learning().
    """
    log_event("♻️ ContinuousLearning loop started.")
    state = _load_state()

    while True:
        try:
            beat(MODULE_NAME)
            state = await _learning_pass(state)

            # Optional: periodically report global winrate
            if int(time.time()) % 300 < CHECK_INTERVAL_SEC:  # ~every 5m
                try:
                    wr = calculate_total_win_rate()
                    log_event(f"[ContinuousLearning] 🧠 Aggregate win rate: {wr:.2%}")
                except Exception:
                    pass

        except Exception as e:
            logging.exception(f"[ContinuousLearning] Fatal loop error: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SEC)
