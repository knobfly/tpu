# modules/token_metadata_fetcher.py

import asyncio
import logging
from typing import Dict, Optional

from core.live_config import config
from utils.http_client import SafeSession
from utils.logger import log_event

# === Constants ===
JUPITER_PRICE_URL = "https://quote-api.jup.ag/v6/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"
BIRDEYE_API = "https://public-api.birdeye.so/public/token"


async def fetch_token_metadata(token_address: str) -> Optional[Dict]:
    """
    Fetches token metadata (name, symbol, decimals, etc.) using Jupiter & Firehose fallback.
    """
    try:
        metadata = await _fetch_jupiter_metadata(token_address)

        if not metadata:
            # Firehose fallback (raw token details)
            metadata = await _fetch_firehose_token_data(token_address)

        if not metadata:
            logging.warning(f"[TokenMeta] No metadata found for {token_address}")
            return None

        log_event(f"[TokenMeta] Metadata loaded for {token_address}: {metadata}")
        return metadata

    except Exception as e:
        logging.warning(f"[TokenMeta] Failed to fetch metadata for {token_address}: {e}")
        return None


async def _fetch_jupiter_metadata(token_address: str) -> Optional[Dict]:
    """
    Uses Jupiter API for metadata (fastest source).
    """
    try:
        async with SafeSession() as session:
            url = f"{JUPITER_PRICE_URL}?inputMint={token_address}&outputMint={SOL_MINT}&amount=1000000"
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                meta = data.get("marketInfo", {})

                return {
                    "name": meta.get("label", "Unknown"),
                    "symbol": meta.get("symbol", "???"),
                    "decimals": int(meta.get("decimals", 9)),
                    "liquidityUSD": float(meta.get("liquidityUSD", 0)),
                    "priceInSOL": float(data.get("outAmount", 0)) / 1e9,
                    "tags": meta.get("tags", []),
                }
    except Exception as e:
        logging.debug(f"[TokenMeta] Jupiter metadata fetch failed: {e}")
        return None


async def _fetch_firehose_token_data(token_address: str) -> Optional[Dict]:
    """
    Placeholder for Firehose token metadata (on-chain parsing).
    We parse live Firehose events for metadata like name, symbol, supply, etc.
    """
    try:
        # Here we simulate a Firehose call — actual parsing will be added
        await asyncio.sleep(0.1)  # simulate latency
        return {
            "name": token_address[:4].upper() + "...",
            "symbol": "UNK",
            "decimals": 9,
            "liquidityUSD": 0,
            "priceInSOL": 0,
            "tags": ["unknown"],
        }
    except Exception as e:
        logging.warning(f"[TokenMeta] Firehose fallback failed: {e}")
        return None


async def get_token_price_in_sol(token_address: str) -> float:
    """
    Returns price in SOL via Jupiter quote API.
    """
    try:
        async with SafeSession() as session:
            url = f"{JUPITER_PRICE_URL}?inputMint={token_address}&outputMint={SOL_MINT}&amount=1000000"
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                return float(data.get("outAmount", 0)) / 1e9
    except Exception as e:
        logging.warning(f"[TokenMeta] Failed to fetch price for {token_address}: {e}")
        return 0.0

async def fetch_sol_volume(token_address: str) -> float:
    """
    Returns the 24h trading volume for a token in SOL using Birdeye.
    Pulls the API key from config.json.
    """
    try:
        api_key = config.get("birdeye_api_key", "")
        if not api_key:
            logging.warning("[MetaFetcher] ⚠️ Birdeye API key missing in config.json")
            return 0.0

        url = f"{BIRDEYE_API}/{token_address}/metrics"
        headers = {
            "accept": "application/json",
            "X-API-KEY": api_key
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                data = await resp.json()
                vol = data.get("data", {}).get("volumes", {}).get("h24", 0.0)
                return float(vol)

    except Exception as e:
        logging.warning(f"[MetaFetcher] ❌ Failed to fetch SOL volume for {token_address}: {e}")
        return 0.0
