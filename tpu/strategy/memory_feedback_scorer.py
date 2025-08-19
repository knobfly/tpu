from memory.token_memory_index import append_token_tag
from strategy.reasoning_weights import reinforce_reasoning_tags
from strategy.risk_trait_expander import flatten_and_expand


def update_trait_memory_from_outcome(reasoning_blocks: list, outcome: str, token_address: str = None):
    """
    Updates the reinforcement weight memory using final trade outcome and traits.
    """
    expanded_tags = flatten_and_expand(reasoning_blocks)

    for tag in expanded_tags:
        reinforce_reasoning_tags([tag], outcome)

        # Optional: Store tag permanently in token memory for deep recall
        if token_address:
            append_token_tag(token_address, tag)
