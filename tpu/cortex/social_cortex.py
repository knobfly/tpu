from inputs.social.alpha_reactor.signal_fusion import get_signal_reactivity_score
from inputs.social.group_reputation import get_group_quality_score
from inputs.social.influencer_scanner import get_influencer_impact
from inputs.social.sentiment_analyzer import analyze_sentiment
from inputs.social.telegram_group_listener import get_token_mention_count
from inputs.social.x_alpha.x_alpha_brain import get_recent_x_mentions
from inputs.social.x_alpha.x_alpha_brain_adapter import detect_alpha_overlap
from memory.token_memory_index import update_token_social_memory
from utils.keyword_tools import extract_keywords


class SocialCortex:
    def __init__(self, memory):
        self.memory = memory

    def analyze_sentiment(self, token_data: dict) -> dict:
        token_address = token_data.get("token_address")
        token_name = token_data.get("token_name")
        text_blobs = token_data.get("social_text", [])
        mode = token_data.get("mode", "trade")

        if not token_address:
            return {
                "sentiment": "neutral",
                "mention_count": 0,
                "keywords": [],
                "social_score": 0,
                "influencer_boost": 0,
                "group_score": 0,
                "alpha_overlap": False,
                "reactivity_score": 0
            }

        sentiment_score = 0
        keywords = []
        sentiment_label = "neutral"

        # === Full sentiment only in trade mode ===
        if text_blobs and mode == "trade":
            sentiment_summary = analyze_sentiment(text_blobs)
            sentiment_label = sentiment_summary.get("label", "neutral")
            sentiment_score = sentiment_summary.get("score", 0)
            keywords = extract_keywords(text_blobs)
        elif text_blobs:
            sentiment_summary = analyze_sentiment(text_blobs[:3])  # Fast mode
            sentiment_label = sentiment_summary.get("label", "neutral")
            sentiment_score = sentiment_summary.get("score", 0)

        # === Mention pulse
        try:
            tg_mentions = get_token_mention_count(token_address)
        except:
            tg_mentions = 0

        try:
            x_mentions = get_recent_x_mentions(token_address)
        except:
            x_mentions = 0

        total_mentions = tg_mentions + x_mentions

        # === Influencer impact
        try:
            influencer_boost = get_influencer_impact(token_address)
        except:
            influencer_boost = 0

        # === Group reputation scoring
        try:
            group_score = get_group_quality_score(token_address)
        except:
            group_score = 0

        # === Alpha overlap
        try:
            alpha_data = detect_alpha_overlap(token_address)
            alpha_overlap = alpha_data.get("hot", False)
        except:
            alpha_data = {}
            alpha_overlap = False

        # === Signal reactivity
        try:
            reactivity_score = get_signal_reactivity_score(token_address)
        except:
            reactivity_score = 0

        # === Fusion score
        social_score = sentiment_score + total_mentions + influencer_boost + group_score + reactivity_score
        if alpha_overlap:
            social_score += 4
            keywords.append("alpha_overlap")

        # === Memory writeback
        try:
            update_token_social_memory(token_address, {
                "token_name": token_name,
                "sentiment": sentiment_label,
                "score": sentiment_score,
                "tg_mentions": tg_mentions,
                "x_mentions": x_mentions,
                "total_social_score": social_score
            })
        except:
            pass

        return {
            "sentiment": sentiment_label,
            "mention_count": total_mentions,
            "keywords": keywords,
            "social_score": round(social_score, 2),
            "influencer_boost": influencer_boost,
            "group_score": group_score,
            "alpha_overlap": alpha_overlap,
            "reactivity_score": reactivity_score
        }
