import logging

from cortex.event_queue import enqueue_event_for_scoring
from inputs.nft.nft_signal_scanner import nft_event_handler  # ✅ Added NFT routing
from inputs.onchain.firehose.event_converter import convert_block_to_events
from inputs.onchain.firehose.firehose_filter_engine import filter_event
from inputs.onchain.firehose.firehose_health_monitor import (
    firehose_health_monitor,  # ✅ Added health monitor import
)
from inputs.wallet.wallet_stream_router import route_wallet_event
from special.insight_logger import log_ai_insight

_handlers = {}

def register_event_handler(event_type: str, coro):
    _handlers.setdefault(event_type, []).append(coro)

async def _dispatch_event(event):
    for coro in _handlers.get(event.get("event_type", ""), []):
        await coro(event)

async def route_event(decoded_block: dict):
    """
    Routes all firehose events to the cortex pipeline, wallet stream router, and NFT scanner.
    """
    if not decoded_block:
        return

    try:
        events = convert_block_to_events(decoded_block)

        for event in events:
            # Basic filter
            if not filter_event(event):
                continue

            # ✅ Record activity for health monitor
            firehose_health_monitor.record_event_heartbeat()

            # Log insight for debugging/analysis
            log_ai_insight("📦 Firehose Event", event)

            # Wallet routing
            await route_wallet_event(event)

            # NFT routing (if event looks like an NFT mint/transfer)
            description = (event.get("description") or "").lower()
            if "nft" in description or "mint" in description or "collection" in description:
                await nft_event_handler(event)

            # Send to scoring/cortex pipeline
            await enqueue_event_for_scoring(event)

    except Exception as e:
        logging.error(f"[StreamRouter] Routing failed: {e}")
