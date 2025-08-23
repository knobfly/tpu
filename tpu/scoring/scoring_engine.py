# /scoring_engine.py
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Literal

from scoring.snipe_score_engine import evaluate_snipe
from scoring.trade_score_engine import evaluate_trade
from scoring.profile_selector import pick_profile_with_bandit
from utils.logger import log_event

Mode = Literal["snipe", "trade"]
LOGGER = logging.getLogger("scoring_router")

__all__ = ["score_token", "score_snipe", "score_trade", "decide_mode"]

try:
    from memory.token_memory_index import get_chart_memory  # cached score
except Exception:
    def get_chart_memory(_): return {}

def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def _run_maybe_async(coro_or_val):
    if asyncio.iscoroutine(coro_or_val):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro_or_val)
        else:
            # we're already in a loop; the caller should be async-aware.
            # If the caller is not async, we can create a task and wait:
            return loop.run_until_complete(coro_or_val)
    return coro_or_val

def decide_mode(ctx: Dict[str, Any]) -> Mode:
    # 1) explicit
    m = (ctx.get("mode") or "").lower()
    if m in ("snipe", "trade"):
        return m  # type: ignore

    # 2) known snipe sources
    src = (ctx.get("scanner_source") or "").lower()
    if src in ("firehose", "snipe_trigger", "amm_listen", "raydium_stream"):
        return "snipe"

    # 3) freshness
    age = ctx.get("age_minutes")
    if isinstance(age, (int, float)) and age < 10:
        return "snipe"

    return "trade"

async def _chart_analyze_async(token: str) -> float:
    """
    Ask ChartCortex for a fresh analysis and extract a normalized chart_score.
    Expected ChartCortex output includes 'chart_score' (roughly 0..20+).
    We normalize it to 0..1 for blending.
    """
    if not token:
        return 0.0
    try:
        res = await _chart_cortex.analyze_token_async({"token_address": token})
        raw = float(res.get("chart_score", 0.0))
        # Normalize: assume 0..20 typical range; clamp then map to 0..1
        return _clamp(raw, 0.0, 20.0) / 20.0
    except Exception as e:
        log_event(f"[ScoreOverlay] chart async analyze failed: {e}")
        return 0.0

def _chart_overlay_score_sync(token: str) -> float:
    """
    Best-effort normalized chart overlay in 0..1 (derived from chart_score).
    Outside an event loop we can await the analyzer; inside, we fall back to cached memory.
    """
    if not token:
        return 0.0
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # outside a loop – if you have an async analyzer, call it here; otherwise skip
        try:
            mem = get_chart_memory(token) or {}
            raw = float(mem.get("chart_score", 0.0))
            return _clamp(raw, 0.0, 20.0) / 20.0
        except Exception as e:
            log_event(f"[ScoreOverlay] chart run() failed: {e}")
            return 0.0
        except Exception:
            return 0.0


def score_token(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Router. Returns a 0..100 'final_score' and an action from the engine.
    Then applies a small chart overlay boost (profile-safe).
    ML predictions are blended into the score if present in ctx.
    """
    mode: Mode = decide_mode(ctx)
    if not ctx.get("profile"):
        prof_name, _prof = pick_profile_with_bandit(mode, ctx)
        ctx = dict(ctx)
        ctx["profile"] = prof_name
        ctx["_profile_chosen_by"] = "bandit"

    if mode == "snipe":
        res = _run_maybe_async(evaluate_snipe(ctx))
    else:
        res = _run_maybe_async(evaluate_trade(ctx))

    if not isinstance(res, dict):
        return {"action": "ignore", "final_score": 0, "_scoring_engine": mode}

    res.setdefault("_scoring_engine", mode)

    # --- ML prediction blending ---
    ml_price_pred = ctx.get("ml_price_pred")
    ml_rug_pred = ctx.get("ml_rug_pred")
    ml_wallet_pred = ctx.get("ml_wallet_pred")
    ml_boost = 0.0
    explain_ml = []
    if ml_price_pred is not None:
        ml_boost += float(ml_price_pred) * 2.0
        explain_ml.append(f"ml_price_pred: {ml_price_pred:.2f} x 2.0 = {float(ml_price_pred)*2.0:.2f}")
    if ml_rug_pred is not None:
        ml_boost -= float(ml_rug_pred) * 3.0
        explain_ml.append(f"ml_rug_pred: {ml_rug_pred:.2f} x -3.0 = {-float(ml_rug_pred)*3.0:.2f}")
    if ml_wallet_pred is not None:
        ml_boost += float(ml_wallet_pred) * 1.5
        explain_ml.append(f"ml_wallet_pred: {ml_wallet_pred:.2f} x 1.5 = {float(ml_wallet_pred)*1.5:.2f}")

    # Apply ML boost to final_score
    if "final_score" in res:
        res["final_score"] = _clamp(float(res["final_score"]) + ml_boost, 0.0, 100.0)
        res["ml_boost"] = ml_boost
        expl = res.get("explain") if isinstance(res.get("explain"), list) else []
        expl.extend(explain_ml)
        expl.append(f"ML blended boost: {ml_boost:.2f}")
        res["explain"] = expl

    # --- Existing overlays: chart, micro-forecast ---
    try:
        token = ctx.get("token_address") or ctx.get("token")
        mode = decide_mode(ctx)
        chart01 = _chart_overlay_score_sync(token)
        if isinstance(res, dict):
            base = float(res.get("final_score", 0.0))
            overlay_cap = 8.0 if mode == "trade" else 5.0
            boost = chart01 * overlay_cap
            res["final_score"] = _clamp(base + boost, 0.0, 100.0)
            res["chart_overlay"] = {
                "mode": mode,
                "chart_norm": round(chart01, 3),
                "boost_cap": overlay_cap,
                "boost_applied": round(boost, 2),
            }
            expl = res.get("explain") if isinstance(res.get("explain"), list) else []
            expl.append(f"chart_overlay:+{boost:.2f} (norm={chart01:.2f}, cap={overlay_cap:.1f})")
            res["explain"] = expl
        try:
            from utils.forecaster import forecast_next
            if forecast_next and isinstance(res, dict):
                mem = get_chart_memory(token) or {}
                ohlcv = mem.get("ohlcv")
                if ohlcv is not None:
                    fc = forecast_next(ohlcv)
                    exp = float(fc.get("expected_return", 0.0)) if isinstance(fc, dict) else 0.0
                    if exp != 0.0:
                        base2 = float(res.get("final_score", 0.0))
                        fweight = 0.05 if mode == "trade" else 0.08
                        boosted2 = base2 + (exp * 100.0 * fweight)
                        res["final_score"] = _clamp(boosted2, 0.0, 100.0)
                        expl2 = res.get("explain") if isinstance(res.get("explain"), list) else []
                        expl2.append(f"forecast_overlay:+{exp*100:.2f}%\u00d7{fweight:.2f}")
                        res["explain"] = expl2
        except Exception as e:
            log_event(f"[ScoreOverlay] forecast blend error: {e}")
    except Exception as e:
        log_event(f"[ScoreOverlay] blend error: {e}")

    try:
        LOGGER.debug(
            "[ScoringRouter] mode=%s token=%s score=%s action=%s",
            mode,
            ctx.get("token_address") or ctx.get("token"),
            res.get("final_score"),
            res.get("action"),
        )
    except Exception:
        pass

    return res


def score_snipe(ctx: Dict[str, Any]) -> Dict[str, Any]:
    out = evaluate_snipe(ctx)
    if isinstance(out, dict):
        out.setdefault("_scoring_engine", "snipe")
    return out

def score_trade(ctx: Dict[str, Any]) -> Dict[str, Any]:
    out = evaluate_trade(ctx)
    if isinstance(out, dict):
        out.setdefault("_scoring_engine", "trade")
    return out

