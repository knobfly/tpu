import logging
import aiohttp

ME_COLLECTION_URL = "https://api-mainnet.magiceden.dev/v2/nfts"

RARITY_MAP = {
    "legendary": 3.5,
    "epic": 2.5,
    "rare": 1.7,
    "uncommon": 1.2,
    "common": 1.0,
}

BASE_NFT_PRICE = 1.0  # fallback in SOL if traits unknown

async def get_nft_traits(mint_address: str) -> dict:
    try:
        url = f"{ME_COLLECTION_URL}/{mint_address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logging.warning(f"[NFTScanner] Trait fetch failed: {await resp.text()}")
                    return {}
                data = await resp.json()
                return data.get("attributes", {})
    except Exception as e:
        logging.error(f"[NFTScanner] Error fetching traits for {mint_address}: {e}")
        return {}


async def get_nft_sell_price(mint_address: str) -> float:
    try:
        traits = await get_nft_traits(mint_address)
        rarity_score = 1.0

        for trait in traits:
            rarity = trait.get("value", "common").lower()
            multiplier = RARITY_MAP.get(rarity, 1.0)
            rarity_score = max(rarity_score, multiplier)

        price = BASE_NFT_PRICE * rarity_score
        logging.info(f"[NFTScanner] Trait-based price for {mint_address}: {price:.2f} SOL")
        return round(price, 2)
    except Exception as e:
        logging.error(f"[NFTScanner] Error in price calc for {mint_address}: {e}")
        return BASE_NFT_PRICE
