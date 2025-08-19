# inputs.onchain/firehose/stream_scanner.py

import asyncio
import logging

from cortex.core_router import handle_event
from cortex.event_queue import dequeue_event
from inputs.onchain.firehose.alpha_poster import post_firehose_alpha
from inputs.onchain.firehose.event_classifier import enrich_event_with_classification
from inputs.onchain.firehose.firehose_memory_tagger import tag_event_with_memory
from inputs.onchain.firehose.firehose_replay_buffer import store_event
from inputs.onchain.firehose.firehose_trace_logger import write_trace
from inputs.onchain.firehose.firehose_watchdog import update_event_heartbeat
from inputs.onchain.firehose.influence_mapper import map_wallet_influence
from inputs.onchain.firehose.nlp_event_summarizer import summarize_event
from inputs.onchain.firehose.sniper_trigger import try_sniper_trigger


async def stream_scanner_loop():
    logging.info("[StreamScanner] 🔁 Starting stream scanner loop")

    while True:
        event = await dequeue_event()
        if not event:
            continue

        try:
            # === Tag and enrich the event before processing ===
            event = enrich_event_with_classification(event)
            event = map_wallet_influence(event)
            event = tag_event_with_memory(event)
            event["summary"] = summarize_event(event)
            store_event(event)

            # === Dispatch to cortex ===
            await handle_event(event)

            # === Score and possibly trigger a trade ===
            score = await try_sniper_trigger(event)

            # === Post Alpha + Trace Log ===
            if score is not None:
                await post_firehose_alpha(event, score)
                write_trace(event, score=score, action="buy" if score >= 75 else "skipped")

            update_event_heartbeat()

        except Exception as e:
            logging.warning(f"[StreamScanner] Failed to process event: {e}")

