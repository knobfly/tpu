import logging

from strategy.strategy_memory import get_tagged_tokens, tag_token_result
from utils.logger import log_event


class TagManager:
    """
    Central tag manager for tokens.
    """

    def __init__(self):
        self.manual_tags = {}

    def apply_manual_tag(self, token: str, tag: str, score: float = 0):
        try:
            tag_token_result(token, tag, score)
            self.manual_tags[token] = tag
            log_event(f"[TagManager] {token} manually tagged as {tag}")
        except Exception as e:
            logging.warning(f"[TagManager] Failed to tag {token}: {e}")

    def list_all_tags(self) -> str:
        tokens = get_tagged_tokens()
        if not tokens:
            return "No tagged tokens found."
        return "\n".join(tokens)

def get_tag_boost_score(tags: list[str]) -> float:
    """
    Scores tokens higher if they have bullish tags like 'ai', 'infrastructure', 'base', etc.
    """
    BOOST_TAGS = {"ai", "infra", "base", "zk", "narrative", "sol", "utility"}
    score = 0
    for tag in tags:
        if tag.lower() in BOOST_TAGS:
            score += 5
    return min(score, 20)  # cap the boost
