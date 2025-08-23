import asyncio
import logging
from datetime import datetime
from core.live_config import config
from special.insight_logger import log_strategy_change
from utils.logger import log_event


class RiskAggressionManager:
    def __init__(self):
        self.last_adjustment = None

    async def run(self):
        log_event("📈 Risk-Graded Aggression logic running...")
        while True:
            try:
                await self.evaluate_conditions()
            except Exception as e:
                log_event(f"⚠️ Risk aggression error: {e}")
            await asyncio.sleep(60)

    async def evaluate_conditions(self):
        score = config.get("last_token_score", 50)
        volatility = config.get("market_volatility", 0.5)
        mode = config.get("mode", "balanced")

        original_buy = config.get("base_buy_amount", 0.1)
        original_sell = config.get("base_sell_profit_percent", 30)

        adjusted_buy = original_buy
        adjusted_sell = original_sell
        strategy_note = "neutral"

        if score > 85 and volatility < 0.3:
            adjusted_buy = min(original_buy * 2, 2.0)
            adjusted_sell = max(original_sell - 10, 15)
            strategy_note = "high confidence + low volatility"
        elif score < 40 or volatility > 0.8:
            adjusted_buy = 0.05
            adjusted_sell = 35
            strategy_note = "low confidence or high volatility"
        else:
            adjusted_buy = original_buy
            adjusted_sell = original_sell

        # Update config live
        config["buy_amount"] = round(adjusted_buy, 4)
        config["sell_profit_percent"] = round(adjusted_sell, 2)

        # Log if changed
        if self.last_adjustment != (adjusted_buy, adjusted_sell):
            log_strategy_change(strategy_note, mode)
            self.last_adjustment = (adjusted_buy, adjusted_sell)


# === Singleton Instance ===
risk_aggression = RiskAggressionManager()


def get_risk_adjusted_amount(token_score: int, volatility: float = 1.0) -> float:
    """
    Called by trade_executor to dynamically size buys based on score and volatility.
    Returns an adjusted buy size within limits.
    """
    base_amount = config.get("buy_amount", 0.1)
    max_amount = config.get("max_buy_amount", 3.0)
    min_amount = 0.05

    confidence_factor = min(token_score / 100, 1.0)
    scaled = base_amount + (max_amount - base_amount) * confidence_factor
    adjusted = scaled / max(1.0, volatility)

    final = round(max(min_amount, min(adjusted, max_amount)), 4)
    return final
