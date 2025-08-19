import json
import logging
import os
from collections import defaultdict

from memory.token_outcome_memory import record_token_outcome
from strategy.recent_result_tracker import get_summary
from strategy.reinforcement_index import update_token_result
from strategy.score_memory_logger import get_score_history
from strategy.strategy_memory import tag_token_with_outcome
from utils.logger import log_event

SIGNAL_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/signal_patterns.json"
RUGGED_OUTCOMES = {"rug", "dead", "loss"}

# === Load / Save Signal Pattern Memory ===

def load_signal_patterns():
    if not os.path.exists(SIGNAL_FILE):
        return {}

    try:
        with open(SIGNAL_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_signal_patterns(data):
    try:
        with open(SIGNAL_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# === Reflect outcome into token memory + strategy ===

def reflect_outcome(trade_result: dict):
    """
    Reflects the outcome of a completed trade into memory.
    Updates:
    - token_outcome_memory
    - strategy memory for token tags
    """
    token = trade_result.get("token")
    address = trade_result.get("token_address")
    outcome = trade_result.get("outcome")
    reasoning = trade_result.get("reasoning", [])
    final_score = trade_result.get("final_score", 0)
    signals = trade_result.get("signals", {})

    if not token or not address or not outcome:
        logging.warning(f"[OutcomeReflector] Incomplete trade result: {trade_result}")
        return

    # Store in token outcome memory
    record_token_outcome(address, outcome)

    # Tag strategy memory with outcome for tag weighting
    tag_token_with_outcome(address, outcome, reasoning, final_score)

    # Learn from associated signal traits
    reflect_outcome_from_signals(signals, outcome)

    log_event(f"[OutcomeReflector] Reflected {outcome} for {token}")

# === Learn from signal patterns (LP, whale, etc.) ===

def reflect_outcome_from_signals(signals: dict, outcome: str):
    """
    Learns which signal traits contributed to a given outcome.
    Example:
        signals = {
            "lp_status": "locked",
            "creator": "9x9e...",
            "sniper_overlap": True,
            "whales": True,
            "bundle": False
        }
    """
    if not signals or not outcome:
        return

    data = load_signal_patterns()

    for key, val in signals.items():
        val = str(val).lower()
        if key not in data:
            data[key] = {}
        if val not in data[key]:
            data[key][val] = {}
        if outcome not in data[key][val]:
            data[key][val][outcome] = 0

        data[key][val][outcome] += 1

    save_signal_patterns(data)

# === Reflect on decision accuracy ===

def reflect_on_outcome(token_address: str, result: str) -> dict:
    """
    Reflects on recent score/action decisions vs actual outcome.
    Returns analysis and stores reinforcement feedback.
    """
    history = get_score_history(token_address)
    if not history:
        logging.warning(f"[OutcomeReflector] No score history for {token_address}")
        return {}

    last_entry = history[-1]
    score = last_entry.get("score", 0)
    action = last_entry.get("action", "unknown")
    reasoning = last_entry.get("reasoning", [])
    mode = last_entry.get("mode", "trade")

    reflection = {
        "token": token_address,
        "mode": mode,
        "predicted_action": action,
        "score": score,
        "actual_result": result,
        "was_correct": False,
        "score_delta": 0,
        "reasoning": reasoning,
        "streak_summary": get_summary()
    }

    # Evaluate correctness
    if result in ["profit", "moon"] and action in ["snipe", "buy", "hold"]:
        reflection["was_correct"] = True
        reflection["score_delta"] = +2 if result == "profit" else +5
    elif result in RUGGED_OUTCOMES and action in ["snipe", "buy", "hold"]:
        reflection["was_correct"] = False
        reflection["score_delta"] = -5 if result == "rug" else -2
    elif action in ["ignore", "watch"] and result in ["rug", "dead"]:
        reflection["was_correct"] = True
        reflection["score_delta"] = +1
    else:
        reflection["was_correct"] = False
        reflection["score_delta"] = -2

    # Store final memory
    update_token_result(token_address, result)

    logging.info(f"[Reflector] 🧠 Reflection: {reflection}")
    return reflection
