import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.wallet_tracker import get_wallet_tag

CABAL_WINDOW_SECONDS = 10
CABAL_WALLET_THRESHOLD = 3

# token -> list of (wallet, timestamp)
cabal_activity = defaultdict(list)
LOGGER = logging.getLogger("cabal_detector")

async def track_wallet_entry(wallet, token):
    now = datetime.utcnow()

    # Tag elite wallets only
    tag = get_wallet_tag(wallet)
    if tag != "elite":
        return

    cabal_activity[token].append((wallet, now))

    # Clean up old entries
    cutoff = now - timedelta(seconds=CABAL_WINDOW_SECONDS)
    cabal_activity[token] = [
        (w, t) for (w, t) in cabal_activity[token] if t > cutoff
    ]

    # Check for coordination threshold
    unique_wallets = {w for w, _ in cabal_activity[token]}
    if len(unique_wallets) >= CABAL_WALLET_THRESHOLD:
        log_event(f"👁 Cabal Detected: {len(unique_wallets)} elite wallets entered {token} together!")
        log_scanner_insight(token, "Cabal", f"{len(unique_wallets)} elite wallets entered in <{CABAL_WINDOW_SECONDS}s")

        # 🧠 Tag and store in librarian
        librarian.tag_token(token, "cabal_move")
        librarian.record_signal({
            "token": token,
            "source": "cabal_detector",
            "wallets": list(unique_wallets),
            "tag": "cabal_move",
            "volume": len(unique_wallets),
            "timestamp": now.timestamp()
        })


def detect_cabal_patterns(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze wallet clusters to detect suspicious group behavior (e.g., cabals).
    Looks for multiple known rugger tags, copy-trading, or high overlap in timing.
    """
    result = {
        "cabal_detected": False,
        "triggering_wallets": [],
        "cabal_score": 0.0,
        "notes": []
    }

    cluster_wallets: List[str] = ctx.get("cluster_wallets") or []
    if not cluster_wallets or not isinstance(cluster_wallets, list):
        return result

    rugger_hits = 0
    overlap_hits = 0

    for wallet in cluster_wallets:
        tag = get_wallet_tag(wallet)
        if tag == "rugger":
            rugger_hits += 1
            result["triggering_wallets"].append(wallet)
        elif tag == "copy_sniper":
            overlap_hits += 1
            result["triggering_wallets"].append(wallet)

    cabal_score = rugger_hits * 0.6 + overlap_hits * 0.4
    result["cabal_score"] = round(cabal_score, 2)

    if cabal_score >= 1.5 or rugger_hits >= 2:
        result["cabal_detected"] = True
        result["notes"].append(f"{rugger_hits} ruggers, {overlap_hits} copy snipers")

    return result
