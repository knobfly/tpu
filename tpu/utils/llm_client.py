# modules/utils/llm_client.py
import asyncio
import logging
import os
from typing import Dict, List, Optional

import aiohttp

DEFAULT_MODEL = os.getenv("NYX_LLM_MODEL", "gpt-4o-mini")  # override in config.json if you want
DEFAULT_URL   = os.getenv("NYX_LLM_ENDPOINT", "https://api.openai.com/v1/chat/completions")

class LLMClient:
    def __init__(self, api_key: Optional[str], base_url: Optional[str] = None, model: Optional[str] = None, timeout: int = 60):
        self.api_key  = api_key
        self.base_url = base_url or DEFAULT_URL
        self.model    = model or DEFAULT_MODEL
        self.timeout  = timeout

    async def chat(self, messages: List[Dict], temperature: float = 0.4, max_tokens: int = 1024, stop: Optional[List[str]] = None) -> str:
        headers = {
            "Content-Type": "application/json",
        }
        if "openai" in self.base_url and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if stop:
            payload["stop"] = stop

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
            async with session.post(self.base_url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    logging.error(f"[LLMClient] HTTP {resp.status}: {txt}")
                    raise RuntimeError(f"LLM call failed ({resp.status})")
                data = await resp.json()
                # OpenAI-compatible schema
                try:
                    return data["choices"][0]["message"]["content"].strip()
                except Exception:
                    logging.error(f"[LLMClient] Bad schema: {data}")
                    return ""
