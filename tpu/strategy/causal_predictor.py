# /causal_predictor.py
# Phase 7 — Prediction / Causality Layer
# Tracks signal→outcome causal lift (Bayesian), logs decisions, estimates counterfactuals,
# and emits weight recommendations back to the scoring engines.

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

from utils.logger import log_event

STATE_PATH = os.environ.get("NYX_CAUSAL_STATE", "/home/ubuntu/nyx/runtime/data/causal_predictor_state.json")
LOCK = threading.RLock()

Outcome = Literal["win", "loss"]
DecisionType = Literal["enter", "skip", "exit"]
SignalName = Literal["wallet", "chart", "sentiment", "volume", "nft", "reflex", "meta"]  # align with SignalFusionEngine


# --------- Data Models ---------

@dataclass
class SignalEffectStats:
    signal: SignalName
    wins: int = 0
    losses: int = 0
    posterior_mean: float = 0.0     # posterior win-rate
    lift_vs_baseline: float = 0.0   # posterior_mean - global_baseline
    last_update: float = field(default_factory=time.time)

    def update(self, win: bool, baseline: float, alpha: float):
        a0, b0 = alpha * baseline, alpha * (1 - baseline)
        if win:
            self.wins += 1
        else:
            self.losses += 1
        post = (self.wins + a0) / (self.wins + self.losses + a0 + b0)
        self.posterior_mean = post
        self.lift_vs_baseline = post - baseline
        self.last_update = time.time()


@dataclass
class DecisionRecord:
    id: str
    ts: float
    token: str
    decision: DecisionType           # enter/skip/exit
    confidence: float                # model confidence at decision time
    fused_score: float               # fused decision score at that time
    signals: Dict[SignalName, float] # raw normalized signals 0..1
    meta: Dict[str, Any] = field(default_factory=dict)
    outcome: Optional[Outcome] = None
    pnl: Optional[float] = None
    holding_time_s: Optional[float] = None


@dataclass
class CausalState:
    global_baseline: float = 0.50     # updated on the fly
    alpha: float = 3.0                # Bayesian prior strength
    effects: Dict[SignalName, SignalEffectStats] = field(default_factory=dict)
    decisions: Dict[str, DecisionRecord] = field(default_factory=dict)
    history: List[str] = field(default_factory=list)           # last N decision ids
    max_history: int = 5000
    last_report: float = 0.0
    version: int = 1


# --------- Core Engine ---------

class CausalPredictor:
    def __init__(self, path: str = STATE_PATH, baseline: float = 0.50, alpha: float = 3.0):
        self.path = path
        self.state: CausalState = CausalState(global_baseline=baseline, alpha=alpha)
        self._load()

    # ---- Public API ----

    def log_decision(
        self,
        *,
        decision_id: str,
        token: str,
        decision: DecisionType,
        confidence: float,
        fused_score: float,
        signals: Dict[SignalName, float],
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Call at decision time (e.g., right before you take a trade or skip it).
        """
        now = time.time()
        rec = DecisionRecord(
            id=decision_id,
            ts=now,
            token=token,
            decision=decision,
            confidence=float(confidence),
            fused_score=float(fused_score),
            signals={k: float(self._clip(v)) for k, v in signals.items()},
            meta=meta or {},
        )
        with LOCK:
            self.state.decisions[decision_id] = rec
            self.state.history.append(decision_id)
            if len(self.state.history) > self.state.max_history:
                # prune oldest
                for old_id in self.state.history[:-self.state.max_history]:
                    self.state.decisions.pop(old_id, None)
                self.state.history = self.state.history[-self.state.max_history:]
        self._save()

    def record_outcome(
        self,
        *,
        decision_id: str,
        pnl: float,
        holding_time_s: float,
    ) -> None:
        """
        Call when the trade (or skip decision) resolves.
        """
        with LOCK:
            rec = self.state.decisions.get(decision_id)
            if not rec:
                return
            outcome: Outcome = "win" if pnl > 0 else "loss"
            rec.outcome = outcome
            rec.pnl = pnl
            rec.holding_time_s = holding_time_s

            # Update global baseline (rolling)
            # Use EWMA to smooth
            self._update_global_baseline(outcome)

            # Update signal effects
            for sig, _val in rec.signals.items():
                eff = self.state.effects.get(sig)
                if not eff:
                    eff = SignalEffectStats(signal=sig)
                    self.state.effects[sig] = eff
                eff.update(win=(outcome == "win"), baseline=self.state.global_baseline, alpha=self.state.alpha)

        self._save()

    def counterfactual_score(self, decision_id: str) -> Optional[Dict[str, float]]:
        """
        Rough estimate of what would have happened if we had inverted the decision (enter vs skip).
        This is heuristic — uses fused score and current lifts to estimate "would-be" probability.
        """
        with LOCK:
            rec = self.state.decisions.get(decision_id)
            if not rec:
                return None

            # estimate using weighted sum of current effects
            lift = 0.0
            for s, v in rec.signals.items():
                eff = self.state.effects.get(s)
                if not eff:
                    continue
                # emphasize signals with high presence in this decision
                lift += v * eff.lift_vs_baseline

            p_enter_win = self._clip(self.state.global_baseline + lift)
            p_skip_win = self._clip(self.state.global_baseline - lift * 0.7)

            return {
                "p_enter_win": p_enter_win,
                "p_skip_win": p_skip_win,
                "delta": p_enter_win - p_skip_win
            }

    def recommended_signal_weights(self, floor: float = 0.05, ceil: float = 0.40) -> Dict[SignalName, float]:
        """
        Suggest weights for signal_fusion_engine based on causal lift.
        Normalizes to sum to 1.0 after clamping.
        """
        with LOCK:
            lifts = {s: eff.lift_vs_baseline for s, eff in self.state.effects.items()}
        # clamp & shift to positive space
        min_lift = min(lifts.values()) if lifts else 0.0
        shifted = {s: (v - min_lift + 1e-6) for s, v in lifts.items()} if lifts else {}
        total = sum(shifted.values()) or 1.0
        raw = {s: v / total for s, v in shifted.items()}
        # clamp
        clamped = {s: self._clip(w, floor, ceil) for s, w in raw.items()}
        # re-normalize
        ssum = sum(clamped.values()) or 1.0
        return {s: w / ssum for s, w in clamped.items()}

    def report(self, top_n: int = 6) -> str:
        with LOCK:
            effects_sorted = sorted(
                self.state.effects.values(),
                key=lambda e: e.lift_vs_baseline,
                reverse=True
            )
            lines = [
                "🧠 Causal Predictor Report",
                f"- Global baseline: {self.state.global_baseline:.3f}",
                f"- Decisions tracked: {len(self.state.decisions)}",
                f"- Effects (top {top_n} by lift):"
            ]
            for e in effects_sorted[:top_n]:
                lines.append(
                    f"  • {e.signal}: lift={e.lift_vs_baseline:+.4f} "
                    f"(post={e.posterior_mean:.3f} | w={e.wins} l={e.losses})"
                )
            return "\n".join(lines)

    # ---- Internals ----

    def _update_global_baseline(self, outcome: Outcome, alpha: float = 0.02):
        # EWMA drift of baseline
        y = 1.0 if outcome == "win" else 0.0
        self.state.global_baseline = (1 - alpha) * self.state.global_baseline + alpha * y

    def _save(self):
        with LOCK:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                # Custom JSON to dump dataclasses
                data = {
                    "global_baseline": self.state.global_baseline,
                    "alpha": self.state.alpha,
                    "effects": {k: asdict(v) for k, v in self.state.effects.items()},
                    "decisions": {k: asdict(v) for k, v in self.state.decisions.items()},
                    "history": self.state.history,
                    "max_history": self.state.max_history,
                    "last_report": self.state.last_report,
                    "version": self.state.version,
                }
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            st = CausalState()
            st.global_baseline = data.get("global_baseline", st.global_baseline)
            st.alpha = data.get("alpha", st.alpha)
            st.effects = {
                k: SignalEffectStats(**v) for k, v in data.get("effects", {}).items()
            }
            st.decisions = {
                k: DecisionRecord(**v) for k, v in data.get("decisions", {}).items()
            }
            st.history = data.get("history", [])
            st.max_history = data.get("max_history", st.max_history)
            st.last_report = data.get("last_report", 0.0)
            st.version = data.get("version", st.version)
            self.state = st
        except Exception as e:
            log_event(f"[CausalPredictor] Failed to load state: {e}")

    @staticmethod
    def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return hi if x > hi else lo if x < lo else x


# --------- Singleton Helpers ---------

_ENGINE: Optional[CausalPredictor] = None


def causal_predictor() -> CausalPredictor:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = CausalPredictor()
    return _ENGINE
