import json
import os
import random
from datetime import datetime

from memory.token_memory_index import update_snipe_score_memory
from memory.token_outcome_memory import get_all_past_trades
from special.ai_self_tuner import tune_strategy
from strategy.ai_reweight_engine import apply_reasoning_weights
from strategy.contextual_weight_tracer import log_weight_trace
from strategy.evaluate_replay import simulate_trade_decision
from strategy.outcome_reflector import reflect_on_outcome
from strategy.reinforcement_tracker import log_trade_feedback
from strategy.self_verifier import verify_evaluation
from utils.logger import log_event

REPLAY_FILE = "/home/ubuntu/nyx/runtime/memory/snapshots/past_snipes.json"
REPLAY_LIMIT = 100  # Number of past trades to simulate


def load_past_snipes():
    if not os.path.exists(REPLAY_FILE):
        return []
    try:
        with open(REPLAY_FILE, "r") as f:
            return json.load(f)
    except:
        return []


def replay_token(token_context: dict) -> dict:
    token_name = token_context.get("token_name")
    token_address = token_context.get("token_address")
    metadata = token_context.get("metadata", {})
    score = 0
    breakdown = {}
    reasons = []
    insights = {}

    # === Simulated decision scoring ===
    if token_context.get("lp_locked"):
        score += 5
        breakdown["lp"] = 5
        reasons.append("LP locked")
    else:
        score -= 5
        breakdown["lp"] = -5
        reasons.append("LP unlocked")

    if token_context.get("wallet_overlap", 0) > 0:
        overlap = token_context["wallet_overlap"]
        score += overlap
        breakdown["wallet_overlap"] = overlap
        reasons.append("Sniper overlap")

    if token_context.get("early_window"):
        score += 3
        breakdown["early_window"] = 3
        reasons.append("Early buy")

    if token_context.get("chart_score", 0) > 0:
        cs = token_context["chart_score"]
        score += cs
        breakdown["chart"] = cs
        reasons.append("Chart boost")

    # === Apply AI reasoning and tune ===
    score = apply_reasoning_weights(score, reasons, token_address)
    score = max(0, min(round(score, 2), 100))
    action = "snipe" if score >= 20 else "watch" if score >= 12 else "ignore"

    tuned = tune_strategy({
        **token_context,
        "mode": "replay",
        "final_score": score,
        "action": action,
        "insights": insights
    })

    evaluation = {
        "action": action,
        "final_score": score,
        "reasoning": reasons,
        "strategy": tuned,
        "insights": insights,
        "verification": verify_evaluation({
            "action": action,
            "final_score": score,
            "reasoning": reasons,
            "insights": insights,
            "strategy": tuned
        })
    }

    # === Log trace + feedback ===
    log_weight_trace(token_address, score, action, breakdown, reasons, mode="replay")

    log_trade_feedback({
        "token": token_name,
        "score": score,
        "action": action,
        "reasoning": reasons,
        "timestamp": str(datetime.utcnow()),
        "context": token_context,
        "strategy": tuned,
        "mode": "replay"
    })

    update_snipe_score_memory(token_address, {
        "score": score,
        "breakdown": breakdown,
        "creator": metadata.get("creator", "")
    })

    return evaluation


def run_alpha_replay(limit=REPLAY_LIMIT):
    """
    Hybrid replay loop: if no synthetic trades found, fall back to memory replay.
    """
    past = load_past_snipes()[-limit:]
    if not past:
        return run_memory_based_replay(limit)

    results = []
    for token in past:
        try:
            result = replay_token(token)
            results.append({
                "token": token.get("token_name"),
                "address": token.get("token_address"),
                "result": result
            })
        except Exception as e:
            results.append({
                "token": token.get("token_name"),
                "error": str(e)
            })
    return results


def run_memory_based_replay(limit=REPLAY_LIMIT):
    """
    Alternative mode: simulate full decision pipeline using past outcome memory only.
    """
    trades = get_all_past_trades()
    random.shuffle(trades)

    if not trades:
        log_event("⚠️ AlphaReplay: No past trades to simulate.")
        return []

    count = 0
    results = []

    for trade in trades:
        token = trade.get("token_address")
        outcome = trade.get("outcome")
        if not token or not outcome:
            continue

        sim = simulate_trade_decision(token)
        if not sim:
            continue

        reflect_on_outcome(token, outcome)
        results.append({
            "token": token,
            "simulated_score": sim.get("score", 0),
            "outcome": outcome
        })

        count += 1
        if count >= limit:
            break

    log_event(f"🔁 AlphaReplay: Simulated {count} memory-based trade decisions.")
    return results
