import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict

from defense.honeypot_similarity_scanner import save_new_rug_pattern  # Auto-learning
from utils.logger import log_event
from utils.service_status import update_status

update_status("reinforcement_tracker")

# === Constants & Globals ===
TRACK_FILE = "/home/ubuntu/nyx/runtime/logs/reinforcement_history.json"
MAX_HISTORY = 500
STREAK_FILE = "/home/ubuntu/nyx/runtime/logs/trade_streaks.json"
MEMORY_FILE = "/home/ubuntu/nyx/runtime/logs/reinforcement_memory.json"

_memory: Dict = {"trades": [], "wins": 0, "losses": 0, "last_update": time.time()}
_streak: Dict = {"win_streak": 0, "loss_streak": 0, "last_result": None}


# === Load & Save ===
def load_history():
    global _memory
    if os.path.exists(TRACK_FILE):
        try:
            with open(TRACK_FILE, "r") as f:
                _memory = json.load(f)
        except Exception as e:
            logging.warning(f"[Reinforcement] Failed to load history: {e}")


def save_history():
    try:
        with open(TRACK_FILE, "w") as f:
            json.dump(_memory, f, indent=2)
    except Exception as e:
        logging.warning(f"[Reinforcement] Failed to save history: {e}")


def save_streak():
    try:
        with open(STREAK_FILE, "w") as f:
            json.dump(_streak, f, indent=2)
    except Exception as e:
        logging.warning(f"[Reinforcement] Failed to save streak: {e}")


def load_memory() -> Dict[str, Any]:
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_memory(data: Dict[str, Any]):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# === Token Result Tracking ===
def update_token_result(token_address: str, result: str):
    """
    Updates the result and reputation score for a token.
    Valid results: 'rug', 'profit', 'loss', 'moon', 'dead'
    """
    memory = load_memory()
    record = memory.get(token_address, {
        "profit": 0, "loss": 0, "rug": 0, "moon": 0, "dead": 0, "score": 0, "log": []
    })

    if result in record:
        record[result] += 1

    score_map = {"profit": 2, "moon": 3, "loss": -1, "rug": -5, "dead": -2}
    record["score"] += score_map.get(result, 0)
    record["log"].append({"result": result, "timestamp": str(datetime.utcnow())})

    memory[token_address] = record
    save_memory(memory)

def get_summary(hours: int = 24) -> dict:
    """
    Summarizes reinforcement feedback logs within the last X hours.
    Returns a dictionary of stats (positive/negative reinforcement counts, avg score changes, etc).
    """
    try:
        if not os.path.exists(TRACK_FILE):
            return {}

        cutoff = time.time() - (hours * 3600)
        positives = 0
        negatives = 0
        total_delta = 0.0
        count = 0

        with open(TRACK_FILE, "r") as f:
            for line in f:
                try:
                    log = json.loads(line)
                    if log.get("timestamp", 0) < cutoff:
                        continue
                    delta = log.get("score_delta", 0)
                    total_delta += delta
                    count += 1
                    if delta > 0:
                        positives += 1
                    elif delta < 0:
                        negatives += 1
                except:
                    continue

        avg_change = total_delta / count if count else 0.0
        return {
            "positives": positives,
            "negatives": negatives,
            "average_delta": round(avg_change, 4),
            "total_events": count
        }

    except Exception as e:
        logging.warning(f"[ReinforcementTracker] Failed to summarize: {e}")
        return {}

def get_token_score(token_address: str) -> int:
    memory = load_memory()
    return memory.get(token_address, {}).get("score", 0)


def tag_token(token_address: str) -> str:
    memory = load_memory()
    rec = memory.get(token_address, {})
    if rec.get("rug", 0) > 0:
        return "RUG"
    elif rec.get("moon", 0) > 0:
        return "MOON"
    elif rec.get("dead", 0) > 0:
        return "DEAD"
    elif rec.get("loss", 0) > 0:
        return "LOSS"
    elif rec.get("profit", 0) > 0:
        return "WIN"
    else:
        return "NEW"


def get_wallet_result_score(wallet_address: str) -> int:
    memory = load_memory()
    score = 0
    seen = 0

    for token, data in memory.items():
        buyers = data.get("wallets", [])
        if wallet_address in buyers:
            seen += 1
            score += (
                data.get("profit", 0) * 2 +
                data.get("moon", 0) * 3 -
                data.get("loss", 0) * 1 -
                data.get("rug", 0) * 4 -
                data.get("dead", 0) * 2
            )

    return 0 if seen == 0 else round(score / seen)


# === Trade Feedback Logging ===
def log_trade_feedback(entry: Dict[str, Any]):
    """
    Logs the trade score, mode, and strategy outcome.
    """
    try:
        token = entry.get("token", "unknown")
        result = entry.get("result") or entry.get("strategy", {}).get("outcome", "unknown")

        log_entry = {
            "mode": entry.get("mode"),
            "score": entry.get("score"),
            "action": entry.get("action"),
            "reasoning": entry.get("reasoning", []),
            "strategy": entry.get("strategy", {}),
            "timestamp": entry.get("timestamp", str(datetime.utcnow())),
        }

        _memory["trades"].append({
            "token": token,
            "result": result,
            **log_entry
        })

        if result == "win" or result == "profit":
            _memory["wins"] += 1
        elif result in ["loss", "rug"]:
            _memory["losses"] += 1

        if len(_memory["trades"]) > MAX_HISTORY:
            _memory["trades"] = _memory["trades"][-MAX_HISTORY:]

        _memory["last_update"] = time.time()

        update_streaks("win" if result in ["win", "profit"] else "loss" if result in ["loss", "rug"] else result)

        # Auto-learn rug patterns
        if result == "rug":
            logging.warning(f"[Reinforcement] Auto-learning rug pattern for {token}")
            save_new_rug_pattern(token, label=f"rug_{int(time.time())}")

        save_history()
        save_streak()
        log_event(f"[Reinforcement] Recorded trade feedback: {token} ({result})")

    except Exception as e:
        logging.warning(f"[Reinforcement] Failed logging trade: {e}")


def track_outcome_feedback() -> Dict:
    try:
        return {
            "wins": _memory.get("wins", 0),
            "losses": _memory.get("losses", 0),
            "total": len(_memory.get("trades", [])),
        }
    except Exception as e:
        logging.warning(f"[Reinforcement] Error reading outcomes: {e}")
        return {"wins": 0, "losses": 0, "total": 0}


# === Streak Management ===
def update_streaks(result: str):
    global _streak
    if result == "win":
        _streak["win_streak"] = _streak["win_streak"] + 1 if _streak["last_result"] == "win" else 1
        _streak["loss_streak"] = 0
    elif result == "loss":
        _streak["loss_streak"] = _streak["loss_streak"] + 1 if _streak["last_result"] == "loss" else 1
        _streak["win_streak"] = 0

    _streak["last_result"] = result
    log_event(f"[Streak] Win streak: {_streak['win_streak']} | Loss streak: {_streak['loss_streak']}")


def get_streak_status() -> Dict:
    return _streak.copy()


# === Init ===
load_history()
