import json
import os
from collections import defaultdict
from typing import Dict, List

MEMORY_PATH = "/home/ubuntunyx/runtime/logs/wallet_strategy_memory.json"

# === Load memory or initialize ===
if os.path.exists(MEMORY_PATH):
    with open(MEMORY_PATH, "r") as f:
        memory: Dict[str, Dict[str, Dict[str, int]]] = json.load(f)
else:
    memory = {}

# === Save to disk ===
def save_memory():
    try:
        with open(MEMORY_PATH, "w") as f:
            json.dump(memory, f, indent=2)
    except Exception as e:
        print(f"❌ Failed to save wallet strategy memory: {e}")

# === Record trade result per wallet and strategy ===
def record_result(wallet_address: str, strategy: str, result: str):
    wallet_data = memory.setdefault(wallet_address, {})
    strat_data = wallet_data.setdefault(strategy, {"wins": 0, "losses": 0})

    if result == "win":
        strat_data["wins"] += 1
    elif result == "loss":
        strat_data["losses"] += 1

    save_memory()

# === Get best performing strategy for a specific wallet ===
def get_best_strategy_for_wallet(wallet_address: str) -> str:
    wallet_data = memory.get(wallet_address, {})
    best_strategy = None
    best_score = -1.0

    for strat, stats in wallet_data.items():
        total = stats["wins"] + stats["losses"]
        if total == 0:
            continue
        winrate = stats["wins"] / total
        if winrate > best_score:
            best_score = winrate
            best_strategy = strat

    return best_strategy or "balanced"

# === Get wallet addresses that perform well with a strategy ===
def get_wallets_for_strategy(strategy: str) -> List[str]:
    good_wallets = []

    for wallet, strategies in memory.items():
        stats = strategies.get(strategy)
        if not stats:
            continue
        total = stats["wins"] + stats["losses"]
        if total < 3:
            continue
        winrate = stats["wins"] / total
        if winrate >= 0.65:
            good_wallets.append(wallet)

    return good_wallets

# === Optional: Clear memory (not exposed by default) ===
def clear_memory():
    global memory
    memory = {}
    save_memory()
