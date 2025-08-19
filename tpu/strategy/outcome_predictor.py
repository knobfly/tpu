# === sniper_bot/modules/strategy/outcome_predictor.py ===

import json
import os
from collections import defaultdict
from datetime import datetime

from strategy.signal_pattern_tracker import load_signal_patterns

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/outcome_predictions.json"
MAX_HISTORY = 200

# === Pattern-based prediction ===
def predict_outcome_from_signals(signals: dict) -> dict:
    """
    Predicts likelihoods of each outcome based on past pattern memory.

    signals = {
        "lp_status": "locked",
        "creator": "9x9eX...",
        "sniper_overlap": True,
        "whales": True,
        "bundle": False
    }

    Returns:
        {
            "profit": 0.56,
            "loss": 0.21,
            "rug": 0.14,
            "moon": 0.05,
            ...
        }
    """
    memory = load_signal_patterns()
    outcome_counts = defaultdict(int)
    total_weight = 0

    for key, val in signals.items():
        str_val = str(val).lower()
        trait_memory = memory.get(key, {}).get(str_val, {})
        for outcome, count in trait_memory.items():
            if count > 0:
                outcome_counts[outcome] += count
                total_weight += count

    if total_weight == 0:
        return {}

    prediction = {
        outcome: round(count / total_weight, 3)
        for outcome, count in outcome_counts.items()
    }

    return prediction


# === Prediction memory tracker ===
def load_predictions():
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_predictions(predictions):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(predictions[-MAX_HISTORY:], f, indent=2)
    except Exception:
        pass

def log_prediction(token_address: str, prediction: str, reasoning: list):
    predictions = load_predictions()
    predictions.append({
        "token": token_address,
        "prediction": prediction,
        "reasoning": reasoning,
        "timestamp": datetime.utcnow().isoformat(),
        "outcome": None
    })
    save_predictions(predictions)

def update_actual_outcome(token_address: str, actual_outcome: str):
    predictions = load_predictions()
    for entry in reversed(predictions):
        if entry["token"] == token_address and entry["outcome"] is None:
            entry["outcome"] = actual_outcome
            break
    save_predictions(predictions)

def get_accuracy_summary():
    data = load_predictions()
    correct = 0
    total = 0
    for entry in data:
        predicted = entry["prediction"]
        actual = entry["outcome"]
        if not actual:
            continue
        if predicted == actual:
            correct += 1
        total += 1
    accuracy = (correct / total) * 100 if total else 0
    return {
        "total": total,
        "correct": correct,
        "accuracy_percent": round(accuracy, 2)
    }

