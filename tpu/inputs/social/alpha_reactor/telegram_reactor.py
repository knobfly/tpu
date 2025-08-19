import logging

from inputs.social.alpha_reactor.signal_fusion import process_alpha_signals
from inputs.social.telegram_nlp_listener import analyze_message
from utils.logger import log_event


class TelegramReactor:
    def __init__(self):
        self.enabled = True

    def handle_message(self, group_name: str, message: str, token: str):
        """
        Analyze Telegram message and push alpha signal.
        """
        if not self.enabled:
            return

        try:
            sentiment_score, keywords = analyze_message(message)
            signal_data = {
                "token": token,
                "sentiment": sentiment_score,
                "volume": len(message),
                "wallets": [],  # Future: Wallet mentions
                "confidence": sentiment_score * 1.2,
            }
            process_alpha_signals("telegram", signal_data)
            log_event(f"[TGReactor] {group_name}: {message[:50]}...")
        except Exception as e:
            logging.warning(f"[TGReactor] Failed to handle message: {e}")
