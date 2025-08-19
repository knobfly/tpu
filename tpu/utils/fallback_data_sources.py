# /fallback_data_sources.py

import logging

import aiohttp


# === Enrich Token Metadata using Solscan ===
async def enrich_with_solscan(token_address: str, session: aiohttp.ClientSession) -> dict:
    url = f"https://public-api.solscan.io/token/meta?tokenAddress={token_address}"
    headers = {
        "accept": "application/json"
    }

    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logging.warning(f"[Solscan] Non-200 response: {resp.status}")
                return {}

            data = await resp.json()

            result = {
                "name": data.get("name"),
                "symbol": data.get("symbol"),
                "decimals": data.get("decimals"),
                "holders": data.get("holderCount"),
                "liquidity_usd": data.get("liquidity", 0),  # fallback field
            }

            return result
    except Exception as e:
        logging.warning(f"[Solscan] Failed to enrich token {token_address}: {e}")
        return {}

# === Enrich Token Metadata using Jupiter ===
async def enrich_with_jupiter(token_address: str, session: aiohttp.ClientSession) -> dict:
    url = "https://quote-api.jup.ag/v6/tokens"

    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logging.warning(f"[Jupiter] Non-200 response: {resp.status}")
                return {}

            tokens = await resp.json()
            token_info = next((t for t in tokens if t["address"] == token_address), None)

            if not token_info:
                return {}

            return {
                "name": token_info.get("name"),
                "symbol": token_info.get("symbol"),
                "decimals": token_info.get("decimals"),
                "liquidity_usd": token_info.get("liquidity", 0),
            }

    except Exception as e:
        logging.warning(f"[Jupiter] Failed to enrich token {token_address}: {e}")
        return {}
