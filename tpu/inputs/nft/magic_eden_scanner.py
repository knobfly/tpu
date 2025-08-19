import asyncio
from datetime import datetime

import aiohttp
from core.live_config import config
from cortex.txn_cortex import TxnCortex  # ← instantiate locally (no singleton)
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status
try:
    from memory.token_memory_index import get_memory_index as _get_token_memory_index
except Exception:
    _get_token_memory_index = None

MAGIC_EDEN_API = "https://api-mainnet.magiceden.dev/v2"
ME_API_KEY = (config.get("magic_eden_api_key") or "").strip()

HEADERS = {"Accept": "application/json"}
if ME_API_KEY:
    HEADERS["X-API-KEY"] = ME_API_KEY

class _NoopMemory:
    """Fallback memory adapter so TxnCortex can be constructed even if token memory isn't ready."""
    def get(self, *a, **k): return None
    def set(self, *a, **k): return None
    def append(self, *a, **k): return None
    def update(self, *a, **k): return None

try:
    _memory = _get_token_memory_index() if _get_token_memory_index else _NoopMemory()
except Exception:
    _memory = _NoopMemory()

txn_cortex = TxnCortex(memory=_memory)


async def fetch_new_listings(session: aiohttp.ClientSession):
    url = f"{MAGIC_EDEN_API}/tokens?limit=10&offset=0"
    try:
        async with session.get(url, headers=HEADERS, timeout=10) as resp:
            if resp.status != 200:
                log_event(f"[MagicEden] HTTP {resp.status}")
                return []
            data = await resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("tokens", []) or data.get("results", []) or []
            return []
    except Exception as e:
        log_event(f"[MagicEden] Error fetching listings: {e}")
        return []


async def run_magic_eden_scanner(interval: int = 30):
    update_status("magic_eden_scanner")
    log_event("🧿 Magic Eden scanner online...")

    seen = set()
    await asyncio.sleep(3)

    try:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    listings = await fetch_new_listings(session)
                    for token in listings:
                        mint = token.get("mintAddress") or token.get("mint") or token.get("address")
                        name = token.get("title") or token.get("symbol") or token.get("name") or ""
                        if not mint or mint in seen:
                            continue

                        seen.add(mint)
                        reason = f"Listed on Magic Eden as '{name}'".strip()

                        context = {
                            "mint": mint,
                            "source": "magic_eden",
                            "name": name,
                            "reason": reason,
                            "ts": datetime.utcnow().isoformat(),
                        }

                        # If you score elsewhere, plug that in; or skip scoring and let txn layer decide
                        score = float(config.get("magic_eden_default_score", 7.0))

                        try:
                            # Prefer async method if available
                            call = getattr(txn_cortex, "register_buy", None)
                            if call is None:
                                raise AttributeError("TxnCortex.register_buy missing")

                            result = call(mint, metadata=context, score=score, origin="magic_eden")
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            log_event(f"[MagicEden] txn_cortex.register_buy error for {mint}: {e}")

                        try:
                            log_scanner_insight("magic_eden", mint, {"score": score, "reason": reason})
                        except Exception:
                            pass

                    await asyncio.sleep(max(5, int(interval)))
                except Exception as loop_err:
                    log_event(f"[MagicEden] loop error: {loop_err}")
                    await asyncio.sleep(max(10, int(interval)))
    except Exception as e:
        log_event(f"[MagicEden] ❌ Scanner error: {e}")
