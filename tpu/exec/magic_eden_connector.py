# exec/magic_eden_connector.py

import logging

import aiohttp
from solana.transaction import Transaction
from utils.nft_trait_scanner import get_nft_sell_price  # Trait-based pricing
from utils.rpc_loader import get_active_rpc
from utils.tx_utils import sign_and_send_tx

MAGIC_EDEN_API_KEY = "743f7e0f-9bc3-4b06-a3bf-ad46ed39a8d5"
MAGIC_EDEN_BASE_URL = "https://api-mainnet.magiceden.dev/v2"

headers = {
    "accept": "application/json",
    "content-type": "application/json",
    "x-api-key": MAGIC_EDEN_API_KEY,
}


async def buy_nft_me(wallet, mint_address: str) -> str | None:
    """
    Attempts to buy an NFT instantly via Magic Eden
    """
    try:
        url = f"{MAGIC_EDEN_BASE_URL}/nft/{mint_address}/buy-now"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logging.warning(f"[MagicEden] Buy-now fetch failed for {mint_address}")
                    return None
                result = await resp.json()
                tx_data = result.get("transaction")
                if not tx_data:
                    logging.warning(f"[MagicEden] No transaction returned for {mint_address}")
                    return None

        tx_bytes = bytes.fromhex(tx_data)
        tx = Transaction.deserialize(tx_bytes)
        sig = await sign_and_send_tx(tx, wallet=wallet)
        logging.info(f"[MagicEden] ✅ NFT Bought: {mint_address} | Sig: {sig}")
        return sig

    except Exception as e:
        logging.error(f"[MagicEden] Buy failed for {mint_address}: {e}")
        return None


async def list_nft_me(wallet, mint_address: str, price_sol: float) -> bool:
    """
    Lists an NFT for sale on Magic Eden at given price.
    """
    try:
        url = f"{MAGIC_EDEN_BASE_URL}/list"
        price_lamports = int(price_sol * 1_000_000_000)
        payload = {
            "mintAddress": mint_address,
            "price": price_lamports,
            "seller": wallet.address,
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logging.warning(f"[MagicEden] Listing failed: {await resp.text()}")
                    return False
                result = await resp.json()
                tx_data = result.get("transaction")
                if not tx_data:
                    logging.warning(f"[MagicEden] No tx returned for listing {mint_address}")
                    return False

        tx_bytes = bytes.fromhex(tx_data)
        tx = Transaction.deserialize(tx_bytes)
        sig = await sign_and_send_tx(tx, wallet=wallet)
        logging.info(f"[MagicEden] ✅ NFT Listed: {mint_address} at {price_sol} SOL | Sig: {sig}")
        return bool(sig)

    except Exception as e:
        logging.error(f"[MagicEden] Listing failed for {mint_address}: {e}")
        return False


async def autosell_nft_by_trait(wallet, mint_address: str):
    """
    Uses trait-based logic to determine NFT value and lists it.
    """
    try:
        price_sol = await get_nft_sell_price(mint_address)
        if price_sol is None or price_sol <= 0:
            logging.warning(f"[MagicEden] Trait-based price unavailable for {mint_address}")
            return False
        return await list_nft_me(wallet, mint_address, price_sol)
    except Exception as e:
        logging.error(f"[MagicEden] Autosell by trait failed: {e}")
        return False
