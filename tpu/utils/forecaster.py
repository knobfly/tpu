from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# Prophet is optional; we'll gracefully fall back to a linear model if missing
try:
    from prophet import Prophet  # pip install prophet
    _HAS_PROPHET = True
except Exception:
    _HAS_PROPHET = False

try:
    from sklearn.linear_model import LinearRegression  # light fallback
    _HAS_SK = True
except Exception:
    _HAS_SK = False


@dataclass
class ForecastResult:
    expected_return: float         # pct return over horizon (e.g., 0.012 = +1.2%)
    conf_low: float                # lower bound pct
    conf_high: float               # upper bound pct
    trend_flip_prob: float         # ~probability of direction change next horizon
    quality: str                   # "prophet", "linear", "na"
    horizon: int                   # bars ahead

    def as_dict(self) -> Dict[str, Any]:
        return {
            "expected_return": float(self.expected_return),
            "conf_low": float(self.conf_low),
            "conf_high": float(self.conf_high),
            "trend_flip_prob": float(self.trend_flip_prob),
            "quality": self.quality,
            "horizon": int(self.horizon),
        }


def _prep_series(df_or_prices) -> Optional[pd.DataFrame]:
    """
    Accepts:
      - DataFrame with columns ['ts','close'] (preferred)
      - list/array of closes
    Returns DataFrame with ['ds','y'] for Prophet / regression.
    """
    try:
        if isinstance(df_or_prices, pd.DataFrame):
            df = df_or_prices.copy()
            if "ts" in df.columns and "close" in df.columns:
                # Ensure datetime
                if not np.issubdtype(df["ts"].dtype, np.datetime64):
                    df["ts"] = pd.to_datetime(df["ts"], unit="s", errors="coerce").fillna(method="ffill")
                out = pd.DataFrame({"ds": pd.to_datetime(df["ts"]), "y": pd.to_numeric(df["close"], errors="coerce")})
                out = out.dropna()
                return out
            # If user passed already 'ds','y'
            if {"ds", "y"}.issubset(df.columns):
                d = df[["ds", "y"]].copy()
                d["ds"] = pd.to_datetime(d["ds"])
                d["y"] = pd.to_numeric(d["y"], errors="coerce")
                return d.dropna()
            # Try generic rename
            if "timestamp" in df.columns and "price" in df.columns:
                d = df.rename(columns={"timestamp": "ds", "price": "y"})[["ds", "y"]].copy()
                d["ds"] = pd.to_datetime(d["ds"])
                d["y"] = pd.to_numeric(d["y"], errors="coerce")
                return d.dropna()
            return None
        # Sequence of closes
        if isinstance(df_or_prices, (list, tuple, np.ndarray)):
            arr = pd.to_numeric(pd.Series(df_or_prices), errors="coerce").dropna()
            if len(arr) < 10:
                return None
            ds = pd.date_range(end=pd.Timestamp.utcnow(), periods=len(arr), freq="T")  # minute bars by default
            return pd.DataFrame({"ds": ds, "y": arr.values})
    except Exception:
        return None
    return None


def _last_direction(y: np.ndarray) -> int:
    """Return +1 for up, -1 for down, 0 for flat over last small window."""
    if len(y) < 3:
        return 0
    d = y[-1] - y[-3]
    return 1 if d > 0 else (-1 if d < 0 else 0)


def _calc_flip_prob(y_hist: np.ndarray, y_pred_path: Sequence[float]) -> float:
    """
    Heuristic: if recent direction differs from median predicted path direction,
    assign higher flip probability.
    """
    if len(y_hist) < 5 or len(y_pred_path) < 2:
        return 0.5
    hist_dir = _last_direction(y_hist)
    pred_dir = 1 if y_pred_path[-1] - y_pred_path[0] > 0 else (-1 if y_pred_path[-1] - y_pred_path[0] < 0 else 0)
    if hist_dir == 0 or pred_dir == 0:
        return 0.5
    return 0.8 if hist_dir != pred_dir else 0.2


def _linear_forecast(df: pd.DataFrame, horizon: int) -> ForecastResult:
    y = df["y"].values.astype(float)
    n = len(y)
    if n < 12 or not _HAS_SK:
        return ForecastResult(0.0, -0.02, 0.02, 0.5, "na", horizon)

    X = np.arange(n).reshape(-1, 1)
    model = LinearRegression()
    model.fit(X, y)

    Xf = np.arange(n, n + horizon).reshape(-1, 1)
    y_pred = model.predict(Xf)

    y0 = y[-1]
    ret = (y_pred[-1] - y0) / max(1e-12, y0)

    # uncertainty proxy: residual std
    resid = y - model.predict(X)
    sigma = np.std(resid) if len(resid) > 1 else np.std(y[-5:]) if n >= 5 else 0.0
    conf = 1.96 * sigma / max(1e-12, y0)
    flip = _calc_flip_prob(y, y_pred)

    return ForecastResult(float(ret), float(ret - conf), float(ret + conf), float(flip), "linear", horizon)


def _prophet_forecast(df: pd.DataFrame, horizon: int) -> ForecastResult:
    if not _HAS_PROPHET:
        return _linear_forecast(df, horizon)

    # Keep Prophet lean for minute bars
    m = Prophet(
        daily_seasonality=False,
        weekly_seasonality=False,
        yearly_seasonality=False,
        interval_width=0.8,
    )
    try:
        m.fit(df.rename(columns={"ds": "ds", "y": "y"}))
        # Minute step future
        future = m.make_future_dataframe(periods=horizon, freq="min", include_history=True)
        fc = m.predict(future)
        last = df["y"].values[-1]
        tail = fc.tail(horizon)
        yhat = tail["yhat"].values
        yhat_lower = tail["yhat_lower"].values
        yhat_upper = tail["yhat_upper"].values

        ret = (yhat[-1] - last) / max(1e-12, last)
        lo = (yhat_lower[-1] - last) / max(1e-12, last)
        hi = (yhat_upper[-1] - last) / max(1e-12, last)
        flip = _calc_flip_prob(df["y"].values, yhat)

        return ForecastResult(float(ret), float(lo), float(hi), float(flip), "prophet", horizon)
    except Exception as e:
        logging.warning(f"[Forecaster] Prophet failed, fallback to linear: {e}")
        return _linear_forecast(df, horizon)


def forecast_next(df_or_prices, horizon: int = 15) -> Dict[str, Any]:
    """
    Predicts short-horizon move (default 15 minutes/bars).
    Input can be:
      - DataFrame with ['ts','close'] or ['ds','y']
      - list/array of closes
    Output:
      {
        expected_return: float,    # e.g., 0.012 = +1.2%
        conf_low: float,
        conf_high: float,
        trend_flip_prob: float,    # 0..1
        quality: "prophet"|"linear"|"na",
        horizon: int
      }
    """
    try:
        df = _prep_series(df_or_prices)
        if df is None or len(df) < 20:
            return ForecastResult(0.0, -0.02, 0.02, 0.5, "na", horizon).as_dict()

        # clip extreme outliers in y to stabilize fit
        y = df["y"].values
        q1, q3 = np.percentile(y, [2, 98])
        y_clipped = np.clip(y, q1, q3)
        df = df.assign(y=y_clipped)

        if _HAS_PROPHET:
            res = _prophet_forecast(df, horizon)
        else:
            res = _linear_forecast(df, horizon)
        return res.as_dict()
    except Exception as e:
        logging.warning(f"[Forecaster] forecast_next error: {e}")
        return ForecastResult(0.0, -0.02, 0.02, 0.5, "na", horizon).as_dict()
