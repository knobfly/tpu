# modules/agents/alpha_agent.py
# Listens to TradeDecisionEvent and TradeOutcomeEvent, then posts alpha summaries.

import asyncio
import logging

from core.llm.alpha_output import alpha_output
from runtime.event_bus import TradeDecisionEvent, TradeOutcomeEvent, event_bus
from utils.logger import log_event
from utils.telegram_utils import send_telegram_message  # optional if you want TG push


class AlphaAgent:
    def __init__(self, enable_telegram: bool = True):
        self._tg = enable_telegram

    async def start(self):
        bus = event_bus()
        bus.subscribe(TradeDecisionEvent, self.on_trade_decision)
        bus.subscribe(TradeOutcomeEvent, self.on_trade_outcome)
        log_event("🧠 AlphaAgent listening for trade events.")

    async def on_trade_decision(self, ev: TradeDecisionEvent):
        try:
            msg = alpha_output().generate_alpha_call(
                token=ev.token,
                decision=ev.decision,
                fused_score=ev.fused_score / 100 if ev.fused_score > 1 else ev.fused_score,
                confidence=ev.confidence,
                reason_tags=ev.reason_tags,
                insights=ev.signals,
                meta=ev.meta,
            )
            log_event(msg)
            if self._tg:
                await self._safe_send_tg(msg)
        except Exception as e:
            logging.warning(f"[AlphaAgent] Failed to handle TradeDecisionEvent: {e}")

    async def on_trade_outcome(self, ev: TradeOutcomeEvent):
        try:
            msg = alpha_output().generate_trade_outcome(
                token=ev.token,
                pnl=ev.pnl,
                holding_time_s=ev.holding_time_s,
                strategy_type=ev.strategy_type,
                decision_id=ev.id,
                meta=ev.meta,
            )
            log_event(msg)
            if self._tg:
                await self._safe_send_tg(msg)
        except Exception as e:
            logging.warning(f"[AlphaAgent] Failed to handle TradeOutcomeEvent: {e}")

    async def _safe_send_tg(self, msg: str):
        try:
            await send_telegram_message(msg)
        except Exception as e:
            logging.warning(f"[AlphaAgent] Telegram send failed: {e}")


# Singleton launcher
_AGENT = None

async def start_alpha_agent(enable_telegram: bool = True):
    global _AGENT
    if _AGENT is None:
        _AGENT = AlphaAgent(enable_telegram=enable_telegram)
        await _AGENT.start()
    return _AGENT
