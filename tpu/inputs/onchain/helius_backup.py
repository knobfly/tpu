# modules/helius_backup.py

import logging
from typing import Any, Dict, List, Optional

import aiohttp
from core.live_config import config
from utils.logger import log_event
from utils.service_status import update_status

# Optional – if you already have this, we’ll use it. Otherwise we soft-fallback.
try:
    from utils.helius_budget_guard import HeliusBudgetGuard
    budget_guard = HeliusBudgetGuard(max_units_per_month=100_000_000)
except Exception:
    class _NoopGuard:
        def allow_usage(self, *_a, **_k): return config.get("enable_helius_backup", False)
        def record_usage(self, *_a, **_k): pass
    budget_guard = _NoopGuard()

HELIUS_API_KEY = config.get("helius_api_key", "")
BASE_URL = "https://api.helius.xyz/v0"

# Estimated “costs” (tune to your plan)
COSTS = {
    "balances": 3_000,
    "txs": 4_000,
    "metadata": 5_000,
    "classify": 2_000,
}

def _enabled() -> bool:
    return bool(config.get("enable_helius_backup", False) and HELIUS_API_KEY)

async def _helius_get(session: aiohttp.ClientSession, path: str, cost_key: str, **params) -> Any:
    if not _enabled():
        return None

    cost = COSTS.get(cost_key, 1000)
    if not budget_guard.allow_usage(cost):
        logging.debug(f"[HeliusBackup] Budget guard blocked '{path}'")
        return None

    params["api-key"] = HELIUS_API_KEY
    url = f"{BASE_URL}{path}"

    try:
        async with session.get(url, params=params, timeout=15) as resp:
            data = await resp.json()
            budget_guard.record_usage(cost)
            return data
    except Exception as e:
        logging.warning(f"[HeliusBackup] GET {path} failed: {e}")
        return None


async def get_token_balances(address: str) -> Dict:
    """
    Very cheap fallback balance fetch.
    """
    update_status("helius_backup")
    if not _enabled():
        return {}
    async with aiohttp.ClientSession() as session:
        data = await _helius_get(session, f"/addresses/{address}/balances", "balances")
        return data or {}


async def get_recent_transactions(address: str, limit: int = 5) -> List[Dict]:
    """
    Tiny recent TX fetch (limit hard-capped to keep costs tiny).
    """
    update_status("helius_backup")
    if not _enabled():
        return []
    limit = min(limit, 10)
    async with aiohttp.ClientSession() as session:
        data = await _helius_get(
            session,
            f"/addresses/{address}/transactions",
            "txs",
            limit=limit
        )
        return data or []


async def classify_address(address: str) -> Dict:
    """
    Light classification endpoint.
    """
    update_status("helius_backup")
    if not _enabled():
        return {}
    async with aiohttp.ClientSession() as session:
        data = await _helius_get(session, f"/addresses/{address}/classify", "classify")
        return data or {}


async def fetch_token_metadata(mint: str) -> Dict:
    """
    Optional: keep if you liked Helius’ metadata responses.
    Prefer your own MagicEden/Opensea/Tensor pipelines otherwise.
    """
    update_status("helius_backup")
    if not _enabled():
        return {}
    async with aiohttp.ClientSession() as session:
        try:
            payload = {"mintAccounts": [mint]}
            url = f"{BASE_URL}/tokens/metadata?api-key={HELIUS_API_KEY}"
            cost = COSTS.get("metadata", 5_000)

            if not budget_guard.allow_usage(cost):
                return {}

            async with session.post(url, json=payload, timeout=15) as resp:
                data = await resp.json()
                budget_guard.record_usage(cost)
                return (data or [{}])[0]
        except Exception as e:
            logging.warning(f"[HeliusBackup] Metadata fetch failed: {e}")
            return {}
