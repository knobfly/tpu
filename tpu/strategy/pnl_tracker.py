import json
import os
from datetime import datetime

PNL_LOG_PATH = "/home/ubuntu/nyx/runtime/memory/strategy/trade_pnl.json"
MAX_HISTORY = 500

def load_pnl_log():
    if not os.path.exists(PNL_LOG_PATH):
        return []
    try:
        with open(PNL_LOG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_pnl_log(data):
    try:
        with open(PNL_LOG_PATH, "w") as f:
            json.dump(data[-MAX_HISTORY:], f, indent=2)
    except Exception:
        pass

def log_trade_pnl(result: dict):
    """
    Records a completed trade result with PnL, outcome, reasoning.
    """
    history = load_pnl_log()
    history.append({
        "token": result.get("token"),
        "address": result.get("token_address"),
        "score": result.get("final_score"),
        "reasoning": result.get("reasoning", []),
        "signals": result.get("signals", {}),
        "pnl": result.get("pnl", 0),
        "outcome": result.get("outcome", "unknown"),
        "timestamp": datetime.utcnow().isoformat()
    })
    save_pnl_log(history)

def get_pnl_summary(limit=50):
    data = load_pnl_log()
    recent = data[-limit:]

    total_trades = len(recent)
    wins = sum(1 for t in recent if t["pnl"] > 0)
    losses = sum(1 for t in recent if t["pnl"] <= 0)
    avg_pnl = round(sum(t["pnl"] for t in recent) / total_trades, 2) if total_trades else 0

    return {
        "total": total_trades,
        "wins": wins,
        "losses": losses,
        "winrate": round((wins / total_trades) * 100, 2) if total_trades else 0,
        "avg_pnl": avg_pnl,
        "history": recent
    }
