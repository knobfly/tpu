# /chart/chart_signal_features.py
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from utils.tech_indicators import bbands, ema, macd, rsi


def build_feature_frame(ohlcv_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Accepts DataFrame with at least ['ts','open','high','low','close','volume'].
    Returns same frame with indicator columns added.
    """
    try:
        df = ohlcv_df.copy()
        need = {"ts","open","high","low","close","volume"}
        if not need.issubset(df.columns):
            return None
        df = df.sort_values("ts").reset_index(drop=True)

        df["rsi14"] = rsi(df["close"], 14)
        macd_line, macd_sig = macd(df["close"], 12, 26, 9)
        df["macd"] = macd_line
        df["macd_sig"] = macd_sig
        df["ema20"] = ema(df["close"], 20)
        df["ema50"] = ema(df["close"], 50)
        try:
            lower, mid, upper = bbands(df["close"], 20, 2.0)
            df["bb_lower"], df["bb_mid"], df["bb_upper"] = lower, mid, upper
        except Exception:
            pass
        return df
    except Exception as e:
        logging.warning(f"[ChartFeatures] build failed: {e}")
        return None
