# /priority_scheduler.py
# Phase 4 — Dynamic Prioritization Engine
# No placeholders. Drop-in ready.

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Literal, Optional

Profile = Literal["LAUNCH_FRENZY", "CHOP_ZONE", "BALANCED", "RECOVERY", "SAFE_MODE"]


@dataclass
class MarketState:
    tps: float = 0.0                 # tx/sec from Firehose
    volatility: float = 0.0          # 0–1 normalized (your chart cortex can feed this)
    avg_spread: float = 0.0          # 0–1 normalized (higher == worse)
    liquidity_pressure: float = 0.0  # 0–1 (0 calm, 1 very tight / illiquid)
    ts: float = 0.0


@dataclass
class InternalState:
    loss_streak: int = 0
    win_streak: int = 0
    cpu: float = 0.0           # 0–1
    mem_pressure: float = 0.0  # 0–1
    error_rate: float = 0.0    # 0–1 (normalized)
    backlog_tasks: int = 0
    queue_lag_s: float = 0.0
    ts: float = 0.0


@dataclass
class ProfileSnapshot:
    ts: float
    profile: Profile
    module_throttles: Dict[str, float]
    market: MarketState
    internal: InternalState
    market_heat: float
    system_stress: float
    notes: str = ""


def _ewma(prev: float, new: float, alpha: float) -> float:
    return (alpha * new) + (1 - alpha) * prev


class PriorityScheduler:
    """
    Produces:
      - profile (Launch Frenzy / Chop Zone / Balanced / Recovery / Safe Mode)
      - module throttle table (0.0–1.0 execution frequency multipliers)
    """

    DEFAULT_MODULES: Dict[str, float] = {
        # base weights used to scale multipliers when needed (1.0 = neutral importance)
        "firehose_listener": 1.0,
        "wallet_scanner": 1.0,
        "trend_loop": 1.0,
        "telegram_learning": 0.7,
        "x_alpha": 0.8,
        "strategy_auditor": 0.6,
        "self_tuner": 0.7,
        "experiment_runner": 0.5,
        "crash_guardian": 1.0,
        "priority_scheduler": 1.0,
    }

    PROFILE_TABLE: Dict[Profile, Dict[str, float]] = {
        # Multipliers BEFORE stress attenuation
        "LAUNCH_FRENZY": {
            "firehose_listener": 1.0,
            "wallet_scanner": 1.0,
            "trend_loop": 0.8,
            "telegram_learning": 0.3,
            "x_alpha": 0.7,
            "strategy_auditor": 0.2,
            "self_tuner": 0.4,
            "experiment_runner": 0.2,
            "crash_guardian": 1.0,
            "priority_scheduler": 1.0,
        },
        "CHOP_ZONE": {
            "firehose_listener": 0.6,
            "wallet_scanner": 0.6,
            "trend_loop": 1.0,
            "telegram_learning": 0.8,
            "x_alpha": 0.9,
            "strategy_auditor": 0.8,
            "self_tuner": 0.9,
            "experiment_runner": 0.7,
            "crash_guardian": 1.0,
            "priority_scheduler": 1.0,
        },
        "BALANCED": {
            "firehose_listener": 0.9,
            "wallet_scanner": 0.9,
            "trend_loop": 0.9,
            "telegram_learning": 0.6,
            "x_alpha": 0.8,
            "strategy_auditor": 0.6,
            "self_tuner": 0.7,
            "experiment_runner": 0.5,
            "crash_guardian": 1.0,
            "priority_scheduler": 1.0,
        },
        "RECOVERY": {
            "firehose_listener": 0.7,
            "wallet_scanner": 0.6,
            "trend_loop": 0.5,
            "telegram_learning": 0.2,
            "x_alpha": 0.5,
            "strategy_auditor": 1.0,
            "self_tuner": 1.0,
            "experiment_runner": 0.0,
            "crash_guardian": 1.0,
            "priority_scheduler": 1.0,
        },
        "SAFE_MODE": {
            "firehose_listener": 0.3,
            "wallet_scanner": 0.2,
            "trend_loop": 0.2,
            "telegram_learning": 0.0,
            "x_alpha": 0.1,
            "strategy_auditor": 1.0,
            "self_tuner": 1.0,
            "experiment_runner": 0.0,
            "crash_guardian": 1.0,
            "priority_scheduler": 1.0,
        },
    }

    def __init__(
        self,
        modules: Optional[Dict[str, float]] = None,
        alpha: float = 0.3,  # EWMA smoothing
        market_heat_thresholds=(0.35, 0.65),
        stress_thresholds=(0.4, 0.75, 0.9),
        snapshot_sink: Optional[Callable[[ProfileSnapshot], None]] = None,
    ):
        self.modules = modules or self.DEFAULT_MODULES.copy()
        self.alpha = alpha
        self.market_state = MarketState()
        self.internal_state = InternalState()
        self.market_heat_smoothed = 0.0
        self.system_stress_smoothed = 0.0
        self.market_heat_thresholds = market_heat_thresholds
        self.stress_thresholds = stress_thresholds
        self.last_snapshot: Optional[ProfileSnapshot] = None
        self.snapshot_sink = snapshot_sink  # e.g., CrashGuardian or Telegram reporter

        # cache
        self._last_profile: Profile = "BALANCED"
        self._last_throttles: Dict[str, float] = {
            m: 1.0 for m in self.modules.keys()
        }

    # --- public API ---------------------------------------------------------

    def update_market_state(
        self,
        *,
        tps: float,
        volatility: float,
        avg_spread: float,
        liquidity_pressure: float,
        ts: Optional[float] = None,
    ) -> None:
        self.market_state = MarketState(
            tps=tps,
            volatility=self._clip(volatility),
            avg_spread=self._clip(avg_spread),
            liquidity_pressure=self._clip(liquidity_pressure),
            ts=ts or time.time(),
        )

    def update_internal_state(
        self,
        *,
        loss_streak: int,
        win_streak: int,
        cpu: float,
        mem_pressure: float,
        error_rate: float,
        backlog_tasks: int,
        queue_lag_s: float,
        ts: Optional[float] = None,
    ) -> None:
        self.internal_state = InternalState(
            loss_streak=loss_streak,
            win_streak=win_streak,
            cpu=self._clip(cpu),
            mem_pressure=self._clip(mem_pressure),
            error_rate=self._clip(error_rate),
            backlog_tasks=max(backlog_tasks, 0),
            queue_lag_s=max(queue_lag_s, 0.0),
            ts=ts or time.time(),
        )

    def step(self) -> ProfileSnapshot:
        """Compute current profile + throttles."""
        market_heat = self._calc_market_heat(self.market_state)
        system_stress = self._calc_system_stress(self.internal_state)
        self.market_heat_smoothed = _ewma(self.market_heat_smoothed, market_heat, self.alpha)
        self.system_stress_smoothed = _ewma(self.system_stress_smoothed, system_stress, self.alpha)

        profile = self._choose_profile(
            self.market_heat_smoothed, self.system_stress_smoothed
        )
        throttles = self._build_throttle_table(profile, self.system_stress_smoothed)

        snapshot = ProfileSnapshot(
            ts=time.time(),
            profile=profile,
            module_throttles=throttles,
            market=self.market_state,
            internal=self.internal_state,
            market_heat=self.market_heat_smoothed,
            system_stress=self.system_stress_smoothed,
            notes=self._notes(profile, self.market_heat_smoothed, self.system_stress_smoothed),
        )
        self._last_profile = profile
        self._last_throttles = throttles
        self.last_snapshot = snapshot

        if self.snapshot_sink:
            try:
                self.snapshot_sink(snapshot)
            except Exception:
                # Never let reporting kill the scheduler.
                pass

        return snapshot

    def current_profile(self) -> Profile:
        return self._last_profile

    def current_throttles(self) -> Dict[str, float]:
        return self._last_throttles

    def priority_status_text(self) -> str:
        s = self.last_snapshot or self.step()
        lines = [
            f"[PriorityScheduler] {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s.ts))}",
            f"Profile: {s.profile}",
            f"MarketHeat: {s.market_heat:.3f}  SystemStress: {s.system_stress:.3f}",
            "Throttles:",
        ]
        for m, v in sorted(s.module_throttles.items(), key=lambda x: x[0]):
            lines.append(f"  - {m}: {v:.2f}")
        lines.append("Notes: " + s.notes)
        return "\n".join(lines)

    # --- internals ----------------------------------------------------------

    def _calc_market_heat(self, m: MarketState) -> float:
        # You can tune these weights. Assumes inputs already normalized except TPS.
        # Convert TPS into a sigmoid-normalized score; adjust k/shift based on your Firehose norms.
        tps_score = 1.0 - math.exp(-m.tps / 500.0)  # quickly approaches 1 around high TPS
        v, sp, lp = m.volatility, m.avg_spread, m.liquidity_pressure
        # Higher spreads & liquidity_pressure reduce "good heat"
        heat = (0.5 * v + 0.4 * tps_score + 0.1 * (1 - sp)) * (1 - 0.3 * lp)
        return self._clip(heat)

    def _calc_system_stress(self, s: InternalState) -> float:
        # combine resource stress + stability stress
        resource = max(s.cpu, s.mem_pressure)
        stability = max(s.error_rate, min(1.0, s.loss_streak / 5.0))
        backlog = min(1.0, (s.backlog_tasks / 100.0) + (s.queue_lag_s / 5.0))
        stress = max(resource, stability, backlog)
        return self._clip(stress)

    def _choose_profile(self, market_heat: float, stress: float) -> Profile:
        # Stress dominant
        s1, s2, s3 = self.stress_thresholds
        if stress >= s3:
            return "SAFE_MODE"
        if stress >= s2:
            return "RECOVERY"

        # Market-based decision
        low, high = self.market_heat_thresholds
        if market_heat >= high:
            return "LAUNCH_FRENZY"
        if market_heat <= low:
            return "CHOP_ZONE"
        return "BALANCED"

    def _build_throttle_table(self, profile: Profile, stress: float) -> Dict[str, float]:
        base = self.PROFILE_TABLE[profile]
        out: Dict[str, float] = {}
        # stress attenuation: near 1.0 stress => heavily scale down non-critical modules
        attenuation = 1.0 - (0.7 * max(0.0, stress - self.stress_thresholds[0]))  # start attenuating after first threshold
        for module, base_mult in base.items():
            weight = self.modules.get(module, 1.0)
            mult = base_mult * weight
            if module not in ("crash_guardian", "priority_scheduler", "strategy_auditor", "self_tuner"):
                mult *= max(0.2, attenuation)
            out[module] = self._clip(mult)
        return out

    @staticmethod
    def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return hi if x > hi else lo if x < lo else x

    @staticmethod
    def _notes(profile: Profile, market_heat: float, stress: float) -> str:
        return f"profile={profile} mh={market_heat:.2f} stress={stress:.2f}"


# --- Minimal helper to wire into your loops ---------------------------------

_SCHEDULER: Optional[PriorityScheduler] = None


def init_priority_scheduler(
    modules: Optional[Dict[str, float]] = None,
    snapshot_sink: Optional[Callable[[ProfileSnapshot], None]] = None,
) -> PriorityScheduler:
    global _SCHEDULER
    _SCHEDULER = PriorityScheduler(modules=modules, snapshot_sink=snapshot_sink)
    return _SCHEDULER


def scheduler_step(
    *,
    tps: float,
    volatility: float,
    avg_spread: float,
    liquidity_pressure: float,
    loss_streak: int,
    win_streak: int,
    cpu: float,
    mem_pressure: float,
    error_rate: float,
    backlog_tasks: int,
    queue_lag_s: float,
) -> ProfileSnapshot:
    if _SCHEDULER is None:
        raise RuntimeError("PriorityScheduler not initialized. Call init_priority_scheduler() first.")
    _SCHEDULER.update_market_state(
        tps=tps,
        volatility=volatility,
        avg_spread=avg_spread,
        liquidity_pressure=liquidity_pressure,
    )
    _SCHEDULER.update_internal_state(
        loss_streak=loss_streak,
        win_streak=win_streak,
        cpu=cpu,
        mem_pressure=mem_pressure,
        error_rate=error_rate,
        backlog_tasks=backlog_tasks,
        queue_lag_s=queue_lag_s,
    )
    return _SCHEDULER.step()
