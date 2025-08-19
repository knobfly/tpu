import logging
import time
from typing import Any, Dict, List, Optional

from core.live_config import config
from utils.http_robust import robust_get_json


def _norm_event(source: str, mint: str, reason: str, extra: Optional[Dict[str, Any]] = None):
    evt = {"source": source, "mint": mint, "reason": reason, "ts": int(time.time())}
    if extra: evt.update(extra)
    return evt

async def dexscreener_new_pairs_solana(limit: int = 20) -> List[Dict[str, Any]]:
    # public, no key; “new pairs” equivalent via search or hot pairs. Example uses “search” for fresh mentions.
    url = "https://api.dexscreener.com/latest/dex/search?q=solana"
    data = await robust_get_json(url)
    out = []
    try:
        pairs = (data or {}).get("pairs", [])[:limit]
        for p in pairs:
            chain = p.get("chainId")
            if chain != "solana":
                continue
            base = (p.get("baseToken") or {}).get("address")
            if not base:
                continue
            reason = "hot_pair" if p.get("txns", {}).get("m5", {}).get("buys", 0) else "pair_seen"
            out.append(_norm_event("dexscreener", base, reason, {"pair": p.get("pairAddress")}))
    except Exception as e:
        logging.warning(f"[DexScreener] parse error: {e}")
    return out

async def birdeye_new_tokens(limit: int = 20) -> List[Dict[str, Any]]:
    from core.live_config import config
    url = "https://public-api.birdeye.so/defi/tokenlist?chain=solana"
    api_key = config.get("birdeye_api_key") or config.get("integrations", {}).get("birdeye", {}).get("api_key")
    headers = {"X-API-KEY": api_key} if api_key else None

    try:
        try:
            data = await robust_get_json(url, headers=headers)  # if supported
        except TypeError:
            data = await robust_get_json(url)
    except Exception as e:
        logging.warning(f"[Birdeye] fetch error: {e}")
        return []

    payload = (data or {}).get("data", [])
    if isinstance(payload, dict):
        items = payload.get("tokens") or payload.get("list") or payload.get("items") or []
    elif isinstance(payload, list):
        items = payload
    else:
        logging.warning(f"[Birdeye] unexpected payload shape for tokenlist: {type(payload)}")
        return []

    out = []
    for t in items[:max(0, int(limit))]:
        if not isinstance(t, dict):
            continue
        mint = t.get("address") or t.get("mint") or t.get("tokenAddress")
        if not mint:
            continue
        payload = {
            "symbol": t.get("symbol") or t.get("name") or "UNKNOWN",
            "name": t.get("name") or t.get("symbol") or "UNKNOWN",
            "decimals": t.get("decimals"),
            "price": t.get("price"),
            "liquidity": t.get("liquidity") or t.get("liquidityUSD"),
            "source": "birdeye",
        }
        out.append(_norm_event("birdeye", mint, "list_seen", payload))
    return out

