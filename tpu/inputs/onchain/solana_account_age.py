# modules/solana_account_age.py

import logging
from datetime import datetime, timezone

from solana.rpc.async_api import AsyncClient


async def get_account_age(token_address: str, rpc_url: str) -> int:
    """
    Returns age of the token account in seconds.
    """
    try:
        async with AsyncClient(rpc_url) as client:
            resp = await client.get_account_info(token_address)
            if not resp["result"] or not resp["result"]["value"]:
                return -1
            lamports = resp["result"]["value"].get("lamports")
            if lamports is None:
                return -1

            slot = resp["result"]["context"]["slot"]
            block_time_resp = await client.get_block_time(slot)
            block_ts = block_time_resp.get("result")

            if block_ts:
                age_sec = (datetime.now(timezone.utc).timestamp()) - block_ts
                return int(age_sec)
    except Exception as e:
        logging.warning(f"⚠️ Failed to fetch account age for {token_address}: {e}")
    return -1
