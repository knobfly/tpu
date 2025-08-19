import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict

from librarian.data_librarian import librarian
from special.insight_logger import log_insight
from utils.http_client import SafeSession

# === Constants ===
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "Es9vMFrzaCERc9eZidMqaDnVCz8jznG9H3vK2eBqnzLz"

price_cache: Dict[str, float] = {}
last_price_update: Dict[str, datetime] = {}
CACHE_TTL = timedelta(minutes=2)

# === Fetch current SOL price in USD ===
async def get_sol_price() -> float:
    now = datetime.utcnow()
    if "SOL" in price_cache and now - last_price_update.get("SOL", now) < CACHE_TTL:
        return price_cache["SOL"]

    url = (
        f"https://quote-api.jup.ag/v6/quote?"
        f"inputMint={SOL_MINT}&"
        f"outputMint={USDC_MINT}&"
        f"amount=1000000000"  # 1 SOL
    )  
    try:
        async with SafeSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                usdc_out = float(data.get("outAmount", 0)) / 1e6
                if usdc_out > 0:
                    price_cache["SOL"] = usdc_out
                    last_price_update["SOL"] = now
                    logging.info(f"[PriceFetcher] ✅ SOL ≈ ${usdc_out:.2f}")
                return round(usdc_out, 2)
    except Exception as e:
        logging.warning(f"[PriceFetcher] Failed to fetch SOL price: {e}")
        return price_cache.get("SOL", 0.0)

# === Fetch token price in SOL ===
async def get_token_price(token_address: str) -> float:
    now = datetime.utcnow()
    if token_address in price_cache:
        if now - last_price_update.get(token_address, now) < CACHE_TTL:
            return price_cache[token_address]

    url = (
        f"https://quote-api.jup.ag/v6/quote?"
        f"inputMint={token_address}&"
        f"outputMint={SOL_MINT}&"
        f"amount=1000000"
    )

    try:
        async with SafeSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                out_amt = float(data.get("outAmount", 0))
                if out_amt > 0:
                    sol_value = out_amt / 1e9
                    price_cache[token_address] = sol_value
                    last_price_update[token_address] = now

                    logging.debug(f"[PriceFetcher] Token {token_address} ≈ {sol_value:.6f} SOL")

                    # Volume insight to librarian
                    volume = float(data.get("marketInfo", {}).get("liquidityUSD", 0))

                    librarian.append_log("price_volume_log", {
                        "token": token_address,
                        "sol_value": sol_value,
                        "usd_volume": volume,
                        "timestamp": datetime.utcnow().isoformat()
                    })

                    log_insight("volume", {
                        "token": token_address,
                        "sol_price": sol_value,
                        "usd_volume": volume
                    })

                    return sol_value
    except Exception as e:
        logging.warning(f"[PriceFetcher] Error fetching price for {token_address}: {e}")

    return 0.0

# === Background SOL price updater ===
async def start_price_websocket():
    while True:
        try:
            await get_sol_price()
        except Exception as e:
            logging.warning(f"[PriceFetcher] WebSocket price loop error: {e}")
        await asyncio.sleep(30)

