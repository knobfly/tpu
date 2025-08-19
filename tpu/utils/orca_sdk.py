# utils/orca_sdk.py
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import aiohttp
from solana.publickey import PublicKey
from solana.rpc.async_api import AsyncClient
from solana.transaction import TransactionInstruction
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc
from utils.token_utils import get_token_mint_info

# Orca AMM program (placeholder; update if you have a canonical ID for your flow)
ORCA_AMM_PROGRAM = PublicKey("9WwWjW9wxyD4QZq6QMQJwPCFSZBvbGB9n6NFr7nq4ZcT")

# Public whirlpool registry (fast path)
_ORCA_URL = "https://api.mainnet.orca.so/v1/whirlpool/list"
_CACHE = {"ts": 0.0, "data": None}
_TTL = 60 * 30  # 30 minutes

__all__ = [
    "get_pool_accounts_for_mint",        # registry (fast path)
    "fetch_orca_pool_state_onchain",     # on-chain fallback
    "resolve_pool_for_mint",             # try both, normalized
    "build_orca_swap_instruction",       # helper to build swap IX
]

# ---------------------------
# HTTP registry (fast path)
# ---------------------------

async def _fetch_json(url: str, timeout: int = 15) -> Any:
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=timeout) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status} for {url}")
            return await r.json()

async def _get_registry(force: bool = False) -> Optional[dict]:
    now = time.time()
    if not force and _CACHE["data"] and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]
    try:
        data = await _fetch_json(_ORCA_URL)
        _CACHE["data"] = data
        _CACHE["ts"] = now
        return data
    except Exception as e:
        logging.warning(f"[OrcaSDK] registry fetch failed: {e}")
        return _CACHE["data"]  # stale if we have it

async def get_pool_accounts_for_mint(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Returns:
      {
        "state": <pool_pubkey>,
        "vault_a": <pubkey>,
        "vault_b": <pubkey>,
        "lp_mint": None,
        "baseMint": <mintA>,
        "quoteMint": <mintB>,
        "fee_bps": <int>,          # NEW
        "tick_spacing": <int>,     # NEW
      }
    """
    reg = await _get_registry()
    if not reg:
        return None

    try:
        pools = reg.get("whirlpools") or reg.get("data") or []
        for p in pools:
            a = (p.get("tokenA") or {}).get("mint")
            b = (p.get("tokenB") or {}).get("mint")
            if token_mint in (a, b):
                return {
                    "state": p.get("address") or p.get("whirlpoolAddress"),
                    "vault_a": p.get("tokenVaultA"),
                    "vault_b": p.get("tokenVaultB"),
                    "lp_mint": None,
                    "baseMint": a,
                    "quoteMint": b,
                    "fee_bps": int((p.get("feeRate") or 0) * 10000) if isinstance(p.get("feeRate"), float) else int(p.get("feeRateBps") or 0),
                    "tick_spacing": int(p.get("tickSpacing") or 0),
                }
    except Exception as e:
        logging.warning(f"[OrcaSDK] parse error: {e}")
    return None

# ---------------------------------
# On-chain fallback (program scan)
# ---------------------------------

async def fetch_orca_pool_state_onchain(token_address: str, rpc_url: Optional[str] = None) -> Optional[Dict]:
    """
    Heuristic probe of program accounts to find a pool referencing `token_address`.
    Registry is preferred; use this as a last resort.
    Returns:
      {"pubkey": "<pool_state_pubkey>", "account": <raw_account_data>} or None
    """
    rpc_url = rpc_url or get_active_rpc()
    try:
        async with AsyncClient(rpc_url) as client:
            # Memcmp offset is heuristic; adjust if you adopt a precise layout.
            filters = [
                {"memcmp": {"offset": 72, "bytes": token_address}},
            ]
            resp = await client.get_program_accounts(ORCA_AMM_PROGRAM, filters=filters)
            if not resp.value:
                return None

            acc = resp.value[0]
            log_event(f"[OrcaSDK] On-chain pool account for {token_address}: {acc.pubkey}")
            return {
                "pubkey": str(acc.pubkey),
                "account": acc.account.data,
            }
    except Exception as e:
        logging.error(f"[OrcaSDK] on-chain fetch failed for {token_address}: {e}")
        return None

# ---------------------------------
# Unified resolver
# ---------------------------------

async def resolve_pool_for_mint(token_mint: str) -> Optional[Dict[str, str]]:
    """
    Try registry first, then on-chain, returning a normalized dict:
      {
        "state": "<pool_state_pubkey>",
        "vault_a": "<token_vault_a>|None",
        "vault_b": "<token_vault_b>|None",
        "lp_mint": None,
        "baseMint": "<mintA>|None",
        "quoteMint": "<mintB>|None",
        "_source": "registry|onchain",
      }
    """
    # 1) Registry (preferred)
    reg = await get_pool_accounts_for_mint(token_mint)
    if reg:
        reg["_source"] = "registry"
        return reg

    # 2) Fallback: on-chain probe (state only)
    oc = await fetch_orca_pool_state_onchain(token_mint)
    if oc:
        return {
            "state": oc.get("pubkey"),
            "vault_a": None,
            "vault_b": None,
            "lp_mint": None,
            "baseMint": None,
            "quoteMint": None,
            "_source": "onchain",
        }

    return None

# ---------------------------------
# Swap IX helper (Orca)
# ---------------------------------

async def build_orca_swap_instruction(
    payer,
    pool_info: Dict[str, str],
    token_mint: str,
    sol_amount: float,
) -> Optional[TransactionInstruction]:
    """
    Build an Orca swap instruction using a resolved pool.
    Requires your helper at: utils/orca_instructions.py (make_swap_instruction)
    """
    try:
        from utils.orca_instructions import make_swap_instruction  # your helper
    except ImportError:
        logging.error("[OrcaSDK] Missing utils.orca_instructions.make_swap_instruction")
        return None

    # Optional: ensure mint exists
    mint_info = await get_token_mint_info(token_mint)
    if not mint_info:
        logging.warning(f"[OrcaSDK] Missing mint info for {token_mint}")
        return None

    try:
        ix = make_swap_instruction(
            payer=PublicKey(payer.address),
            pool_pubkey=PublicKey(pool_info["state"]),
            token_mint=PublicKey(token_mint),
            amount_in=int(sol_amount * 10**9),  # SOL lamports
            min_amount_out = int(expected_out_amount * (1 - slippage_tolerance))
        )
        log_event(f"[OrcaSDK] Swap IX built for {token_mint}")
        return ix
    except Exception as e:
        logging.error(f"[OrcaSDK] Swap build failed: {e}")
        return None
