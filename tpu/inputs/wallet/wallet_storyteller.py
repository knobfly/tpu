# modules/wallet_storyteller.py

import json
import logging

from openai import AsyncOpenAI

from datetime import datetime

from core.live_config import config
from special.insight_logger import log_scanner_insight
from special.reverse_learning import record_wallet_outcome  # Optional LLM learning
from utils.logger import log_event
from utils.service_status import update_status
aclient = AsyncOpenAI(api_key=config.get("openai_api_key"))


# === Generate GPT-Based Wallet Bio ===
async def generate_wallet_bio(wallet: str, trades: list) -> dict:
    update_status("wallet_storyteller")

    if not trades:
        return {
            "wallet": wallet,
            "nickname": "No Activity",
            "story": "This wallet hasn’t made any trades worth writing about yet."
        }

    try:
        lines = []
        for t in trades[-10:]:
            token = t.get("token", "UnknownToken")
            result = t.get("result", "unknown")
            score = t.get("score", "?")
            timestamp = t.get("time", "unknown")
            lines.append(f"- {token} | Outcome: {result} | Score: {score} | Time: {timestamp}")

        prompt = f"""
You are a clever and witty crypto biographer. Based on the wallet's recent trades, write a fun and insightful summary of this wallet's behavior. Give it a memorable nickname and a short story.

Wallet: {wallet}
Recent Trades:
{chr(10).join(lines)}

Return JSON with:
- wallet: wallet address
- nickname: something fun (e.g. The DJ, Paperhands Pete, The Whale Whisperer)
- story: short clever paragraph about how this wallet behaves

Only respond with valid JSON. No explanation.
"""

        response = await aclient.chat.completions.create(model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7)
        result = response.choices[0].message.content.strip()
        parsed = json.loads(result[result.find("{"): result.rfind("}") + 1])

        record_wallet_outcome(wallet, nickname=parsed.get("nickname"))

        log_event(f"[WalletStory] 📜 {wallet[:6]}... aka {parsed.get('nickname')}")
        log_scanner_insight(
            token="wallet_story",
            source="wallet_storyteller",
            sentiment=0.8,  # or whatever fits based on context
            volume=1,       # number of story events, or just 1
            result="story_generated",
            tags=[
                f"nickname:{parsed.get('nickname', 'unknown')}",
                "wallet_story"
            ]
        )
        return parsed

    except Exception as e:
        logging.warning(f"[WalletStory] ❌ GPT story generation failed: {e}")
        return {
            "wallet": wallet,
            "nickname": "Unknown",
            "story": "Unable to analyze this wallet at the moment."
        }

# === Fallback GPT-free Personality ===
def get_wallet_story(wallet_address: str, metadata: dict = None) -> str:
    metadata = metadata or {}

    tags = metadata.get("tags", [])
    score = metadata.get("score", 0)
    risk = metadata.get("risk", "unknown")

    if "gamble" in tags:
        personality = "degen gambler"
    elif "early buyer" in tags:
        personality = "alpha seeker"
    elif "paperhands" in tags:
        personality = "paperhanded coward"
    else:
        personality = "cautious sniper"

    if score > 85:
        tone = "This wallet is a straight-up apex hunter. It sees blood and goes in full throttle."
    elif score > 60:
        tone = "A cautious but calculated trader. Probably checks the contract twice."
    else:
        tone = "This wallet might be copy-trading its cat. Risky, erratic, but sometimes lucky."

    return (
        f"🧾 Wallet {wallet_address[:6]}...:\n"
        f"• Personality: {personality}\n"
        f"• Risk Rating: {risk}\n"
        f"• Score: {score}\n\n"
        f"{tone}"
    )
