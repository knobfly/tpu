import asyncio
import logging
from typing import Dict, List

from utils.solscan_pro_client import SolscanProClient
from librarian.data_librarian import librarian
from scoring.snipe_score_engine import evaluate_snipe

CHECK_INTERVAL = 240  # every 4 minutes
TX_CACHE: Dict[str, List[str]] = {}  # addr → [seen_tx_ids]

class WalletWatchTrigger:
    def __init__(self):
        self.client = SolscanProClient()

    def get_watch_wallets(self) -> List[str]:
        wallets = set()
        watchlist = librarian.memory.get("watchlist", {})
        for entry in watchlist.values():
            context = entry.get("context", {})
            dev = context.get("dev_profile", {}).get("address")
            holders = context.get("top_wallets", [])
            if dev:
                wallets.add(dev)
            for h in holders:
                wallets.add(h)
        return list(wallets)

    async def fetch_recent_tx_ids(self, address: str) -> List[str]:
        data = await self.client.get_account_transactions(address, limit=10)
        if not data or "data" not in data:
            return []

        return [tx.get("txHash") for tx in data["data"] if tx.get("txHash")]

    async def process_wallet(self, address: str):
        tx_ids = await self.fetch_recent_tx_ids(address)
        if not tx_ids:
            return

        seen = TX_CACHE.setdefault(address, [])
        new_ids = [tx for tx in tx_ids if tx not in seen]
        TX_CACHE[address] = tx_ids[:20]

        if not new_ids:
            return

        for tx_id in new_ids:
            logging.info(f"[WalletWatch] 🔔 {address} triggered by TX {tx_id}")
            await self.handle_trigger(tx_id, address)

    async def handle_trigger(self, tx_id: str, address: str):
        tx_detail = await self.client._fetch("/transaction/detail", {"tx": tx_id})
        if not tx_detail or "data" not in tx_detail:
            return

        tokens = []
        for ins in tx_detail["data"].get("instructions", []):
            if ins.get("type") in ("swap", "transfer", "buy") and ins.get("tokenAddress"):
                tokens.append(ins["tokenAddress"])

        for mint in tokens:
            context = {
                "triggered_by_wallet": address,
                "trigger_tx": tx_id,
            }
            result = await evaluate_snipe(mint, context)
            librarian.log_trigger_result(mint, result)

    async def run(self):
        while True:
            try:
                wallets = self.get_watch_wallets()
                for wallet in wallets:
                    await self.process_wallet(wallet)
                    await asyncio.sleep(1)
            except Exception as e:
                logging.warning(f"[WalletWatch] Error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)
