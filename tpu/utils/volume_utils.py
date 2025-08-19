# modules/utils/volume_utils.py

import logging
from typing import Dict, List

import aiohttp

DUNE_API_URL = "https://api.dune.com/api/v1/query/3844062/results"
DUNE_API_KEY = "dune_4VFNkYRxNpz84r53azbbsmzKnb4f3ZnK"

HEADERS = {
    "x-dune-api-key": DUNE_API_KEY
}

async def get_recent_token_volumes(minutes: int = 5) -> dict:
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(DUNE_API_URL, timeout=10) as resp:
                data = await resp.json()
                rows = data.get("result", {}).get("rows", [])
                # Format: {token_address: volume_in_SOL}
                return {
                    row["token_address"]: float(row.get("volume_sol", 0))
                    for row in rows if "token_address" in row and "volume_sol" in row
                }
    except Exception as e:
        logging.warning(f"[VolumeUtils] Failed to fetch volume data: {e}")
        return {}

def _get_candle_volume(candle: Dict) -> float:
    """
    Extracts the trading volume from a single OHLCV candle.
    Expected candle format: {"o": float, "h": float, "l": float, "c": float, "v": float}
    """
    try:
        return float(candle.get("v", 0))
    except Exception:
        return 0.0


def detect_volume_spike(ohlcv: List[Dict], lookback: int = 10, spike_multiplier: float = 2.5) -> Dict:
    """
    Detects volume spikes by comparing the last candle's volume to the average of prior candles.
    Args:
        ohlcv: List of OHLCV candles with keys "o", "h", "l", "c", "v".
        lookback: Number of candles to calculate baseline average.
        spike_multiplier: How much larger the last candle's volume must be vs baseline to trigger.
    Returns:
        {
            "spike": bool,
            "current_volume": float,
            "average_volume": float,
            "volatility_level": str ("low", "medium", "high"),
        }
    """
    if not ohlcv or len(ohlcv) < lookback + 1:
        return {"spike": False, "current_volume": 0.0, "average_volume": 0.0, "volatility_level": "low"}

    try:
        current_candle = ohlcv[-1]
        current_vol = _get_candle_volume(current_candle)
        baseline_candles = ohlcv[-(lookback + 1):-1]
        baseline_volumes = [_get_candle_volume(c) for c in baseline_candles]
        avg_vol = sum(baseline_volumes) / max(len(baseline_volumes), 1)

        spike = current_vol >= avg_vol * spike_multiplier

        # Determine volatility level
        if current_vol > avg_vol * 5:
            vol_level = "high"
        elif current_vol > avg_vol * 2:
            vol_level = "medium"
        else:
            vol_level = "low"

        return {
            "spike": spike,
            "current_volume": round(current_vol, 4),
            "average_volume": round(avg_vol, 4),
            "volatility_level": vol_level,
        }
    except Exception as e:
        logging.warning(f"[VolumeUtils] Error in detect_volume_spike: {e}")
        return {"spike": False, "current_volume": 0.0, "average_volume": 0.0, "volatility_level": "low"}


def get_average_volume(ohlcv: List[Dict], window: int = 10) -> float:
    """
    Calculates the average trading volume over a specified number of candles.
    """
    if not ohlcv:
        return 0.0
    volumes = [_get_candle_volume(c) for c in ohlcv[-window:]]
    return sum(volumes) / max(len(volumes), 1)

