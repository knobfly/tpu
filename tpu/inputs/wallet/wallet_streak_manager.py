# /wallet_streak_manager.py
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, Optional

STREAK_FILE = "/home/ubuntu/nyx/runtime/logs/wallet_streaks.json"
ROLLING_MAX = 200  # how many outcomes to keep per wallet


@dataclass
class StreakStats:
    wins: int = 0
    losses: int = 0
    rugs: int = 0
    win_streak: int = 0
    loss_streak: int = 0
    last_result: Optional[str] = None
    last_update_ts: float = 0.0
    avg_pnl: float = 0.0
    avg_holding_s: float = 0.0
    decisions: int = 0
    rolling_pnl_sum: float = 0.0
    rolling_hold_sum: float = 0.0
    rolling_n: int = 0
    error_count: int = 0

    def update(self, result: str, pnl: float, holding_s: float):
        now = time.time()
        self.last_update_ts = now
        self.decisions += 1

        if result == "win":
            self.wins += 1
            if self.last_result == "win":
                self.win_streak += 1
            else:
                self.win_streak = 1
            self.loss_streak = 0
        elif result in ("loss", "bad_exit"):
            self.losses += 1
            if self.last_result in ("loss", "bad_exit"):
                self.loss_streak += 1
            else:
                self.loss_streak = 1
            self.win_streak = 0
        elif result == "rug":
            self.rugs += 1
            self.loss_streak += 1
            self.win_streak = 0

        self.last_result = result

        # rolling metrics
        self.rolling_pnl_sum += pnl
        self.rolling_hold_sum += max(0.0, holding_s)
        self.rolling_n += 1
        self.avg_pnl = self.rolling_pnl_sum / max(1, self.rolling_n)
        self.avg_holding_s = self.rolling_hold_sum / max(1, self.rolling_n)

    def mark_error(self):
        self.error_count += 1
        self.last_update_ts = time.time()


class WalletStreakManager:
    def __init__(self, path: str = STREAK_FILE):
        self.path = path
        self._stats: Dict[str, StreakStats] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as f:
                raw = json.load(f)
            for w, d in raw.items():
                self._stats[w] = StreakStats(**d)
        except Exception as e:
            logging.warning(f"[WalletStreakManager] failed to load: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump({w: asdict(s) for w, s in self._stats.items()}, f, indent=2)
        except Exception as e:
            logging.warning(f"[WalletStreakManager] failed to save: {e}")

    def record_outcome(self, wallet_addr: str, result: str, pnl: float, holding_s: float):
        stats = self._stats.get(wallet_addr) or StreakStats()
        stats.update(result=result, pnl=pnl, holding_s=holding_s)
        self._stats[wallet_addr] = stats
        self._save()

    def record_error(self, wallet_addr: str):
        stats = self._stats.get(wallet_addr) or StreakStats()
        stats.mark_error()
        self._stats[wallet_addr] = stats
        self._save()

    def get_stats(self, wallet_addr: str) -> StreakStats:
        return self._stats.get(wallet_addr) or StreakStats()

    def get_health_score(self, wallet_addr: str, decay_minutes: float = 60.0) -> float:
        """Return 0..1 health metric for wallet routing."""
        s = self.get_stats(wallet_addr)
        # basic: penalize loss streaks/rugs, boost win rate & avg pnl
        total = max(1, s.wins + s.losses + s.rugs)
        win_rate = s.wins / total
        loss_penalty = min(1.0, s.loss_streak / 5.0)
        rug_penalty = min(1.0, s.rugs / 10.0)
        pnl_sigmoid = 1 / (1 + pow(2.71828, -s.avg_pnl))  # crude

        base = 0.5 * win_rate + 0.3 * pnl_sigmoid + 0.2 * (1.0 - loss_penalty)
        base *= (1.0 - rug_penalty)

        # time-decay boost if no trades / hidden stale effect (optional)
        # skipping for simplicity

        return max(0.0, min(1.0, base))

    def rank_wallets(self, wallet_addrs):
        scored = [(w, self.get_health_score(w)) for w in wallet_addrs]
        # highest score first
        return sorted(scored, key=lambda x: x[1], reverse=True)

# singleton
wallet_streak_manager = WalletStreakManager()
