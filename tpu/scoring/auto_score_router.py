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

    # --- ML prediction blending for routing ---
    ml_price_pred = token_context.get("ml_price_pred")
    ml_rug_pred = token_context.get("ml_rug_pred")
    ml_wallet_pred = token_context.get("ml_wallet_pred")

    # If ML price prediction is high and rug risk is low, prefer trade engine
    if ml_price_pred is not None and ml_rug_pred is not None:
        if float(ml_price_pred) > 0.7 and float(ml_rug_pred) < 0.3:
            logging.info(f"📈 ML Routed to: TRADE | ML price: {ml_price_pred} | ML rug: {ml_rug_pred}")
            result = evaluate_trade(token_context)
            if isinstance(result, dict):
                result["ml_routing"] = "trade"
            return result
        elif float(ml_rug_pred) > 0.7:
            logging.info(f"⚡ ML Routed to: SNIPE (high rug risk) | ML rug: {ml_rug_pred}")
            result = evaluate_snipe(token_context)
            if isinstance(result, dict):
                result["ml_routing"] = "snipe"
            return result

    if (
        age_minutes < ROUTE_LOGIC["min_age_for_trade"] or
        buyer_count < ROUTE_LOGIC["min_buyer_count"] or
        social_mentions < ROUTE_LOGIC["min_social_mentions"]
    ):
        logging.info(f"⚡ Routed to: SNIPE | Age: {age_minutes}m | Buyers: {buyer_count} | Mentions: {social_mentions}")
        result = evaluate_snipe(token_context)
        if isinstance(result, dict):
            result["ml_routing"] = "snipe"
        return result
    else:
        logging.info(f"📈 Routed to: TRADE | Age: {age_minutes}m | Buyers: {buyer_count} | Mentions: {social_mentions}")
        result = evaluate_trade(token_context)
        if isinstance(result, dict):
            result["ml_routing"] = "trade"
        return result
