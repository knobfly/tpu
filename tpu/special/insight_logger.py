import json
import logging
import os
from datetime import datetime

from core.live_config import config
from librarian.data_librarian import librarian
from strategy.strategy_memory import update_meta_keywords
from utils.logger import log_event as core_log_event
from utils.service_status import update_status
from utils.universal_input_validator import validate_token_record

INSIGHT_PATH = "/home/ubuntu/nyx/runtime/logs/insights.json"
MAX_ENTRIES = 1000
_cached = []
TRADE_LOG_PATH = "/home/ubuntu/nyx/runtime/data/trade_history.json"


# === Core Insight Logging ===
def log_insight(category: str, data: dict):
    try:
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "category": category,
            "data": validate_token_record(data)
        }
        _cached.append(entry)
        if len(_cached) >= 10:
            flush_insights()
    except Exception as e:
        logging.warning(f"⚠️ Insight logging failed: {e}")


def flush_insights():
    try:
        update_status("insight_logger")
        if not _cached:
            return

        if not os.path.exists(INSIGHT_PATH):
            with open(INSIGHT_PATH, "w") as f:
                json.dump([], f)

        with open(INSIGHT_PATH, "r") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = []

        existing.extend(_cached)
        if len(existing) > MAX_ENTRIES:
            existing = existing[-MAX_ENTRIES:]

        with open(INSIGHT_PATH, "w") as f:
            json.dump(existing, f, indent=2)

        _cached.clear()
    except Exception as e:
        logging.warning(f"⚠️ Failed to flush insights: {e}")


# === Strategy & Scoring Insights ===
def log_strategy_change(reason: str, new_mode: str):
    log_insight("strategy", {
        "reason": reason,
        "new_mode": new_mode,
        "current_config": {
            "mode": config.get("mode"),
            "turbo": config.get("turbo_mode"),
            "compute_boost": config.get("use_compute_unit_boost")
        }
    })


def log_scoring_insight(token: str, features: dict, score: float, decision: str):
    log_insight("score", {
        "token": token,
        "features": validate_token_record(features),
        "score": round(score, 3),
        "decision": decision
    })


# === Wallet Insights ===
def log_wallet_usage(event: str, details: dict):
    log_insight("wallet", {
        "event": event,
        "details": validate_token_record(details)
    })


# === AI & Trade Insights ===
def log_ai_insight(event: str, context: dict = None):
    context = validate_token_record(context or {})

    if context.get("result") in ["win", "loss", "manual", "rug", "exit"]:
        append_trade_log({
            "timestamp": context.get("timestamp") or datetime.utcnow().isoformat(),
            "token": context.get("token", "N/A"),
            "outcome": context.get("result", "unknown"),
            "reason": context.get("reason", "N/A"),
            "pnl_pct": context.get("pnl", 0),
            "wallet": context.get("wallet", "unknown"),
            "score": context.get("score", 0),
            "tags": context.get("tags", []),
        })

    log_insight("ai", {
        "event": event,
        "context": context,
        "config_snapshot": {
            "mode": config.get("mode"),
            "buy_amount": config.get("buy_amount"),
            "sell_profit_percent": config.get("sell_profit_percent"),
            "strategy_rotation": config.get("strategy_rotation"),
            "ai_strategy": config.get("ai_strategy"),
            "rebalance_enabled": config.get("wallet_rebalance", False),
            "stop_snipe": config.get("use_stop_snipe", False),
        }
    })


def log_sell_insight(token: str, profit_pct: float, reason: str, metadata: dict = None, strategy: str = None):
    log_insight("sell", {
        "token": token,
        "profit_pct": round(profit_pct, 4),
        "exit_reason": reason,
        "strategy": strategy or config.get("mode"),
        "metadata": validate_token_record(metadata or {}),
        "ai": {
            "enabled": config.get("ai_strategy"),
            "trailing_stop": config.get("trailing_stop", {}).get("enabled"),
            "goal_profit": config.get("goal_profit")
        }
    })


def log_scanner_insight(source, *args, **kwargs):
    insight = {
        "source": source,
        "timestamp": datetime.utcnow().isoformat()
    }

    if args:
        if isinstance(args[0], dict):
            insight.update(validate_token_record(args[0]))
        elif len(args) >= 3:
            insight["token"] = args[0]
            insight["sentiment"] = args[1]
            insight["volume"] = args[2]
            if len(args) > 3:
                insight["result"] = args[3]

    if kwargs:
        insight.update(validate_token_record(kwargs))

    token = insight.get("token") or insight.get("mint") or insight.get("address")
    if token:
        # Extract keywords from insight keys
        keywords = [k for k in insight.keys() if isinstance(k, str)]
        update_meta_keywords(token, keywords)

    core_log_event(f"[Insight] {source} → {insight}")


def log_trade_insight(
    token: str,
    action: str,
    profit_pct: float = None,
    duration_sec: float = None,
    token_type: str = None,
    scanner: str = None,
    score: float = None,
    strategy: str = None,
    slippage: float = None,
    confidence: float = None,
    meta_keywords: list = None,
    volume: float = None,
    fees: dict = None
):
    log_insight("trade", validate_token_record({
        "token": token,
        "action": action,
        "profit_pct": round(profit_pct, 4) if profit_pct is not None else None,
        "duration_sec": round(duration_sec, 2) if duration_sec else None,
        "token_type": token_type,
        "scanner": scanner,
        "score": round(score, 3) if score is not None else None,
        "strategy": strategy or config.get("mode"),
        "slippage": round(slippage, 2) if slippage else None,
        "confidence": round(confidence, 2) if confidence else None,
        "meta_keywords": meta_keywords or [],
        "volume": round(volume, 2) if volume else None,
        "fees": fees or {},
        "ai_enabled": config.get("ai_strategy")
    }))


# === Trade Logs ===
def append_trade_log(entry: dict):
    try:
        entry = validate_token_record(entry)

        if not os.path.exists(TRADE_LOG_PATH):
            with open(TRADE_LOG_PATH, "w") as f:
                json.dump([entry], f, indent=2)
            return

        with open(TRADE_LOG_PATH, "r") as f:
            try:
                trades = json.load(f)
            except json.JSONDecodeError:
                trades = []

        trades.append(entry)
        if len(trades) > 500:
            trades = trades[-500:]

        with open(TRADE_LOG_PATH, "w") as f:
            json.dump(trades, f, indent=2)
    except Exception as e:
        logging.warning(f"⚠️ Failed to append trade log: {e}")


def get_trade_history_summary(limit=20):
    if not os.path.exists(TRADE_LOG_PATH):
        return "❌ No trade history found."

    try:
        with open(TRADE_LOG_PATH, "r") as f:
            trades = json.load(f)
    except Exception as e:
        return f"❌ Failed to read trade log: {e}"

    if not isinstance(trades, list):
        return "❌ Trade history is corrupted or in the wrong format."

    trades = trades[-limit:]
    lines = ["📊 *Recent Trades:*"]
    for trade in reversed(trades):
        trade = validate_token_record(trade)
        token = trade.get("token", "Unknown")
        outcome = trade.get("outcome", "N/A")
        reason = trade.get("reason", "No reason")
        pnl = trade.get("pnl_pct", "??")
        ts = trade.get("timestamp", "")[:16].replace("T", " ")
        lines.append(f"• `{token}` | *{outcome}* | {pnl}% | `{reason}` — `{ts}`")

    return "\n".join(lines)


def generate_daily_summary() -> str:
    if not os.path.exists(INSIGHT_PATH):
        return "No trade data available."

    try:
        with open(INSIGHT_PATH, "r") as f:
            data = json.load(f)
    except Exception:
        return "Could not read insights file."

    today = datetime.utcnow().date()
    trades = [d for d in data if d["category"] == "trade" and d.get("timestamp", "").startswith(str(today))]
    if not trades:
        return "No trades recorded today."

    wins = [t for t in trades if (t["data"].get("profit_pct") or 0) > 0]
    losses = [t for t in trades if (t["data"].get("profit_pct") or 0) <= 0]
    rugs = [t for t in trades if "rug" in (t["data"].get("token_type", "") or "").lower()]
    total_profit = sum(t["data"].get("profit_pct", 0) for t in trades)

    best = max(trades, key=lambda x: x["data"].get("profit_pct", 0))
    worst = min(trades, key=lambda x: x["data"].get("profit_pct", 0))

    summary = [
        f"📅 *Daily Summary* — `{today}`",
        f"• Total Trades: {len(trades)}",
        f"• Wins: {len(wins)} | Losses: {len(losses)} | Rugs: {len(rugs)}",
        f"• Win Rate: {round((len(wins)/len(trades))*100, 1)}%",
        f"• Total Profit: {round(total_profit, 4)}%",
        f"• Best Trade: `{best['data']['token']}` {best['data'].get('profit_pct', 0):.2f}%",
        f"• Worst Trade: `{worst['data']['token']}` {worst['data'].get('profit_pct', 0):.2f}%"
    ]
    return "\n".join(summary)
