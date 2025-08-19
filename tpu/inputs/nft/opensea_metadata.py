# modules/opensea_metadata.py

import logging
from datetime import datetime

import aiohttp
from utils.logger import log_event
from utils.service_status import update_status

OPENSEA_ENDPOINT = "https://api.opensea.io/api/v2/chain/solana/contract/{mint_address}"
HEADERS = {
    "accept": "application/json",
    "User-Agent": "NyxNFTScanner/1.0"
}

async def fetch_opensea_metadata(mint_address: str) -> dict:
    url = OPENSEA_ENDPOINT.format(mint_address=mint_address)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=HEADERS, timeout=10) as response:
                if response.status != 200:
                    return {}

                data = await response.json()
                contract = data.get("contract", {})
                collection = data.get("collection", {})

                enriched = {
                    "verified": contract.get("is_verified", False),
                    "slug": collection.get("slug", ""),
                    "name": collection.get("name", ""),
                    "description": collection.get("description", ""),
                    "twitter": collection.get("twitter_username", ""),
                    "discord": collection.get("discord_url", ""),
                    "floor_price": collection.get("stats", {}).get("floor_price", 0),
                    "total_supply": collection.get("stats", {}).get("total_supply", 0),
                    "holders": collection.get("stats", {}).get("num_owners", 0),
                    "volume": collection.get("stats", {}).get("total_volume", 0),
                    "created_date": collection.get("created_date", ""),
                    "image_url": collection.get("image_url", ""),
                    "external_url": collection.get("external_url", "")
                }

                log_event(f"[NFTMeta] Enriched {mint_address} with OpenSea data: {enriched.get('name', '?')}")
                return enriched

    except Exception as e:
        logging.warning(f"[NFTMeta] Failed to fetch OpenSea data for {mint_address}: {e}")
        return {}
