import asyncio
import logging
from typing import List, Dict

from utils.solscan_pro_client import SolscanProClient
from core.wallet_identity_graph import analyze_wallet_identity
from librarian.data_librarian import librarian
from inputs.onchain.auto_watchlist import add_to_watchlist

# === Config ===
DISCOVERY_BATCH_SIZE = 20
MAX_HOLDERS_TO_CHECK = 10

class TokenDiscoveryEngine:
    def __init__(self, api_key: str):
        self.client = SolscanProClient()


    async def fetch_trending_tokens(self) -> List[str]:
        result = await self.client.get_token_trending()
        if not result or "data" not in result:
            return []
        return [entry["tokenAddress"] for entry in result["data"][:DISCOVERY_BATCH_SIZE]]

    async def analyze_token(self, mint: str) -> Dict:
        meta = await self.client.get_token_meta(mint)
        dev = meta.get("owner") or meta.get("updateAuthority")

        if not dev:
            return {"mint": mint, "skip": True, "reason": "No dev found"}

        dev_profile = await analyze_wallet_identity(dev)

        context = {
            "mint": mint,
            "token_name": meta.get("tokenName"),
            "symbol": meta.get("symbol"),
            "trusted_dev": "junkfarmer" not in dev_profile.get("tags", []),
            "dev_profile": dev_profile,
        }

        # Optional: profile top holders
        try:
            holders = await self.client.get_token_holders(mint, limit=MAX_HOLDERS_TO_CHECK)
            context["top_wallets"] = [entry["owner"] for entry in holders.get("data", [])]
        except:
            context["top_wallets"] = []

        librarian.ingest_token_context(mint, context)
        add_to_watchlist(mint, context)
        return context

    async def run_discovery_cycle(self):
        logging.info("[DiscoveryEngine] Scanning trending tokens...")
        mints = await self.fetch_trending_tokens()
        results = []
        for mint in mints:
            try:
                result = await self.analyze_token(mint)
                results.append(result)
            except Exception as e:
                logging.warning(f"[DiscoveryEngine] Failed on {mint}: {e}")
        return results
