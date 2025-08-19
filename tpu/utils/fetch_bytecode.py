import logging

import aiohttp

# === Solana Explorer API wrapper ===
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"  # Replace with your preferred endpoint

async def fetch_contract_bytecode(token_address: str, session: aiohttp.ClientSession, rpc_url: str = DEFAULT_RPC) -> str:
    """
    Attempts to fetch bytecode (program data) of a token's associated program (usually the mint authority).
    Returns raw base64 or hex code as string if successful.
    """
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [token_address, {"encoding": "base64"}]
        }
        async with session.post(rpc_url, json=payload) as resp:
            data = await resp.json()
            result = data.get("result", {}).get("value")
            if result:
                return result.get("data", ["", ""])[0]  # base64
    except Exception as e:
        logging.warning(f"[Bytecode] Fetch error for {token_address}: {e}")

    return ""

