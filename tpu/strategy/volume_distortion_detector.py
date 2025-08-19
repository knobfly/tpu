from statistics import mean, stdev

from chart.chart_data_loader import get_recent_candles


def detect_volume_distortion(token_address: str, window: int = 30) -> dict:
    """
    Analyzes recent volume data for signs of spoofing or manipulation.

    Returns:
        {
            "score": int,
            "distorted": bool,
            "reason": str
        }
    """
    candles = get_recent_candles(token_address, limit=window)
    if not candles or len(candles) < 5:
        return {
            "score": 0,
            "distorted": False,
            "reason": "Insufficient candle data"
        }

    volumes = [c["volume"] for c in candles if c.get("volume") is not None]

    if len(volumes) < 5:
        return {
            "score": 0,
            "distorted": False,
            "reason": "Not enough volume points"
        }

    avg_volume = mean(volumes)
    vol_stdev = stdev(volumes)

    recent = volumes[-1]
    prev = volumes[-5:-1]

    if vol_stdev == 0 or avg_volume == 0:
        return {
            "score": 0,
            "distorted": False,
            "reason": "Flat volume"
        }

    if recent > avg_volume * 3 and recent > max(prev) * 2:
        return {
            "score": -8,
            "distorted": True,
            "reason": f"⚠️ Detected sharp volume spike: {recent} vs avg {avg_volume}"
        }

    return {
        "score": 0,
        "distorted": False,
        "reason": "✅ Volume stable"
    }
