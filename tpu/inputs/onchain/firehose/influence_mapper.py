# /firehose/influence_mapper.py

import logging

from inputs.onchain.firehose.wallet_insight import get_wallet_tags, is_known_influencer
from strategy.strategy_memory import tag_token_result


def map_wallet_influence(event: dict) -> dict:
    try:
        wallets = event.get("wallets", [])
        token = event.get("token", "unknown")
        influencer_hits = []
        tag_list = []

        for wallet in wallets:
            tags = get_wallet_tags(wallet)
            tag_list.extend(tags)

            if is_known_influencer(wallet):
                influencer_hits.append(wallet)

        if influencer_hits:
            event["influencer_alert"] = True
            event["influencer_wallets"] = influencer_hits
            tag_token_result(token, "influencer_flag", 90)

        if tag_list:
            event["wallet_tags"] = list(set(tag_list))

    except Exception as e:
        logging.warning(f"[InfluenceMapper] Failed to map wallets: {e}")

    return event

def get_influential_wallet_trigger(token: str, recent_txns: list) -> str | None:
    """
    Scans recent txns for known influencer or sniper wallets that bought the token.
    Returns the first influential wallet address if found, else None.
    """
    seen_wallets = set()

    for tx in recent_txns:
        if tx.get("token") != token:
            continue
        wallet = tx.get("signer")
        if not wallet or wallet in seen_wallets:
            continue
        seen_wallets.add(wallet)

        try:
            if is_known_influencer(wallet):
                log_event(f"🌟 Influential wallet {wallet[:6]}... found on {token}")
                return wallet
        except Exception as e:
            log_event(f"[InfluenceMapper] Error analyzing {wallet}: {e}")

    return None
