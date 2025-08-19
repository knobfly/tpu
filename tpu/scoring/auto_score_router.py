import logging

from scoring.snipe_score_engine import evaluate_snipe
from scoring.trade_score_engine import evaluate_trade
from utils.time_utils import get_token_age_minutes
from utils.wallet_helpers import count_unique_buyers

ROUTE_LOGIC = {
    "min_age_for_trade": 6,         # minutes
    "min_buyer_count": 10,
    "min_social_mentions": 5
}

def route_score_engine(token_context: dict) -> dict:
    token_name = token_context.get("token_name", "unknown")
    token_address = token_context.get("token_address")
    logging.info(f"📊 Auto-routing token scoring: {token_name} | {token_address}")

    age_minutes = get_token_age_minutes(token_context)
    buyer_count = count_unique_buyers(token_context)
    social_mentions = token_context.get("social_mentions", 0)

    if (
        age_minutes < ROUTE_LOGIC["min_age_for_trade"] or
        buyer_count < ROUTE_LOGIC["min_buyer_count"] or
        social_mentions < ROUTE_LOGIC["min_social_mentions"]
    ):
        logging.info(f"⚡ Routed to: SNIPE | Age: {age_minutes}m | Buyers: {buyer_count} | Mentions: {social_mentions}")
        return evaluate_snipe(token_context)
    else:
        logging.info(f"📈 Routed to: TRADE | Age: {age_minutes}m | Buyers: {buyer_count} | Mentions: {social_mentions}")
        return evaluate_trade(token_context)
