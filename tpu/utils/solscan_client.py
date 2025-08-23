import aiohttp
import logging

BASE_URL = "https://public-api.solscan.io"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

async def fetch_solscan_json(endpoint: str, params: dict = None):
    url = f"{BASE_URL}{endpoint}"
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
                logging.warning(f"[Solscan] {resp.status} error for {url}")
    except Exception as e:
        logging.error(f"[Solscan] Fetch failed for {endpoint}: {e}")
    return {}

# === Token Info ===
async def get_token_metadata(mint: str):
    return await fetch_solscan_json(f"/token/meta", {"tokenAddress": mint})

# === Wallet Holdings ===
async def get_wallet_tokens(address: str):
    return await fetch_solscan_json(f"/account/tokens", {"account": address})

# === Transactions (optional expansion) ===
async def get_wallet_txs(address: str, limit=20):
    return await fetch_solscan_json(f"/account/transactions", {"account": address, "limit": limit})
