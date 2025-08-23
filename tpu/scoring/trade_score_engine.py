import logging
import time
from datetime import datetime

from defense.ai_sniper_intuition import apply_sniper_intuition
from defense.bundle_launch_detector import detect_bundle_launch
from defense.honeypot_similarity_scanner import get_similarity_penalty
from inputs.social.alpha_reactor.alpha_router import AlphaRouter
from inputs.social.influencer_scanner import InfluencerSignalTracker
from inputs.social.sentiment_fusion import SentimentFusion
from cortex.strategy_cortex import apply_strategy_logic
# === Fusion Modules ===
from inputs.wallet.wallet_influence import WalletInfluenceTracker

# === Librarian Context Builder ===
from librarian.data_librarian import librarian
from memory.token_memory_index import update_score_memory
from memory.token_outcome_memory import log_token_outcome
from runtime.event_bus import TradeDecisionEvent, event_bus, now
from scoring.profile_config import get_defaults, get_profile
from special.adaptive_trade_controller import adaptive_controller
from special.ai_self_tuner import should_skip_trade, tune_strategy
from special.micro_strategy_tuner import tune_strategy_context
from special.signal_fusion_engine import signal_fusion_engine
from strategy.ai_reweight_engine import apply_reasoning_weights
from strategy.causal_predictor import causal_predictor
from strategy.confidence_reweighter import reweight_confidence
from strategy.evaluation_tracker import log_evaluation
from strategy.outcome_predictor import predict_outcome_from_signals
from strategy.reinforcement_tracker import log_trade_feedback, update_token_result
from strategy.score_cluster_embedder import embed_trade_context
from strategy.score_memory_adjuster import apply_memory_adjustment
from strategy.score_memory_logger import log_score_event
from strategy.self_adjustment_engine import apply_self_adjustments
from strategy.self_verifier import verify_evaluation
from strategy.signal_pattern_tracker import record_token_signals
from strategy.trade_verdict_enhancer import enhance_verdict
from strategy.trait_weight_engine import apply_trait_scores, update_trait_weights
from utils.logger import log_event
from utils.time_utils import get_token_age_minutes
from utils.token_utils import get_lp_lock_status, get_token_fees, is_blacklisted_token
from utils.wallet_helpers import count_unique_buyers

wallet_influence = WalletInfluenceTracker()
influencer_tracker = InfluencerSignalTracker()
sentiment_fusion = SentimentFusion()


def _get(ctx: dict, *keys, default=None):
    d = ctx
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d if d is not None else default

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def _to_pct(x: float, lo: float, hi: float) -> float:
    """Map x in [lo,hi] -> 0..1; clamp outside."""
    if hi <= lo:
        return 0.0
    return _clamp01((x - lo) / (hi - lo))


async def evaluate_trade(token_context: dict) -> dict:
    """
    Profile-driven trade scorer:
      - gates (risk guards)
      - bucket scores (chart/onchain/social/memory/flow) -> weighted blend (0..100)
      - dynamic adjustments (early, trusted-source, bundle)
      - action mapping via profile thresholds + sizing hints
    """
    token_name = token_context.get("token_name")
    token_address = token_context.get("token_address")
    metadata = token_context.get("metadata", {}) or {}

    logging.info(f"[TradeScoreEngine] Evaluating trade: {token_name} | {token_address}")

    # === Blacklist early exit
    if is_blacklisted_token(token_address):
        log_event(f"⛔ Blacklisted token blocked: {token_address}")
        return {"action": "ignore", "final_score": 0, "reasoning": ["blacklisted"], "strategy": {}}

    # Load profile (allow override via ctx["profile"])
    profile = get_profile("trade", token_context.get("profile") or "momentum") or {}
    defaults = get_defaults() or {}

    reasons: list[str] = []
    breakdown: dict[str, float] = {}
    insights: dict = {}
    outcome = "pending"

    # ---------- Risk gates ----------
    # Honeypot similarity gate (you already had this)
    similarity_penalty = get_similarity_penalty(token_address)
    if similarity_penalty >= 20:
        return {"action": "ignore", "final_score": 0, "reasoning": ["honeypot_similarity"], "strategy": {}}

    # LP lock / fees / taxes
    lp_lock_required = profile.get("gates", {}).get("lp_locked_required", defaults.get("lp_locked_required", False))
    lp_status = get_lp_lock_status(token_address)  # "locked" | "unlocked" | "unknown"
    if lp_lock_required and lp_status != "locked":
        return {"action": "ignore", "final_score": 0, "reasoning": ["lp_not_locked"], "strategy": {}}

    try:
        fees = get_token_fees(token_address) or {}
    except Exception as e:
        logging.warning(f"[TradeScore] Fee fetch failed: {e}")
        fees = {}
    total_tax_bps = int(fees.get("total_tax_bps") or fees.get("total_tax") or 0)
    max_tax_bps = profile.get("gates", {}).get("tax_max_bps", defaults.get("tax_max_bps", 600))
    if total_tax_bps > max_tax_bps:
        return {"action": "ignore", "final_score": 0, "reasoning": [f"tax_too_high:{total_tax_bps}"], "strategy": {}}

    # Optional context-provided gates (if your ctx/librarian provides them)
    spread_pct = _get(token_context, "market", "spread_pct", default=None)
    slippage_bps = _get(token_context, "market", "est_slippage_bps", default=None)
    depth_sol = _get(token_context, "market", "depth_sol", default=None)

    max_spread = profile.get("gates", {}).get("max_spread_pct", defaults.get("max_spread_pct", 2.0))
    max_slip = profile.get("gates", {}).get("max_slippage_bps", defaults.get("max_slippage_bps", 250))
    min_depth = profile.get("gates", {}).get("min_depth_sol", defaults.get("min_depth_sol", 30))

    if spread_pct is not None and spread_pct > max_spread:
        return {"action": "ignore", "final_score": 0, "reasoning": [f"spread_too_wide:{spread_pct}%"], "strategy": {}}
    if slippage_bps is not None and slippage_bps > max_slip:
        return {"action": "ignore", "final_score": 0, "reasoning": [f"slippage_too_high:{slippage_bps}bps"], "strategy": {}}
    if depth_sol is not None and depth_sol < min_depth:
        return {"action": "ignore", "final_score": 0, "reasoning": [f"depth_too_low:{depth_sol}"], "strategy": {}}

    # Bundle penalty / confirm logic (still scoreable, but may affect thresholds)
    try:
        bundle = detect_bundle_launch(token_address) or {}
    except Exception as e:
        logging.warning(f"[TradeScore] Bundle check failed: {e}")
        bundle = {}

    # ---------- Context build (you already used librarian) ----------
    context = await librarian.build_context(token_context)  # assumes sync in your codebase
    txn = context.get("txn", {}) or {}
    wallets = context.get("wallets", {}) or {}
    chart = context.get("chart", {}) or {}
    insights.update(context)

    # ---------- Bucket scores (0..10 each) ----------
    # CHART
    chart_score_raw = float(chart.get("chart_score", 0.0))
    # Expecting your chart_score around 0..20; map to 0..10 softly:
    chart_score_10 = max(0.0, min(10.0, chart_score_raw / 2.0))
    breakdown["chart"] = round(chart_score_10, 2)

    # ONCHAIN: LP, events, fees (inverse), whales, reputation, buyers
    onchain_points = 0.0
    if lp_status == "locked":
        onchain_points += 2.0
    if txn.get("lp_added"):
        onchain_points += 2.0
    if wallets.get("whales_present"):
        onchain_points += 2.0
    onchain_points += float(wallets.get("avg_reputation", 0.0)) * 0.3  # normalize if your rep is 0..10
    buyers = count_unique_buyers(token_context)
    if buyers >= 100:
        onchain_points += 2.0
    elif buyers >= 50:
        onchain_points += 1.0
    # tax penalty: map 0..max_tax_bps to 0..2 negative
    onchain_points -= 2.0 * _to_pct(total_tax_bps, 0.0, float(max_tax_bps))
    onchain_points = max(0.0, min(10.0, onchain_points))
    breakdown["onchain"] = round(onchain_points, 2)

    # SOCIAL: influencer + sentiment fusion
    try:
        infl = float(influencer_tracker.get_signal_boost(token_name) or 0.0)
    except Exception:
        infl = 0.0
    try:
        sent = float(sentiment_fusion.compute_sentiment_score(token_name) or 0.0)
    except Exception:
        sent = 0.0
    # assume infl/sent are roughly 0..10 already; clamp
    social_points = max(0.0, min(10.0, infl * 0.6 + sent * 0.4))
    breakdown["social"] = round(social_points, 2)

    # MEMORY: your reinforcement/feature store can feed a 0..10-ish
    try:
        mem_boost = float(_get(context, "memory", "composite", default=0.0))
    except Exception:
        mem_boost = 0.0
    memory_points = max(0.0, min(10.0, mem_boost))
    breakdown["memory"] = round(memory_points, 2)

    # FLOW: txn velocity / RVOL; we’ll derive something if provided
    try:
        rvol = float(_get(context, "txn", "rvol_1m", default=1.0))  # 1.0 baseline
    except Exception:
        rvol = 1.0
    flow_points = max(0.0, min(10.0, (rvol - 1.0) * 2.5))  # rvol 1.0->0, 2.0->2.5 etc.
    breakdown["flow"] = round(flow_points, 2)

    # ---------- Weights & base blended score (0..100) ----------
    w = profile.get("weights", {}) or {}
    w_chart   = float(w.get("chart", 0.0))
    w_onchain = float(w.get("onchain", 0.0))
    w_social  = float(w.get("social", 0.0))
    w_memory  = float(w.get("memory", 0.0))
    w_flow    = float(w.get("flow", 0.0))
    w_sum = w_chart + w_onchain + w_social + w_memory + w_flow
    blended = (
        (chart_score_10 * w_chart) +
        (onchain_points * w_onchain) +
        (social_points  * w_social) +
        (memory_points  * w_memory) +
        (flow_points    * w_flow)
    ) * (10.0 / w_sum) if w_sum > 0 else 0.0

    # --- ML prediction blending ---
    ml_price_pred = token_context.get("ml_price_pred")
    ml_rug_pred = token_context.get("ml_rug_pred")
    ml_wallet_pred = token_context.get("ml_wallet_pred")
    ml_boost = 0.0
    if ml_price_pred is not None:
        ml_boost += float(ml_price_pred) * 2.0
        reasons.append(f"ml_price_pred: {ml_price_pred:.2f} x 2.0 = {float(ml_price_pred)*2.0:.2f}")
    if ml_rug_pred is not None:
        ml_boost -= float(ml_rug_pred) * 3.0
        reasons.append(f"ml_rug_pred: {ml_rug_pred:.2f} x -3.0 = {-float(ml_rug_pred)*3.0:.2f}")
    if ml_wallet_pred is not None:
        ml_boost += float(ml_wallet_pred) * 1.5
        reasons.append(f"ml_wallet_pred: {ml_wallet_pred:.2f} x 1.5 = {float(ml_wallet_pred)*1.5:.2f}")

    score = blended + ml_boost
    reasons.append(f"ML blended boost: {ml_boost:.2f}")
    reasons.append(f"buckets chart={breakdown['chart']} onchain={breakdown['onchain']} social={breakdown['social']} memory={breakdown['memory']} flow={breakdown['flow']}")

    # ---------- Your existing “extras” layered in (but softly) ----------
    # Intuition & Traits (keep, but cap their influence)
    try:
        keywords = f"{metadata.get('name','')} {metadata.get('symbol','')} {metadata.get('creator','')}".split()
    except Exception:
        keywords = []

    sniper_boost, sniper_reason = apply_sniper_intuition(token_address, keywords)
    if sniper_boost:
        # scale to at most +5
        add = max(-5.0, min(5.0, float(sniper_boost)))
        score += add
        breakdown["intuition"] = add
        reasons.append(f"AI intuition: {sniper_reason} (+{add:.1f})")

    trait_boost, trait_reasons = apply_trait_scores(keywords)
    if trait_boost:
        add = max(-5.0, min(5.0, float(trait_boost)))
        score += add
        breakdown["traits"] = add
        reasons.extend(trait_reasons)

    # Fusion “snapshot” (keep existing plumbing)
    snapshot = signal_fusion_engine().ingest_signals(
        token=token_address,
        wallet_score=float(wallets.get("avg_reputation", 0.0)),
        chart_score=chart_score_raw,
        sentiment_score=social_points,  # use our social_points blend as proxy
        volume_score=float(txn.get("sniper_pressure", 0.0) or 0.0) / 10.0,
    )
    decision_id = f"{token_address}-{int(time.time() * 1000)}"
    causal_predictor().log_decision(
        decision_id=decision_id,
        token=token_address,
        decision="enter" if snapshot.fused_score > 0.7 else "skip",
        confidence=snapshot.fused_score,
        fused_score=snapshot.fused_score,
        signals=snapshot.signals,
        meta={"tags": snapshot.reason_tags},
    )

    # ---------- Dynamic adjustments ----------
    # Early-window relief (using token age if available)
    age_min = get_token_age_minutes(token_context)
    early = profile.get("dynamic", {}).get("early_window", {})
    if isinstance(age_min, (int, float)) and age_min is not None:
        if age_min <= float(early.get("seconds", defaults.get("early_window", {}).get("seconds", 120)) / 60.0):
            relief = float(early.get("probe_relief", defaults.get("early_window", {}).get("probe_relief", 0)))
            if relief:
                breakdown["early_relief"] = relief
                reasons.append(f"early_window_relief {relief:+.1f}")
                # Apply only to **probe threshold** later (we'll store it as adj, not in score)
                early_relief = relief
            else:
                early_relief = 0.0
        else:
            early_relief = 0.0
    else:
        early_relief = 0.0

    # Trusted-source relief (if you set in ctx)
    trusted = bool(token_context.get("trusted_source_ping") or _get(context, "social", "trusted_source", default=False))
    trusted_relief = 0.0
    if trusted:
        trusted_relief = float(profile.get("dynamic", {}).get("trusted_source_relief", defaults.get("trusted_source_relief", 0.0)))
        if trusted_relief:
            breakdown["trusted_relief"] = trusted_relief
            reasons.append(f"trusted_source_relief {trusted_relief:+.1f}")

    # Bundle penalty
    bundle_pen = 0.0
    if bundle.get("is_bundle"):
        bundle_pen = float(profile.get("dynamic", {}).get("bundle_penalty", defaults.get("bundle_penalty", 0.0)))
        if bundle_pen:
            breakdown["bundle_penalty"] = -bundle_pen
            reasons.append(f"bundle_penalty {-bundle_pen:.1f}")
            score = max(0.0, score - bundle_pen)

    # ---------- Self / memory reweighters (keep, but clamp 0..100 afterwards) ----------
    score = apply_self_adjustments(score, token_address)
    score = apply_reasoning_weights(score, reasons, token_address)
    score = apply_memory_adjustment(score, reasons, {**token_context, "wallets": wallets, "txn": txn})
    score = max(0.0, min(round(score, 2), 100.0))

    # ---------- Micro strategy & global freeze ----------
    micro_strategy = tune_strategy_context({
        "token_name": token_name,
        "meta_score": score,
        "wallet_count": int(wallets.get("wallet_count", 0) or 0),
        "chart_trend": chart.get("trend", "unknown"),
        "volatility": chart.get("volatility", 0.0),
        "bundle_detected": bool(bundle.get("is_bundle", False)),
    })
    score = float(micro_strategy.get("final_score", score))

    if micro_strategy.get("skip_trade") or should_skip_trade():
        return {
            "action": "ignore",
            "final_score": score,
            "reasoning": reasons + ["Global freeze"],
            "strategy": micro_strategy
        }

    # ---------- Action mapping via standardized bands (0–100, adjustable) ----------
    # Defaults for bands (can be overridden in profile.thresholds)
    # ignore: 0..ignore_max
    # watch : (ignore_max+1)..watch_max
    # buy   : [effective_buy_min]..buy_max
    # aggr  : (buy_max+1)..agg_max
    # auto  : (agg_max+1)..100

    th = (profile.get("thresholds") or {}) if isinstance(profile, dict) else {}

    bands = {
        "ignore_max": int(th.get("ignore_max", 24)),
        "watch_max":  int(th.get("watch_max",  49)),
        "buy_max":    int(th.get("buy_max",    74)),
        "agg_max":    int(th.get("agg_max",    89)),
    }

    # Legacy “probe relief” concept preserved:
    # Shift the BUY entry boundary downward (less strict) by early/trusted relief and optional buy_relief.
    # You can also hard-set buy_min in the profile to bypass relief math entirely.
    buy_relief = float(th.get("buy_relief", 0.0))
    default_buy_min = 50.0
    effective_buy_min = float(th.get("buy_min", max(0.0, default_buy_min - (early_relief + trusted_relief + buy_relief))))

    s = float(score or 0.0)

    # Decide policy band
    if s > bands["agg_max"]:
        decision = "AUTO"
    elif s > bands["buy_max"]:
        decision = "AGGRESSIVE_BUY"
    elif s >= effective_buy_min and s <= bands["buy_max"]:
        decision = "BUY"
    elif s > bands["ignore_max"]:
        decision = "WATCH"
    else:
        decision = "IGNORE"

    # Map to engine actions (keep your strings the same)
    # Optional "probe" inside BUY band: set thresholds.probe_split in (0,1) to carve off a lower slice as "probe"
    probe_split = float(th.get("probe_split", 0.0))  # e.g., 0.4 → lower 40% of BUY band is "probe"
    if decision in ("AUTO", "AGGRESSIVE_BUY", "BUY"):
        if 0.0 < probe_split < 1.0 and decision == "BUY":
            split_point = effective_buy_min + (bands["buy_max"] - effective_buy_min) * probe_split
            action = "probe" if s < split_point else "buy"
        else:
            action = "buy"
    elif decision == "WATCH":
        action = "watch"
    else:
        action = "ignore"

    # ---------- Bandit variant attach (only for buy-side bands) ----------
    if decision in ("BUY", "AGGRESSIVE_BUY", "AUTO"):
        try:
            from strategy.contextual_bandit import get_bandit_sync
            bm = get_bandit_sync()
            variant = bm.choose_variant_for_band(
                decision,
                {**context, "final_score": s, "_profile": profile_name},
                default_id="balanced",
            )
        except Exception:
            # deterministic fallback if bandit unavailable
            variant = {
                "id":    "balanced",
                "size":  0.35 if decision == "BUY" else (0.60 if decision == "AGGRESSIVE_BUY" else 0.80),
                "ladder": 1 if decision == "BUY" else 2,
                "route": "routerA",
                "arm":   "balanced",
            }

        intent.update({
            "variant": variant.get("id", "balanced"),
            "size":    float(variant.get("size", 0.35)),
            "ladder":  int(variant.get("ladder", 1)),
            "route":   variant.get("route", "routerA"),
            "arm":     variant.get("arm", "balanced"),
        })

    # ---------- Logging, memory, verdict ----------
    log_trade_feedback({
        "token": token_name,
        "score": score,
        "action": action,
        "reasoning": reasons,
        "timestamp": str(datetime.utcnow()),
        "context": token_context,
        "strategy": micro_strategy,
        "mode": "trade",
    })
    log_score_event(token_address, score, action, reasons, mode="trade")
    log_evaluation(score, outcome="pending", mode="trade")
    record_token_signals(token_address, outcome, {"bundle": bool(bundle.get("is_bundle", False))})
    update_trait_weights(keywords, outcome)
    update_score_memory(token_address, "trade", {"score": score, "breakdown": breakdown})

    verdict = enhance_verdict(token_address, score, reasons, action)

    # Add sizing/stop hints from profile.sizing
    sizing = profile.get("sizing", {}) or {}
    verdict.update({
        "final_score": score,
        "reasoning": reasons,
        "strategy": {
            **micro_strategy,
            "sizing": sizing,
        },
        "insights": insights,
        "verification": verify_evaluation({
            "action": action,
            "final_score": score,
            "reasoning": reasons,
            "strategy": micro_strategy,
        }),
        "_profile": "trade.momentum" if "profile" not in token_context else f"trade.{token_context['profile']}",
        "_thresholds": {
            "ignore_max": bands["ignore_max"],
            "watch_max":  bands["watch_max"],
            "buy_min":    effective_buy_min,
            "buy_max":    bands["buy_max"],
            "agg_max":    bands["agg_max"],
            # legacy keys for dashboards that might read them
            "buy": bands["buy_max"],
            "probe": default_buy_min,  # legacy anchor
            "probe_effective": effective_buy_min,
            "watch_hold": bands["watch_max"],
            "exit": float((profile.get("thresholds") or {}).get("exit", 35)),
        }

    })


    # === First-class strategy cortex scoring ===
    # Build feature frame for strategy cortex
    features = {
        "chart": chart,
        "wallets": wallets,
        "txn": txn,
        "memory": context.get("memory", {}),
        "profile": profile,
        "breakdown": breakdown,
        "score": score,
        "reasons": reasons,
        "action": action,
        "micro_strategy": micro_strategy,
        "ml_price_pred": token_context.get("ml_price_pred"),
        "ml_rug_pred": token_context.get("ml_rug_pred"),
        "ml_wallet_pred": token_context.get("ml_wallet_pred"),
        "bundle": bundle,
        "trusted": trusted,
        "early_relief": early_relief,
        "trusted_relief": trusted_relief,
        "sizing": sizing,
        "bands": bands,
        "effective_buy_min": effective_buy_min,
    }

    # Call strategy cortex logic for adaptive scoring and overrides
    strategy_result = apply_strategy_logic(features, context)
    if strategy_result and isinstance(strategy_result, dict):
        score = strategy_result.get("final_score", score)
        reasons = strategy_result.get("reasoning", reasons)
        action = strategy_result.get("action", action)
        fallback_triggered = strategy_result.get("fallback_triggered", False)
        override_trace = strategy_result.get("override_trace", [])
        strategy_verdict = strategy_result.get("verdict", {})
    else:
        fallback_triggered = False
        override_trace = []
        strategy_verdict = {}

    # Unified output: always return full verdict with strategy cortex as first-class
    verdict.update({
        "final_score": score,
        "action": action,
        "reasoning": reasons,
        "strategy": {
            **micro_strategy,
            "sizing": sizing,
            "strategy_cortex": strategy_verdict,
            "fallback": fallback_triggered,
            "overrides": override_trace,
        },
        "insights": insights,
        "verification": verify_evaluation({
            "action": action,
            "final_score": score,
            "reasoning": reasons,
            "strategy": micro_strategy,
        }),
    })

    return verdict
