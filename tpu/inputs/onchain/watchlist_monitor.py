import asyncio
import logging
from typing import Dict

from utils.solscan_pro_client import SolscanProClient
from core.wallet_identity_graph import analyze_wallet_identity
from librarian.data_librarian import librarian

PROMOTION_VOLUME_THRESHOLD = 2000  # adjust per strategy
PROMOTION_HOLDER_COUNT = 20

class WatchlistMonitor:
    def __init__(self, api_key: str):
        self.client = SolscanProClient(api_key)

    async def evaluate_watch_token(self, mint: str, entry: Dict):
        context = entry.get("context", {})
        meta = await self.client.get_token_meta(mint)
        holders = await self.client.get_token_holders(mint, limit=30)
        top_wallets = [h["owner"] for h in holders.get("data", [])]
        context["top_wallets"] = top_wallets

        # Re-analyze dev
        dev_wallet = meta.get("owner") or meta.get("updateAuthority")
        if dev_wallet:
            dev_profile = await analyze_wallet_identity(dev_wallet)
            context["dev_profile"] = dev_profile
            context["trusted_dev"] = "junkfarmer" not in dev_profile.get("tags", [])

        # Volume or holder check
        if len(top_wallets) >= PROMOTION_HOLDER_COUNT:
            librarian.promote_token(mint, context)
            logging.info(f"[WatchlistMonitor] ✅ Promoted token: {mint}")
        else:
            logging.info(f"[WatchlistMonitor] Not enough holders yet: {mint}")

    async def run_watchlist_recheck(self):
        tokens = librarian.memory.get("watchlist", {})
        if not tokens:
            logging.info("[WatchlistMonitor] Watchlist empty.")
            return

        logging.info(f"[WatchlistMonitor] Rechecking {len(tokens)} tokens...")
        for mint, entry in tokens.items():
            try:
                await self.evaluate_watch_token(mint, entry)
                await asyncio.sleep(2)  # prevent CU spike
            except Exception as e:
                logging.warning(f"[WatchlistMonitor] Error checking {mint}: {e}")
