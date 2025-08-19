# modules/meta_llm_analyzer.py

import logging
import re

from openai import AsyncOpenAI

import json
from datetime import datetime
from typing import Optional

from core.live_config import config
from inputs.meta_data.meta_clusters import meta_clusters
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import update_meta_keywords
from utils.logger import log_event
from utils.service_status import update_status
aclient = AsyncOpenAI(api_key=config.get("openai_api_key"))

# === Persistent theme scoring memory ===
theme_tag_scores = {}

# === GPT-4 Meta Profiler ===
async def analyze_meta_description(name: str, symbol: str, description: str = "") -> dict:
    update_status("meta_llm_analyzer")
    prompt = f"""
You are a crypto meta analyst. Given the following token info, classify it across multiple traits:

Token Name: {name}
Token Symbol: {symbol}
Token Description: {description}

Return a JSON object with:
- theme: One of meme, ai, gaming, defi, scam, nft, tool, social, pumpfun, unknown
- personality: A short label (e.g. elon clone, edgy meme, hype bait, organic)
- vibes: A short summary of the token's energy or vibe (1 sentence max)
- suspicious: true/false based on rug-like characteristics
- keywords: list of extracted important words
- meta_tags: list of cluster-style labels for scoring (e.g. elon, pepe, doge, community, etc)

Respond with JSON only. No explanation.
"""
    try:
        logging.info(f"[MetaLLM] Analyzing token: {name} / {symbol}")
        response = await aclient.chat.completions.create(model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4)
        result = response.choices[0].message.content.strip()
        json_str = result[result.find("{"): result.rfind("}") + 1]
        parsed = json.loads(json_str)

        log_event(f"[MetaLLM] ✅ LLM profile: {parsed}")
        log_scanner_insight("meta_llm", {
            "name": name,
            "symbol": symbol,
            "tags": parsed.get("meta_tags", []),
            "theme": parsed.get("theme"),
            "vibe": parsed.get("vibes")
        })

        update_theme_scores(parsed.get("meta_tags", []), parsed.get("theme"))
        return parsed

    except Exception as e:
        logging.warning(f"[MetaLLM] ❌ Failed GPT analysis: {e}")
        return fallback_cluster_analysis(name, symbol, description)

# === Fallback Pattern Classifier (LLM-style) ===
def fallback_cluster_analysis(name: str, symbol: str, creator: str = "") -> dict:
    text = f"{name} {symbol} {creator}".lower()
    matched_clusters = []

    for cluster, keywords in meta_clusters.items():
        if any(kw in text for kw in keywords):
            matched_clusters.append(cluster)

    extracted_keywords = re.findall(r"\b[a-zA-Z]{3,12}\b", text)
    keyword_freq = {}
    for word in extracted_keywords:
        keyword_freq[word] = keyword_freq.get(word, 0) + 1

    confidence = 50
    for word in keyword_freq:
        if word in config.get("keyword_boosts", {}):
            confidence += min(10, keyword_freq[word] * 2)
    for cluster in matched_clusters:
        confidence += 5
        update_theme_scores([cluster])

    confidence = max(0, min(confidence, 100))

    insight = {
        "theme": "unknown",
        "personality": "fallback",
        "vibes": "fallback classification used",
        "suspicious": False,
        "keywords": list(keyword_freq.keys()),
        "meta_tags": matched_clusters,
        "confidence": round(confidence, 2),
        "summary": f"LLM fallback matched {len(matched_clusters)} clusters",
        "timestamp": datetime.utcnow().isoformat()
    }

    log_event(f"[MetaLLM Fallback] 🔎 {name}/{symbol} matched {matched_clusters} with confidence {confidence}")
    log_scanner_insight("meta_llm_fallback", insight)
    update_meta_keywords(name)

    return insight

# === Theme Tag Learning ===
def update_theme_scores(tags: list, theme: Optional[str] = None):
    for tag in tags:
        tag = tag.lower()
        score = theme_tag_scores.get(tag, 1.0)
        score += 0.2
        theme_tag_scores[tag] = round(min(score, 10.0), 2)

    if theme:
        theme_tag_scores[theme] = round(min(theme_tag_scores.get(theme, 1.5) + 0.3, 10.0), 2)

# === Quick Summary Classifier ===
def simple_meta_summary(text: str) -> str:
    if not text:
        return "unknown"
    t = text.lower()
    if any(w in t for w in ["pepe", "elon", "doge", "trump"]):
        return "meme"
    if "ai" in t:
        return "ai"
    if "game" in t:
        return "gaming"
    if "pump.fun" in t:
        return "pumpfun"
    return "unknown"

# === Register Memory ===
from librarian.data_librarian import librarian

librarian.register("meta_llm", {
    "meta_clusters": meta_clusters,
    "tag_scores": theme_tag_scores
})
