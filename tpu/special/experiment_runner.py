# /experiment_runner.py
import asyncio
import logging
import random
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.live_config import config
from inputs.onchain.firehose.firehose_wallet_tools import get_balance
from librarian.feature_store import FeatureStore
from scoring.trade_score_engine import evaluate_trade
from strategy.contextual_bandit import get_bandit_sync
from strategy.strategy_memory import log_strategy_result, record_result, tag_token_result
from utils.crash_guardian import beat, register_module, wrap_safe_loop
from utils.logger import log_event
from utils.recent_failures import log_failed_trade
from utils.rpc_loader import get_active_rpc
from utils.service_status import update_status

# === Config ===
HEARTBEAT_EVERY = 20
EXPERIMENT_INTERVAL = 300          # seconds between experiments
MAX_DAILY_EXPERIMENTS = 20
MIN_SOL_BALANCE = 0.05             # skip if balance is too low
DEFAULT_PROBE_SIZE_SOL = 0.01      # micro trades
REWARD_DECAY = 0.997               # smoothed reward

@dataclass
class ArmConfig:
    name: str
    buy_amount_sol: float
    take_profit_pct: float
    stop_loss_pct: float
    hold_time_sec: int
    notes: str = ""

DEFAULT_ARMS: List[ArmConfig] = [
    ArmConfig("baseline",     0.01, 25, 15, 1800, "Current prod defaults"),
    ArmConfig("aggressive",   0.01, 40, 20, 900,  "High TP, high SL"),
    ArmConfig("scalp",        0.01, 12, 7,  240,  "Quick in-out"),
    ArmConfig("conservative", 0.01, 18, 9,  2400, "Lower risk"),
    ArmConfig("meta_trend",   0.01, 30, 12, 1200, "Meta/theme driven"),
]

class ExperimentRunner:
    """
    Runs micro-trades with different arms (strategies) to gather feedback and train the bandit.
    """
    def __init__(self, ai_brain=None, engine=None, wallet=None):
        self.name = "experiment_runner"
        self.ai = ai_brain
        self.engine = engine
        self.wallet = wallet
        self.fs = FeatureStore
        self.enabled = bool(config.get("experiments_enabled", True))
        self.min_balance = float(config.get("experiments_min_sol", MIN_SOL_BALANCE))
        self.default_probe = float(config.get("experiments_probe_sol", DEFAULT_PROBE_SIZE_SOL))

        self._arms: Dict[str, ArmConfig] = {arm.name: arm for arm in DEFAULT_ARMS}
        self._inflight: Dict[str, Dict[str, Any]] = {}
        self._last_reward: Dict[str, float] = {}
        self._last_context: Dict[str, Any] = {}
        self.daily_experiments = 0
        self.last_reset = datetime.utcnow()
        self._last_loop = 0.0
        try:
            self.bandit = get_bandit_sync()
        except RuntimeError:
            self.bandit = None  # Will be attached later if needed

        # Register for CrashGuardian
        register_module(self.name, wrap_safe_loop(self.name, self.run, heartbeat_every=HEARTBEAT_EVERY))

    # === Public API ===
    def arms(self) -> Dict[str, ArmConfig]:
        return self._arms

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "daily_experiments": self.daily_experiments,
            "arms": {k: asdict(v) for k, v in self._arms.items()},
            "inflight": len(self._inflight),
            "last_context": self._last_context,
            "last_reward": self._last_reward,
            "bandit_stats": self.bandit.get_stats() if self.bandit else {},
        }

    def attach_bandit(self, bandit):
        self.bandit = bandit


    async def run(self):
        update_status(self.name)
        log_event("🧪 ExperimentRunner online.")
        while True:
            beat(self.name)
            self._last_loop = time.time()

            self._reset_daily_counter()
            if not self.enabled or self.daily_experiments >= MAX_DAILY_EXPERIMENTS:
                await asyncio.sleep(EXPERIMENT_INTERVAL)
                continue

            try:
                await self._maybe_run_probe()
                self.daily_experiments += 1
            except Exception as e:
                log_event(f"❌ ExperimentRunner error: {e}")
                logging.error(traceback.format_exc())
                log_failed_trade("experiment_runner", str(e))

            await asyncio.sleep(EXPERIMENT_INTERVAL)

    async def process_trade_feedback(self, tx_signature: str, arm_name: str, pnl_pct: float, duration_sec: float, meta: Optional[Dict[str, Any]] = None):
        """
        Called from trade settlement logic to convert trade results → bandit reward.
        """
        reward = self._decay_reward(arm_name, self._to_reward(pnl_pct, duration_sec))
        ctx = self._inflight.get(tx_signature, {}).get("context", {})

        if self.bandit:
            self.bandit.report_result(arm_name, reward, ctx)

        token = self._inflight.get(tx_signature, {}).get("token")
        outcome = "win" if pnl_pct > 0 else "loss"
        if token:
            tag_token_result(token, f"exp_{arm_name}_{outcome}")
            record_result(arm_name, outcome)

        self._log_result_to_fs(arm_name, reward, pnl_pct, duration_sec, token, ctx, meta or {})
        self._last_reward[arm_name] = reward
        self._inflight.pop(tx_signature, None)

        log_event(f"🧪 [EXP] {arm_name} reward={reward:.4f} pnl={pnl_pct:.2f}% dur={duration_sec:.1f}s")

    # === Internals ===
    def _reset_daily_counter(self):
        now = datetime.utcnow()
        if (now - self.last_reset).days >= 1:
            self.daily_experiments = 0
            self.last_reset = now
            log_event("🔄 ExperimentRunner: Daily counter reset.")

    async def _maybe_run_probe(self):
        if not self.wallet:
            return

        bal = None

        # 1️⃣ Firehose-first balance check
        try:
            bal = await get_balance(self.wallet.address)
        except Exception as e:
            log_event(f"[ExperimentRunner] Firehose balance fetch failed: {e}")

        # 2️⃣ RPC fallback if Firehose is None
        if bal is None:
            try:
                rpc_url = get_active_rpc()
                bal = await self.wallet.get_balance(rpc_url)
            except Exception as e:
                log_event(f"[ExperimentRunner] RPC balance fetch failed: {e}")
                return

        if bal < self.min_balance:
            return

        # 3️⃣ Get candidate tokens for experiments
        candidates = []
        try:
            candidates = self.ai.get_candidates_for_experiments(limit=3) if self.ai else []
        except Exception:
            pass
        if not candidates:
            return

        # 4️⃣ Build context & choose strategy arm
        ctx = self._build_context_snapshot()
        self._last_context = ctx
        arm_name = self.bandit.choose_arm(ctx) if self.bandit else random.choice(list(self._arms.keys()))
        arm = self._arms.get(arm_name, self._arms["baseline"])
        token = candidates[0]["token"] if isinstance(candidates[0], dict) else candidates[0]

        # 5️⃣ Execute probe buy
        sig = await self._try_probe_buy(token, arm, arm.buy_amount_sol or self.default_probe, ctx)
        if sig:
            self._inflight[sig] = {
                "arm": arm.name,
                "token": token,
                "start_ts": time.time(),
                "context": ctx,
                "target_tp": arm.take_profit_pct,
                "target_sl": arm.stop_loss_pct,
            }

    def _build_context_snapshot(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        try:
            if self.ai and hasattr(self.ai, "get_bandit_context"):
                d.update(self.ai.get_bandit_context())
        except Exception:
            pass
        d["minute"] = datetime.utcnow().minute
        d["risk_mode"] = config.get("risk_mode", "balanced")
        d["frenzy"] = bool(config.get("auto_frenzy"))
        return d

    async def _try_probe_buy(self, token: str, arm: ArmConfig, size_sol: float, ctx: Dict[str, Any]) -> Optional[str]:
        try:
            sig = await self.engine.executor.experiment_buy(
                token_mint=token,
                amount_sol=size_sol,
                tp_pct=arm.take_profit_pct,
                sl_pct=arm.stop_loss_pct,
                max_hold_s=arm.hold_time_sec,
                meta={"arm": arm.name, "context": ctx},
            )
            if sig:
                log_event(f"🧪 [EXP] {arm.name} on {token} @ {size_sol} SOL (TP {arm.take_profit_pct}% / SL {arm.stop_loss_pct}%)")
            return sig
        except Exception as e:
            logging.warning(f"[ExperimentRunner] buy failed: {e}")
            return None

    def _to_reward(self, pnl_pct: float, duration_sec: float) -> float:
        return pnl_pct / 100.0

    def _decay_reward(self, arm_name: str, reward: float) -> float:
        prev = self._last_reward.get(arm_name, reward)
        return prev * REWARD_DECAY + reward * (1 - REWARD_DECAY)

    def _log_result_to_fs(self, arm: str, reward: float, pnl_pct: float, duration: float, token: Optional[str], context: Dict[str, Any], meta: Dict[str, Any]):
        if not self.fs:
            return
        try:
            self.fs.log_experiment({
                "ts": time.time(),
                "arm": arm,
                "reward": reward,
                "pnl_pct": pnl_pct,
                "duration_sec": duration,
                "token": token,
                "context": context,
                "meta": meta,
                "type": "experiment_result",
            })
        except Exception as e:
            logging.warning(f"[ExperimentRunner] FS log failed: {e}")


# === Global Runner ===
experiment_runner = ExperimentRunner()

async def start_experiment_runner():
    await experiment_runner.run()

def get_experiment_status():
    return experiment_runner.get_status()
