# /smart_token_group_analyzer.py

import asyncio
from datetime import datetime

from core.live_config import config
from cortex.meta_cortex import MetaCortex
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import get_tagged_tokens
from utils.logger import log_event
from utils.service_status import update_status
from utils.universal_input_validator import coerce_to_dict, coerce_to_list, log_validation_warning

ANALYSIS_INTERVAL = 60  # seconds
meta_cortex = MetaCortex(memory=librarian)

async def run():
    update_status("smart_token_group_analyzer")
    while True:
        await analyze_trending_groups()
        await asyncio.sleep(ANALYSIS_INTERVAL)

async def analyze_trending_groups():
    try:
        token_data = get_tagged_tokens()
        token_data = coerce_to_dict(token_data)

        group_scores = {}
        dynamic_groups = await get_dynamic_groups()

        for token, info in token_data.items():
            info = coerce_to_dict(info, "GroupAnalyzer.token_info")
            tags = coerce_to_list(info.get("tags", []), "GroupAnalyzer.tags")

            token_group = identify_group(tags, dynamic_groups)
            if not token_group:
                continue

            group_scores[token_group] = group_scores.get(token_group, 0) + 1

        for group, count in group_scores.items():
            strength = min(count, 10)

            # Inject into MetaCortex instead of AI brain
            try:
                await meta_cortex.inject_group_strength(group, strength)
            except Exception as e:
                log_event(f"⚠️ Failed to inject group score for {group}: {e}")

            log_event(f"📊 [GroupAnalyzer] Group '{group}' score: {strength}")
            log_scanner_insight("group", "neutral", strength, group)

    except Exception as e:
        log_event(f"⚠️ [GroupAnalyzer] Failed: {e}")

async def get_dynamic_groups():
    try:
        group_map = await librarian.get_group_map("group_keywords")
        if group_map and isinstance(group_map, dict):
            return group_map
    except Exception as e:
        log_event(f"⚠️ Failed to fetch group map from Librarian: {e}")

    return config.get("default_token_groups", {
        "elon": ["elon", "musk", "tesla", "spacex"],
        "doge": ["doge", "shiba", "dog", "floki"],
        "meta": ["meta", "vr", "ai", "metaverse"],
        "milady": ["milady", "coquette", "aesthetic", "edgelord"]
    })

def identify_group(tags, group_map):
    if not isinstance(group_map, dict) or not isinstance(tags, list):
        return None
    for group, keywords in group_map.items():
        for tag in tags:
            if isinstance(tag, str) and tag.lower() in keywords:
                return group
    return None

