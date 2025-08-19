import math
from typing import Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

Number = Union[int, float]
SeriesLike = Union[pd.Series, Sequence[Number]]
OHLCV = Iterable[dict]

# ---------------------------
# Helpers
# ---------------------------

def _to_series(x: SeriesLike, name: str = "x") -> pd.Series:
    if isinstance(x, pd.Series):
        return x.astype(float)
    return pd.Series(list(x), dtype=float, name=name)

def _safe_last(seq: Sequence[Number], default: float = 0.0) -> float:
    return float(seq[-1]) if len(seq) else float(default)

def _pct_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return (b - a) / abs(a)

def _extract_close(data: Union[OHLCV, SeriesLike]) -> pd.Series:
    """
    Accepts:
      - list[dict] with 'close' or 'c'
      - list/series of floats
    Returns a float Series of closes.
    """
    if isinstance(data, pd.Series):
        return data.astype(float)

    if isinstance(data, (list, tuple)) and data and isinstance(data[0], dict):
        rows = []
        for d in data:
            v = d.get("close", d.get("c"))
            if v is not None:
                rows.append(float(v))
        return pd.Series(rows, dtype=float, name="close")

    # assume simple numeric list
    return _to_series(data, name="close")

# ---------------------------
# Core MAs / Bands / Momentum
# ---------------------------

def sma(x: SeriesLike, window: int) -> pd.Series:
    s = _to_series(x)
    return s.rolling(window=window, min_periods=1).mean()

def ema(x: SeriesLike, window: int) -> pd.Series:
    s = _to_series(x)
    return s.ewm(span=window, adjust=False).mean()

def rsi(x: SeriesLike, period: int = 14) -> pd.Series:
    """
    Wilder's RSI returning a full Series (aligned).
    """
    s = _to_series(x)
    delta = s.diff()

    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)

    roll_up = up.ewm(alpha=1/period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, adjust=False).mean()

    rs = roll_up / roll_down.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(method="bfill").fillna(50.0)

def macd(x: SeriesLike, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series]:
    s = _to_series(x)
    ema_fast = ema(s, fast)
    ema_slow = ema(s, slow)
    line = ema_fast - ema_slow
    sig = ema(line, signal)
    return line, sig

def bbands(x: SeriesLike, window: int = 20, n_std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    s = _to_series(x)
    mid = sma(s, window)
    std = s.rolling(window=window, min_periods=1).std(ddof=0)
    upper = mid + n_std * std
    lower = mid - n_std * std
    return lower, mid, upper

# ---------------------------
# Extras (optional, non-breaking)
# ---------------------------

def atr(high: SeriesLike, low: SeriesLike, close: SeriesLike, period: int = 14) -> pd.Series:
    h, l, c = _to_series(high, "high"), _to_series(low, "low"), _to_series(close, "close")
    prev_close = c.shift(1)
    tr = pd.concat([
        (h - l).abs(),
        (h - prev_close).abs(),
        (l - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()

def vwap(high: SeriesLike, low: SeriesLike, close: SeriesLike, volume: SeriesLike) -> pd.Series:
    h, l, c, v = _to_series(high, "high"), _to_series(low, "low"), _to_series(close, "close"), _to_series(volume, "volume")
    tp = (h + l + c) / 3.0
    cum_v = v.cumsum().replace(0, np.nan)
    return (tp * v).cumsum() / cum_v

# ---------------------------
# Scores used by ChartCortex
# ---------------------------

def get_trend_score(prices_or_ohlcv: Union[SeriesLike, OHLCV], min_len: int = 5) -> float:
    """
    Returns 0.0..1.0 uptrend score combining % change and monotonicity.
    Accepts closes array or OHLCV list[dict].
    """
    closes = _extract_close(prices_or_ohlcv)
    if closes is None or len(closes) < min_len:
        return 0.0

    prices = closes.values.tolist()
    start, end = prices[0], prices[-1]
    pct = _pct_change(start, end)

    # monotonic up ratio
    up_steps = sum(1 for i in range(1, len(prices)) if prices[i] >= prices[i - 1])
    monotonic = up_steps / max(1, (len(prices) - 1))

    # combine (pct can be negative). weight pct more than monotonic.
    raw = (pct * 0.7) + (monotonic * 0.3)

    # clamp & normalize to 0..1
    score = max(0.0, min(1.0, (raw + 1.0) / 2.0))
    return round(float(score), 4)

def get_momentum_score(prices_or_ohlcv: Union[SeriesLike, OHLCV], window: int = 5) -> float:
    """
    0.0..1.0 momentum score: recent slope normalized by stddev.
    Accepts closes array or OHLCV list[dict].
    """
    closes = _extract_close(prices_or_ohlcv)
    if closes is None or len(closes) < 2:
        return 0.0

    prices = closes.values.tolist()
    tail = prices[-window:] if len(prices) >= window else prices[:]
    if len(tail) < 2:
        return 0.0

    slope = tail[-1] - tail[0]
    mean = sum(tail) / len(tail)
    var = sum((p - mean) ** 2 for p in tail) / max(1, len(tail) - 1)
    std = math.sqrt(var)

    if std == 0:
        return 0.0

    norm = slope / (std * len(tail))
    score = max(0.0, min(1.0, (norm + 1.0) / 2.0))
    return round(float(score), 4)

# ---------------------------
# Your existing functions (kept)
# ---------------------------

def calculate_rsi(prices: Sequence[Number], period: int = 14) -> Optional[float]:
    """
    Legacy helper returning the **latest** RSI value (float).
    Kept for backward compatibility.
    """
    if len(prices) < period + 1:
        return None

    deltas = np.diff(np.asarray(prices, dtype=float))
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0.0

    rsi_vals = np.zeros(len(prices), dtype=float)
    rsi_vals[:period] = 100.0 - 100.0 / (1.0 + rs)

    up_val, down_val = up, down
    for i in range(period, len(prices)):
        delta = deltas[i - 1]
        upv = max(delta, 0.0)
        downv = -min(delta, 0.0)
        up_val = (up_val * (period - 1) + upv) / period
        down_val = (down_val * (period - 1) + downv) / period
        rs = up_val / down_val if down_val != 0 else 0.0
        rsi_vals[i] = 100.0 - 100.0 / (1.0 + rs)

    return round(float(rsi_vals[-1]), 2)

def get_ema_trend(prices: Sequence[Number], short_window: int = 9, long_window: int = 21) -> str:
    if len(prices) < long_window:
        return "neutral"
    s = pd.Series(prices, dtype=float)
    short_ema = s.ewm(span=short_window, adjust=False).mean().iloc[-1]
    long_ema = s.ewm(span=long_window, adjust=False).mean().iloc[-1]
    if short_ema > long_ema:
        return "uptrend"
    if short_ema < long_ema:
        return "downtrend"
    return "neutral"

def detect_reversal_candle(prices: Sequence[Number]) -> bool:
    if len(prices) < 3:
        return False
    p1, p2, p3 = prices[-3:]
    return (p2 > p1) and (p2 > p3) and ((p2 - p1) > (p3 - p2))

# ---------------------------
# Exports (for clarity)
# ---------------------------

__all__ = [
    "sma", "ema", "rsi", "macd", "bbands",
    "atr", "vwap",
    "get_trend_score", "get_momentum_score",
    "calculate_rsi", "get_ema_trend", "detect_reversal_candle",
]

