from strategy.reasoning_weights import get_reasoning_bias
from strategy.risk_trait_expander import flatten_and_expand


def reweight_reasoning(reasoning_blocks: list) -> dict:
    """
    Applies reinforcement-based score adjustments based on known trait outcomes.

    Returns:
        {
            "adjusted_total": 6,
            "adjustments": {
                "bundle": -3,
                "creator_overlap": -2,
                "whale": +4
            }
        }
    """
    expanded = flatten_and_expand(reasoning_blocks)
    adjustments = {}
    total = 0

    for tag in expanded:
        score = get_reasoning_bias(tag)
        if score == 0:
            continue
        adjustments[tag] = score
        total += score

    return {
        "adjusted_total": total,
        "adjustments": adjustments
    }
