# utils/amm_pools.py
from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

from core.live_config import config
from solana.publickey import PublicKey
from solana.transaction import TransactionInstruction
from utils.logger import log_event

# ──────────────────────────────────────────────────────────────────────────────
# Known program IDs (seeded with what you already use; extendable via config)
# ──────────────────────────────────────────────────────────────────────────────

KNOWN_PROGRAM_IDS = {
    # From your onchain_listener.py
    "raydium": {
        # Program IDs you’re already watching
        "owners": {
            # Raydium AMM families you had:
            "RVKd61ztZW9mq8wCnD1iDfbTtyjcibGZ2y3sPzoiUJq",
            "9tdctL2kJHREvJBCXJhfpapM7pWxEt49RdcMGsTCTQnD",
        },
    },
    "orca": {
        "owners": {
            # Orca program IDs you had:
            "82yxjeMs7fA3C5exzL84uCPpGEECJpLfByZYkzqUvvjD",
            "6UeJFcVRZ5bN2W2kE3KYuyUTqz9KtZctUPBhYfczEeqF",
            # NOTE: If you later add Whirlpool owner id, drop it here too.
        },
    },
}

# Allow config to extend/override without code edits
try:
    extra = (config.get("amm_program_ids") or {})
    for dex, blob in extra.items():
        KNOWN_PROGRAM_IDS.setdefault(dex, {}).setdefault("owners", set()).update(set(blob.get("owners", [])))
except Exception:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# Registry-fed pool discovery (cheap + fast) — reuses your SDK helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _lookup_raydium_registry(token_mint: str) -> Optional[Dict[str, Any]]:
    """Return unified pool dict if token is in Raydium registry."""
    try:
        from utils.raydium_sdk import get_pool_accounts_for_mint as ray_get
    except Exception:
        return None

    try:
        p = await ray_get(token_mint)
        if not p:
            return None
        return {
            "dex": "raydium",
            "state": p.get("state"),
            "vault_a": p.get("vault_a"),
            "vault_b": p.get("vault_b"),
            "lp_mint": p.get("lp_mint"),
            "baseMint": p.get("baseMint"),
            "quoteMint": p.get("quoteMint"),
        }
    except Exception as e:
        logging.debug(f"[AMM] Raydium registry lookup failed: {e}")
        return None


async def _lookup_orca_registry(token_mint: str) -> Optional[Dict[str, Any]]:
    """Return unified pool dict if token is in Orca registry."""
    try:
        from utils.orca_sdk import get_pool_accounts_for_mint as orca_get
    except Exception:
        return None

    try:
        p = await orca_get(token_mint)
        if not p:
            return None
        return {
            "dex": "orca",
            "state": p.get("state"),
            "vault_a": p.get("vault_a"),
            "vault_b": p.get("vault_b"),
            "lp_mint": p.get("lp_mint"),
            "baseMint": p.get("baseMint"),
            "quoteMint": p.get("quoteMint"),
        }
    except Exception as e:
        logging.debug(f"[AMM] Orca registry lookup failed: {e}")
        return None


async def discover_pool_for_mint(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Unified pool discovery:
      1) Raydium registry
      2) Orca registry
    Returns {'dex','state','vault_a','vault_b','lp_mint','baseMint','quoteMint'} or None
    """
    if not token_mint or len(token_mint) < 32:
        return None

    # Raydium first (usually broader coverage for meme tokens)
    pool = await _lookup_raydium_registry(token_mint)
    if pool:
        return pool

    # Orca whirlpools/AMM
    pool = await _lookup_orca_registry(token_mint)
    if pool:
        return pool

    return None

# ──────────────────────────────────────────────────────────────────────────────
# Slippage-aware swap builder (delegates to your *_instructions helpers)
# ──────────────────────────────────────────────────────────────────────────────

async def _quote_out_amount(token_mint: str, amount_in_sol: float) -> Optional[float]:
    """
    Best-effort quote. Prefer your JupiterSwap quote if available; else None.
    Expected to return *token out* units (not SOL).
    """
    try:
        from utils.jupiter_swap import JupiterSwap

        # Using a throwaway sessionless quote method if you expose one; otherwise return None.
        # If your JupiterSwap requires an instance, you can wire a cached one instead.
        out = await JupiterSwap.quick_quote_in_sol(token_mint, amount_in_sol)  # <- if you expose this
        return float(out) if out else None
    except Exception:
        return None


def _compute_min_out(expected_out: Optional[float], slippage_bps: int) -> int:
    """
    Convert expected_out (float token units) into a conservative min_out (integer),
    applying slippage tolerance in basis points (e.g., 50 = 0.5%).
    Fallback to 1 if no quote is available.
    """
    if not expected_out or expected_out <= 0:
        return 1
    slip = max(0, slippage_bps) / 10_000.0
    min_out = expected_out * (1.0 - slip)
    return max(1, int(math.floor(min_out)))


async def build_slippage_aware_swap_ix(
    payer,                    # wallet manager or object with .address
    token_mint: str,
    amount_in_sol: float,
    dex_hint: Optional[str] = None,   # "raydium" | "orca" | None (auto)
    slippage_bps: int = 50,           # 0.50% default
) -> Tuple[Optional[TransactionInstruction], Optional[Dict[str, Any]]]:
    """
    Build a single swap instruction for the best-known pool, with minAmountOut slippage protection.
    Returns (ix, meta) where meta includes {'dex','min_out','expected_out'}.
    """
    pool = await discover_pool_for_mint(token_mint)
    if not pool:
        logging.debug(f"[AMM] No pool found for {token_mint}")
        return None, None

    dex = pool["dex"]
    if dex_hint and dex_hint in ("raydium", "orca"):
        dex = dex_hint  # allow explicit override

    # Best-effort expected out (so min_out respects your slippage)
    expected_out = None
    try:
        expected_out = await _quote_out_amount(token_mint, amount_in_sol)
    except Exception:
        pass

    min_out = _compute_min_out(expected_out, slippage_bps)

    try:
        if dex == "raydium":
            # You already have helper imports for Raydium in your tree
            from utils.raydium_instructions import make_swap_instruction as ray_swap  # your helper
            from utils.raydium_sdk import get_pool_accounts_for_mint  # noqa: F401 (keeps parity)
            ix = ray_swap(
                payer=PublicKey(payer.address),
                pool_pubkey=PublicKey(pool["state"]),
                token_mint=PublicKey(token_mint),
                amount_in=int(amount_in_sol * 10**9),   # lamports
                min_amount_out=min_out,
            )
        else:
            from utils.orca_instructions import make_swap_instruction as orca_swap  # your helper
            from utils.orca_sdk import get_pool_accounts_for_mint  # noqa: F401
            ix = orca_swap(
                payer=PublicKey(payer.address),
                pool_pubkey=PublicKey(pool["state"]),
                token_mint=PublicKey(token_mint),
                amount_in=int(amount_in_sol * 10**9),
                min_amount_out=min_out,
            )
        meta = {
            "dex": dex,
            "min_out": min_out,
            "expected_out": expected_out,
            "pool": pool,
            "slippage_bps": slippage_bps,
        }
        return ix, meta
    except Exception as e:
        logging.warning(f"[AMM] Swap IX build failed for {dex}/{token_mint}: {e}")
        return None, None
