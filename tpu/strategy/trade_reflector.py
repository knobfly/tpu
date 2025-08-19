from strategy.reasoning_chain_memory import get_high_value_chains, get_risky_chains
from strategy.reasoning_memory import summarize_reasoning
from strategy.reinforcement_tracker import get_recent_outcomes


def reflect_on_trade(result: dict) -> str:
    """
    Creates a reflective summary of the trade.

    result = {
        "token": "LUNA",
        "token_address": "...",
        "final_score": 72,
        "reasoning": ["celeb", "whale", "chart"],
        "signals": {...},
        "outcome": "profit",
        ...
    }
    """
    token = result.get("token", "Unknown")
    address = result.get("token_address")
    score = result.get("final_score", 0)
    reasoning = result.get("reasoning", [])
    outcome = result.get("outcome", "unknown")

    if not address or not reasoning:
        return f"No meaningful reflection available for {token}."

    summary = summarize_reasoning(address)
    history = get_recent_outcomes(address)
    winrate = history.get("win_rate", 0)

    high_chains = get_high_value_chains()
    risky_chains = get_risky_chains()

    # Tag analysis
    tag_summary = []
    for tag in reasoning:
        tag_score = summary["win_keys"].get(tag, 0) - summary["fail_keys"].get(tag, 0)
        tag_summary.append(f"- `{tag}` (impact score: {tag_score})")

    # Chain detection
    chain_key = "|".join(sorted(set(reasoning)))
    chain_comment = ""
    for k, ratio, total in high_chains:
        if k == chain_key:
            chain_comment = f"✅ This tag chain has historically led to profit ({ratio*100:.1f}% win rate)"
            break
    for k, ratio, total in risky_chains:
        if k == chain_key:
            chain_comment = f"⚠️ This chain has a high rug/loss rate ({(1 - ratio)*100:.1f}% fail rate)"
            break

    reflection = f"""🎯 *Trade Reflection: {token}*

📈 Final Score: *{score}*
🧠 Reasoning Tags:
{chr(10).join(tag_summary)}

📊 Outcome: *{outcome}*
💭 Historical Winrate on token: *{winrate:.1f}%*

{chain_comment or "ℹ️ No strong historical pattern for this tag combo."}

🤔 Would I take this trade again? {'✅ Yes' if score >= 50 else '⚠️ Unlikely'}

---
"""

    return reflection
