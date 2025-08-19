# /meta_strategy_engine.py
# Phase 7 — Strategic Memory Fusion
# Merges trade outcomes, style evolution, and reflex data into a long-term adaptive strategy layer.

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Literal, Optional

from core.llm.style_evolution import style_evolution
from special.adaptive_trade_controller import adaptive_controller
from utils.logger import log_event

STRATEGY_STATE_PATH = os.environ.get("NYX_STRATEGY_STATE", "data/meta_strategy_state.json")
StrategyArchetype = Literal["sniper", "swing", "scalper", "defensive", "balanced"]


@dataclass
class StrategyStats:
    wins: int = 0
    losses: int = 0
    avg_pnl: float = 0.0
    avg_holding_time: float = 0.0
    last_update: float = time.time()


@dataclass
class MetaStrategyState:
    current_strategy: StrategyArchetype = "balanced"
    confidence: float = 0.5  # 0–1
    last_rotation: float = time.time()
    streak_type: Literal["neutral", "win", "loss"] = "neutral"
    streak_count: int = 0
    performance: Dict[StrategyArchetype, StrategyStats] = field(default_factory=lambda: {
        "sniper": StrategyStats(),
        "swing": StrategyStats(),
        "scalper": StrategyStats(),
        "defensive": StrategyStats(),
        "balanced": StrategyStats(),
    })
    last_report: str = ""
    version: int = 1


def _clip(x: float, lo=0.0, hi=1.0) -> float:
    return max(lo, min(hi, x))


class MetaStrategyEngine:
    """
    Decides which strategy archetype Nyx should currently favor.
    Uses trade results, reflex boosts, and style cues to evolve strategy.
    """

    def __init__(self, path: str = STRATEGY_STATE_PATH):
        self.path = path
        self.state: MetaStrategyState = self._load_or_default()
        self.rotation_cooldown = 300  # minimum 5 min between strategy shifts
        self.loss_streak_limit = 3
        self.win_streak_boost = 3

    # ------------------ Public API ------------------

    def record_trade_result(self, archetype: StrategyArchetype, pnl: float, holding_time_s: float):
        """
        Update performance and consider strategy shifts.
        """
        s = self.state.performance[archetype]
        if pnl > 0:
            s.wins += 1
            self._update_streak("win")
        else:
            s.losses += 1
            self._update_streak("loss")

        # Update averages
        total_trades = max(s.wins + s.losses, 1)
        s.avg_pnl = ((s.avg_pnl * (total_trades - 1)) + pnl) / total_trades
        s.avg_holding_time = ((s.avg_holding_time * (total_trades - 1)) + holding_time_s) / total_trades
        s.last_update = time.time()

        # Influence style evolution
        style_evolution().record_trade_feedback(
            pnl=pnl,
            confidence=0.5 + 0.5 * self.state.confidence,
            was_early_exit=False,
            was_rug=False,
            latency_ms=holding_time_s * 1000
        )

        # Check for rotation
        self._maybe_rotate()
        self.save()

    def current_strategy(self) -> StrategyArchetype:
        return self.state.current_strategy

    def confidence_level(self) -> float:
        return self.state.confidence

    def get_performance_snapshot(self) -> Dict[str, Dict]:
        return {k: asdict(v) for k, v in self.state.performance.items()}

    def report(self) -> str:
        lines = [
            f"📊 Meta Strategy Report ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())})",
            f"Current Strategy: **{self.state.current_strategy.upper()}**",
            f"Confidence: {self.state.confidence:.2f}",
            f"Streak: {self.state.streak_type} ({self.state.streak_count})",
        ]
        for strat, stats in self.state.performance.items():
            lines.append(
                f"- {strat}: W {stats.wins} / L {stats.losses}, avg_pnl={stats.avg_pnl:.4f}, hold={stats.avg_holding_time:.1f}s"
            )
        self.state.last_report = "\n".join(lines)
        return self.state.last_report

    # ------------------ Internals ------------------

    def _update_streak(self, outcome: Literal["win", "loss"]):
        if outcome == self.state.streak_type:
            self.state.streak_count += 1
        else:
            self.state.streak_type = outcome
            self.state.streak_count = 1

        # Adjust confidence
        if outcome == "win":
            self.state.confidence = _clip(self.state.confidence + 0.05)
        else:
            self.state.confidence = _clip(self.state.confidence - 0.05)

    def _maybe_rotate(self):
        now = time.time()
        if now - self.state.last_rotation < self.rotation_cooldown:
            return

        # Rotate if loss streak is too high
        if self.state.streak_type == "loss" and self.state.streak_count >= self.loss_streak_limit:
            new_strat = self._choose_defensive()
            self._rotate(new_strat, reason=f"Loss streak {self.state.streak_count}")

        # Boost aggressive strategies if winning
        elif self.state.streak_type == "win" and self.state.streak_count >= self.win_streak_boost:
            new_strat = self._choose_aggressive()
            self._rotate(new_strat, reason=f"Win streak {self.state.streak_count}")

    def _rotate(self, new_strat: StrategyArchetype, reason: str):
        if new_strat == self.state.current_strategy:
            return
        old = self.state.current_strategy
        self.state.current_strategy = new_strat
        self.state.last_rotation = time.time()
        log_event(f"[MetaStrategy] Switched from {old} -> {new_strat} due to {reason}")
        style_evolution().record_message_feedback(
            engagement=0.5,
            sentiment=0.0,
            length_tokens=5,
            context=f"strategy_rotation:{reason}"
        )

    def _choose_defensive(self) -> StrategyArchetype:
        return "defensive" if self.state.current_strategy != "defensive" else "balanced"

    def _choose_aggressive(self) -> StrategyArchetype:
        return "sniper" if self.state.current_strategy != "sniper" else "scalper"

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self.state), f, indent=2)
        os.replace(tmp, self.path)

    def _load_or_default(self) -> MetaStrategyState:
        if not os.path.exists(self.path):
            return MetaStrategyState()
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            base = MetaStrategyState()
            for k, v in data.items():
                if hasattr(base, k):
                    if k == "performance":
                        # load nested stats
                        base.performance = {kk: StrategyStats(**vv) for kk, vv in v.items()}
                    else:
                        setattr(base, k, v)
            return base
        except Exception:
            return MetaStrategyState()


# ------------------ Singleton Helpers ------------------

_ENGINE: Optional[MetaStrategyEngine] = None


def meta_strategy_engine() -> MetaStrategyEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = MetaStrategyEngine()
    return _ENGINE
