import asyncio
import logging

from core.live_config import config
from inputs.trending.emit import emit_trend
from inputs.trending.sources import birdeye_new_tokens, dexscreener_new_pairs_solana
from utils.crash_guardian import beat


async def run_trending_fanin():
    cfg = config.get("trending", {})
    interval = int(cfg.get("dexscreener_interval_s", 45))
    be_interval = int(cfg.get("birdeye_interval_s", 45))
    token_cooldown = int(cfg.get("token_cooldown_s", 600))

    t = 0
    while True:
        # Heartbeat once per loop
        beat("TrendingFanIn")
        try:
            if cfg.get("enabled", True):
                # stagger the two calls so they don't align every tick
                if t % interval == 0:
                    for evt in await dexscreener_new_pairs_solana():
                        await emit_trend(evt, token_cooldown_s=token_cooldown)
                if t % be_interval == 0:
                    for evt in await birdeye_new_tokens():
                        await emit_trend(evt, token_cooldown_s=token_cooldown)
        except Exception as e:
            logging.warning(f"[TrendingFanIn] loop error: {e}")
        await asyncio.sleep(1)
        t += 1
