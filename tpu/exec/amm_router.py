import logging
from typing import Optional

from exec.orca_pool_builder import build_orca_swap_ix
from exec.raydium_pool_builder import build_raydium_swap_ix
from solana.transaction import TransactionInstruction
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc


async def build_swap_ix(payer, token_address: str, amount_sol: float, rpc_url: Optional[str] = None) -> Optional[TransactionInstruction]:
    """
    Determine which AMM (Raydium/Orca) has liquidity and build swap instruction.
    """
    try:
        rpc = rpc_url or get_active_rpc()
        log_event(f"[AMM Router] Building swap IX for {token_address} @ {amount_sol} SOL")

        # Attempt Raydium first
        ray_ix = await build_raydium_swap_ix(payer, token_address, amount_sol, rpc)
        if ray_ix:
            return ray_ix

        # Fallback to Orca
        orca_ix = await build_orca_swap_ix(payer, token_address, amount_sol, rpc)
        if orca_ix:
            return orca_ix

        logging.warning(f"[AMM Router] No pool found for {token_address} on Raydium/Orca.")
        return None

    except Exception as e:
        logging.error(f"[AMM Router] build_swap_ix failed: {e}")
        return None
