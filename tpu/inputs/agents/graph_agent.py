# /agents/graph_agent.py
from memory.graph_store import graph_store
from runtime.event_bus import MessageFeedbackEvent, TradeDecisionEvent, TradeOutcomeEvent, event_bus


async def on_decision(ev: TradeDecisionEvent):
    g = graph_store()
    g.add_node(ev.token, "token")
    # optional: store fused score/confidence
    g.add_edge("strategy_core", ev.token, "decision", score=ev.fused_score, decision=ev.decision)
    g.save()

async def on_outcome(ev: TradeOutcomeEvent):
    g = graph_store()
    g.add_node(ev.token, "token")
    g.add_edge("strategy_core", ev.token, "outcome", pnl=ev.pnl, holding_time_s=ev.holding_time_s)
    # optional: wallet info if in ev.meta
    wallets = ev.meta.get("wallets_used", [])
    for w in wallets:
        g.record_wallet_trade(wallet=w, token=ev.token, action="sell" if ev.pnl != 0 else "buy", pnl=ev.pnl)
    g.save()

async def on_message(ev: MessageFeedbackEvent):
    g = graph_store()
    g.add_node(ev.channel, "group")
    # You may parse tokens mentioned in ev.meta["tokens"] and link them
    for t in ev.meta.get("tokens", []):
        g.record_group_mention(ev.channel, t)
    g.save()

def register():
    bus = event_bus()
    bus.subscribe(TradeDecisionEvent, on_decision)
    bus.subscribe(TradeOutcomeEvent, on_outcome)
    bus.subscribe(MessageFeedbackEvent, on_message)
