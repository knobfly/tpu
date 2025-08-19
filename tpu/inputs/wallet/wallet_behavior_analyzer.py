import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

import aiohttp
from core.live_config import config
from core.llm.llm_brain import analyze_wallet_profile
from inputs.social.group_reputation import get_wallet_group_mentions
from inputs.wallet.wallet_cluster_analyzer import get_cluster_metadata, update_wallet_clusters
from librarian.data_librarian import librarian
from special.insight_logger import get_trade_history_summary, log_scanner_insight
from strategy.strategy_memory import get_strategy_tags_for_wallet
from utils.logger import log_event
from utils.service_status import update_status
from utils.universal_input_validator import validate_token_record
from utils.wallet_helpers import get_all_tracked_wallets, get_wallet_trade_history

# === Globals ===
wallet_profiles: Dict = {}
_wallet_activity_cache: Dict[str, Dict] = {}
CACHE_TTL = 3600  # 1 hour
ANALYSIS_WINDOW = 300  # 5 minutes

RUG_BEHAVIOR_LOG = "/home/ubuntu/nyx/runtime/memory/wallets/known_ruggers.json"

_known_ruggers = {}  # wallet -> { "score": float, "last_seen": timestamp }


# === Firehose Event Integration ===
async def process_wallet_activity(wallets: List[str], event: Dict):
    try:
        for wallet in wallets:
            _wallet_activity_cache[wallet] = {
                "last_seen": time.time(),
                "last_event": event,
            }
            log_event(f"[WalletAnalyzer] Activity: {wallet} in {event.get('token', '?')}")

        update_wallet_clusters(wallets, event)

    except Exception as e:
        logging.warning(f"[WalletAnalyzer] Failed to process wallet activity: {e}")


def get_wallet_activity(wallet: str) -> Dict:
    data = _wallet_activity_cache.get(wallet, {})
    if not data or (time.time() - data.get("last_seen", 0)) > CACHE_TTL:
        return {}
    return data


def get_all_active_wallets() -> List[str]:
    now = time.time()
    return [w for w, d in _wallet_activity_cache.items() if (now - d.get("last_seen", 0)) < CACHE_TTL]


# === Wallet Behavior Analytics ===
async def analyze_wallet_behavior(wallet: str):
    trades = get_wallet_trade_history(wallet, window_seconds=ANALYSIS_WINDOW)
    if not trades:
        return

    outcomes = defaultdict(int)
    entry_times, exit_times = [], []
    tokens_seen = set()

    for trade in trades:
        trade = validate_token_record(trade)
        token = trade.get("token")
        action = trade.get("action")
        timestamp = trade.get("timestamp")
        tokens_seen.add(token)

        if action == "buy":
            entry_times.append(timestamp)
        elif action == "sell":
            exit_times.append(timestamp)

        outcomes[trade.get("outcome", "unknown")] += 1

    behavior_tags = []
    if outcomes["rug"] >= 2:
        behavior_tags.append("rugger")

    if entry_times and exit_times:
        try:
            parsed_entries = [datetime.fromisoformat(t) for t in entry_times if isinstance(t, str)]
            parsed_exits = [datetime.fromisoformat(t) for t in exit_times if isinstance(t, str)]
            if parsed_entries and parsed_exits:
                time_diff = (min(parsed_exits) - max(parsed_entries)).total_seconds()
                if time_diff < 90:
                    behavior_tags.append("sniper-dump")
        except Exception:
            pass

    if len(entry_times) >= 3 and len(exit_times) == 0:
        behavior_tags.append("diamond-hands")

    if len(tokens_seen) >= 6:
        behavior_tags.append("scattergun")

    if behavior_tags:
        log_event(f"🔍 Wallet {wallet[:6]}... → Behavior: {', '.join(behavior_tags)}")
        log_scanner_insight("wallet_behavior", wallet, {
            "tags": behavior_tags,
            "rugged": outcomes.get("rug", 0),
            "profits": outcomes.get("profit", 0),
            "losses": outcomes.get("loss", 0),
        })

        for tag in behavior_tags:
            librarian.learn_wallet_tag(wallet, tag)


def record_wallet_behavior(wallet: str, behavior: dict):
    if not wallet or not behavior:
        logging.warning("⚠️ [WalletBehavior] Invalid wallet or behavior input.")
        return
    behavior = validate_token_record(behavior)
    profile = wallet_profiles.setdefault(wallet, {"tags": set(), "score": 0})
    tags = behavior.get("tags", [])
    score = behavior.get("score", 0)
    profile["tags"].update(tags)
    profile["score"] += score
    log_event(f"📊 Recorded behavior for wallet {wallet}: Tags={tags}, Score+{score}")


def get_wallet_profile(wallet: str):
    return wallet_profiles.get(wallet, {"tags": set(), "score": 0})


# === Recent Activity & External Data ===
async def get_recent_wallet_activity(wallet: str = None, since=None):
    if wallet:
        activity = {
            "wallet": wallet,
            "cluster": {},
            "strategy_tags": [],
            "group_mentions": [],
            "trades": [],
            "behavior_profile": {},
            "external_links": {
                "solscan": f"https://solscan.io/account/{wallet}",
                "birdeye": f"https://birdeye.so/wallet/{wallet}",
            },
            "birdeye_data": {},
            "solscan_data": {},
            "last_seen": None,
            "win_rate": None,
            "risk_score": None
        }

        try:
            activity["cluster"] = validate_token_record(get_cluster_metadata(wallet))
            activity["strategy_tags"] = validate_token_record(get_strategy_tags_for_wallet(wallet))
            activity["group_mentions"] = validate_token_record(get_wallet_group_mentions(wallet))

            all_trades = get_trade_history_summary(limit=500)
            wallet_trades = [t for t in all_trades if wallet in str(t)]
            wallet_trades = [validate_token_record(t) for t in wallet_trades]
            wallet_trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            activity["trades"] = wallet_trades[:10]

            if wallet_trades:
                activity["last_seen"] = wallet_trades[0].get("timestamp")
                wins = sum(1 for t in wallet_trades if t.get("outcome") == "win")
                activity["win_rate"] = f"{100 * wins // len(wallet_trades)}%"

            try:
                activity["behavior_profile"] = await analyze_wallet_profile(wallet)
            except Exception as e:
                log_event(f"⚠️ LLM wallet profiling failed for {wallet}: {e}")

            try:
                async with aiohttp.ClientSession() as session:
                    birdeye_url = f"https://public-api.birdeye.so/public/wallet/overview?wallet={wallet}"
                    async with session.get(birdeye_url) as resp:
                        if resp.status == 200:
                            birdeye_json = await resp.json()
                            activity["birdeye_data"] = {
                                "total_value": birdeye_json.get("data", {}).get("total_value", 0),
                                "token_count": len(birdeye_json.get("data", {}).get("tokens", [])),
                                "tx_count": birdeye_json.get("data", {}).get("tx_count", "N/A"),
                            }
            except Exception as e:
                log_event(f"⚠️ Birdeye fetch failed: {e}")

            try:
                async with aiohttp.ClientSession() as session:
                    solscan_url = f"https://public-api.solscan.io/account/{wallet}"
                    async with session.get(solscan_url, headers={"accept": "application/json"}) as resp:
                        if resp.status == 200:
                            solscan_json = await resp.json()
                            activity["solscan_data"] = {
                                "sol_balance": solscan_json.get("lamports", 0) / 1e9,
                                "nft_count": len(solscan_json.get("tokens", [])),
                            }
            except Exception as e:
                log_event(f"⚠️ Solscan fetch failed: {e}")

        except Exception as e:
            log_event(f"❌ Error in get_recent_wallet_activity({wallet}): {e}")
            activity["error"] = str(e)

        return activity

    else:
        logs = librarian.load_json_file("/home/ubuntu/nyx/runtime/wallet_logs/wallet_behavior.json") or []
        results = []
        for entry in logs:
            try:
                entry = validate_token_record(entry)
                ts = datetime.fromisoformat(entry.get("timestamp"))
                if since is None or ts >= since:
                    results.append(entry)
            except:
                continue
        return results

def load_known_ruggers():
    global _known_ruggers
    if os.path.exists(RUG_BEHAVIOR_LOG):
        try:
            with open(RUG_BEHAVIOR_LOG, "r") as f:
                _known_ruggers = json.load(f)
        except Exception as e:
            logging.warning(f"[RuggerTracker] Failed to load ruggers: {e}")
            _known_ruggers = {}

def save_known_ruggers():
    try:
        with open(RUG_BEHAVIOR_LOG, "w") as f:
            json.dump(_known_ruggers, f, indent=2)
    except Exception as e:
        logging.warning(f"[RuggerTracker] Failed to save ruggers: {e}")

def mark_wallet_as_rugger(wallet: str, confidence: float = 1.0):
    now = time.time()
    entry = _known_ruggers.get(wallet, {"score": 0.0, "last_seen": now})
    entry["score"] += confidence
    entry["last_seen"] = now
    _known_ruggers[wallet] = entry
    save_known_ruggers()

def is_known_rugger(wallet: str, threshold: float = 1.0) -> bool:
    """
    Returns True if the wallet is known to be associated with rug behavior.
    """
    data = _known_ruggers.get(wallet)
    if not data:
        return False
    return data.get("score", 0.0) >= threshold

def get_known_ruggers(min_score: float = 1.0) -> list[str]:
    return [wallet for wallet, data in _known_ruggers.items() if data.get("score", 0.0) >= min_score]

# Auto-load on import
load_known_ruggers()


# === Main Loop ===
async def run_wallet_behavior_analyzer():
    log_event("🧠 Wallet Behavior Analyzer started.")
    update_status("wallet_behavior_analyzer")
    while True:
        try:
            wallets = get_all_tracked_wallets()
            for wallet in wallets:
                await analyze_wallet_behavior(wallet)
        except Exception as e:
            logging.warning(f"[WalletBehavior] Error: {e}")
        await asyncio.sleep(60)
