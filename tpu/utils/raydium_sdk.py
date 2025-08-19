# utils/raydium_sdk.py
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

# Raydium AMM v4
RAYDIUM_AMM_PROGRAM = PublicKey("675kPX9MHTjS2zt1qfr1NYHqWgJz7jzH6tK5g84n4hS")

# Public registry (fast path)
_RAYDIUM_URL = "https://api.raydium.io/v2/sdk/liquidity/mainnet.json"
_CACHE = {"ts": 0.0, "data": None}
_TTL = 60 * 30  # 30 minutes

__all__ = [
    "get_pool_accounts_for_mint",     # registry (fast path)
    "fetch_raydium_pool_state_onchain",# on-chain fallback
    "resolve_pool_for_mint",           # try both, normalized
    "build_raydium_swap_instruction",  # helper to build swap IX
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
    # serve cache if fresh
    if not force and _CACHE["data"] and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]
    try:
        data = await _fetch_json(_RAYDIUM_URL)
        _CACHE["data"] = data
        _CACHE["ts"] = now
        return data
    except Exception as e:
        logging.warning(f"[RaydiumSDK] registry fetch failed: {e}")
        # stale is better than none
        return _CACHE["data"]

async def get_pool_accounts_for_mint(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Returns:
      {
        "state": <ammId>,
        "vault_a": <baseVault>,
        "vault_b": <quoteVault>,
        "lp_mint": <lpMint>,
        "baseMint": <mint>,
        "quoteMint": <mint>,
        "fee_bps": <int>,       # NEW, if present in registry
      }
    """
    reg = await _get_registry()
    if not reg:
        return None

    try:
        pools = []
        for group in reg.values():
            if isinstance(group, list):
                pools.extend(group)

        for p in pools:
            base = p.get("baseMint")
            quote = p.get("quoteMint")
            if token_mint in (base, quote):
                fee_bps = 0
                # Raydium formats vary; try a couple common fields
                if "tradeFeeRate" in p and isinstance(p["tradeFeeRate"], (int, float)):
                    fee_bps = int(float(p["tradeFeeRate"]) * 10000)  # e.g., 0.003 -> 30 bps
                elif "fees" in p and isinstance(p["fees"], dict):
                    fee_bps = int(float(p["fees"].get("tradeFeeRate", 0)) * 10000)

                return {
                    "state": p.get("id") or p.get("ammId"),
                    "vault_a": p.get("baseVault") or p.get("baseVaultAddress"),
                    "vault_b": p.get("quoteVault") or p.get("quoteVaultAddress"),
                    "lp_mint": p.get("lpMint"),
                    "baseMint": base,
                    "quoteMint": quote,
                    "fee_bps": fee_bps,
                }
    except Exception as e:
        logging.warning(f"[RaydiumSDK] parse error: {e}")

    return None

# ---------------------------------
# On-chain fallback (program scan)
# ---------------------------------

async def fetch_raydium_pool_state_onchain(token_address: str, rpc_url: Optional[str] = None) -> Optional[Dict]:
    """
    Query AMM v4 program accounts to find a pool that references `token_address`.
    NOTE: This is a heuristic. Exact layout varies by pool version; registry is preferred.
    Returns:
      {"pubkey": "<pool_state_pubkey>", "account": <raw_account_data>} or None
    """
    rpc_url = rpc_url or get_active_rpc()
    try:
        async with AsyncClient(rpc_url) as client:
            # Minimal memcmp probe — offset may vary by pool layout.
            # This can be tuned further if needed.
            filters = [
                {"memcmp": {"offset": 72, "bytes": token_address}},
            ]
            resp = await client.get_program_accounts(RAYDIUM_AMM_PROGRAM, filters=filters)
            if not resp.value:
                return None

            acc = resp.value[0]
            log_event(f"[RaydiumSDK] On-chain pool account for {token_address}: {acc.pubkey}")
            return {
                "pubkey": str(acc.pubkey),
                "account": acc.account.data,  # raw bytes/base64 (RPC dependent)
            }
    except Exception as e:
        logging.error(f"[RaydiumSDK] on-chain fetch failed for {token_address}: {e}")
        return None

# ---------------------------------
# Unified resolver
# ---------------------------------

async def resolve_pool_for_mint(token_mint: str) -> Optional[Dict[str, str]]:
    """
    Try registry first, then on-chain, and return a normalized structure:
      {
        "state": "<pool_state_pubkey>",
        "vault_a": "<base_vault_pubkey>|None",
        "vault_b": "<quote_vault_pubkey>|None",
        "lp_mint": "<lp_mint_pubkey>|None",
        "baseMint": "<base_mint>|None",
        "quoteMint": "<quote_mint>|None",
        "_source": "registry|onchain",
    }
    """
    # 1) Registry (preferred)
    reg = await get_pool_accounts_for_mint(token_mint)
    if reg:
        reg["_source"] = "registry"
        return reg

    # 2) Fallback to on-chain probe (state only)
    oc = await fetch_raydium_pool_state_onchain(token_mint)
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
# Swap IX helper (Raydium)
# ---------------------------------

async def build_raydium_swap_instruction(
    payer,
    pool_info: Dict[str, str],
    token_mint: str,
    sol_amount: float,
) -> Optional[TransactionInstruction]:
    """
    Build a Raydium swap instruction using a resolved pool.
    Requires your helper at: utils/raydium_instructions.py (make_swap_instruction)
    """
    try:
        from utils.raydium_instructions import make_swap_instruction  # your existing helper
    except ImportError:
        logging.error("[RaydiumSDK] Missing utils.raydium_instructions.make_swap_instruction")
        return None

    # Ensure mint exists (optional, but helps fail fast)
    mint_info = await get_token_mint_info(token_mint)
    if not mint_info:
        logging.warning(f"[RaydiumSDK] Missing mint info for {token_mint}")
        return None

    try:
        ix = make_swap_instruction(
            payer=PublicKey(payer.address),
            pool_pubkey=PublicKey(pool_info["state"]),
            token_mint=PublicKey(token_mint),
            amount_in=int(sol_amount * 10**9),  # SOL lamports
            min_amount_out = int(expected_out_amount * (1 - slippage_tolerance))
        )
        log_event(f"[RaydiumSDK] Swap IX built for {token_mint}")
        return ix
    except Exception as e:
        logging.error(f"[RaydiumSDK] Swap build failed: {e}")
        return None
