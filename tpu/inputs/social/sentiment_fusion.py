import logging
from datetime import datetime
from statistics import mean

from inputs.social.telegram_nlp_listener import fetch_group_sentiment
from inputs.social.x_alpha.x_feed_scanner import fetch_x_mentions
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.universal_input_validator import validate_token_record


class SentimentFusion:
    """
    Fuses social signals from Telegram, X (Twitter), and sentiment analyzers
    to produce a unified sentiment score.
    """

    def __init__(self):
        self.cache = {}

    def evaluate_token_sentiment(self, token_context: dict) -> dict:
        token_address = token_context.get("token_address")
        if not token_address:
            return {"sentiment_score": 0, "reason": "No token address"}

        log_event(f"[SentimentFusion] Evaluating social signals for {token_address}")

        # === Telegram sentiment
        telegram_score, telegram_keywords = self._analyze_telegram(token_address)

        # === X mentions sentiment
        x_score, x_keywords = self._analyze_x_mentions(token_context)

        # === Final sentiment fusion
        sentiment_score = round(mean([telegram_score, x_score]), 2)
        keywords = list(set(telegram_keywords + x_keywords))

        self.cache[token_address] = {
            "timestamp": datetime.utcnow().isoformat(),
            "sentiment_score": sentiment_score,
            "keywords": keywords
        }

        log_scanner_insight("social_fusion", token_context, {
            "sentiment_score": sentiment_score,
            "keywords": keywords
        })

        return {"sentiment_score": sentiment_score, "keywords": keywords}

    def _analyze_telegram(self, token_address: str) -> tuple:
        try:
            score_data = fetch_group_sentiment(token_address)
            score = score_data.get("score", 0)
            keywords = score_data.get("keywords", [])
            return score, keywords
        except Exception as e:
            logging.warning(f"[SentimentFusion] Telegram sentiment failed: {e}")
            return 0, []

    def _analyze_x_mentions(self, token_context: dict) -> tuple:
        try:
            symbol = token_context.get("symbol")
            mentions = fetch_x_mentions(symbol)
            sentiment_values = [m.get("sentiment_score", 0) for m in mentions]
            keywords = [m.get("keyword") for m in mentions if "keyword" in m]
            score = round(mean(sentiment_values), 2) if sentiment_values else 0
            return score, keywords
        except Exception as e:
            logging.warning(f"[SentimentFusion] X mentions analysis failed: {e}")
            return 0, []

    def get_cached_sentiment(self, token_address: str):
        return self.cache.get(token_address, None)
