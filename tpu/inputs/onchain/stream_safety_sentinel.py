import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from core.live_config import config
from runtime.event_bus import event_bus
from utils.logger import log_event

# Reuse your existing helpers if present
try:
    from defense.liquidity_monitor import check_lp_status  # returns e.g. "locked"/"unlocked"/None
except Exception:
    async def check_lp_status(token: str) -> Optional[str]:  # type: ignore
        return None

try:
    from defense.honeypot_scanner import is_honeypot
except Exception:
    def is_honeypot(_token: str) -> bool:  # type: ignore
        return False


class StreamSafetySentinel:
    """
    Watches decoded pool updates from your stream and emits:
      - lp_unlock
      - vault_drain  (severity: low/medium/high)
      - honeypot_detected
    Call `observe_pool_update(...)` from your stream handler.
    Optionally call `observe_honeypot(token)` on deploy/first trade.
    """

    def __init__(self):
        cfg = config.get("stream_safety", {})
        self.window_s = float(cfg.get("window_seconds", 60.0))
        self.vault_drain_medium = float(cfg.get("vault_drain_medium_pct", 0.25))  # 25% in window
        self.vault_drain_high = float(cfg.get("vault_drain_high_pct", 0.45))      # 45% in window
        self.min_vault_notional = float(cfg.get("min_vault_notional", 0.5))       # ignore dust pools (in SOL)
        self.realert_cooldown_s = float(cfg.get("realert_cooldown_s", 90.0))

        # token -> deque[(ts, vault_a_sol, vault_b_sol)]
        self.history: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(lambda: deque(maxlen=120))
        # token -> last_alert_ts
        self.last_alert: Dict[Tuple[str, str], float] = {}  # key = (token, type)

    def _cooldown(self, token: str, kind: str) -> bool:
        k = (token, kind)
        now = time.time()
        ts = self.last_alert.get(k, 0.0)
        if now - ts < self.realert_cooldown_s:
            return True
        self.last_alert[k] = now
        return False

    async def observe_honeypot(self, token: str) -> None:
        try:
            if is_honeypot(token) and not self._cooldown(token, "honeypot_detected"):
                await event_bus().emit({"type": "honeypot_detected", "token": token})
                log_event(f"🚨 honeypot_detected → {token}")
        except Exception as e:
            logging.debug(f"[SafetySentinel] honeypot check failed: {e}")

    async def observe_lp_status(self, token: str) -> None:
        try:
            st = await check_lp_status(token)
            if st == "unlocked" and not self._cooldown(token, "lp_unlock"):
                await event_bus().emit({"type": "lp_unlock", "token": token})
                log_event(f"🚨 lp_unlock → {token}")
        except Exception as e:
            logging.debug(f"[SafetySentinel] lp status check failed: {e}")

    async def observe_pool_update(
        self,
        token: str,
        vault_a_sol: float,
        vault_b_sol: float,
        ts: Optional[float] = None,
        also_check_lp: bool = False,
        also_check_honeypot: bool = False,
    ) -> None:
        """
        Call this on every decoded pool/vault change.
        vault_* should be in SOL (or same unit consistently).
        """
        now = ts or time.time()
        h = self.history[token]
        h.append((now, float(vault_a_sol), float(vault_b_sol)))

        # prune old
        while h and (now - h[0][0]) > self.window_s:
            h.popleft()

        if len(h) < 2:
            # not enough data yet
            return

        # ignore dust pools
        cur_a, cur_b = h[-1][1], h[-1][2]
        if max(cur_a, cur_b) < self.min_vault_notional:
            return

        # compute window deltas
        start_ts, start_a, start_b = h[0]
        drop_a = (start_a - cur_a) / max(start_a, 1e-9)
        drop_b = (start_b - cur_b) / max(start_b, 1e-9)
        worst = max(drop_a, drop_b)

        severity = None
        if worst >= self.vault_drain_high:
            severity = "high"
        elif worst >= self.vault_drain_medium:
            severity = "medium"

        if severity and not self._cooldown(token, "vault_drain"):
            await event_bus().emit({"type": "vault_drain", "token": token, "severity": severity})
            log_event(f"🚨 vault_drain[{severity}] → {token} ΔA={drop_a:.2%} ΔB={drop_b:.2%} in {self.window_s:.0f}s")

        # optional periodic checks piggybacked on updates
        if also_check_lp:
            await self.observe_lp_status(token)
        if also_check_honeypot:
            await self.observe_honeypot(token)
