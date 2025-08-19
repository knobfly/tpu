from strategy.reinforced_trait_reweighter import reweight_reasoning
from strategy.score_cluster_embedder import load_cluster_memory, tag_meta_cluster


def apply_memory_adjustment(score: float, reasons: list[str], context: dict) -> float:
    """
    Adjusts the input score using:
    - Reasoning weights (tag-based reinforcement)
    - Cluster bias (pattern-level memory)
    """
    adjustment = 0

    # === Reasoning weights
    for reason in reasons:
        reason_score = reweight_reasoning(reason)
        if reason_score:
            adjustment += reason_score * 0.1  # Modest influence per reason

    # === Cluster bias
    try:
        clusters = load_cluster_memory()
        cluster = tag_meta_cluster(context)
        if cluster in clusters:
            cluster_data = clusters[cluster][-50:]  # Focus on recent patterns
            if cluster_data:
                avg_score = sum([entry["score"] for entry in cluster_data]) / len(cluster_data)
                score_bias = (avg_score - 50) * 0.05  # Bias = relative to neutral
                adjustment += score_bias
    except Exception:
        pass

    final = max(0, min(score + adjustment, 100))
    return round(final, 2)
