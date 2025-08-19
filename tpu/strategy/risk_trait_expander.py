import json
import os

TRAIT_MAP_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/trait_relationships.json"

DEFAULT_TRAIT_MAP = {
    "bundle": ["creator_overlap", "multi_token_deploy", "same_launch_pattern"],
    "honeypot": ["high_tax", "fee_obfuscation", "no_sells", "snipe_block"],
    "rug": ["sudden_lp_pull", "zero_buyer_return", "team_dump"],
    "moon": ["early_dip_buy", "top_wallet_conviction", "sniper_followthrough"],
    "dead": ["low_txn_velocity", "buyer_dropout", "early_exit_meta"]
}

def load_trait_map():
    if not os.path.exists(TRAIT_MAP_FILE):
        return DEFAULT_TRAIT_MAP
    try:
        with open(TRAIT_MAP_FILE, "r") as f:
            return json.load(f)
    except:
        return DEFAULT_TRAIT_MAP

def expand_traits(input_tags: list[str]) -> list[str]:
    """
    Expands given tags to include related traits.
    Example: ["bundle"] → ["bundle", "creator_overlap", "multi_token_deploy", ...]
    """
    trait_map = load_trait_map()
    expanded = set(input_tags)

    for tag in input_tags:
        related = trait_map.get(tag, [])
        expanded.update(related)

    return list(expanded)

def flatten_and_expand(reasoning_blocks: list) -> list[str]:
    """
    Accepts raw reasoning blocks like:
    [{"chart_score": 5, "details": {...}}, {"wallet_score": 3, "details": {...}}]
    Extracts top-level keys and expands them into related traits.
    """
    flat_tags = []
    for block in reasoning_blocks:
        flat_tags.extend([k for k in block if k != "details"])
    return expand_traits(flat_tags)
