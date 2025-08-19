# /theme_llm_cluster.py

import logging
from collections import defaultdict
from datetime import datetime

from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight


def detect_trending_clusters() -> dict:
    """Detect trending theme clusters using librarian-sourced meta + cluster data."""
    try:
        meta_data = librarian.get("meta_trends") or {}
        cluster_data = librarian.get("cluster_stats") or {}
    except Exception as e:
        logging.warning(f"[LLM Cluster] ⚠️ Failed to fetch cluster/meta data: {e}")
        return {}

    cluster_score = defaultdict(int)
    for cluster, stat in cluster_data.items():
        count = stat.get("count", 0)
        bonus = stat.get("bonus", 0)
        score = count * 2 + bonus
        if score > 0:
            cluster_score[cluster] += score

    trending_clusters = sorted(cluster_score.items(), key=lambda x: x[1], reverse=True)[:5]
    summary = ", ".join([f"{c}({s})" for c, s in trending_clusters])

    insight = {
        "timestamp": datetime.utcnow().isoformat(),
        "top_themes": [c for c, _ in trending_clusters],
        "scores": dict(trending_clusters),
        "summary": summary
    }

    log_scanner_insight({
        "source": "theme_llm_cluster",
        "insight": f"🔥 Trending Meta Themes: {summary}",
        "clusters": insight["top_themes"]
    })

    logging.info(f"[LLM Cluster] Detected themes: {summary}")
    return insight

def get_cluster_score(cluster_name: str) -> int:
    """
    Returns the score for a given cluster name, using the same logic as detect_trending_clusters().
    """
    try:
        cluster_data = librarian.get("cluster_stats") or {}
        stat = cluster_data.get(cluster_name, {})
        count = stat.get("count", 0)
        bonus = stat.get("bonus", 0)
        return count * 2 + bonus
    except Exception as e:
        logging.warning(f"[LLM Cluster] Failed to get score for cluster {cluster_name}: {e}")
        return 0
