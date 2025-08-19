import logging

import aiohttp
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import tag_token_result

RUGCHECK_API_URL = "https://api.rugcheck.xyz/token/{}"
CACHE = {}
TIMEOUT = 10  # seconds

async def check_token_rug_score(token_address: str) -> dict:
    if token_address in CACHE:
        return CACHE[token_address]

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            async with session.get(RUGCHECK_API_URL.format(token_address)) as resp:
                if resp.status != 200:
                    logging.warning(f"⚠️ RugCheck API failed for {token_address} — Status {resp.status}")
                    return {}

                result = await resp.json()
                CACHE[token_address] = result

                # Scoring + Tagging
                score = result.get("score")
                risk = result.get("risk", "").lower()

                if score is not None:
                    log_scanner_insight(
                        token=token_address,
                        source="rugcheck_api",
                        sentiment=1.0 - (score / 100),
                        volume=0.0,
                        result=risk
                    )

                    if "high" in risk or score < 50:
                        tag_token_result(token_address, "high_rug_score")

                return result
    except Exception as e:
        logging.error(f"❌ RugCheck API error for {token_address}: {e}")
        return {}

def interpret_rug_score(result: dict) -> str:
    if not result:
        return "unknown"

    score = result.get("score", 0)
    risk = result.get("risk", "").lower()

    if score >= 80 and "low" in risk:
        return "safe"
    elif score >= 50 and "medium" in risk:
        return "moderate"
    elif score < 50 or "high" in risk:
        return "risky"
    return "unknown"
