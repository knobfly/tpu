# /llm_interface.py
import logging
from core.live_config import config

from openai import AsyncOpenAI

aclient = AsyncOpenAI(api_key=config.get("openai_api_key"))


# === [1] Meta Theme Analyzer (for trending keyword clusters) ===
async def gpt_analyze_trend_theme(keywords: list) -> str:
    prompt = f"""
You are a crypto market intelligence AI. Given these trending token keywords:

{", ".join(keywords)}

What is the most likely dominant theme? (e.g., meme coins, AI hype, anime meta, launchpad wave)

Respond in one clear sentence.
"""
    try:
        response = await aclient.chat.completions.create(model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5)
        return response.choices[0].message.content.strip()

    except Exception as e:
        logging.warning(f"[LLM] Theme analysis failed: {e}")
        return "Unable to determine theme."


# === [2] Token Meta Labeler ===
async def gpt_label_token_theme(name: str, metadata: dict = None) -> str:
    prompt = f"""
Analyze the following token name and metadata to guess its market narrative (e.g., meme, elon, AI, anime, utility, staking, community).

Token: {name}
"""
    if metadata:
        prompt += f"Metadata: {metadata}\n"
    prompt += "Respond with a short category label only."

    try:
        response = await aclient.chat.completions.create(model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5)
        return response.choices[0].message["content"].strip()
    except Exception as e:
        logging.warning(f"[LLM] Token theme labeling failed: {e}")
        return "unknown"


# === [3] Token Name Pump Score ===
async def gpt_score_token_name(name: str, metadata: dict = None) -> float:
    prompt = f"""
Rate this token's name and theme on a scale of 0 to 100 for how likely it is to attract hype and pump.

Token: {name}
"""
    if metadata:
        prompt += f"Metadata: {metadata}\n"
    prompt += "Respond with a single number from 0 to 100 only."

    try:
        response = await aclient.chat.completions.create(model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6)
        text = response.choices[0].message["content"].strip()
        score = float(text)
        return max(0, min(100, score))  # Clamp
    except Exception as e:
        logging.warning(f"[LLM] Token score failed: {e}")
        return 0.0


# === [4] Trade Exit Estimator ===
async def gpt_predict_exit_window(symbol: str, early_data: dict) -> str:
    prompt = f"""
You are an exit strategy AI. A token named {symbol} launched with:

- Volume: {early_data.get("volume")}
- Liquidity: {early_data.get("liquidity")}
- Mentions: {early_data.get("social_mentions")}
- Holders: {early_data.get("holders")}

Suggest a smart exit window in hours or a condition (e.g., "exit when volume fades" or "hold max 3h unless it hits 2x").
"""
    try:
        response = await aclient.chat.completions.create(model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6)
        return response.choices[0].message.content.strip()

    except Exception as e:
        logging.warning(f"[LLM] Exit window prediction failed: {e}")
        return "No exit advice available."


# === [5] Trade Journal Recap ===
async def gpt_journal_trade(token: str, decision: str, result: str, reason: list) -> str:
    prompt = f"""
Summarize this trade in a smart but witty 1-2 sentence recap.

Token: {token}
Action Taken: {decision}
Final Result: {result}
Rationale: {", ".join(reason)}
"""
    try:
        response = await aclient.chat.completions.create(model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7)
        return response.choices[0].message.content.strip()

    except Exception as e:
        logging.warning(f"[LLM] Trade journal recap failed: {e}")
        return "No trade summary available."
