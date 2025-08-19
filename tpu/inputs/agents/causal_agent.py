# modules/agents/causal_agent.py
import asyncio

from runtime.event_bus import TradeDecisionEvent, TradeOutcomeEvent, event_bus
from strategy.causal_predictor import causal_predictor


async def on_trade_decision(ev: TradeDecisionEvent):
    causal_predictor().log_decision(
        decision_id=ev.id,
        token=ev.token,
        decision=ev.decision,
        confidence=ev.confidence,
        fused_score=ev.fused_score,
        signals=ev.signals,
        meta={"tags": ev.reason_tags, **ev.meta},
    )

async def on_trade_outcome(ev: TradeOutcomeEvent):
    causal_predictor().record_outcome(
        decision_id=ev.id,
        pnl=ev.pnl,
        holding_time_s=ev.holding_time_s
    )

def register():
    bus = event_bus()
    bus.subscribe(TradeDecisionEvent, on_trade_decision)
    bus.subscribe(TradeOutcomeEvent, on_trade_outcome)
