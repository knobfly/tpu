# cortex/chart_cortex.py

import asyncio
import logging
from typing import Any, Dict, List, Optional

import pandas as pd
from chart.bitquery_analytics import get_bitquery_token  # external boost

# REUSE your existing helpers (no duplicates):
from chart.chart_data_loader import get_chart_data  # fetch OHLCV
from chart.chart_pattern_detector import detect_chart_patterns  # patterns
from chart.heatmap_optimizer import get_heatmap_boost  # heatmap boost
from chart.pump_pattern_classifier import detect_pump_signals  # pump detector
from memory.token_memory_index import update_chart_memory  # writeback
from utils.forecaster import forecast_next
from utils.logger import log_event
from utils.tech_indicators import (  # TA; scoring utils
    bbands,
    ema,
    get_momentum_score,
    get_trend_score,
    macd,
    rsi,
    sma,
)
from utils.volume_utils import detect_volume_spike  # volume


class ChartCortex:
    """
    Centralized chart analysis:
      - Pulls recent OHLCV
      - Builds indicators (RSI/MACD/EMAs/Bands)
      - Detects patterns / volume spikes / pump signatures
      - Blends into a chart_score and writes back memory
    Public API:
      - analyze_token_async({ token_address, interval?, lookback_bars?, mode? })
      - score_token(token_address, interval='1m', lookback_bars=200)
    """

    def __init__(self, memory: Optional[object] = None):
        self.memory = memory

    # ---------- public (sync wrapper) ----------
    def analyze_token(self, token_context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.analyze_token_async(token_context))
        else:
            return loop.run_until_complete(self.analyze_token_async(token_context))

    async def score_token(self, token_address: str, interval: str = "1m", lookback_bars: int = 200) -> float:
        res = await self.analyze_token_async({
            "token_address": token_address,
            "interval": interval,
            "lookback_bars": lookback_bars,
            "mode": "trade",
        })
        return float(res.get("chart_score", 0.0))

    # ---------- main async analysis ----------
    async def analyze_token_async(self, token_context: Dict[str, Any]) -> Dict[str, Any]:
        token_address = token_context.get("token_address")
        if not token_address:
            return self._empty_resp()

        interval = token_context.get("interval", "1m")
        lookback_bars = int(token_context.get("lookback_bars", 200))
        mode = token_context.get("mode", "trade")

        # 1) OHLCV
        ohlcv: List[Dict[str, Any]] = token_context.get("ohlcv") or []
        if not ohlcv:
            try:
                ohlcv = await get_chart_data(
                    token_address,
                    interval=interval,
                    lookback_bars=lookback_bars,
                    force_rebuild=False,
                )
            except Exception as e:
                logging.warning(f"[ChartCortex] OHLCV load failed for {token_address}: {e}")
                return self._empty_resp()

        if not ohlcv:
            return self._empty_resp()

        # normalize to DataFrame
        df = self._normalize_ohlcv(ohlcv)
        if df is None or len(df) < 30:
            return self._empty_resp()

        # 2) build indicator features
        df = self._build_feature_frame(df)

        # 3) detectors & external signals
        chart_tags: List[str] = []
        volatility = "unknown"
        volume_spike = None
        bitquery_boost = 0.0
        heatmap_boost = 0.0

        # volume spike
        try:
            volume_spike = detect_volume_spike(ohlcv)
            volatility = volume_spike.get("volatility_level", "low") if volume_spike else "low"
            if volume_spike:
                chart_tags.append("volume_spike")
        except Exception as e:
            logging.warning(f"[ChartCortex] volume spike failed: {e}")

        # pump signatures
        try:
            pump_signals = detect_pump_signals(ohlcv)
            if pump_signals:
                chart_tags.extend(pump_signals.get("tags", []))
        except Exception as e:
            logging.warning(f"[ChartCortex] pump pattern failed: {e}")

        # external analytics boosts (optional)
        try:
            insight = await get_bitquery_token(token_address)
            if insight and float(insight.get("score", 0) or 0) > 0:
                chart_tags.append("bitquery_positive")
                bitquery_boost = float(insight.get("score", 0) or 0)
        except Exception as e:
            logging.warning(f"[ChartCortex] bitquery insight failed: {e}")

        try:
            heatmap_boost = float(get_heatmap_boost(token_address, interval) or 0.0)
            if heatmap_boost:
                chart_tags.append("heatmap_boosted")
        except Exception as e:
            logging.warning(f"[ChartCortex] heatmap boost failed: {e}")

        # 4) patterns/trend/momentum
        try:
            patterns = detect_chart_patterns(ohlcv)
            trend = patterns.get("trend", "neutral")
            chart_tags.extend(patterns.get("tags", []))
        except Exception as e:
            logging.warning(f"[ChartCortex] pattern detection failed: {e}")
            patterns = {}
            trend = "neutral"

        try:
            trend_score = float(get_trend_score(ohlcv) or 0.0)
        except Exception as e:
            logging.warning(f"[ChartCortex] get_trend_score failed: {e}")
            trend_score = 0.0

        try:
            momentum_score = float(get_momentum_score(ohlcv) or 0.0)
        except Exception as e:
            logging.warning(f"[ChartCortex] get_momentum_score failed: {e}")
            momentum_score = 0.0

        # 5) optional Prophet short-horizon forecast (price)
        forecast_boost = 0.0
        if forecast_next is not None:
            try:
                # Build a minimal frame the forecaster expects
                _df = df if isinstance(df, pd.DataFrame) else pd.DataFrame(ohlcv)
                fdf = _df[["ts", "close"]].rename(columns={"close": "y"}).copy()

                fc = forecast_next(fdf)  # may be dict OR scalar yhat
                forecast_boost = 0.0

                if isinstance(fc, dict):
                    # Dict-style API
                    exp = float(fc.get("expected_return", 0.0))                     # e.g., +0.01 = +1%
                    unc = float(fc.get("conf_high", 0.0) - fc.get("conf_low", 0.0)) # band width
                    flip = float(fc.get("trend_flip_prob", 0.5))                    # 0..1
                    # Heuristic bump: conservative to start
                    bump = 0.0
                    bump += max(0.0, exp) * 10.0           # +1% exp => +0.10 pts
                    bump -= max(0.0, (unc - 0.02)) * 5.0   # penalize bands wider than ~2%
                    bump -= max(0.0, (flip - 0.5)) * 2.0   # discourage high flip risk
                    forecast_boost += bump

                    # Keep a small tag for traceability
                    chart_tags.append(f"forecast:{fc.get('quality', 'na')}")
                else:
                    # Scalar-style API: treat as direct next price (yhat)
                    yhat = float(fc)
                    close = float(fdf["y"].iloc[-1] or 0.0)
                    if yhat and close > 0:
                        rel = (yhat / close) - 1.0
                        # map ±5% move → ±0.6 score bump (clamped)
                        forecast_boost += max(min(rel / 0.05, 0.6), -0.6)

                chart_score = float(chart_score) + float(forecast_boost)
            except Exception as e:
                logging.debug(f"[ChartCortex] forecast hook skipped: {e}")

        # 6) compose score (kept on same 0–10 type scale many of your modules use)
        chart_score = trend_score + momentum_score + bitquery_boost + heatmap_boost + forecast_boost
        if volume_spike:
            chart_score += 5.0 if mode == "trade" else 3.0

        # clamp gently if your global scale is 0–10
        chart_score = max(min(float(chart_score), 10.0), 0.0)

        # 7) memory writeback (best effort)
        try:
            update_chart_memory(token_address, {
                "trend": trend,
                "momentum": momentum_score,
                "volatility": volatility,
                "score": chart_score,
                "tags": chart_tags,
                "interval": interval,
                "bars_used": int(len(df)),
            })
        except Exception as e:
            logging.debug(f"[ChartCortex] memory writeback skipped: {e}")

        return {
            "trend": trend,
            "momentum": momentum_score,
            "volatility": volatility,
            "chart_score": round(chart_score, 2),
            "chart_tags": chart_tags,
            "interval": interval,
            "bars_used": int(len(df)),
        }

    # ---------- internals ----------
    @staticmethod
    def _normalize_ohlcv(ohlcv: List[Dict[str, Any]]) -> Optional[pd.DataFrame]:
        try:
            if not ohlcv:
                return None
            # accept [{'ts':..., 'open':..., 'high':..., 'low':..., 'close':..., 'volume':...}, ...]
            df = pd.DataFrame(ohlcv)
            mapping = {"timestamp": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
            df = df.rename(columns={k: v for k, v in mapping.items() if k in df.columns})
            needed = {"ts", "open", "high", "low", "close", "volume"}
            if not needed.issubset(set(df.columns)):
                return None
            df = df.sort_values("ts").reset_index(drop=True)
            return df
        except Exception as e:
            logging.debug(f"[ChartCortex] normalize failed: {e}")
            return None

    @staticmethod
    def _build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
        try:
            # indicators on price
            df["rsi14"] = rsi(df["close"], 14)
            macd_line, macd_sig = macd(df["close"], 12, 26, 9)
            df["macd"] = macd_line
            df["macd_sig"] = macd_sig
            df["ema20"] = ema(df["close"], 20)
            df["ema50"] = ema(df["close"], 50)
            # bollinger bands (lower, mid, upper) if your impl returns that tuple
            try:
                lower, mid, upper = bbands(df["close"], 20, 2.0)
                df["bb_lower"], df["bb_mid"], df["bb_upper"] = lower, mid, upper
            except Exception:
                pass
            return df
        except Exception as e:
            logging.debug(f"[ChartCortex] feature build failed: {e}")
            return df

    # ---------- fallbacks ----------
    @staticmethod
    def _empty_resp() -> Dict[str, Any]:
        return {
            "trend": "unknown",
            "momentum": 0.0,
            "volatility": "unknown",
            "chart_score": 0.0,
            "chart_tags": [],
            "interval": "1m",
            "bars_used": 0,
        }


chart_cortex = ChartCortex(memory=None)
