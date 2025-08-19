import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List

from core.live_config import config
from inputs.wallet.wallet_core import WalletManager
from special.insight_logger import log_scanner_insight, log_trade_insight
from strategy.strategy_memory import record_result
from utils.logger import log_event
from utils.service_status import update_status
from utils.social_sources import extract_token_mentions, fetch_recent_posts
from utils.telegram_utils import send_telegram_message
from utils.token_utils import get_token_category, is_blacklisted_token, tag_token_result

INFLUENCER_LOG_PATH = "/home/ubuntu/nyx/runtime/logs/influencer_mentions.json"  # Adjust path if needed


INFLUENCER_KEYWORDS = ["🔥", "💰", "🚀", "$", "#", "live", "now live", "pump", "charting", "dropping"]
TRUSTED_INFLUENCERS = ["@TrustedCallerX", "@AlphaShiller", "0xInfluencerWallet1", "0xInfluencerWallet2"]

MIN_SOCIAL_SCORE = 50
MIN_BUY_SCORE = 70
CHECK_INTERVAL = 30
MENTION_WINDOW_MINUTES = 5

seen_posts = set()

class InfluencerSignalTracker:
    def __init__(self):
        self.influencer_signals = []

    def add_signal(self, influencer: str, token: str, sentiment: float):
        try:
            signal = {
                "influencer": influencer,
                "token": token,
                "sentiment": sentiment,
                "timestamp": datetime.utcnow().isoformat()
            }
            self.influencer_signals.append(signal)
            log_event(f"[InfluencerSignal] {influencer} → {token} (sent={sentiment:.2f})")
        except Exception as e:
            logging.warning(f"[InfluencerSignal] Failed to add signal: {e}")

    def get_signal_boost(self, token: str) -> float:
        token_signals = [s for s in self.influencer_signals if s["token"] == token]
        if not token_signals:
            return 0.0
        avg_sent = sum(s["sentiment"] for s in token_signals) / len(token_signals)
        return avg_sent * 2.0


class InfluencerScanner:
    def __init__(self, wallet: WalletManager, executor, telegram=None):
        self.wallet = wallet
        self.executor = executor
        self.tg = telegram
        self.cache = set()
        self.influencer_signals = []

    async def start(self):
        log_event("📣 Influencer scanner running...")
        while True:
            try:
                update_status("influencer_scanner")
                await self.scan_tokens()
                await self.scan_recent_posts()
            except Exception as e:
                logging.error(f"[InfluencerScanner] Error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def scan_tokens(self):
        from scoring.scoring_engine import score_token
        trending = config.get("trending_token_list", [])
        for token in trending:
            if token in self.cache or is_blacklisted_token(token):
                continue

            social_data = await fetch_social_data(token)
            social_score = self.analyze_social_signal(token, social_data)
            if social_score < MIN_SOCIAL_SCORE:
                continue

            score_result = await score_token(token, config, self.wallet, override_filters=True)
            buy_score = score_result.get("score", 0)
            token_meta = score_result.get("metadata", {})
            token_type = get_token_category(token_meta)
            token_keywords = token_meta.get("keywords", [])

            if buy_score < MIN_BUY_SCORE:
                continue

            tag_token_result(token, "influencer")
            self.cache.add(token)

            log_event(f"📢 Influencer snipe: {token} (social={social_score}, score={buy_score})")
            log_scanner_insight(token, "influencer", sentiment=0.8, volume=0.0, result="buy")

            if self.tg:
                await self.tg.send_message(
                    f"📢 *Influencer Signal Triggered!*\n"
                    f"Token: `{token}`\nSocial Score: `{social_score}`\nBuy Score: `{buy_score}`\nCategory: `{token_type}`",
                    parse_mode="Markdown"
                )

            await self.executor.buy_token(
                token_address=token,
                amount=config.get("buy_amount", 0.1),
                wallet=self.wallet,
                override_filters=True,
                scanner_source="influencer"
            )

            record_result(token, "influencer", buy_score)

            log_trade_insight(
                token=token,
                action="buy",
                profit_pct=None,
                token_type=token_type,
                scanner="influencer",
                score=buy_score,
                strategy=getattr(self.executor, "strategy", "default"),
                confidence=None,
                meta_keywords=token_keywords
            )

    async def scan_recent_posts(self):
        cutoff = datetime.utcnow() - timedelta(minutes=MENTION_WINDOW_MINUTES)
        posts = await fetch_recent_posts()
        if not posts:
            return

        for post in posts:
            post_id = post.get("id")
            if not post_id or post_id in seen_posts:
                continue

            try:
                post_time = datetime.fromisoformat(post.get("timestamp"))
                if post_time < cutoff:
                    continue
            except:
                continue

            seen_posts.add(post_id)

    def analyze_social_signal(self, token: str, social_data: dict) -> int:
        score = 0
        posts = social_data.get("posts", [])
        for post in posts:
            content = post.get("text", "").lower()
            author = post.get("author", "")
            platform = post.get("platform", "")
            if any(k in content for k in INFLUENCER_KEYWORDS):
                score += 10
            if any(auth.lower() in author.lower() for auth in TRUSTED_INFLUENCERS):
                score += 20
            if platform in {"x", "telegram"}:
                score += 5
        return min(score, 100)


# === Helper: Stubbed fetch_social_data ===
async def fetch_social_data(token: str) -> dict:
    posts = await fetch_recent_posts()
    relevant = []
    for post in posts:
        content = f"{post.get('text', '')} {post.get('title', '')}".lower()
        if token.lower() in content:
            relevant.append(post)
    return {"posts": relevant}


# === Public Scoring ===
async def get_influencer_score(token_address: str) -> float:
    try:
        posts = await fetch_recent_posts()
        score = 0
        for post in posts:
            if token_address in post.get("text", "") or token_address in post.get("title", ""):
                if post.get("verified"):
                    score += 25
                elif post.get("platform") in {"x", "telegram"}:
                    score += 15
                else:
                    score += 5
        return min(score, 100)
    except Exception as e:
        logging.warning(f"[InfluencerScanner] Failed to get influencer score: {e}")
        return 0

def load_influencer_mentions() -> List[Dict]:
    if not os.path.exists(INFLUENCER_LOG_PATH):
        return []
    try:
        with open(INFLUENCER_LOG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []

def get_influencer_impact(token_address: str, minutes: int = 60) -> Dict:
    """
    Returns the impact score and related influencer posts for a specific token
    within the last `minutes`. Impact is based on post frequency and engagement.
    """
    mentions = load_influencer_mentions()
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)

    impact_score = 0
    posts = []

    for post in mentions:
        try:
            if post.get("token") != token_address:
                continue
            ts = datetime.fromisoformat(post.get("timestamp"))
            if ts >= cutoff:
                posts.append(post)
                likes = int(post.get("likes", 0))
                replies = int(post.get("replies", 0))
                reposts = int(post.get("reposts", 0))
                impact_score += 1 + (likes * 0.01) + (reposts * 0.05) + (replies * 0.02)
        except Exception:
            continue

    return {
        "impact_score": round(impact_score, 2),
        "mentions": posts
    }
