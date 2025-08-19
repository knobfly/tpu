# modules/token_theme_profiler.py

import logging

from openai import AsyncOpenAI

import json
from datetime import datetime

from core.live_config import config
from special.insight_logger import log_scanner_insight
from special.reverse_learning import record_token_theme_outcome
from utils.logger import log_event
from utils.service_status import update_status
aclient = AsyncOpenAI(api_key=config.get("openai_api_key"))


# === LLM Theme Profiler ===
async def get_token_theme_profile(name: str, symbol: str, description: str = "") -> dict:
    update_status("token_theme_profiler")

    prompt = f"""
You are an expert crypto trend analyst.
Analyze the theme and market positioning of this token.

Token Name: {name}
Symbol: {symbol}
Description: {description or "N/A"}

Return ONLY a JSON object with:
- sector: one of meme, ai, defi, gaming, nft, tool, launchpad, scam, pumpfun, community, unknown
- sub_sector: optional deep category (e.g. elon clone, pepe meme, L2 infra)
- emotion: primary driver (e.g. hype, humor, loyalty, fomo, greed, utility)
- trend_alignment: low / medium / high (market fit)
- predicted_lifespan_days: estimated days it will trend (1–30)
- comment: short 1-sentence market summary
"""

    try:
        logging.info(f"[ThemeProfiler] Analyzing {name} / {symbol}")
        response = await aclient.chat.completions.create(model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.45,
        max_tokens=250)

        result = response.choices[0].message.content.strip()
        json_str = result[result.find("{"): result.rfind("}") + 1]
        parsed = json.loads(json_str)

        # Log and inject into insights
        log_event(f"[ThemeProfiler] 🧠 {name}/{symbol}: {parsed.get('sector')} | {parsed.get('emotion')}")
        log_scanner_insight("theme_profile", {
            "name": name,
            "symbol": symbol,
            **parsed,
            "timestamp": datetime.utcnow().isoformat()
        })

        # Feed into long-term AI memory
        record_token_theme_outcome(name, symbol, parsed)

        return parsed

    except Exception as e:
        logging.warning(f"[ThemeProfiler] ❌ GPT theme analysis failed: {e}")
        return {}
