import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from typing import Any, Iterable, Mapping, Optional
import aiohttp

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=15)

class SafeSession:
    """
    Reusable aiohttp session with:
      - lazy creation
      - global timeout & connector limits
      - concurrency-safe init
      - context-manager support
      - optional retries/backoff on 429/5xx
    """
    def __init__(
        self,
        *,
        timeout: aiohttp.ClientTimeout = _DEFAULT_TIMEOUT,
        max_connections: int = 100,
        headers: Optional[Mapping[str, str]] = None,
        base_url: Optional[str] = None,
        retry_statuses: Iterable[int] = (429, 500, 502, 503, 504),
        max_retries: int = 2,
        retry_backoff_base: float = 0.6,
    ):
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = timeout
        self._connector = aiohttp.TCPConnector(limit=max_connections, enable_cleanup_closed=True)
        self._headers = dict(headers or {})
        self._base_url = base_url
        self._retry_statuses = set(retry_statuses)
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._lock = asyncio.Lock()
        self.created_at = ''.join(traceback.format_stack(limit=3))  # short trace

    async def _ensure(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        async with self._lock:
            if self._session and not self._session.closed:
                return self._session
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                connector=self._connector,
                headers=self._headers,
                base_url=self._base_url or None,
            )
            logging.debug(f"[SafeSession] Created session:\n{self.created_at}")
            return self._session

    @property
    def session(self) -> aiohttp.ClientSession:
        # Synchronous accessor for legacy code paths.
        # Note: prefer 'await get()' or '.request()' which guarantees creation under concurrency.
        if not self._session or self._session.closed:
            # This path is *not* concurrency-safe; keep for compatibility.
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                connector=self._connector,
                headers=self._headers,
                base_url=self._base_url or None,
            )
            logging.debug(f"[SafeSession] Created session via property:\n{self.created_at}")
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            try:
                await self._session.close()
                logging.debug("[SafeSession] Closed session successfully.")
            except Exception as e:
                logging.warning(f"[SafeSession] Failed to close session: {e}")

        # Also close connector explicitly to free sockets
        try:
            await self._connector.close()
        except Exception as e:
            logging.warning(f"[SafeSession] Failed to close connector: {e}")

    async def __aenter__(self) -> aiohttp.ClientSession:
        return await self._ensure()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _retry_sleep(self, attempt: int, retry_after: Optional[float]) -> None:
        if retry_after is not None:
            await asyncio.sleep(retry_after)
        else:
            await asyncio.sleep(self._retry_backoff_base * (2 ** attempt))

    @asynccontextmanager
    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ):
        """
        Usage:
            async with safe.request("GET", "/path", params=...) as resp:
                data = await resp.json()
        Retries on 429/5xx with exponential backoff (and respects Retry-After).
        """
        sess = await self._ensure()
        attempt = 0
        while True:
            try:
                resp = await sess.request(method, url, **kwargs)
                if resp.status in self._retry_statuses and attempt < self._max_retries:
                    retry_after = None
                    try:
                        ra = resp.headers.get("Retry-After")
                        if ra:
                            retry_after = float(ra)
                    except Exception:
                        retry_after = None
                    await resp.release()
                    await self._retry_sleep(attempt, retry_after)
                    attempt += 1
                    continue
                yield resp
                break
            except aiohttp.ClientError as e:
                if attempt < self._max_retries:
                    await self._retry_sleep(attempt, None)
                    attempt += 1
                    continue
                raise e

    async def get_json(url: str, *, headers=None, params=None, timeout=15):
        """Single-shot GET that always closes the session and response."""
        try:
            timeout_cfg = aiohttp.ClientTimeout(total=timeout, connect=5, sock_connect=5, sock_read=timeout)
            async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                async with session.get(url, headers=headers, params=params) as r:
                    txt = await r.text()
                    if r.status >= 400:
                        raise RuntimeError(f"HTTP {r.status}: {txt[:500]}")
                    try:
                        return await r.json(content_type=None)
                    except Exception:
                        # fallback to text if not JSON
                        return {"_raw": txt}
        except Exception as e:
            logging.warning(f"[HTTP] {url} failed: {e}")
            raise

    async def post_json(self, url: str, json: Any = None, **kwargs) -> Any:
        async with self.request("POST", url, json=json, **kwargs) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def fetch_json(url: str, *, headers=None, params=None, timeout=15):
        to = aiohttp.ClientTimeout(total=timeout, connect=5, sock_connect=5, sock_read=timeout)
        async with aiohttp.ClientSession(timeout=to) as session:
            async with session.get(url, headers=headers, params=params) as r:
                txt = await r.text()
                if r.status >= 400:
                    raise RuntimeError(f"HTTP {r.status}: {txt[:500]}")
                try:
                    return await r.json(content_type=None)
                except Exception:
                    return {"_raw": txt}


