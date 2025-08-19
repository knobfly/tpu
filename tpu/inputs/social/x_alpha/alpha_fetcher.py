import asyncio
import logging

import aiohttp

API_URL = "https://api.coingecko.com/api/v3/search/trending"

async def fetch_alpha_trending() -> dict:
    """
    Fetch trending coins for alpha signals.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logging.warning(f"[AlphaFetcher] Non-200 response: {resp.status}")
                    return {}
    except Exception as e:
        logging.warning(f"[AlphaFetcher] Failed to fetch alpha trending: {e}")
        return {}

def fetch_alpha_trending_sync() -> dict:
    return asyncio.run(fetch_alpha_trending())
