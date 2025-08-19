# /memory_merge_engine.py

from memory.token_memory_index import token_memory_index
from memory.wallet_memory_index import wallet_memory_index
from strategy.strategy_memory import tag_token_result


def reinforce_success(token, wallet, profit):
    if profit > 0:
        token_memory_index.record(token, "win", profit=profit)
        wallet_memory_index.record(wallet, token, "bought_early", "win")
        tag_token_result(token, "profitable")
    else:
        token_memory_index.record(token, "loss", profit=profit)
        wallet_memory_index.record(wallet, token, "bought_early", "loss")
        tag_token_result(token, "losing")

    return token_memory_index.get_stats(token)
