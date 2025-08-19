import logging
from datetime import datetime

from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import tag_token_result, update_meta_keywords
from utils.logger import log_event


class TokenMetaLearner:
    """
    Learns and tags tokens based on observed behavior, scanner inputs,
    and firehose events.
    """

    def __init__(self):
        self.token_profiles = {}

    def record_token_activity(self, token: str, event: str, meta: dict):
        """
        Record raw activity and update meta keywords.
        """
        try:
            self.token_profiles.setdefault(token, {"events": []})
            self.token_profiles[token]["events"].append({
                "event": event,
                "meta": meta,
                "timestamp": datetime.utcnow().isoformat()
            })
            log_event(f"[MetaLearner] {token}: {event} → {meta}")
            keywords = meta.get("keywords", [])
            if keywords:
                update_meta_keywords(token, keywords)
            log_scanner_insight("meta_learner", token, meta)
        except Exception as e:
            logging.warning(f"[MetaLearner] Failed to record token activity: {e}")

    def score_token_behavior(self, token: str) -> float:
        """
        Assign a confidence score based on frequency & quality of events.
        """
        try:
            events = self.token_profiles.get(token, {}).get("events", [])
            if not events:
                return 0.0
            score = sum(1.0 for e in events if "positive" in e["meta"].get("tags", []))
            score -= sum(0.5 for e in events if "warning" in e["meta"].get("tags", []))
            log_event(f"[MetaLearner] {token} scored {score:.2f}")
            return score
        except Exception as e:
            logging.warning(f"[MetaLearner] Failed to score token {token}: {e}")
            return 0.0

    def auto_tag(self, token: str):
        """
        Apply auto-tags based on observed activity.
        """
        try:
            score = self.score_token_behavior(token)
            if score >= 3:
                tag_token_result(token, "meta_bullish", score)
            elif score <= -1:
                tag_token_result(token, "meta_risky", score)
        except Exception as e:
            logging.warning(f"[MetaLearner] Auto-tagging failed: {e}")
