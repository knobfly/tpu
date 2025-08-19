# /agents/meta_strategy_agent.py
from inputs.meta_data.meta_strategy_engine import meta_strategy_engine
from runtime.event_bus import TradeOutcomeEvent, event_bus


async def on_trade_outcome(ev: TradeOutcomeEvent):
    meta_strategy_engine().record_trade_result(
        archetype=ev.strategy_type,
        pnl=ev.pnl,
        holding_time_s=ev.holding_time_s
    )

def register():
    event_bus().subscribe(TradeOutcomeEvent, on_trade_outcome)
