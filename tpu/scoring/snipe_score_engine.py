import logging
import time
from datetime import datetime

from defense.ai_sniper_intuition import apply_sniper_intuition
from defense.bundle_launch_detector import detect_bundle_launch
from defense.honeypot_similarity_scanner import get_similarity_penalty
from inputs.social.alpha_reactor.alpha_router import AlphaRouter
from inputs.social.influencer_scanner import InfluencerSignalTracker
from inputs.social.sentiment_fusion import SentimentFusion

# === Fusion Modules ===
from inputs.wallet.wallet_influence import WalletInfluenceTracker
from librarian.data_librarian import librarian
from memory.token_memory_index import record_sniper_pattern, update_score_memory
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
from utils.wallet_helpers import count_unique_buyers, get_wallet_signal_bonus

wallet_influence = WalletInfluenceTracker()
influencer_tracker = InfluencerSignalTracker()
sentiment_fusion = SentimentFusion()

def _get(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d

def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x

def _to_pct(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _clamp01((float(x) - lo) / (hi - lo))

async def evaluate_snipe(token_context: dict) -> dict:
    """
    Profile-driven SNIPE scorer.
    Profiles (examples): t0_liquidity, anti_mev, social_react, breakout
    - risk gates (honeypot, tax, optional LP/MEV constraints)
    - bucket blend (t0_flow / wallet / liquidity / social / chart) -> 0..100
    - dynamic adjustments (ultra-early, trusted-source)
    - action mapping via profile thresholds (snipe/probe/watch/ignore)
    """
    token_name    = token_context.get("token_name")
    token_address = token_context.get("token_address")
    metadata      = token_context.get("metadata", {}) or {}
    creator       = metadata.get("creator", "")

    logging.info(f"[SnipeEngine] ⚡ Evaluating snipe: {token_name} | {token_address}")

    # ---------------- Risk gates (early exits) ----------------
    if is_blacklisted_token(token_address):
        log_event(f"⛔ Blacklisted token blocked: {token_address}")
        return {"action": "ignore", "final_score": 0, "reasoning": ["blacklisted"], "strategy": {}}

    profile_name = token_context.get("profile") or "t0_liquidity"
    profile  = get_profile("snipe", profile_name) or {}
    defaults = get_defaults() or {}

    reasons: list[str] = []
    breakdown: dict[str, float] = {}
    insights: dict = {}
    outcome = "pending"

    # Honeypot similarity
    similarity_penalty = get_similarity_penalty(token_address)
    max_sim_pen = int(profile.get("gates", {}).get("max_similarity_penalty", 15))
    if similarity_penalty >= max_sim_pen:
        return {"action": "ignore", "final_score": 0, "reasoning": ["honeypot_similarity"], "strategy": {}}

    # LP lock requirement (most snipe profiles do NOT require lock at t0)
    lp_lock_required = bool(profile.get("gates", {}).get("lp_locked_required", defaults.get("lp_locked_required", False)))
    lp_status = get_lp_lock_status(token_address)  # "locked" | "unlocked" | "unknown"
    if lp_lock_required and lp_status != "locked":
        return {"action": "ignore", "final_score": 0, "reasoning": ["lp_not_locked"], "strategy": {}}

    # Taxes (bps)
    try:
        fees = get_token_fees(token_address) or {}
    except Exception as e:
        logging.warning(f"[SnipeScore] Fee fetch failed: {e}")
        fees = {}
    total_tax_bps = int(fees.get("total_tax_bps") or fees.get("total_tax") or 0)
    max_tax_bps = int(profile.get("gates", {}).get("tax_max_bps", defaults.get("tax_max_bps", 1200)))
    if total_tax_bps > max_tax_bps:
        return {"action": "ignore", "final_score": 0, "reasoning": [f"tax_too_high:{total_tax_bps}"], "strategy": {}}

    # Optional market gates (if present in ctx)
    spread_pct   = _get(token_context, "market", "spread_pct", default=None)
    slippage_bps = _get(token_context, "market", "est_slippage_bps", default=None)
    depth_sol    = _get(token_context, "market", "depth_sol", default=None)

    max_spread = float(profile.get("gates", {}).get("max_spread_pct", defaults.get("max_spread_pct", 3.5)))
    max_slip   = int(profile.get("gates", {}).get("max_slippage_bps", defaults.get("max_slippage_bps", 350)))
    min_depth  = float(profile.get("gates", {}).get("min_depth_sol", defaults.get("min_depth_sol", 10)))

    if spread_pct is not None and spread_pct > max_spread:
        return {"action": "ignore", "final_score": 0, "reasoning": [f"spread_too_wide:{spread_pct}%"], "strategy": {}}
    if slippage_bps is not None and slippage_bps > max_slip:
        return {"action": "ignore", "final_score": 0, "reasoning": [f"slippage_too_high:{slippage_bps}bps"], "strategy": {}}
    if depth_sol is not None and depth_sol < min_depth:
        return {"action": "ignore", "final_score": 0, "reasoning": [f"depth_too_low:{depth_sol}"], "strategy": {}}

    # Bundle penalty (don’t hard fail—just penalize later)
    try:
        bundle = detect_bundle_launch(token_address) or {}
    except Exception as e:
        logging.warning(f"[SnipeScore] Bundle check failed: {e}")
        bundle = {}

    # ---------------- Context build (librarian) ----------------
    context = await librarian.build_context(token_context)
    txn     = context.get("txn", {}) or {}
    wallets = context.get("wallets", {}) or {}
    chart   = context.get("chart", {}) or {}
    insights.update(context)

    # ---------------- Bucket scores (0..10 each) ----------------
    # T0_FLOW: launch/lp/velocity/pressure
    t0_points = 0.0
    if txn.get("lp_added"):
        t0_points += 4.0
    sniper_pressure = float(txn.get("sniper_pressure", 0.0) or 0.0)  # assume 0..10-ish
    t0_points += min(4.0, sniper_pressure * 0.6)
    first_min_buyers = int(_get(txn, "buyers_1m", default=0))
    if first_min_buyers >= 50:
        t0_points += 2.0
    elif first_min_buyers >= 20:
        t0_points += 1.0
    t0_points = max(0.0, min(10.0, t0_points))
    breakdown["t0_flow"] = round(t0_points, 2)

    # WALLET: whales, overlap snipers, avg reputation
    wallet_pts = 0.0
    if wallets.get("whales_present"):
        wallet_pts += 3.0
    wallet_pts += float(wallets.get("overlap_snipers", 0.0)) * 0.5  # overlap count scaled
    wallet_pts += float(wallets.get("avg_reputation", 0.0)) * 0.3   # normalized if 0..10
    wallet_pts = max(0.0, min(10.0, wallet_pts))
    breakdown["wallet"] = round(wallet_pts, 2)

    # LIQUIDITY: depth, LP lock status, spread (inverse), fees (inverse)
    liq_pts = 0.0
    if lp_status == "locked":
        liq_pts += 2.0
    if depth_sol is not None:
        liq_pts += min(4.0, _to_pct(depth_sol, 5.0, 50.0) * 4.0)  # 5→0, 50→~4
    if spread_pct is not None:
        liq_pts += (1.0 - _clamp01(spread_pct / max_spread)) * 2.0
    # taxes: 0..max_tax_bps → 2..0 points
    liq_pts += (1.0 - _to_pct(total_tax_bps, 0.0, float(max_tax_bps))) * 2.0
    liq_pts = max(0.0, min(10.0, liq_pts))
    breakdown["liquidity"] = round(liq_pts, 2)

    # SOCIAL: influencer + sentiment fusion (fast-react)
    try:
        infl = float(influencer_tracker.get_signal_boost(token_name) or 0.0)
    except Exception:
        infl = 0.0
    try:
        sent = float(sentiment_fusion.compute_sentiment_score(token_name) or 0.0)
    except Exception:
        sent = 0.0
    social_pts = max(0.0, min(10.0, infl * 0.7 + sent * 0.3))
    breakdown["social"] = round(social_pts, 2)

    # CHART: very light at t0; if you already compute chart_score (0..20), map to 0..10
    chart_raw = float(chart.get("chart_score", 0.0))
    chart_pts = max(0.0, min(10.0, chart_raw / 2.0))
    breakdown["chart"] = round(chart_pts, 2)

    # ---------------- Weighted blend (0..100) ----------------
    w = profile.get("weights", {}) or {}
    w_t0   = float(w.get("t0_flow", 0.0))
    w_wal  = float(w.get("wallet", 0.0))
    w_liq  = float(w.get("liquidity", 0.0))
    w_soc  = float(w.get("social", 0.0))
    w_chr  = float(w.get("chart", 0.0))
    w_sum = max(1e-6, w_t0 + w_wal + w_liq + w_soc + w_chr)

    blended = (
        (t0_points * w_t0) +
        (wallet_pts * w_wal) +
        (liq_pts * w_liq) +
        (social_pts * w_soc) +
        (chart_pts * w_chr)
    ) * (10.0 / w_sum)  # 10 * weighted_avg -> 0..100

    score = blended
    reasons.append(f"buckets t0={breakdown['t0_flow']} wallet={breakdown['wallet']} liq={breakdown['liquidity']} social={breakdown['social']} chart={breakdown['chart']}")

    # ---------------- Your existing extras (soft caps) ----------------
    # Wallet bonus (legacy helper)
    try:
        from utils.wallet_helpers import get_wallet_signal_bonus
        wb = float(get_wallet_signal_bonus(token_address) or 0.0)
    except Exception:
        wb = 0.0
    if wb:
        add = max(-5.0, min(5.0, wb))
        score += add
        breakdown["wallet_signal"] = add

    # Buyers surge (fast hysteresis)
    buyers = count_unique_buyers(token_context)
    if buyers > 30:
        score += 2.0
        breakdown["buyers"] = 2.0

    # Intuition & Traits
    try:
        keywords = f"{metadata.get('name','')} {metadata.get('symbol','')} {metadata.get('creator','')}".split()
    except Exception:
        keywords = []
    try:
        sniper_boost, sniper_reason = apply_sniper_intuition(token_address, keywords)
    except Exception:
        sniper_boost, sniper_reason = 0.0, None
    if sniper_boost:
        add = max(-6.0, min(6.0, float(sniper_boost)))
        score += add
        breakdown["intuition"] = add
        if sniper_reason:
            reasons.append(f"AI boost: {sniper_reason} (+{add:.1f})")

    trait_boost, trait_reasons = apply_trait_scores(keywords)
    if trait_boost:
        add = max(-4.0, min(4.0, float(trait_boost)))
        score += add
        breakdown["traits"] = add
        reasons.extend(trait_reasons)

    # Fusion snapshot hook (unchanged plumbing)
    snapshot = signal_fusion_engine().ingest_signals(
        token=token_address,
        wallet_score=float(wallets.get("avg_reputation", 0.0)),
        chart_score=chart_raw,
        sentiment_score=social_pts,
        volume_score=sniper_pressure / 10.0,
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

    # ---------------- Dynamic adjustments ----------------
    # Ultra-early relief
    age_minutes = get_token_age_minutes(token_context)
    dyn = profile.get("dynamic", {}) or {}
    early_cfg = dyn.get("ultra_early_window", defaults.get("ultra_early_window", {}))
    early_sec = float(early_cfg.get("seconds", 90))
    probe_relief = float(early_cfg.get("probe_relief", 0.0))
    early_relief = 0.0
    if isinstance(age_minutes, (int, float)) and age_minutes * 60.0 <= early_sec:
        early_relief = probe_relief
        if early_relief:
            breakdown["early_relief"] = early_relief
            reasons.append(f"early_window_relief {early_relief:+.1f}")

    # Trusted signal relief
    trusted_relief = 0.0
    if bool(token_context.get("trusted_source_ping") or _get(context, "social", "trusted_source", default=False)):
        trusted_relief = float(dyn.get("trusted_source_relief", defaults.get("trusted_source_relief", 0.0)))
        if trusted_relief:
            breakdown["trusted_relief"] = trusted_relief
            reasons.append(f"trusted_source_relief {trusted_relief:+.1f}")

    # Bundle penalty (apply to score)
    bundle_pen = float(dyn.get("bundle_penalty", defaults.get("bundle_penalty", 0.0))) if bundle.get("is_bundle") else 0.0
    if bundle_pen:
        score = max(0.0, score - bundle_pen)
        breakdown["bundle_penalty"] = -bundle_pen
        reasons.append(f"bundle_penalty {-bundle_pen:.1f}")

    # ---------------- Self/memory reweights (clamp to 0..100) ----------------
    score = apply_self_adjustments(score, token_address)
    score = apply_reasoning_weights(score, reasons, token_address)
    score = apply_memory_adjustment(score, reasons, {**token_context, "wallets": wallets, "txn": txn})
    score = max(0.0, min(round(score, 2), 100.0))

    # ---------------- Micro-strategy / global freeze ----------------
    micro_strategy = tune_strategy_context({
        "token_name": token_name,
        "meta_score": score,
        "social_score": float(wallets.get("avg_reputation", 0.0)),
        "chart_trend": chart.get("trend", "unknown"),
        "wallet_count": int(wallets.get("wallet_count", 0) or 0),
        "bundle_detected": bool(bundle.get("is_bundle", False)),
        "volatility": chart.get("volatility", 0.0),
    })
    score = float(micro_strategy.get("final_score", score))

    if micro_strategy.get("skip_trade") or should_skip_trade():
        return {"action": "ignore", "final_score": score, "reasoning": reasons + ["Global freeze"], "strategy": micro_strategy}


    # ---------------- Action mapping via standardized bands (0–100, adjustable) ----------------

    # Bands (defaults) — can be overridden per profile in `profile["thresholds"]`:
    #   ignore: 0..ignore_max
    #   watch : (ignore_max+1)..watch_max
    #   BUY   : [effective_buy_min]..buy_max
    #   AGG   : (buy_max+1)..agg_max
    #   AUTO  : (agg_max+1)..100
    th = (profile.get("thresholds") or {}) if isinstance(profile, dict) else {}

    bands = {
        "ignore_max": int(th.get("ignore_max", 24)),
        "watch_max":  int(th.get("watch_max",  49)),
        "buy_max":    int(th.get("buy_max",    74)),
        "agg_max":    int(th.get("agg_max",    89)),
    }

    # Preserve early/trusted relief by easing the BUY entry floor.
    # You can hard-set buy_min in the profile to bypass relief math entirely.
    buy_relief = float(th.get("buy_relief", 0.0))
    default_buy_min = 50.0
    effective_buy_min = float(
        th.get("buy_min", max(0.0, default_buy_min - (early_relief + trusted_relief + buy_relief)))
    )

    s = float(score or 0.0)

    # Determine decision band
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

    # Map policy bands to snipe engine actions.
    # Optional: keep a "probe" slice inside the BUY band using thresholds.probe_split in (0,1).
    probe_split = float(th.get("probe_split", 0.0))  # e.g., 0.35 => lower 35% of BUY band → "probe"

    if decision in ("AUTO", "AGGRESSIVE_BUY", "BUY"):
        if decision == "BUY" and 0.0 < probe_split < 1.0:
            split_point = effective_buy_min + (bands["buy_max"] - effective_buy_min) * probe_split
            action = "probe" if s < split_point else "snipe"
        else:
            action = "snipe"
    elif decision == "WATCH":
        action = "watch"
    else:
        action = "ignore"

    # Requires `intent` dict to exist (with at least type/mint set).
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

        # Attach to intent (no change to action naming)
        intent.update({
            "variant": variant.get("id", "balanced"),
            "size":    float(variant.get("size", 0.35)),
            "ladder":  int(variant.get("ladder", 1)),
            "route":   variant.get("route", "routerA"),
            "arm":     variant.get("arm", "balanced"),
        })

    # ---------------- Confidence reweight (kept) ----------------
    score = reweight_confidence(score, reasons, mode="snipe")

    # ---------------- Predictive meta (kept) ----------------
    prediction = predict_outcome_from_signals({
        "lp_status": lp_status,
        "creator": creator,
        "sniper_overlap": wallets.get("overlap_snipers", 0) > 0,
        "whales": wallets.get("whales_present", False),
        "bundle": bundle.get("is_bundle", False),
    })

    # ---------------- Reinforcement & memory (kept) ----------------
    log_trade_feedback({
        "token": token_name,
        "score": score,
        "action": action,
        "reasoning": reasons,
        "timestamp": str(datetime.utcnow()),
        "context": token_context,
        "strategy": micro_strategy,
        "mode": "snipe",
    })
    log_score_event(token_address, score, action, reasons, mode="snipe")
    log_evaluation(score, outcome="pending", mode="snipe")

    record_token_signals(token_address, outcome, {
        "lp_status": lp_status,
        "creator": creator,
        "sniper_overlap": wallets.get("overlap_snipers", 0) > 0,
        "whales": wallets.get("whales_present", False),
        "bundle": bundle.get("is_bundle", False),
    })

    update_trait_weights(keywords, outcome)
    record_sniper_pattern(token_address, {
        "score": score,
        "breakdown": breakdown,
        "age": age_minutes,
        "creator": creator,
        "lp_status": lp_status,
    })
    update_score_memory(token_address, "snipe", {"score": score, "breakdown": breakdown})

    verdict = enhance_verdict(token_address, score, reasons, action)
    verdict.update({
        "final_score": score,
        "reasoning": reasons,
        "strategy": {
            **micro_strategy,
            "sizing": profile.get("sizing", {}),  # probe size / max size / trail model per profile
        },
        "insights": insights,
        "_profile": f"snipe.{profile_name}",
        "_thresholds": {
            "ignore_max": bands["ignore_max"],
            "watch_max":  bands["watch_max"],
            "buy_min":    effective_buy_min,
            "buy_max":    bands["buy_max"],
            "agg_max":    bands["agg_max"],
            # legacy names for dashboards that may still expect them:
            "watch": bands["watch_max"],
            "probe": default_buy_min,
            "probe_effective": effective_buy_min,
        }
    })
    return verdict

def adjust_score(token: str, base_score: float) -> float:
    reflex = adaptive_controller().get_reflex_for_token(token)
    return base_score * reflex.get("buy_boost", 1.0)
