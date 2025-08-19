import logging
from typing import Optional

from solana.transaction import TransactionInstruction
from utils.logger import log_event
from utils.raydium_sdk import build_raydium_swap_instruction, fetch_raydium_pool_state


async def build_raydium_swap_ix(payer, token_address: str, amount_sol: float, rpc_url: str) -> Optional[TransactionInstruction]:
    """
    Builds a Raydium AMM swap instruction for token_address.
    """
    try:
        pool_info = await fetch_raydium_pool_state(token_address, rpc_url)
        if not pool_info:
            return None

        ix = await build_raydium_swap_instruction(
            payer=payer,
            pool_info=pool_info,
            token_mint=token_address,
            sol_amount=amount_sol
        )
        log_event(f"[Raydium] Built swap IX for {token_address} (SOL={amount_sol})")
        return ix
    except Exception as e:
        logging.warning(f"[Raydium] Failed to build swap IX: {e}")
        return None
