# modules/nft_metadata_enricher.py

import logging

import aiohttp
from core.llm.llm_brain import analyze_token_name
from utils.logger import log_event
from utils.service_status import update_status

# Fallback to Tensor if Magic Eden not available yet
ME_BASE = "https://api-mainnet.magiceden.dev/v2"
TENSOR_COLLECTIONS = "https://tensor-hyperdrive.vercel.app/api/collections"

async def enrich_nft_metadata(mint_address: str):
    update_status("nft_metadata_enricher")
    try:
        meta = await fetch_from_magic_eden(mint_address)
        if not meta:
            meta = await fetch_from_tensor(mint_address)
        if not meta:
            log_event(f"❌ NFT Enricher failed for {mint_address}")
            return None

        # Optional: Theme inference
        theme = analyze_token_name(meta.get("name", ""))  # Reuse LLM analyzer
        meta["theme"] = theme

        log_event(f"🧠 NFT Enriched: {meta['name']} | Floor: {meta.get('floorPrice')} SOL | Theme: {theme}")
        return meta

    except Exception as e:
        logging.error(f"[NFTEnricher] Failed to enrich {mint_address}: {e}")
        return None

# === Try Magic Eden ===
async def fetch_from_magic_eden(mint):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{ME_BASE}/tokens/{mint}", timeout=10) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return {
                    "mint": mint,
                    "name": data.get("name"),
                    "symbol": data.get("symbol"),
                    "collection": data.get("collection"),
                    "floorPrice": round(data.get("listingPrice", 0) / 1e9, 3),
                    "supply": data.get("supply", 0),
                    "volume": data.get("volumeAll", 0)
                }
    except:
        return None

# === Try Tensor as fallback ===
async def fetch_from_tensor(mint):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{TENSOR_COLLECTIONS}", timeout=10) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                for c in data:
                    if mint in c.get("mintList", []):
                        return {
                            "mint": mint,
                            "name": c.get("name"),
                            "collection": c.get("symbol"),
                            "floorPrice": round(c.get("floorPrice", 0), 3),
                            "supply": c.get("supply", 0),
                            "volume": c.get("volume", 0)
                        }
    except:
        return None
