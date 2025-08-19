import json
import os
from collections import defaultdict
from datetime import datetime

META_STRATEGY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/meta_strategy_log.json"
MAX_HISTORY = 500

def load_meta_strategy_log():
    if not os.path.exists(META_STRATEGY_FILE):
        return []
    try:
        with open(META_STRATEGY_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_meta_strategy_log(log):
    try:
        with open(META_STRATEGY_FILE, "w") as f:
            json.dump(log[-MAX_HISTORY:], f, indent=2)
    except:
        pass

def log_meta_strategy(strategy_name: str, outcome: str, context: dict = {}):
    log = load_meta_strategy_log()
    log.append({
        "timestamp": datetime.utcnow().isoformat(),
        "strategy": strategy_name,
        "outcome": outcome,
        "context": context
    })
    save_meta_strategy_log(log)

def get_meta_strategy_summary():
    """
    Aggregates success rates of different strategies across history.
    """
    data = load_meta_strategy_log()
    summary = defaultdict(lambda: defaultdict(int))

    for entry in data:
        strategy = entry.get("strategy")
        outcome = entry.get("outcome")
        if strategy and outcome:
            summary[strategy][outcome] += 1

    final = {}
    for strat, outcomes in summary.items():
        total = sum(outcomes.values())
        if total == 0:
            continue
        win_score = outcomes.get("profit", 0) + 3 * outcomes.get("moon", 0)
        loss_score = outcomes.get("loss", 0) + 2 * outcomes.get("dead", 0) + 4 * outcomes.get("rug", 0)
        win_ratio = round(win_score / total, 3)
        loss_ratio = round(loss_score / total, 3)
        final[strat] = {
            "total": total,
            "win_ratio": win_ratio,
            "loss_ratio": loss_ratio,
            "raw": dict(outcomes)
        }

    return dict(final)

def get_best_strategies(threshold=0.6):
    summary = get_meta_strategy_summary()
    return [s for s, v in summary.items() if v["win_ratio"] >= threshold]

def get_underperforming_strategies(threshold=0.4):
    summary = get_meta_strategy_summary()
    return [s for s, v in summary.items() if v["loss_ratio"] >= threshold]

