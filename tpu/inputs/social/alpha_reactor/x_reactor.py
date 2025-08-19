import logging

from inputs.social.alpha_reactor.signal_fusion import process_alpha_signals
from inputs.social.x_alpha.x_feed_scanner import analyze_x_post
from utils.logger import log_event


class XReactor:
    def __init__(self):
        self.enabled = True

    def handle_post(self, user: str, content: str, token: str):
        """
        Analyze X (Twitter) post and push alpha signal.
        """
        if not self.enabled:
            return

        try:
            sentiment_score, tags = analyze_x_post(content)
            signal_data = {
                "token": token,
                "sentiment": sentiment_score,
                "volume": len(content),
                "wallets": [],  # Future wallet mentions
                "confidence": sentiment_score * 1.1,
            }
            process_alpha_signals("x", signal_data)
            log_event(f"[XReactor] {user}: {content[:50]}...")
        except Exception as e:
            logging.warning(f"[XReactor] Failed to handle post: {e}")
