import json
import logging
import os
from typing import Any, Dict

from librarian.data_librarian import librarian
from special.insight_logger import log_ai_insight
from utils.wallet_tracker import get_wallet_rug_history, get_wallet_tag

REPUTATION_FILE = os.path.expanduser("/home/ubuntu/nyx/runtime/data/dev_reputation.json")
LOGGER = logging.getLogger("dev_reputation")

DEFAULT_DEV_RECORD = {"tokens": 0, "rug": 0, "moon": 0, "loss": 0, "profit": 0, "dead": 0, "score": 0}

def load_reputation() -> Dict[str, Any]:
    if not os.path.exists(REPUTATION_FILE):
        return {}
    try:
        with open(REPUTATION_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"❌ Failed to load dev reputation: {e}")
        return {}

def save_reputation(data: Dict[str, Any]):
    try:
        with open(REPUTATION_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"❌ Failed to save dev reputation: {e}")

def update_dev_score(dev_address: str, result: str):
    """
    Updates a developer's reputation based on the result of a token trade.
    result can be: 'rug', 'moon', 'profit', 'loss', or 'dead'.
    """
    try:
        rep = load_reputation()
        dev_record = rep.get(dev_address, DEFAULT_DEV_RECORD.copy())
        dev_record["tokens"] += 1
        if result in dev_record:
            dev_record[result] += 1

        score_mods = {
            "rug": -5,
            "moon": 3,
            "profit": 2,
            "loss": -1,
            "dead": -2
        }

        dev_record["score"] += score_mods.get(result, 0)
        rep[dev_address] = dev_record
        save_reputation(rep)

        logging.info(f"📈 Updated dev score for {dev_address} → {dev_record['score']} ({result})")

        log_ai_insight("dev_score_update", {
            "dev": dev_address,
            "score": dev_record["score"],
            "result": result,
            "stats": dev_record
        })

        # Optional: tag memory for known rug/moon devs
        if result == "rug":
            librarian.tag_entity(dev_address, "rug_dev")
        elif result == "moon":
            librarian.tag_entity(dev_address, "smart_dev")

    except Exception as e:
        logging.error(f"⚠️ Failed to update dev score for {dev_address}: {e}")  

def get_dev_score(dev_address: str) -> int:
    rep = load_reputation()
    return rep.get(dev_address, {}).get("score", 0)

def is_dev_flagged(dev_address: str) -> bool:
    """
    Returns True if a developer is flagged as high risk based on their reputation score.
    """
    return get_dev_score(dev_address) <= -10

def is_dev_trusted(dev_address: str) -> bool:
    """
    Returns True if a developer has strong positive history.
    """
    return get_dev_score(dev_address) >= 8

def score_dev_reputation(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scores the reputation of the token's dev wallet.
    Penalizes if wallet has rugged before, or is tagged as suspicious.
    """
    result = {
        "dev_reputation_score": 1.0,
        "dev_wallet": None,
        "tag": None,
        "rug_history_count": 0,
        "flags": [],
    }

    dev_wallet = ctx.get("creator_wallet") or ctx.get("deployer_wallet")
    if not dev_wallet:
        result["dev_reputation_score"] = 0.5  # Neutral if unknown
        result["flags"].append("no_wallet")
        return result

    result["dev_wallet"] = dev_wallet

    # Get tag and rug history
    tag = get_wallet_tag(dev_wallet)
    rug_history = get_wallet_rug_history(dev_wallet) or 0

    result["tag"] = tag
    result["rug_history_count"] = rug_history

    score = 1.0
    if tag in ("rugger", "blacklisted", "copy_sniper"):
        score -= 0.3
        result["flags"].append(f"tag:{tag}")
    if rug_history >= 1:
        score -= 0.4
        result["flags"].append("past_rugs")

    # Clamp to range [0, 1]
    result["dev_reputation_score"] = round(max(0.0, min(score, 1.0)), 2)
    return result
