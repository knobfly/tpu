#strategy/contextual_bandit.py
import asyncio
import json
import logging
import math
import os
import random
import time
import contextlib
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from core.live_config import config, save_config
from librarian.feature_store import FeatureStore, get_feature_store_sync, init_feature_store
from utils.logger import log_event
from utils.service_status import update_status

# --------------------------
# Config & defaults
# --------------------------

DEFAULT_BANDIT_CFG = {
    "policy": "thompson",          # "thompson" | "ucb1"
    "arms": ["balanced", "passive", "aggro", "scalper", "meta_trend"],
    "min_pulls": 5,                # warmup pulls per arm before TS/UCB kicks in
    "reward_horizon_sec": 86_400,  # how far back we look for rewards (24h)
    "refresh_sec": 15,             # how often to refresh weights
    "epsilon": 0.0,                # optional epsilon-greedy on top (0 = disabled)
    "clip_reward_min": -1.0,
    "clip_reward_max": 1.0,
    "persist_path": "runtime/library/bandit/bandit_state.json"
}

# --------------------------
# Helpers
# --------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v

def _now() -> float:
    return time.time()

@dataclass
class ArmStats:
    name: str
    pulls: int = 0
    total_reward: float = 0.0
    mean_reward: float = 0.0
    m2: float = 0.0                 # Welford for variance
    last_updated: float = field(default_factory=_now)

    def update(self, reward: float):
        self.pulls += 1
        self.total_reward += reward
        delta = reward - self.mean_reward
        self.mean_reward += delta / self.pulls
        delta2 = reward - self.mean_reward
        self.m2 += delta * delta2
        self.last_updated = _now()

    @property
    def variance(self) -> float:
        return self.m2 / (self.pulls - 1) if self.pulls > 1 else 1.0

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

class BanditManager:
    """
    Strategy bandit manager (Thompson Sampling + UCB1) built over FeatureStore logs.
    """

    def __init__(self, fs: FeatureStore, cfg: Dict):
        self.fs = fs
        self.cfg = {**DEFAULT_BANDIT_CFG, **(cfg or {})}
        self.policy = self.cfg["policy"]
        self.arms: Dict[str, ArmStats] = {a: ArmStats(a) for a in self.cfg["arms"]}
        self.min_pulls = self.cfg["min_pulls"]
        self.reward_horizon = self.cfg["reward_horizon_sec"]
        self.refresh_sec = self.cfg["refresh_sec"]
        self.epsilon = self.cfg["epsilon"]
        self.clip_min = self.cfg["clip_reward_min"]
        self.clip_max = self.cfg["clip_reward_max"]
        self.persist_path = self.cfg["persist_path"]

        self._loop_task: Optional[asyncio.Task] = None
        self._last_weights: Dict[str, float] = {a: 1.0 / len(self.arms) for a in self.arms}
        self._last_choice: Optional[str] = None

        self.load_state()

    # ---------- lifecycle ----------

    async def start(self):
        if self._loop_task:
            return
        self._loop_task = asyncio.create_task(self._refresh_loop())
        log_event("🎯 BanditManager started.")

    async def stop(self):
        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        self.save_state()
        log_event("🎯 BanditManager stopped.")

    def save_state(self):
        try:
            data = {
                "cfg": self.cfg,
                "arms": {
                    k: {
                        "pulls": v.pulls,
                        "total_reward": v.total_reward,
                        "mean_reward": v.mean_reward,
                        "m2": v.m2,
                        "last_updated": v.last_updated,
                    } for k, v in self.arms.items()
                }
            }
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.persist_path)  # atomic on same fs
        except Exception as e:
            logging.warning(f"[Bandit] Failed to save state: {e}")

    def load_state(self):
        try:
            with open(self.persist_path, "r") as f:
                data = json.load(f)
            if "arms" in data:
                for k, s in data["arms"].items():
                    self.arms.setdefault(k, ArmStats(k))
                    arm = self.arms[k]
                    arm.pulls        = int(s.get("pulls", 0))
                    arm.total_reward = float(s.get("total_reward", 0.0))
                    arm.mean_reward  = float(s.get("mean_reward", 0.0))
                    arm.m2           = float(s.get("m2", 0.0))
                    arm.last_updated = float(s.get("last_updated", _now()))
            # allow live switching via config, but clamp epsilon sanity
            bcfg = config.get("bandit", {})
            self.policy  = bcfg.get("policy", self.policy)
            self.epsilon = max(0.0, min(float(bcfg.get("epsilon", self.epsilon)), 0.2))
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.warning(f"[Bandit] Failed to load state: {e}")


 # ---------- main selection API ----------

    def choose_strategy(self) -> str:
        """
        Fast, in-memory choice (assumes periodic refresh loop keeps stats fresh).
        """
        update_status("bandit_manager")

        # epsilon-greedy override
        if self.epsilon > 0 and random.random() < self.epsilon:
            arm = random.choice(list(self.arms.keys()))
            self._last_choice = arm
            return arm

        # warmup
        cold_arms = [a for a, s in self.arms.items() if s.pulls < self.min_pulls]
        if cold_arms:
            arm = random.choice(cold_arms)
            self._last_choice = arm
            return arm

        if self.policy == "ucb1":
            arm = self._choose_ucb1()
        else:
            arm = self._choose_thompson()

        self._last_choice = arm
        return arm

    # ---------- record rewards ----------

    def record_reward(self, strategy: str, reward: float):
        """
        Direct reward push (e.g., after a trade realized).
        """
        if strategy not in self.arms:
            return
        reward = _clamp(reward, self.clip_min, self.clip_max)
        self.arms[strategy].update(reward)
        self.save_state()

    async def sync_rewards_from_feature_store(self):
        """
        Pull recent rewards from feature_store -> update arm stats.
        Expected Source: feature_store.record_strategy_weight(strategy, weight, reward)
        """
        horizon = self.reward_horizon
        now_ts = _now()

        for arm in self.arms.values():
            rewards = self.fs.get_recent_rewards_by_strategy(arm.name, horizon_sec=horizon)
            # Only re-aggregate what is new: approximate by diffing counts (quick & dirty)
            already = arm.pulls
            if len(rewards) > already:
                for r in rewards[already:]:
                    r = _clamp(float(r), self.clip_min, self.clip_max)
                    arm.update(r)

        self.save_state()

    # ---------- policies ----------

    def _choose_ucb1(self) -> str:
        total_pulls = sum(a.pulls for a in self.arms.values()) + 1
        best_arm, best_ucb = None, -1e9
        for a in self.arms.values():
            if a.pulls == 0:
                return a.name
            bonus = math.sqrt(2.0 * math.log(total_pulls) / a.pulls)
            ucb = a.mean_reward + bonus
            if ucb > best_ucb:
                best_ucb = ucb
                best_arm = a.name
        return best_arm

    def _choose_thompson(self) -> str:
        best_arm, best_sample = None, -1e9
        for a in self.arms.values():
            # Gaussian sampling with Normal(mean, std/sqrt(n+1)) fallback
            std = a.std / math.sqrt(a.pulls + 1.0)
            sample = random.gauss(a.mean_reward, std if std > 1e-6 else 1e-6)
            if sample > best_sample:
                best_sample = sample
                best_arm = a.name
        return best_arm


    SAFE_VARIANTS_BY_BAND = {
        "BUY": [
            {"id":"balanced",   "size":0.35, "ladder":1, "route":"routerA"},
            {"id":"passive",    "size":0.25, "ladder":2, "route":"routerA"},
            {"id":"aggro",      "size":0.50, "ladder":2, "route":"routerB"},
            {"id":"scalper",    "size":0.30, "ladder":3, "route":"routerA"},
            {"id":"meta_trend", "size":0.40, "ladder":2, "route":"routerB"},
        ],
        "AGGRESSIVE_BUY": [
            {"id":"balanced",   "size":0.60, "ladder":2, "route":"routerB"},
            {"id":"aggro",      "size":0.80, "ladder":3, "route":"routerB"},
            {"id":"scalper",    "size":0.50, "ladder":3, "route":"routerA"},
            {"id":"meta_trend", "size":0.70, "ladder":2, "route":"routerB"},
        ],
        "AUTO": [
            {"id":"aggro",      "size":1.00, "ladder":2, "route":"routerB"},
            {"id":"balanced",   "size":0.80, "ladder":2, "route":"routerB"},
            {"id":"meta_trend", "size":0.90, "ladder":2, "route":"routerA"},
        ],
    }

    def choose_variant_for_band(
        self,
        action_band: str,
        context: Dict,
        *,
        default_id: str = "balanced",
        size_caps: Dict[str,float] = None,
     ) -> Dict:
        """
        Map selected arm to a concrete execution variant for the given band.
        Never escalates/changes the scorer's band. Returns a dict with keys:
        {id, size, ladder, route, arm}
        """
        size_caps = size_caps or {"BUY":0.5, "AGGRESSIVE_BUY":1.0, "AUTO":1.0}
        variants = SAFE_VARIANTS_BY_BAND.get(action_band, [])
        if not variants:
            return {}

        # pick arm via bandit (fast) or fall back deterministically
        arm = self.choose_strategy()
        # find matching variant by id; if none, fall back to default_id then first
        variant = next((v for v in variants if v["id"] == arm), None)
        if variant is None:
            variant = next((v for v in variants if v["id"] == default_id), variants[0])

        # cap size & ladder to be extra safe; keep your terminology
        v = dict(variant)
        cap = float(size_caps.get(action_band, 0.5))
        v["size"]   = min(float(v["size"]), cap)
        v["ladder"] = int(max(1, min(int(v["ladder"]), 4)))
        v["route"]  = v.get("route", "routerA")
        v["arm"]    = arm
        return v

    # ---------- weights (informational only) ----------

    def current_weights(self) -> Dict[str, float]:
        """
        Not used for selection (we select directly), but useful for display.
        We’ll approximate by softmax of mean rewards.
        """
        vals = {a: self.arms[a].mean_reward for a in self.arms}
        m = max(vals.values()) if vals else 0.0
        exps = {k: math.exp(v - m) for k, v in vals.items()}
        s = sum(exps.values()) or 1.0
        return {k: exps[k] / s for k in exps}

    def last_choice(self) -> Optional[str]:
        return self._last_choice

    def health_snapshot(self) -> Dict:
        return {
            "policy": self.policy,
            "epsilon": self.epsilon,
            "arms": {
                k: {
                    "pulls": v.pulls,
                    "mean_reward": round(v.mean_reward, 4),
                    "std": round(v.std, 4),
                    "last_updated": v.last_updated,
                } for k, v in self.arms.items()
            },
            "weights": self.current_weights(),
            "last_choice": self._last_choice,
            "ts": _now(),
        }


    # ---------- loop ----------

    async def _refresh_loop(self):
        while True:
            try:
                await asyncio.sleep(self.refresh_sec)
                await self.sync_rewards_from_feature_store()
                os.makedirs("runtime/reports", exist_ok=True)
                with open("runtime/reports/bandit_health.json", "w") as f:
                    json.dump(self.health_snapshot(), f, indent=2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.warning(f"[Bandit] refresh loop error: {e}")

# --------------------------
# Singleton wiring
# --------------------------

_bandit_manager: Optional[BanditManager] = None
_bandit_lock = asyncio.Lock()

async def init_bandit_manager() -> BanditManager:
    global _bandit_manager
    if _bandit_manager is None:
        async with _bandit_lock:
            if _bandit_manager is None:
                fs = get_feature_store_sync()  # must be initialized earlier
                os.makedirs("runtime/library/bandit", exist_ok=True)
                cfg = config.get("bandit", {})
                _bandit_manager = BanditManager(fs, cfg)
                await _bandit_manager.start()
                log_event("🎯 BanditManager initialized.")
    return _bandit_manager

def get_bandit_sync() -> BanditManager:
    if _bandit_manager is None:
        raise RuntimeError("Bandit not initialized. Call await init_bandit_manager() early in startup.")
    return _bandit_manager
