import asyncio
import logging

from utils.jupiter_swap import JupiterSwap
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc


async def get_best_route(wallet, token_address: str, amount: float) -> dict:
    """
    Checks Raydium and Orca pools through Jupiter for the optimal route.
    """
    try:
        session = JupiterSwap.get_session()
        jupiter = JupiterSwap(session, wallet)

        # Query Jupiter for best swap route
        route = await jupiter.get_best_route(token_address, amount)
        if not route:
            return {"platform": "none", "expected_output": 0}

        log_event(f"[Router] Best route for {token_address}: {route.get('platform')} ({route.get('outAmount')})")
        return route
    except Exception as e:
        logging.warning(f"[RaydiumOrcaRouter] Failed to get route for {token_address}: {e}")
        return {"platform": "error", "expected_output": 0}
