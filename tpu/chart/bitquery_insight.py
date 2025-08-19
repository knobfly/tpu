# /inputs/insight/bitquery_insight.py
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
from core.live_config import config
from exec.trade_executor import TradeExecutor
from scoring.snipe_score_engine import evaluate_snipe
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status

# === Bitquery Credentials / Endpoints ===
BITQUERY_API_KEY = (
    config.get("bitquery_api_key")
    or os.environ.get("BITQUERY_API_KEY")
    or ""
)

CLIENT_ID = config.get("bitquery_client_id", "74a37332-0218-4bf4-9609-68a1a75787a4")
CLIENT_SECRET = config.get("bitquery_client_secret", "F8RJp6n5dkOtAULPjqmsGVXa5W")

TOKEN_URL = config.get("bitquery_token_url", "https://oauth2.bitquery.io/oauth2/token")
# Prefer non-streaming HTTP GraphQL for polling; streaming may require WS and different auth.
GRAPHQL_URL = config.get("bitquery_graphql_url", "https://graphql.bitquery.io/")

# Content-Type guard
JSON_CT = "application/json"

# === Token Cache ===
access_token: Optional[str] = None
token_expiry: float = 0.0

# === GraphQL Query ===
SOLANA_QUERY = """
query {
  solana {
    transfers(
      limit: {count: 8, offset: 0},
      date: {since: "-10m"},
      amount: {gt: 10000000}
    ) {
      amount
      currency {
        address
        symbol
        name
      }
      sender { address }
      receiver { address }
      transaction { signature success }
      block { timestamp { iso8601 } }
    }
  }
}
"""

# === OAuth / API-Key helper ===
async def fetch_token(session: aiohttp.ClientSession) -> Optional[str]:
    """
    Fetch OAuth token unless BITQUERY_API_KEY is provided (API key mode).
    Returns:
        "__API_KEY__" sentinel when using API key mode,
        OAuth access_token string when using OAuth,
        or None on failure.
    """
    global access_token, token_expiry

    if BITQUERY_API_KEY:
        return "__API_KEY__"

    now = time.time()
    if access_token and now < token_expiry:
        return access_token

    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "api",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": JSON_CT}

    try:
        async with session.post(TOKEN_URL, data=payload, headers=headers) as resp:
            ct = resp.headers.get("Content-Type", "")
            if resp.status != 200 or ct.split(";")[0] != JSON_CT:
                text = await resp.text()
                logging.error(f"[Bitquery] Token fetch failed (HTTP {resp.status}, CT={ct}): {text[:300]}")
                return None
            data = await resp.json()
            token = data.get("access_token")
            if not token:
                logging.error(f"[Bitquery] No access_token in response: {data}")
                return None
            access_token = token
            token_expiry = now + float(data.get("expires_in", 1800)) - 10
            log_event("🔑 Bitquery token refreshed.")
            return access_token
    except Exception as e:
        logging.error(f"[Bitquery] Token fetch exception: {e}")
        return None

# === GraphQL fetch ===
async def fetch_and_process_data(session: aiohttp.ClientSession):
    token = await fetch_token(session)
    if not token:
        return

    headers = {"Accept": JSON_CT, "Content-Type": JSON_CT}
    if token == "__API_KEY__":
        headers["X-API-KEY"] = BITQUERY_API_KEY
    else:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with session.post(GRAPHQL_URL, headers=headers, json={"query": SOLANA_QUERY}) as resp:
            ct = resp.headers.get("Content-Type", "")
            if resp.status == 402:
                # Quota/billing error is often returned as text/plain
                text = await resp.text()
                logging.error(f"[Bitquery] 402 Payment/Quota: {text[:300]}")
                return
            if ct.split(";")[0] != JSON_CT:
                text = await resp.text()
                logging.error(f"[Bitquery] Unexpected content-type {ct} (HTTP {resp.status}): {text[:300]}")
                return
            data = await resp.json()
            await process_bitquery_data(data)
    except Exception as e:
        logging.error(f"[Bitquery] GraphQL fetch failed: {e}")

# === Process + scoring / trigger ===
async def process_bitquery_data(data: Dict[str, Any]):
    transfers = (((data or {}).get("data") or {}).get("solana") or {}).get("transfers", []) or []
    for tx in transfers:
        currency = tx.get("currency", {}) or {}
        token = currency.get("address")
        symbol = currency.get("symbol", "???")
        name = currency.get("name", "???")
        # NOTE: adjust decimals if your feed is in different base units
        amount = float(tx.get("amount", 0)) / 1e6
        timestamp = (((tx.get("block") or {}).get("timestamp") or {}).get("iso8601")) or ""
        tx_success = ((tx.get("transaction") or {}).get("success")) is True

        if not token or amount <= 0:
            continue

        # evaluate_snipe expects a context dict; read 'final_score'
        snipe_ctx = {"token_address": token, "profile": "t0_liquidity"}
        try:
            result = await evaluate_snipe(snipe_ctx)  # prefer async
        except TypeError:
            # If evaluate_snipe is sync in this runtime
            result = evaluate_snipe(snipe_ctx)

        final_score = float(result.get("final_score", result.get("score", 0.0)))
        # If no explicit model confidence, proxy 0..100 → 0..10
        confidence = float(result.get("confidence", 0.0)) or max(0.0, min(10.0, final_score / 10.0))

        log_event(f"📊 Bitquery: {symbol} moved {amount:.2f} → Score: {final_score:.2f} | AI: {confidence:.2f}")

        try:
            log_scanner_insight(
                token=token,
                source="bitquery",
                sentiment=confidence,
                volume=amount,
                result="bitquery_match",
                tags=[
                    f"symbol:{symbol}",
                    f"name:{name}",
                    f"score:{final_score:.2f}",
                    f"tx_success:{tx_success}",
                    f"timestamp:{timestamp}",
                ],
            )
        except Exception:
            pass

        # Trigger (threshold per your config/heuristic)
        if confidence >= 6.9:
            try:
                maybe_coro = TradeExecutor.buy_token(
                    token=token,
                    score=final_score,
                    source="bitquery",
                    metadata={"volume": amount},
                )
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro
            except Exception as e:
                logging.warning(f"[Bitquery] buy_token error for {token}: {e}")

# === Loop Entry ===
async def run_bitquery_insight():
    log_event("📡 Bitquery Insight Scanner started.")
    update_status("bitquery_insight")

    try:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await fetch_and_process_data(session)
                except Exception as e:
                    logging.error(f"[BitqueryScanner] Error: {e}")
                await asyncio.sleep(60)
    except Exception as e:
        logging.error(f"[BitqueryScanner] Session error: {e}")
