import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from special.micro_strategy_tuner import register_trade_result
from utils.logger import log_event

TRADE_LOG_PATH = "/home/ubuntu/nyx/runtime/data/trade_history.json"

TRADE_LOG = []

def log_trade_result(token: str, result: str, reason: str, timestamp=None):
    """Logs the outcome of a trade to history and sends to strategy tuner."""
    entry = {
        "token": token,
        "result": result,
        "reason": reason,
        "timestamp": timestamp or datetime.utcnow().isoformat()
    }

    try:
        os.makedirs(os.path.dirname(TRADE_LOG_PATH), exist_ok=True)
        if os.path.exists(TRADE_LOG_PATH):
            with open(TRADE_LOG_PATH, "r") as f:
                data = json.load(f)
        else:
            data = []

        data.append(entry)

        with open(TRADE_LOG_PATH, "w") as f:
            json.dump(data[-500:], f, indent=2)  # keep last 500

        # Register result to tuner
        register_trade_result(result.lower())
        log_event(f"📊 Trade result logged + sent to tuner: {token} — {result}/{reason}")

    except Exception as e:
        print(f"[TradeHistory] Failed to write trade result: {e}")

def get_recent_trade_results(hours: int = 6) -> list:
    """
    Return list of trade results within the last `hours` timeframe.
    """
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [trade for trade in TRADE_LOG if datetime.fromisoformat(trade.get("timestamp", "1970-01-01T00:00:00")) >= cutoff]
    except Exception as e:
        import logging
        logging.warning(f"[TradeHistory] Failed to fetch recent trades: {e}")
        return []

def post_trade_strategy_feedback(trade: dict):
    """
    Evaluates a finished trade (crypto or NFT) and logs result to strategy tuner.

    trade = {
        "token_name": str,
        "is_nft": bool,
        "result_reason": str,
        "exit_price": float,
        "entry_price": float,
        "pnl_percent": float,
        "status": str
    }
    """
    result = trade.get("result_reason", "").lower() or trade.get("status", "").lower()
    pnl = trade.get("pnl_percent", 0)

    if "win" in result or "tp" in result or pnl >= 10:
        normalized_result = "win"
    elif "rug" in result or pnl < -95:
        normalized_result = "rug"
    elif "sl" in result or "loss" in result or -60 < pnl < 0:
        normalized_result = "loss"
    elif "early" in result or -5 < pnl < 5:
        normalized_result = "early_exit"
    else:
        normalized_result = "unknown"

    token = trade.get("token_name", "unknown")

    log_trade_result(token, normalized_result, result)


def get_trade_history_summary(since: Optional[datetime] = None, limit=100) -> List[Dict]:
    """Returns a list of trades within the last X hours."""
    if not os.path.exists(TRADE_LOG_PATH):
        return []

    try:
        with open(TRADE_LOG_PATH, "r") as f:
            all_trades = json.load(f)
    except Exception:
        return []

    since = since or (datetime.utcnow() - timedelta(hours=24))
    summary = []

    for trade in all_trades[-limit:]:  # most recent first
        try:
            ts = datetime.fromisoformat(trade.get("timestamp"))
            if ts >= since:
                summary.append({
                    "token": trade.get("token", "???"),
                    "result": trade.get("result", "unknown"),
                    "reason": trade.get("reason", "unspecified"),
                    "timestamp": trade.get("timestamp")
                })
        except Exception:
            continue

    return summary
