import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Tuple

#token -> deque of (bucket_ts, o,h,l,c,v)
_OHLC = defaultdict(lambda: deque(maxlen=10_000))
_BUCKET = 60

def _bucket(ts: float, granularity: int) -> int:
    return int(math.floor(ts / granularity) * granularity)

def push_trade(trade: dict):
    token = trade["token"]
    ts = trade["ts"]
    price = trade["price"]
    vol = trade.get("amount", 0.0)

    bucket_ts = _bucket(ts, _BUCKET)
    dq: Deque[Tuple[int, float, float, float, float, float]] = _OHLC[token]

    if dq and dq[-1][0] == bucket_ts:
        _, o, h, l, c, v = dq[-1]
        dq[-1] = (bucket_ts, o, max(h, price), min(l, price), price, v + vol)
    else:
        dq.append((bucket_ts, price, price, price, price, vol))

def get_ohlcv_window(token: str, window_seconds: int = 1800, granularity_s: int = 60):
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = now_ts - window_seconds
    out = []
    for ts, o, h, l, c, v in _OHLC.get(token, []):
        if ts >= cutoff:
            out.append({
                "ts": ts,
                "open": o, "high": h, "low": l, "close": c,
                "volume": v,
            })
    return out
