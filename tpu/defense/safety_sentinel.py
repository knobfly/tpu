# defense/safety_sentinel.py
import asyncio
import logging

from runtime.event_bus import event_bus

# plug in your existing detectors if you want stronger checks
from utils.token_utils import detect_honeypot, get_lp_lock_status


class SafetySentinel:
    def __init__(self, lp_unlock_drop_pct: float = 50.0, vault_drain_drop_pct: float = 40.0):
        self.lp_unlock_drop_pct = lp_unlock_drop_pct
        self.vault_drain_drop_pct = vault_drain_drop_pct
        self._last_vault = {}  # token -> (vault_a, vault_b)

    async def observe_pool_update(
        self,
        token: str,
        vault_a_sol: float,
        vault_b_sol: float,
        also_check_lp: bool = True,
        also_check_honeypot: bool = False,
    ):
        try:
            prev = self._last_vault.get(token)
            self._last_vault[token] = (vault_a_sol, vault_b_sol)

            # 1) vault drain heuristic (relative drop vs last seen)
            if prev:
                pa, pb = prev
                da = (pa - vault_a_sol) / pa * 100.0 if pa > 0 else 0.0
                db = (pb - vault_b_sol) / pb * 100.0 if pb > 0 else 0.0
                if max(da, db) >= self.vault_drain_drop_pct:
                    await event_bus().emit({"type": "vault_drain", "token": token, "drop_pct": max(da, db)})
            
            # 2) opportunistic LP unlock check
            if also_check_lp:
                try:
                    status = get_lp_lock_status(token)  # your existing util
                    if status == "unlocked":
                        await event_bus().emit({"type": "lp_unlock", "token": token})
                except Exception:
                    pass

            # 3) lightweight honeypot ping (optional, keep it sparse)
            if also_check_honeypot:
                try:
                    if detect_honeypot(token):
                        await event_bus().emit({"type": "honeypot_detected", "token": token})
                except Exception:
                    pass

        except Exception as e:
            logging.warning(f"[SafetySentinel] observe_pool_update error for {token}: {e}")

# singleton-style accessor
_sentinel = SafetySentinel()
def safety() -> SafetySentinel:
    return _sentinel
