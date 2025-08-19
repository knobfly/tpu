import logging

from inputs.social.alpha_reactor.signal_fusion import get_recent_signals
from utils.logger import log_event


class AlphaRouter:
    def __init__(self):
        self.recent_signals = []

    def route_signals(self, token: str):
        """
        Return combined alpha confidence for scoring engines.
        """
        try:
            signals = [s for s in get_recent_signals() if s["token"] == token]
            total_conf = sum(s["confidence"] for s in signals)
            avg_conf = total_conf / len(signals) if signals else 0.0
            log_event(f"[AlphaRouter] {token} avg alpha conf={avg_conf:.2f}")
            return avg_conf
        except Exception as e:
            logging.warning(f"[AlphaRouter] Failed to route signals: {e}")
            return 0.0
