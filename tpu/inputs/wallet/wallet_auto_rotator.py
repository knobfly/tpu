# /wallet_auto_rotator.py
import logging
from typing import List, Optional, Tuple

from core.live_config import config as live_config
from inputs.wallet.multi_wallet_manager import multi_wallet
from inputs.wallet.wallet_streak_manager import wallet_streak_manager


class WalletAutoRotator:
    def __init__(self):
        self.min_balance_sol = float(live_config.get("min_wallet_balance_sol", 0.05))
        self.max_risk_wallets = int(live_config.get("max_risk_wallets", 2))  # choose top N

    async def choose_wallet_for_strategy(
        self,
        strategy: str,
        min_threshold: float = 0.5,
        role: Optional[str] = None
    ):
        """
        strategy: e.g. 'sniper', 'trade', 'scalper'
        min_threshold: fallback to any wallet above this health if no good ones
        role: optional tag if you tag wallets by role in your manager
        """
        wallets = multi_wallet.get_all_wallets(role=role)
        if not wallets:
            return None

        # Filter by balance
        eligible = []
        for w in wallets:
            balance = await w.get_balance()
            if balance < self.min_balance_sol:
                continue
            eligible.append(w)

        if not eligible:
            logging.warning("[WalletAutoRotator] No wallet with enough SOL.")
            return None

        # Rank by health
        ranked: List[Tuple[object, float]] = []
        for w in eligible:
            score = wallet_streak_manager.get_health_score(w.address)
            ranked.append((w, score))

        ranked.sort(key=lambda x: x[1], reverse=True)

        # If top is too low, fallback to best available
        if ranked and ranked[0][1] < min_threshold:
            logging.warning(
                f"[WalletAutoRotator] Best wallet health {ranked[0][1]:.2f} < {min_threshold}, "
                f"using anyway."
            )
            return ranked[0][0]

        # Optionally: diversify across top N
        topN = ranked[: self.max_risk_wallets] if len(ranked) > self.max_risk_wallets else ranked
        chosen = topN[0][0] if topN else ranked[0][0]
        return chosen

    def record_outcome(self, wallet_addr: str, result: str, pnl: float, holding_s: float):
        wallet_streak_manager.record_outcome(wallet_addr, result, pnl, holding_s)

    def record_error(self, wallet_addr: str):
        wallet_streak_manager.record_error(wallet_addr)


# singleton
wallet_auto_rotator = WalletAutoRotator()
