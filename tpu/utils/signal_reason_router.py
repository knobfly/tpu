from inputs.social.group_reputation import update_group_score
from memory.token_memory_index import token_memory_index
from memory.wallet_memory_index import wallet_memory_index
from strategy.strategy_memory import tag_token_result, update_meta_keywords
from utils.logger import log_event


def route_decoded_signal(mint, source, reason, meta=None, chat_name=None):
    tags = []
    meta = meta or {}

    if "early" in reason or "just launched" in reason:
        tags.append("new_launch")
    if "degen" in reason or "gamble" in reason:
        tags.append("high_risk")
    if "community" in reason:
        tags.append("social_meta")
    if "celebrity" in reason:
        tags.append("influencer_meta")
    if "pre-pump" in reason:
        tags.append("potential_pump")

    for tag in tags:
        tag_token_result(mint, tag, score=0.5)

    token_memory_index.record(mint, "signal_reason", reason)
    update_meta_keywords(meta)

    if chat_name:
        wallet_memory_index.tag_chat_association(chat_name, mint)
        update_group_score(chat_name, delta=1)

    log_event(f"[ReasonRouter] Tagged {mint} with: {tags} from {source} — {reason}")
