# utils/rpc_helper.py

import asyncio
import logging

import aiohttp


async def fetch_token_account_info_async(account: str, rpc_url: str) -> dict:
    """
    Fetch token account info using Solana RPC.
    """
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountBalance",
                "params": [account]
            }
            async with session.post(rpc_url, json=payload, timeout=8) as resp:
                return await resp.json()
    except Exception as e:
        logging.warning(f"[RPCHelper] Async fetch failed: {e}")
        return {}

def fetch_token_account_info(account: str, rpc_url: str) -> dict:
    """
    Sync wrapper for environments where async is not available.
    """
    try:
        return asyncio.run(fetch_token_account_info_async(account, rpc_url))
    except Exception as e:
        logging.warning(f"[RPCHelper] Sync wrapper failed: {e}")
        return {}

async def get_token_accounts_by_owner(wallet_address: str, rpc_url: str) -> dict:
    """
    Fetch all token accounts owned by the wallet.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            wallet_address,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"}
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(rpc_url, json=payload, timeout=8) as resp:
                data = await resp.json()
                
                # Distinguish between empty and failed result
                result = data.get("result")
                if result is None:
                    logging.warning(f"[RPCHelper] Null result from RPC for wallet {wallet_address}")
                elif isinstance(result.get("value", []), list) and len(result["value"]) == 0:
                    logging.info(f"[RPCHelper] No token accounts found for wallet {wallet_address}")

                return data

    except Exception as e:
        logging.warning(f"[RPCHelper] Failed to fetch token accounts: {e}")
        return {}
