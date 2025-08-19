import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

from core.live_config import config
from utils.http_client import SafeSession

# In-memory ETag cache: {url -> (etag, ts)}
_ETAG_CACHE: Dict[str, Tuple[str, float]] = {}
# Simple cooldown per URL to avoid hammering after 429/errors
_COOLDOWN: Dict[str, float] = {}

def _cooldown_active(url: str) -> bool:
    until = _COOLDOWN.get(url, 0)
    return time.time() < until

async def robust_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    max_retries: int = 3,
    timeout: int = 8,
    use_etag: bool = True,
    cooldown_after_429_s: int = 120,
) -> Optional[Dict[str, Any]]:
    """
    GET JSON with retries, ETag, backoff, and 429 cooldown.
    Returns parsed JSON or None.
    """
    sess = SafeSession().session
    hdrs = dict(headers or {})
    if use_etag and url in _ETAG_CACHE and "If-None-Match" not in hdrs:
        etag, _ = _ETAG_CACHE[url]
        if etag:
            hdrs["If-None-Match"] = etag

    # Global X API key (e.g., Birdeye)
    be_key = config.get("birdeye_api_key")
    if be_key and "birdeye" in url and "X-API-KEY" not in hdrs:
        hdrs["X-API-KEY"] = be_key

    # Respect cooldown if we recently got 429
    if _cooldown_active(url):
        logging.info(f"[HTTP] Cooldown active; skipping {url}")
        return None

    delay = 0.5
    for attempt in range(1, max_retries + 1):
        try:
            async with sess.get(url, headers=hdrs, timeout=timeout) as resp:
                status = resp.status
                if status == 304:
                    logging.info(f"[HTTP] 304 Not Modified for {url}")
                    return None  # unchanged; caller can skip
                if status == 429:
                    logging.warning(f"[HTTP] 429 Too Many Requests for {url}")
                    _COOLDOWN[url] = time.time() + cooldown_after_429_s
                    return None
                if status >= 500:
                    text = await resp.text()
                    logging.warning(f"[HTTP] {status} from {url} body={text[:256]}")
                    raise RuntimeError(f"server error {status}")

                if status != 200:
                    text = await resp.text()
                    logging.warning(f"[HTTP] {status} from {url} body={text[:256]}")
                    return None

                # Capture ETag
                if use_etag:
                    etag = resp.headers.get("ETag")
                    if etag:
                        _ETAG_CACHE[url] = (etag, time.time())

                data = await resp.json(content_type=None)
                return data

        except asyncio.TimeoutError:
            logging.warning(f"[HTTP] Timeout on {url} (attempt {attempt}/{max_retries})")
        except Exception as e:
            logging.warning(f"[HTTP] Error on {url} (attempt {attempt}/{max_retries}): {e}")

        await asyncio.sleep(delay)
        delay = min(delay * 2, 10.0)

    return None
