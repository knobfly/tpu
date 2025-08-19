# modules/bitquery_analytics.py

import logging
import statistics
import time
from datetime import datetime, timedelta

import aiohttp
from core.live_config import config
from utils.logger import log_event

# === Bitquery Auth ===
BITQUERY_CLIENT_ID = "74a37332-0218-4bf4-9609-68a1a75787a4"
BITQUERY_CLIENT_SECRET = "F8RJp6n5dkOtAULPjqmsGVXa5W"
BITQUERY_TOKEN_URL = "https://oauth2.bitquery.io/oauth2/token"
BITQUERY_GRAPHQL_URL = "https://streaming.bitquery.io/graphql"

_bitquery_token = None
_token_expiry = 0

async def get_bitquery_token(session):
    global _bitquery_token, _token_expiry
    now = time.time()
    if _bitquery_token and now < _token_expiry:
        return _bitquery_token

    payload = {
        'grant_type': 'client_credentials',
        'client_id': BITQUERY_CLIENT_ID,
        'client_secret': BITQUERY_CLIENT_SECRET,
        'scope': 'api'
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        async with session.post(BITQUERY_TOKEN_URL, data=payload, headers=headers) as resp:
            data = await resp.json()
            _bitquery_token = data.get("access_token")
            _token_expiry = now + data.get("expires_in", 1800) - 10
            log_event("🧠 Bitquery OHLCV token refreshed.")
            return _bitquery_token
    except Exception as e:
        logging.error(f"[Bitquery Analytics] Token fetch failed: {e}")
        return None

async def get_token_ohlcv(token_address, chain="solana", interval="30m"):
    async with aiohttp.ClientSession() as session:
        token = await get_bitquery_token(session)
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        query = f"""
        query {{
          dexTrades(
            date: {{since: "-2d"}}
            exchangeAddress: {{is: "{token_address}"}}
            exchangeName: {{is: "{chain}"}}
          ) {{
            timeInterval {{
              minute(count: 30)
            }}
            baseCurrency {{
              symbol
              address
            }}
            quoteCurrency {{
              symbol
            }}
            tradeAmount(in: USD)
            quotePrice
            maximum_price: quotePrice(calculate: maximum)
            minimum_price: quotePrice(calculate: minimum)
            open_price: minimum(of: block, get: quote_price)
            close_price: maximum(of: block, get: quote_price)
            trades: count
          }}
        }}
        """

        try:
            async with session.post(BITQUERY_GRAPHQL_URL, headers=headers, json={"query": query}) as resp:
                data = await resp.json()
                return data.get("data", {}).get("dexTrades", [])
        except Exception as e:
            logging.error(f"[Bitquery Analytics] OHLCV fetch failed: {e}")
            return None

async def detect_volume_spike(token_address, chain="solana", window=12, threshold=2.5):
    """
    Detects a significant volume spike using a rolling average.
    :param token_address: The token's address
    :param chain: Which chain to pull from (default: solana)
    :param window: How many intervals to use (e.g., 12 * 30min = 6h)
    :param threshold: Spike factor (e.g., 2.5x average volume)
    :return: dict with spike info or None
    """
    ohlcv_data = await get_token_ohlcv(token_address, chain)
    if not ohlcv_data or len(ohlcv_data) < window + 1:
        return None

    try:
        recent_data = ohlcv_data[-(window+1):]
        volumes = [float(bar["tradeAmount(in: USD)"]) for bar in recent_data[:-1]]
        latest_volume = float(recent_data[-1]["tradeAmount(in: USD)"])
        avg_volume = statistics.mean(volumes)

        if latest_volume >= avg_volume * threshold:
            return {
                "token": token_address,
                "chain": chain,
                "spike_volume": latest_volume,
                "avg_volume": avg_volume,
                "multiplier": latest_volume / avg_volume,
                "timestamp": recent_data[-1]["timeInterval"]["minute"]
            }

        return None
    except Exception as e:
        logging.error(f"[Bitquery Analytics] Spike detection failed: {e}")
        return None

async def get_wallet_activity(wallet_address, chain="solana", limit=20):
    """
    Fetches recent DEX trade activity from a wallet.
    :param wallet_address: Public address of wallet
    :param chain: Which chain to query (default: solana)
    :param limit: Max number of recent trades to return
    :return: dict summary with trades, volume, and token metadata
    """
    async with aiohttp.ClientSession() as session:
        token = await get_bitquery_token(session)
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        query = f"""
        query {{
          dexTrades(
            options: {{desc: ["block.height"], limit: {limit} }},
            txSender: {{is: "{wallet_address}"}},
            exchangeName: {{is: "{chain}"}}
          ) {{
            transaction {{
              hash
              block {{
                timestamp {{
                  iso8601
                }}
              }}
            }}
            tradeAmount(in: USD)
            baseCurrency {{
              symbol
              address
            }}
            quoteCurrency {{
              symbol
            }}
            quotePrice
          }}
        }}
        """

        try:
            async with session.post(BITQUERY_GRAPHQL_URL, headers=headers, json={"query": query}) as resp:
                data = await resp.json()
                trades = data.get("data", {}).get("dexTrades", [])

                # Analyze behavior
                summary = {
                    "wallet": wallet_address,
                    "chain": chain,
                    "total_trades": len(trades),
                    "total_volume": 0,
                    "tokens": {},
                    "trades": []
                }

                for tx in trades:
                    symbol = tx["baseCurrency"].get("symbol", "???")
                    token_address = tx["baseCurrency"].get("address", "???")
                    volume = float(tx.get("tradeAmount(in: USD)", 0))
                    summary["total_volume"] += volume
                    summary["tokens"].setdefault(symbol, {"count": 0, "volume": 0, "address": token_address})
                    summary["tokens"][symbol]["count"] += 1
                    summary["tokens"][symbol]["volume"] += volume
                    summary["trades"].append(tx)

                return summary
        except Exception as e:
            logging.error(f"[Bitquery Analytics] Wallet activity fetch failed: {e}")
            return None

async def get_top_gainers_losers(chain="solana", metric="volume", limit=10, timeframe_hours=6):
    """
    Returns top tokens by volume or price gain.
    :param chain: Which chain to search (solana, ethereum, bsc, etc.)
    :param metric: 'volume' or 'price'
    :param limit: Number of top tokens to return
    :param timeframe_hours: Time window to consider
    :return: List of tokens with stats
    """
    async with aiohttp.ClientSession() as session:
        token = await get_bitquery_token(session)
        if not token:
            logging.error("[Bitquery Analytics] No token available for top gainers/losers.")
            return []

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        date_filter = f'-{timeframe_hours}h'

        query = f"""
        query {{
          dexTrades(
            date: {{since: "{date_filter}"}}
            exchangeName: {{is: "{chain}"}}
          ) {{
            baseCurrency {{
              symbol
              address
            }}
            quoteCurrency {{
              symbol
            }}
            tradeAmount(in: USD)
            quotePrice
            trades: count
          }}
        }}
        """

        try:
            async with session.post(BITQUERY_GRAPHQL_URL, headers=headers, json={"query": query}) as resp:
                if resp.status != 200:
                    logging.error(f"[Bitquery Analytics] Non-200 response: {resp.status}")
                    return []

                raw = await resp.json()
                data = raw.get("data", {})
                if not data or "dexTrades" not in data:
                    logging.warning("[Bitquery Analytics] No dexTrades data returned.")
                    return []

                trades = data.get("dexTrades", [])

                tokens = {}
                for tx in trades:
                    base = tx.get("baseCurrency", {})
                    sym = base.get("symbol", "???")
                    addr = base.get("address", "???")
                    volume = float(tx.get("tradeAmount(in: USD)", 0) or 0)
                    price = float(tx.get("quotePrice", 0) or 0)

                    tokens.setdefault(sym, {"volume": 0, "address": addr, "count": 0, "last_price": 0})
                    tokens[sym]["volume"] += volume
                    tokens[sym]["count"] += 1
                    tokens[sym]["last_price"] = price

                if not tokens:
                    logging.info("[Bitquery Analytics] No token data collected.")
                    return []

                sorted_tokens = sorted(tokens.items(), key=lambda x: x[1].get(metric, 0), reverse=True)
                return sorted_tokens[:limit]

        except Exception as e:
            logging.error(f"[Bitquery Analytics] Gainers/losers fetch failed: {e}")
            return []

async def get_lp_add_events(token_address, chain="solana", hours=2, min_lp_value_usd=5000):
    """
    Detects large LP adds (token transfers into known DEX contracts).
    :param token_address: Target token address
    :param chain: Blockchain (solana, ethereum, etc.)
    :param hours: Time window
    :param min_lp_value_usd: Minimum value to trigger as a real LP event
    :return: List of LP adds
    """
    async with aiohttp.ClientSession() as session:
        token = await get_bitquery_token(session)
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        query = f"""
        query {{
          transfers(
            options: {{desc: "block.timestamp.iso8601"}}
            amount: {{gt: 0}}
            currency: {{is: "{token_address}"}}
            date: {{since: "-{hours}h"}}
          ) {{
            amount
            sender {{
              address
            }}
            receiver {{
              address
            }}
            transaction {{
              hash
            }}
            block {{
              timestamp {{
                iso8601
              }}
            }}
          }}
        }}
        """

        try:
            async with session.post(BITQUERY_GRAPHQL_URL, headers=headers, json={"query": query}) as resp:
                data = await resp.json()
                transfers = data.get("data", {}).get("transfers", [])

                lp_events = []
                for tx in transfers:
                    sender = tx["sender"]["address"]
                    receiver = tx["receiver"]["address"]
                    amount = float(tx.get("amount", 0)) / 1e6  # Assuming SPL 6 decimals
                    timestamp = tx["block"]["timestamp"]["iso8601"]

                    # Heuristic: LP adds usually go to Raydium/Orca system addresses
                    if "111111" in receiver or len(receiver) > 40:
                        continue  # skip system or junk

                    if amount * 1.0 >= min_lp_value_usd:
                        lp_events.append({
                            "amount": amount,
                            "sender": sender,
                            "receiver": receiver,
                            "timestamp": timestamp,
                            "tx_hash": tx["transaction"]["hash"]
                        })

                return lp_events
        except Exception as e:
            logging.error(f"[Bitquery Analytics] LP detection failed: {e}")
            return None
