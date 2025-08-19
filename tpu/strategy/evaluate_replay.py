import json
import logging
import os
from datetime import datetime
from typing import Dict, List

REPLAY_FILE = "/home/ubuntu/nyx/runtime/logs/alpha_replay.json"
MAX_REPLAYS = 100


def _load_replays():
    if not os.path.exists(REPLAY_FILE):
        return []
    try:
        with open(REPLAY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[EvaluateReplay] Failed to load: {e}")
        return []


def _save_replays(data):
    try:
        with open(REPLAY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[EvaluateReplay] Failed to save: {e}")


def record_replay(token: str, signals: dict, outcome: str):
    """
    Records a replay of token analysis and final outcome.
    """
    replays = _load_replays()
    replays.append({
        "token": token,
        "signals": signals,
        "outcome": outcome,
        "timestamp": datetime.utcnow().isoformat()
    })
    if len(replays) > MAX_REPLAYS:
        replays = replays[-MAX_REPLAYS:]
    _save_replays(replays)


def get_replays(limit=20):
    return _load_replays()[-limit:]

def evaluate_replay(trade_history: List[Dict]) -> Dict:
    """
    Evaluates past trades and calculates performance metrics.
    """
    try:
        wins = sum(1 for t in trade_history if t.get("outcome") == "win")
        losses = sum(1 for t in trade_history if t.get("outcome") == "loss")
        total = len(trade_history)

        avg_score = sum(t.get("score", 0) for t in trade_history) / total if total > 0 else 0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / total) * 100, 2) if total > 0 else 0,
            "avg_score": round(avg_score, 2),
        }
    except Exception as e:
        logging.warning(f"[EvaluateReplay] Error evaluating replay: {e}")
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "avg_score": 0}
