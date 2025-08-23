import logging
from typing import Any, Dict
from defense.market_mood_tracker import get_current_meta_trend
from inputs.meta_data.meta_llm_analyzer import analyze_meta_description
from inputs.meta_data.tag_manager import get_tag_boost_score
from inputs.meta_data.theme_llm_cluster import get_cluster_score
from inputs.meta_data.token_holder_filter import get_holder_classification
from inputs.meta_data.token_metadata_fetcher import fetch_token_metadata
from inputs.meta_data.token_theme_profiler import get_token_theme_profile
from librarian.data_librarian import librarian
from memory.reinforced_trait_reweighter import get_trait_confidence_boost
from memory.token_memory_index import update_token_meta_memory


class MetaCortex:
    def __init__(self, memory):
        self.memory = memory

    # --- Supervisor Integration Hooks ---
    def receive_chart_signal(self, token_context: dict, chart_insights: dict):
        """
        Receive chart signals broadcast from supervisor or other modules.
        Can be used to update meta scoring or trigger analytics.
        """
        print(f"[MetaCortex] Received chart signal for {token_context.get('token_address')}: {chart_insights}")

    def update_persona_context(self, persona_context: dict):
        """
        Receive persona context updates for adaptive meta logic.
        """
        print(f"[MetaCortex] Persona context updated: {persona_context}")

    def receive_analytics_update(self, update: dict):
        """
        Receive analytics/state updates for unified decision-making.
        """
        print(f"[MetaCortex] Analytics update received: {update}")

    def contribute_features(self, token_context: dict) -> dict:
        """
        Contribute meta-derived features for cross-module analytics.
        """
        insights = self.analyze_meta(token_context)
        return {
            "meta_score": insights.get("meta_score", 0.0),
            "cluster_score": insights.get("cluster_score", 0.0),
            "alignment_score": insights.get("alignment_score", 0.0),
        }

    def analyze_meta(self, token_data: dict) -> dict:
        token_address = token_data.get("token_address")
        token_name = token_data.get("token_name")

        if not token_address or not token_name:
            return {
                "theme": {},
                "cluster_score": 0,
                "meta_trend": "unknown",
                "alignment_score": 0,
                "tag_boost": 0,
                "trait_boost": 0,
                "holder_class": "unknown",
                "trending": False,
                "meta_score": 0
            }

        # === Core meta dimensions
        try:
            theme_data = get_token_theme_profile(token_name, token_address)
        except:
            theme_data = {}

        try:
            cluster_score = get_cluster_score(theme_data)
        except:
            cluster_score = 0

        try:
            meta_trend = get_current_meta_trend()
        except:
            meta_trend = "unknown"

        try:
            alignment_score = analyze_meta_description(theme_data, meta_trend)
        except:
            alignment_score = 0

        # === Optional layers
        try:
            metadata = fetch_token_metadata(token_address)
        except:
            metadata = {}

        try:
            tag_boost = get_tag_boost_score(token_address)
        except:
            tag_boost = 0

        try:
            trait_boost = get_trait_confidence_boost(theme_data)
        except:
            trait_boost = 0

        try:
            holder_class = get_holder_classification(token_address)
        except:
            holder_class = "unknown"

        holder_adjust = 0
        if holder_class == "degen":
            holder_adjust = -3
        elif holder_class == "solid":
            holder_adjust = 2


        # === Score fusion
        meta_score = (
            cluster_score +
            alignment_score +
            tag_boost +
            trait_boost +
            holder_adjust +
            trending_boost
        )

        # === Memory save
        try:
            update_token_meta_memory(token_address, {
                "token_name": token_name,
                "theme": theme_data,
                "cluster_score": cluster_score,
                "meta_trend": meta_trend,
                "alignment_score": alignment_score,
                "tag_boost": tag_boost,
                "trait_boost": trait_boost,
                "holder_class": holder_class,
                "trending": trending,
                "meta_score": round(meta_score, 2)
            })
        except:
            pass

        return {
            "theme": theme_data,
            "cluster_score": cluster_score,
            "meta_trend": meta_trend,
            "alignment_score": alignment_score,
            "tag_boost": tag_boost,
            "trait_boost": trait_boost,
            "holder_class": holder_class,
            "trending": trending,
            "meta_score": round(meta_score, 2)
        }

def score_token_with_meta(ctx: Dict[str, Any]) -> float:
    """
    Calculate a meta score for a token using theme tags, volume trends, holder traits, etc.
    This is not the final trade score, just a sub-component based on metadata.

    Args:
        ctx (dict): Full token context with optional fields like traits, tags, volume, holders

    Returns:
        float: meta score between 0 and 100
    """
    try:
        score = 0.0

        # Tag-based boost
        tags = ctx.get("tags", [])
        for tag in tags:
            boost = librarian.get_tag_boost(tag)
            score += boost

        # Trait confidence
        traits = ctx.get("traits", [])
        for trait in traits:
            boost = librarian.get_trait_confidence(trait)
            score += boost

        # Token age penalty
        age = ctx.get("age_minutes", 0)
        if age > 60:
            score *= 0.95
        if age > 360:
            score *= 0.85

        return round(min(score, 100.0), 2)

    except Exception as e:
        logging.warning(f"[MetaCortex] Failed to score token with meta: {e}")
        return 0.0

def assess_confidence(token: str, metadata: dict, score: float = 0.0) -> float:
    """
    Estimate confidence level based on token metadata and score.
    Returns float between 0.0 and 10.0.
    """
    if not metadata:
        return 0.0

    base = 2.5

    if metadata.get("is_verified"):
        base += 1.0

    holders = metadata.get("holders", 0)
    if holders >= 500:
        base += 2.0
    elif holders >= 100:
        base += 1.0

    twitter = metadata.get("twitter_followers", 0)
    if twitter >= 10000:
        base += 2.0
    elif twitter >= 1000:
        base += 1.0

    website = metadata.get("website") or ""
    if website and "https://" in website:
        base += 0.5

    if score > 5.0:
        base += 1.0

    return min(base, 10.0)

def is_overlap_candidate(token_tags: list[str]) -> bool:
    """
    Determines if a token has characteristics that may indicate a meta or overlap candidate.
    Usually called during early alpha analysis.
    """
    keywords = {"ai", "bot", "modular", "infra", "meta", "tool", "aggregator", "platform", "router"}
    if not token_tags:
        return False
    return any(kw in tag.lower() for tag in token_tags for kw in keywords)
