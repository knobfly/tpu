# /firehose_wallet_tools.py

import logging

from inputs.onchain.firehose.firehose_cache import (
    get_recent_txns_from_firehose,
    get_wallet_balance_from_firehose,
)
from utils.http_client import SafeSession
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc
from utils.service_status import update_status

update_status("firehose_wallet_tools")


# === Fetch SOL Balance (Firehose First) ===
async def get_balance(wallet: str) -> float:
    """
    Try firehose balance cache first. Fallback to RPC if unavailable.
    Logs source for debugging.
    """
    #try:
    #   fh_balance = get_wallet_balance_from_firehose(wallet)
    #    if fh_balance is not None:
    #        log_event(f"[FirehoseWalletTools] Balance for {wallet[:6]}.. from Firehose: {fh_balance:.4f} SOL")
    #        return fh_balance
    #except Exception as e:
    #    logging.warning(f"[FirehoseWalletTools] Firehose balance error: {e}")

    try:
        rpc = get_active_rpc()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [wallet]
        }
        async with SafeSession() as session:
            async with session.post(rpc, json=payload) as resp:
                data = await resp.json()
                lamports = data.get("result", {}).get("value", 0)
                sol_balance = lamports / 1e9
                log_event(f"[FirehoseWalletTools] Balance for {wallet[:6]}.. from RPC: {sol_balance:.4f} SOL (via {rpc})")
                return sol_balance
    except Exception as e:
        logging.warning(f"[FirehoseWalletTools] RPC balance fetch failed: {e}")
        return 0.0


# === Fetch Recent Transactions (Firehose First) ===
async def get_recent_transactions(wallet: str, limit: int = 10) -> list:
    """
    Try firehose recent transaction cache. Fallback to RPC if unavailable.
    Logs source for debugging.
    """
    try:
        txns = get_recent_txns_from_firehose(wallet, limit)
        if txns:
            log_event(f"[FirehoseWalletTools] Recent txns for {wallet[:6]}.. from Firehose: {len(txns)} found")
            return txns
    except Exception as e:
        logging.warning(f"[FirehoseWalletTools] Firehose tx fetch error: {e}")

    try:
        rpc = get_active_rpc()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet, {"limit": limit}]
        }
        async with SafeSession() as session:
            async with session.post(rpc, json=payload) as resp:
                data = await resp.json()
                log_event(f"[FirehoseWalletTools] Recent txns for {wallet[:6]}.. from RPC: {len(data) if isinstance(data, list) else 0} found (via {rpc})")
                return data
    except Exception as e:
        logging.warning(f"[FirehoseWalletTools] RPC tx fetch failed: {e}")
        return []


# === NFT Metadata (No Firehose, Just Public API) ===
async def fetch_nft_metadata(mint: str) -> dict:
    """
    Fetch NFT metadata using public APIs (no firehose available).
    """
    try:
        async with SafeSession() as session:
            url = f"https://api-mainnet.magiceden.dev/v2/tokens/{mint}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    log_event(f"[FirehoseWalletTools] NFT metadata fetched for {mint[:6]}.. from Magic Eden API")
                    return await resp.json()
    except Exception as e:
        logging.warning(f"[FirehoseWalletTools] NFT metadata fetch failed: {e}")
    return {}
