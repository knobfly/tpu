#/dexscreener_adapter.py

import asyncio
import logging

import aiohttp

BASE_URL = "https://api.dexscreener.com/latest/dex/tokens/"

async def fetch_dexscreener_data(token_address: str) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}{token_address}") as resp:
                if resp.status != 200:
                    return {}
                raw = await resp.json()
                pairs = raw.get("pairs", [])
                if not pairs:
                    return {}
                return pairs[0]  # use top result
    except Exception as e:
        logging.warning(f"[Dexscreener] Fetch failed: {e}")
        return {}


def get_dexscreener_summary(token_address: str) -> dict:
    """
    Sync wrapper for getting key summary stats.
    Returns:
        {
            "volume_5m": float,
            "liquidity": float,
            "fdv": float,
            "price": float
        }
    """
    try:
        data = asyncio.run(fetch_dexscreener_data(token_address))
        return {
            "volume_5m": float(data.get("volume", {}).get("m5", 0)),
            "liquidity": float(data.get("liquidity", {}).get("usd", 0)),
            "fdv": float(data.get("fdv", 0)),
            "price": float(data.get("priceUsd", 0)),
        }
    except Exception:
        return {}
