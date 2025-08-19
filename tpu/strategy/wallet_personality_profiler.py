import json
import os
from collections import defaultdict
from datetime import datetime

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/wallets/personality_profiles.json"
MAX_HISTORY = 200

def load_wallet_profiles():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_wallet_profiles(data):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except:
        pass

def update_wallet_personality(wallet: str, traits: dict):
    """
    Update the personality profile of a wallet based on traits observed in a trade.
    Example traits:
      {
        "early_entry": True,
        "fast_exit": True,
        "sniper": True,
        "holds_rugs": True,
        "high_conf": False,
        "exit_on_launch": True
      }
    """
    data = load_wallet_profiles()
    profile = data.get(wallet, {
        "traits": defaultdict(int),
        "last_updated": None,
        "history": []
    })

    for k, v in traits.items():
        if v:
            profile["traits"][k] = profile["traits"].get(k, 0) + 1

    profile["last_updated"] = datetime.utcnow().isoformat()
    profile["history"].append(traits)

    if len(profile["history"]) > MAX_HISTORY:
        profile["history"] = profile["history"][-MAX_HISTORY:]

    data[wallet] = profile
    save_wallet_profiles(data)

def get_wallet_profile(wallet: str) -> dict:
    data = load_wallet_profiles()
    profile = data.get(wallet)
    if not profile:
        return {"traits": {}, "summary": "Unknown personality."}

    traits = profile["traits"]
    summary = []

    if traits.get("early_entry", 0) >= 5:
        summary.append("Early sniper")
    if traits.get("fast_exit", 0) >= 5:
        summary.append("Quick dumper")
    if traits.get("holds_rugs", 0) >= 3:
        summary.append("Rug magnet")
    if traits.get("high_conf", 0) >= 4:
        summary.append("Strong signaler")
    if traits.get("exit_on_launch", 0) >= 4:
        summary.append("Launch phase exit")

    return {
        "traits": traits,
        "summary": ", ".join(summary) if summary else "Neutral wallet"
    }

def get_top_personality_wallets(trait: str, limit=10):
    data = load_wallet_profiles()
    scored = []
    for wallet, profile in data.items():
        count = profile["traits"].get(trait, 0)
        if count > 0:
            scored.append((wallet, count))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
