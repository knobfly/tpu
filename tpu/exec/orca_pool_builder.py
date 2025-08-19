import logging
from typing import Optional

from solana.transaction import TransactionInstruction
from utils.logger import log_event
from utils.orca_sdk import build_orca_swap_instruction, fetch_orca_pool_state


async def build_orca_swap_ix(payer, token_address: str, amount_sol: float, rpc_url: str) -> Optional[TransactionInstruction]:
    """
    Builds an Orca AMM swap instruction for token_address.
    """
    try:
        pool_info = await fetch_orca_pool_state(token_address, rpc_url)
        if not pool_info:
            return None

        ix = await build_orca_swap_instruction(
            payer=payer,
            pool_info=pool_info,
            token_mint=token_address,
            sol_amount=amount_sol
        )
        log_event(f"[Orca] Built swap IX for {token_address} (SOL={amount_sol})")
        return ix
    except Exception as e:
        logging.warning(f"[Orca] Failed to build swap IX: {e}")
        return None
