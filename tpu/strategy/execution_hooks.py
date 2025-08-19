import logging

from strategy.outcome_predictor import log_prediction
from strategy.reaction_router import handle_trade_result


def before_trade(token_address: str, prediction: str, reasoning: list):
    """
    Log the AI's prediction BEFORE executing the trade.
    """
    log_prediction(token_address, prediction, reasoning)
    logging.info(f"[ExecutionHooks] Logged pre-trade prediction for {token_address}: {prediction}")

def after_trade(result: dict):
    """
    Run post-trade reaction after the trade completes.
    """
    handle_trade_result(result)
