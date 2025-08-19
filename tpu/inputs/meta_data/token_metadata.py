# modules/token_metadata.py

import base64
import logging
import struct

import aiohttp
from librarian.data_librarian import librarian
from solana.publickey import PublicKey
from solana.rpc.async_api import AsyncClient
from spl.token._layouts import MINT_LAYOUT
from utils.fallback_data_sources import enrich_with_jupiter, enrich_with_solscan
from cortex.core_router import handle_event

# === Dust Detection Thresholds ===
MIN_LIQUIDITY_USD = 1000
MIN_HOLDERS = 50
MIN_SYMBOL_LENGTH = 2
SOL_DECIMALS = 9

# === Parse Token Metadata from Solana ===
async def parse_token_metadata(token_address: str, session: aiohttp.ClientSession) -> dict:
    metadata = {
        "name": None,
        "symbol": None,
        "decimals": SOL_DECIMALS,
        "liquidity_usd": 0,
        "holders": 0,
        "supply": 0,
        "mint_authority": None,
        "freeze_authority": None,
        "created_at": None
    }

    try:
        async with AsyncClient("https://api.mainnet-beta.solana.com") as client:
            resp = await client.get_account_info(PublicKey(token_address), encoding="base64")
            value = resp.get("result", {}).get("value")
            if not value:
                logging.warning(f"[Metadata] No data for token {token_address}")
                return metadata

            data_b64 = value.get("data", [None])[0]
            if not data_b64:
                return metadata

            decoded_data = base64.b64decode(data_b64)
            parsed = MINT_LAYOUT.parse(decoded_data)

            metadata.update({
                "supply": parsed.supply / (10 ** parsed.decimals),
                "mint_authority": str(parsed.mint_authority) if parsed.mint_authority else None,
                "freeze_authority": str(parsed.freeze_authority) if parsed.freeze_authority else None,
                "created_at": value.get("lamports", 0)
            })
            await handle_event({
                "token": mint,
                "action": "meta_update",
                "meta": metadata_dict,   # name, symbol, mc, supply, creation_ts, etc.
                "source": "metadata_fetcher",
            })
    except Exception as e:
        logging.error(f"[Metadata] Solana metadata fetch failed: {e}")

    # === Solscan fallback ===
    try:
        solscan_data = await enrich_with_solscan(token_address, session)
        for k, v in solscan_data.items():
            if v and not metadata.get(k):
                metadata[k] = v
    except Exception as e:
        logging.warning(f"[Metadata] Solscan fallback failed: {e}")

    # === Jupiter fallback (final layer) ===
    try:
        jup_data = await enrich_with_jupiter(token_address, session)
        for k, v in jup_data.items():
            if v and not metadata.get(k):
                metadata[k] = v
    except Exception as e:
        logging.warning(f"[Metadata] Jupiter fallback failed: {e}")

    return metadata

# === Dust Check ===
def is_dust_token(metadata: dict) -> bool:
    try:
        if not metadata:
            return True

        symbol = metadata.get("symbol", "") or ""
        liquidity = metadata.get("liquidity_usd", 0) or 0
        holders = metadata.get("holders", 0) or 0

        if len(symbol) < MIN_SYMBOL_LENGTH:
            return True
        if liquidity < MIN_LIQUIDITY_USD:
            return True
        if holders < MIN_HOLDERS:
            return True

        return False
    except Exception as e:
        logging.error(f"[Metadata] Failed dust check: {e}")
        return True

# === Token History (for AI memory pattern reinforcement) ===
def get_token_history(token_address: str) -> dict:
    """
    Returns historical token memory for reinforcement logic.
    Flags if Nyx has seen/traded this token before and what happened.
    """
    try:
        memory = librarian.load_json_file("/home/ubuntu/nyx/runtime/token_memory/token_history.json") or {}
        entry = memory.get(token_address, {})
        if not entry:
            return {}

        return {
            "past_success": entry.get("result") == "win",
            "last_score": entry.get("score"),
            "tags": entry.get("tags", []),
            "traded_before": True
        }
    except Exception as e:
        logging.warning(f"[Token History] Failed to fetch history for {token_address}: {e}")
        return {}
