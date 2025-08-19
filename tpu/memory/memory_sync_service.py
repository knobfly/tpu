import asyncio

from memory.token_confidence_engine import update_token_confidence
from memory.token_memory_index import token_memory_index
from strategy.strategy_memory import get_strategy_performance, get_tagged_tokens
from utils.crash_guardian import crash_guardian
from utils.logger import log_event
from utils.universal_input_validator import coerce_to_dict
from cortex.core_router import handle_event
MEMORY_SYNC_INTERVAL_SEC = 60

async def run_memory_sync():
    log_event("🔁 Memory Sync Service running...")
    while True:
        try:
            tagged = coerce_to_dict(get_tagged_tokens())
            perf = coerce_to_dict(get_strategy_performance())

            for token, tags in tagged.items():
                score = 0
                if "new_launch" in tags: score += 0.05
                if "alpha_mention" in tags: score += 0.1
                if "high_risk" in tags: score -= 0.1

                outcome = perf.get(token)
                if outcome:
                    score += 0.1 if outcome["result"] == "win" else -0.1

                update_token_confidence(token, score, source="strategy_sync")
                token_memory_index.record(token, "memory_sync_score", score)
                await handle_event({"token": mint, "action": "refresh", "source": "memory_sync"})

            log_event(f"[MemorySync] Synced {len(tagged)} tokens to confidence layer")
            crash_guardian.beat("MemorySync")
        except Exception as e:
            log_event(f"💥 MemorySync iteration failed: {e}")
        await asyncio.sleep(MEMORY_SYNC_INTERVAL_SEC)
