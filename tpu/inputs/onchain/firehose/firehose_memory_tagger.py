# modules/firehose/firehose_memory_tagger.py

import logging

from inputs.onchain.firehose.wallet_insight import get_wallet_cluster_score, get_wallet_tags
from strategy.strategy_memory import get_token_tags, is_blacklisted_token


def tag_event_with_memory(event: dict) -> dict:
    try:
        token = event.get("token", None)
        wallets = event.get("wallets", [])
        token_tags = get_token_tags(token) if token else []

        if is_blacklisted_token(token):
            event["memory_blacklisted"] = True
            event["score_penalty"] = 100
            return event  # Skip further tagging

        # Add token memory traits
        if token_tags:
            event["memory_tags"] = list(set(token_tags))

        # Add wallet memory tags
        wallet_tags_combined = []
        cluster_score_total = 0

        for wallet in wallets:
            tags = get_wallet_tags(wallet)
            wallet_tags_combined.extend(tags)
            cluster_score_total += get_wallet_cluster_score(wallet)

        if wallet_tags_combined:
            event["wallet_memory_tags"] = list(set(wallet_tags_combined))

        if cluster_score_total:
            event["wallet_cluster_score"] = cluster_score_total / max(1, len(wallets))

    except Exception as e:
        logging.warning(f"[MemoryTagger] Failed to tag event: {e}")

    return event
