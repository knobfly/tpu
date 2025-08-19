# /agents/style_agent.py
from core.llm.lexicon_tracker import lexicon_tracker
from core.llm.style_evolution import style_evolution
from runtime.event_bus import MessageFeedbackEvent, TradeOutcomeEvent, event_bus


async def on_trade_outcome(ev: TradeOutcomeEvent):
    meta = ev.meta or {}
    style_evolution().record_trade_feedback(
        pnl=ev.pnl,
        confidence=meta.get("model_confidence", 0.5),
        was_early_exit=meta.get("early_exit", False),
        was_rug=meta.get("rug", False),
        latency_ms=meta.get("decision_latency_ms", 0),
    )
    # Optional lexicon linkage if keywords present
    if "keywords" in meta:
        lexicon_tracker().record_outcome(meta["keywords"], win=ev.pnl > 0)

async def on_message(ev: MessageFeedbackEvent):
    style_evolution().record_message_feedback(
        engagement=ev.engagement,
        sentiment=ev.sentiment,
        length_tokens=ev.length_tokens,
        context=ev.channel
    )
    lexicon_tracker().add_from_text(ev.content, source=ev.channel, context="msg_feedback")

def register():
    bus = event_bus()
    bus.subscribe(TradeOutcomeEvent, on_trade_outcome)
    bus.subscribe(MessageFeedbackEvent, on_message)
