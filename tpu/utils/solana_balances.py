# utils/solana_balances.py
import logging

import aiohttp
from utils.rpc_loader import get_active_rpc


async def get_token_account_ui_amount(ata_pubkey: str) -> float | None:
    """
    Returns the UI amount (float) for a SPL token account (getTokenAccountBalance).
    """
    try:
        rpc = get_active_rpc()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountBalance",
            "params": [ata_pubkey, {"commitment": "processed"}],
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(rpc, json=payload, timeout=10) as r:
                js = await r.json()
                v = (((js or {}).get("result") or {}).get("value") or {})
                ui = v.get("uiAmount")
                return float(ui) if ui is not None else None
    except Exception as e:
        logging.debug(f"[Balances] getTokenAccountBalance failed for {ata_pubkey}: {e}")
        return None
