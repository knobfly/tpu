import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta

from strategy.trait_weight_engine import get_trait_score
from strategy.wallet_risk_weighter import score_wallet_risk

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/risk_thermometer.json"
MAX_HISTORY = 100  # Track the last 100 trades

# === Load/save memory ===

def load_risk_memory():
    if not os.path.exists(MEMORY_FILE):
        return deque(maxlen=MAX_HISTORY)
    try:
        with open(MEMORY_FILE, "r") as f:
            data = json.load(f)
            return deque(data, maxlen=MAX_HISTORY)
    except Exception as e:
        logging.warning(f"[RiskThermometer] Failed to load memory: {e}")
        return deque(maxlen=MAX_HISTORY)

def save_risk_memory(data: deque):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(list(data), f, indent=2)
    except Exception as e:
        logging.warning(f"[RiskThermometer] Failed to save memory: {e}")

# === Append outcome after a trade completes ===

def log_trade_outcome(outcome: str):
    history = load_risk_memory()
    history.append({
        "outcome": outcome,
        "timestamp": datetime.utcnow().isoformat()
    })
    save_risk_memory(history)

# === Temporal check helper ===

def within_last_minutes(ts: str, minutes: int) -> bool:
    try:
        dt = datetime.fromisoformat(ts)
        return datetime.utcnow() - dt <= timedelta(minutes=minutes)
    except:
        return False

# === Analyze recent result pressure ===

def get_risk_pressure_summary() -> dict:
    history = load_risk_memory()
    recent = [entry for entry in history if within_last_minutes(entry["timestamp"], 60)]

    rug_count = sum(1 for e in recent if e["outcome"] == "rug")
    loss_count = sum(1 for e in recent if e["outcome"] == "loss")
    dead_count = sum(1 for e in recent if e["outcome"] == "dead")
    profit_count = sum(1 for e in recent if e["outcome"] in ("profit", "moon"))

    total = len(recent)
    risk_score = (rug_count * 2 + loss_count + dead_count * 1.5) - (profit_count * 0.5)

    if risk_score > 8:
        level = "severe"
    elif risk_score > 5:
        level = "high"
    elif risk_score > 2:
        level = "moderate"
    else:
        level = "low"

    return {
        "risk_score": round(risk_score, 2),
        "rug": rug_count,
        "loss": loss_count,
        "dead": dead_count,
        "profit": profit_count,
        "total": total,
        "level": level
    }

# === Live risk estimator ===

def get_risk_temperature(keywords: list[str], wallets: list[str]) -> dict:
    """
    Calculates aggregate risk level from traits and wallet memory.

    Returns:
        {
            "level": "low" | "medium" | "high" | "extreme",
            "score": int,
            "reasons": [str, ...]
        }
    """
    trait_score = 0
    trait_reasons = []

    for k in keywords:
        score = get_trait_score(k)
        if score < 0:
            trait_score += score
            trait_reasons.append(f"{k}: {score}")

    wallet_score, wallet_reasons = score_wallet_risk(wallets)

    total_score = trait_score + wallet_score
    reasons = trait_reasons + wallet_reasons

    # Risk buckets
    if total_score <= -12:
        level = "extreme"
    elif total_score <= -6:
        level = "high"
    elif total_score <= -2:
        level = "medium"
    else:
        level = "low"

    return {
        "level": level,
        "score": total_score,
        "reasons": reasons
    }
