import logging
from typing import Dict, Any, List

from utils.solscan_client import get_wallet_tokens
from librarian.data_librarian import librarian

WALLET_CACHE: Dict[str, Dict] = {}

async def analyze_wallet_identity(address: str) -> Dict[str, Any]:
    if address in WALLET_CACHE:
        return WALLET_CACHE[address]

    raw = await get_wallet_tokens(address)
    token_count = len(raw or [])
    live_tokens = [t for t in raw if float(t.get("tokenAmount", {}).get("uiAmount", 0)) > 0]
    live_count = len(live_tokens)

    tags = []
    if token_count > 80 and live_count == 0:
        tags.append("junkfarmer")
    if live_count >= 3:
        tags.append("active_trader")
    if 1 <= token_count <= 3 and live_count == 0:
        tags.append("burned_out")

    score = round((live_count / max(token_count, 1)) * 10, 2)

    profile = {
        "address": address,
        "token_count": token_count,
        "live_count": live_count,
        "score": score,
        "tags": tags,
        "token_mints": [t.get("tokenAddress") for t in live_tokens],
    }

    WALLET_CACHE[address] = profile
    librarian.ingest_wallet_profile(profile)
    return profile

async def batch_analyze_wallets(wallets: List[str]) -> Dict[str, Dict[str, Any]]:
    results = {}
    for addr in wallets:
        profile = await analyze_wallet_identity(addr)
        results[addr] = profile
    return results
