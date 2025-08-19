import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp
from core.live_config import config
from core.llm.llm_brain import detect_chart_trend
from cortex.txn_cortex import register_buy
from inputs.onchain.firehose.packet_listener import get_recent_ohlcv
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import record_result
from utils.logger import log_event
from utils.service_status import update_status

TOKEN_INTERVAL_MIN = 1
CONFIDENCE_THRESHOLD = 0.72
CANDLE_LOOKBACK = 30

seen_patterns = {}
chart_confidence_cache = {}

_bitquery_token = None
_bitquery_expiry = datetime.utcnow()


# ========== Bitquery Fallback OAuth ========== #
async def _fetch_bitquery_token():
    global _bitquery_token, _bitquery_expiry
    if _bitquery_token and datetime.utcnow() < _bitquery_expiry:
        return _bitquery_token

    client_id = config.get("bitquery_client_id")
    client_secret = config.get("bitquery_client_secret")
    if not client_id or not client_secret:
        logging.warning("[ChartPattern] Bitquery creds missing.")
        return None

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "api",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://oauth2.bitquery.io/oauth2/token", headers={"Content-Type": "application/x-www-form-urlencoded"}, data=payload) as resp:
            data = await resp.json()
            _bitquery_token = data.get("access_token")
            _bitquery_expiry = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 3600))
            log_event("📊 Bitquery chart token refreshed.")
            return _bitquery_token


# ========== Candle Fetch (Firehose preferred) ========== #
async def fetch_token_candles(token_address: str):
    try:
        ohlcv = await get_recent_ohlcv(token_address, CANDLE_LOOKBACK)
        if ohlcv and len(ohlcv) >= 4:
            return ohlcv
    except Exception as e:
        logging.warning(f"[ChartPattern] Firehose OHLCV failed: {e}")

    token = await _fetch_bitquery_token()
    if not token:
        return []

    query = {
        "query": f"""
        {{
          solana {{
            dexTrades(
              date: {{since: "-{CANDLE_LOOKBACK}m"}},
              exchangeName: "Jupiter Aggregator",
              baseCurrency: {{is: "{token_address}"}}
            ) {{
              timeInterval {{ minute }}
              baseCurrency {{ symbol }}
              volume
              quotePrice
            }}
          }}
        }}
        """
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://streaming.bitquery.io/graphql", headers=headers, json=query) as resp:
                result = await resp.json()
                return result.get("data", {}).get("solana", {}).get("dexTrades", [])
    except Exception as e:
        logging.error(f"[ChartPattern] Bitquery fallback failed: {e}")
        return []


# ========== Pattern Analysis Utils ========== #
def get_trendline_direction(candles):
    closes = [c.get("quotePrice") for c in candles if c.get("quotePrice") is not None]
    if len(closes) < 2:
        return "unknown"
    delta = closes[-1] - closes[0]
    if delta > 0.02:
        return "uptrend"
    elif delta < -0.02:
        return "downtrend"
    return "flat"


def evaluate_timing(candles):
    for i, candle in enumerate(candles[1:], start=1):
        prev_vol = candles[i - 1].get("volume", 0)
        curr_vol = candle.get("volume", 0)
        if curr_vol > 3 * prev_vol:
            return i / len(candles)
    return 1.0


def record_chart_pattern(token_address: str, pattern: str, confidence: float):
    chart_confidence_cache[token_address] = round(confidence, 2)


def get_chart_confidence(token_address: str) -> float:
    return chart_confidence_cache.get(token_address, 0.0)


# ========== Main Pattern Detector ========== #
async def scan_chart_patterns(brain) -> None:
    update_status("chart_pattern_detector")
    try:
        tokens = brain.get_tracked_tokens()
        for token in tokens:
            address = token.get("address")
            symbol = token.get("symbol")
            if not address:
                continue

            candles = await fetch_token_candles(address)
            if not candles or len(candles) < 4:
                continue

            pattern_result = detect_chart_trend(candles)
            if not pattern_result or pattern_result.get("confidence", 0) < CONFIDENCE_THRESHOLD:
                continue

            pattern_type = pattern_result["type"]
            confidence = pattern_result["confidence"]
            pattern_id = f"{address}-{pattern_type}"
            if seen_patterns.get(pattern_id):
                continue
            seen_patterns[pattern_id] = True

            trend = get_trendline_direction(candles)
            timing = evaluate_timing(candles)

            confidence_boost = 0.0
            if trend == "uptrend" and timing <= 0.5:
                confidence_boost += 0.2
            elif trend == "downtrend":
                confidence_boost -= 0.3

            final_confidence = confidence + confidence_boost
            record_chart_pattern(address, pattern_type, final_confidence)

            metadata = {
                "pattern": pattern_type,
                "trend": trend,
                "timing": timing,
                "confidence": final_confidence,
                "symbol": symbol,
                "recent_price": candles[-1].get("quotePrice"),
                "volume": candles[-1].get("volume", 0),
                "time": datetime.utcnow().isoformat(),
            }

            log_event(f"[ChartPattern] {symbol} → {pattern_type} | {trend} | confidence={final_confidence:.2f}")
            log_scanner_insight("chart_pattern", metadata)

            try:
                brain.record_scanner_source(address, "chart_pattern")
                brain.score_with_theme_meta(symbol=symbol, tag=pattern_type, boost=round(final_confidence * 100))
                threshold = config.get("chart_pattern_threshold", 7.0)
                if final_confidence >= threshold:
                    score = brain.evaluate_score_only(address)
                    from exec.trade_executor import TradeExecutor
                    tx = TradeExecutor.buy_token(address, metadata=metadata, score=score, source="chart_pattern")
                    register_buy(token, wallet=None, tx=tx)
            except Exception as e:
                logging.debug(f"[ChartPattern] brain hook error: {e}")

            record_result({
                "type": "trend_eval",
                "mint": address,
                "trend": trend,
                "timing": timing,
                "score": final_confidence,
                "timestamp": datetime.utcnow().isoformat()
            })

    except Exception as e:
        logging.error(f"[ChartScanner] Fatal error: {e}")


# ========== Runtime Loop ========== #
async def run_chart_pattern_detector(brain):
    update_status("chart_pattern_detector")
    log_event("📈 Chart Pattern Detector started.")
    interval = TOKEN_INTERVAL_MIN * 60
    while True:
        try:
            await scan_chart_patterns(brain)
        except Exception as e:
            logging.warning(f"[ChartPattern] Runtime loop error: {e}")
        await asyncio.sleep(interval)


# Alias for manual triggers
detect_chart_patterns = scan_chart_patterns
