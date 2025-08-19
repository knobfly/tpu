# /wallet_stream_router.py

import asyncio
import logging
import time
from typing import Dict, List

from core.live_config import config
from inputs.wallet.multi_wallet_manager import multi_wallet
from inputs.wallet.wallet_behavior_analyzer import process_wallet_activity
from inputs.wallet.wallet_cluster_analyzer import update_wallet_clusters
from utils.logger import log_event
from utils.service_status import update_status

update_status("wallet_stream_router")

# === Globals ===
WATCHED_WALLETS: List[str] = []
_last_wallet_event_ts = 0.0
WALLET_EVENT_THROTTLE = 2.0  # seconds between wallet activity updates


def load_watched_wallets():
    """
    Load watched wallets from multi_wallet_manager or config.
    """
    global WATCHED_WALLETS
    try:
        WATCHED_WALLETS = multi_wallet.get_all_addresses()
        log_event(f"[WalletStreamRouter] Loaded {len(WATCHED_WALLETS)} wallets.")
    except Exception as e:
        logging.warning(f"[WalletStreamRouter] Failed to load watched wallets: {e}")
        WATCHED_WALLETS = []


async def route_wallet_event(event: Dict):
    """
    Routes a firehose event to wallet modules if it involves a watched wallet.
    """
    global _last_wallet_event_ts
    if not WATCHED_WALLETS:
        return

    try:
        wallets_in_event = event.get("wallets", [])
        if not wallets_in_event:
            return

        # Quick check if any watched wallet is in the event
        if not any(w in WATCHED_WALLETS for w in wallets_in_event):
            return

        now = time.time()
        if now - _last_wallet_event_ts < WALLET_EVENT_THROTTLE:
            return  # avoid flooding wallet analyzers

        _last_wallet_event_ts = now

        # Process wallet behavior
        await process_wallet_activity(wallets_in_event, event)

        # Update wallet clusters if needed
        update_wallet_clusters(wallets_in_event, event)

        log_event(f"[WalletStreamRouter] Routed wallet event: {wallets_in_event}")

    except Exception as e:
        logging.warning(f"[WalletStreamRouter] Failed to route wallet event: {e}")


def get_last_wallet_event_age() -> float:
    """
    Returns seconds since last wallet event was processed.
    """
    return time.time() - _last_wallet_event_ts if _last_wallet_event_ts else -1


# === Firehose Hook ===
async def wallet_stream_listener(event_queue: asyncio.Queue):
    """
    Continuously listens for firehose events and routes wallet-related ones.
    Call this from main.py with:
        asyncio.create_task(wallet_stream_listener(firehose_event_queue))
    """
    log_event("[WalletStreamRouter] Listening for wallet events…")
    load_watched_wallets()

    while True:
        try:
            event = await event_queue.get()
            if not event:
                continue
            await route_wallet_event(event)
        except Exception as e:
            logging.warning(f"[WalletStreamRouter] Listener error: {e}")
            await asyncio.sleep(1)
