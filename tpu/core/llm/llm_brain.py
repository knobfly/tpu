import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import openai
import yaml
from core.live_config import config
from core.llm.embedding_model import embed_text
from core.llm.lexicon_tracker import lexicon_tracker
from core.llm.personality_core import PersonalityCore, init_personality_core
from core.llm.socratic_loop import SocraticLoop, init_socratic_loop
from core.llm.style_evolution import init_style_evolution, style_evolution
from memory.conversation_memory import ConversationMemory
from utils.llm_client import LLMClient
from utils.logger import log_event


class LLMRunMode:
    PUBLIC  = "public"
    OWNER   = "owner"
    SYSTEM  = "system"

class NyxLLMBrain:
    """
    Nyx's high-level language brain:
    - Persona injection (via PersonalityCore)
    - Conversational memory
    - Reflection loops
    - Socratic reasoning (via SocraticLoop)
    - Asking owner for feedback
    - Contextual tone controls
    """

    def __init__(self, persona: Dict[str, Any], llm: LLMClient, memory: ConversationMemory):
        self.personality: PersonalityCore = init_personality_core()
        self.socratic: SocraticLoop = init_socratic_loop()
        self._style = init_style_evolution()

        self.persona = persona
        self.llm = llm
        self.memory = memory
        self.latest_decision: Optional[Dict] = None
        self.owner_chat_id = str(config.get("telegram_chat_id", ""))

        # state flags
        self._paused = False
        self._losing_streak = 0
        self._last_question_ts: Optional[str] = None
        self.logger = logging.getLogger("NyxLLMBrain")
        self._paused = False

    @classmethod
    def load(cls):
        with open(cls.personality_path(), "r") as f:
            persona = yaml.safe_load(f)
        llm = LLMClient(
            api_key=config.get("openai_api_key"),
            base_url=config.get("llm_base_url", None),
            model=config.get("llm_model", None),
            timeout=config.get("llm_timeout", 60)
        )
        memory = ConversationMemory(
            max_messages=config.get("memory_max_messages", 3000),
            ttl_days=config.get("memory_ttl_days", 30)
        )
        return cls(persona, llm, memory)

    def style_prompt() -> str:
        return _style.style_directives()

    @staticmethod
    def personality_path() -> str:
        return "/home/ubuntu/nyx/core/llm/nyx_persona.yaml"

    # === Control flags
    def attach_engine(self, engine):
        self.engine = engine

    def attach_librarian(self, librarian_instance):
        self.librarian = librarian_instance
        self.log("✅ LLM Librarian attached.")


    def log(self, msg: str):
        self.logger.info(msg)

    def attach_wallet(self, wallet_manager):
        self.wallet = wallet_manager
        self.log("🔗 LLM Attached wallet manager.")

    def pause(self):   self._paused = True
    def resume(self):  self._paused = False
    def is_paused(self) -> bool: return self._paused

    async def run(self):
        while True:
            if not self._paused:
                try:
                    await self._pulse()
                except Exception as e:
                    self.logger.warning(f"[LLMBrain] Pulse failed: {e}")
            await asyncio.sleep(90)  # slightly slower than AI brain

    async def _pulse(self):
        """
        Passive loop for Nyx's LLM memory syncing, theme tracking, and cleanup.
        """
        self.logger.info("🧠 LLM Pulse started")

        try:
            # 1. Refresh keyword embeddings or cache if applicable
            from core.llm.lexicon_tracker import lexicon_tracker
            lexicon_tracker()

            self.logger.info("✅ LLM Pulse complete")
        except Exception as e:
            self.logger.warning(f"[LLMBrain] Pulse inner task failed: {e}")

    async def generate_response(prompt: str) -> str:
        style_directive = style_evolution().style_directives()
        full_prompt = f"{style_directive}\n\n{prompt}"
        return await llm.generate(full_prompt)

     # === Persona + system prompt
    def _system_prompt(self, mode: str) -> str:
        tone = self.personality.get_tone(mode)
        ethics = ", ".join(self.personality.get_ethics())
        raw = self.personality.get_mission()
        mission = ", ".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item)
            for item in raw
        )
        identity_rules = ", ".join(self.personality.get_identity_rules())
        rituals = ", ".join(self.personality.get_rituals())
        query_back_rules = ", ".join(self.personality.get_query_back_rules())

        base = f"""You are {self.personality.get_name()}, {self.personality.get_role()}.
Tone: {tone}.
Ethics: {ethics}
Mission: {mission}
Identity rules: {identity_rules}
Rituals: {rituals}
Query rules: {query_back_rules}
Current Mood: {self.personality.get_mood()}

Mode: {mode}.
- If mode is 'public': speak with tone: {self.personality.get_tone('public')}
- If mode is 'owner': speak with tone: {self.personality.get_tone('owner')}
- Always include any critical risk flags in a concise bullet if relevant.
- NEVER leak private secrets.
"""

        # Append quick summary memory
        mem_excerpt = self.memory.summarize_for_prompt(limit=60)
        if mem_excerpt:
            base += f"\nRecent conversation context:\n{mem_excerpt}\n"
        return base

    async def reply(self, user_text: str, mode: str = LLMRunMode.OWNER, source: str = "telegram") -> str:
        if self._paused:
            return "🛑 I'm paused. (resume with /resume_brain)"

        self.memory.add("user", user_text, source=source)
        system_prompt = self._system_prompt(mode)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_text}
        ]
        out = await self.llm.chat(messages, temperature=0.5, max_tokens=1024)
        out = out.strip()
        if out:
            self.memory.add("assistant", out, source=source)
        return out

    async def explain_latest_decision(self) -> str:
        if not self.latest_decision:
            return "No decision to explain yet."
        jd = self.latest_decision
        text = (
            f"Token: {jd.get('token')}\n"
            f"Action: {jd.get('action')}\n"
            f"Score: {jd.get('score')}\n"
            f"Reasons: {', '.join(jd.get('reasons', []))}"
        )
        system = self._system_prompt(LLMRunMode.OWNER)
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": f"Explain this decision to me:\n{text}"}
        ]
        out = await self.llm.chat(messages, temperature=0.2)
        return out

    def log_trade_outcome(self, score: float, outcome: str):
        if outcome in ("win", "profit"):
            self._losing_streak = 0
        else:
            self._losing_streak += 1

    async def reflect(self, trade_context: Dict):
        """
        Called after big trades or batches. Writes a post-mortem and stores insights.
        """
        if not trade_context:
            return
        system = self._system_prompt(LLMRunMode.SYSTEM)
        user = (
            "Reflect on the following trade:\n"
            f"{trade_context}\n"
            "What went well, what went wrong, what will you change in scoring/tuning?"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]
        out = await self.llm.chat(messages, temperature=0.4, max_tokens=900)
        self.memory.note(f"Trade reflection: {out}")
        return out

    async def maybe_ask_owner(self, engine_insight: Dict) -> Optional[str]:
        """
        Triggered when confidence is low or losing streak grows.
        Uses SocraticLoop to generate questions.
        """
        q = await self.socratic.evaluate(engine_insight, self._losing_streak)
        if q:
            self.memory.add("assistant", q, source="owner_query")
            return q
        return None

    def capture_decision(self, token: str, action: str, score: float, reasons: List[str], ctx: Dict):
        self.latest_decision = {
            "time": datetime.utcnow().isoformat(),
            "token": token, "action": action, "score": score,
            "reasons": reasons, "context": ctx
        }

    # direct API for engine/telegram
    async def owner_say(self, text: str) -> str:
        return await self.reply(text, mode=LLMRunMode.OWNER)

    async def public_say(self, text: str) -> str:
        return await self.reply(text, mode=LLMRunMode.PUBLIC)

    def inject_identity(self) -> str:
        """
        A lightweight identity stub for places where you want
        a short brand stamp.
        """
        return f"🧠 *{self.personality.get_name()} activated.*\n"

    def enriched_personality_context():
        dynamic_vocab = lexicon_tracker().export_for_personality()
        return "Dynamic Vocabulary: " + ", ".join(dynamic_vocab[:10])

    async def reply_to_group(self, text: str, chat_id: int) -> str:
        """
        Uses Nyx's user account (via Telethon) to reply in a group chat.
        """
        try:
            response = await self.analyze(text)
            if not response:
            return None

            client = await ensure_user_client_started()
            await client.send_message(chat_id, response)
            return response
        except Exception as e:
            log_event(f"[LLMBrain] reply_to_group failed: {e}")
            return None



def detect_chart_trend(candles: list[dict]) -> dict:
    """
    Pure heuristic fallback (no LLM inference).
    Returns: {"type": str, "confidence": float}
    candles: [{"quotePrice": float, "volume": float, ...}, ...]
    """
    closes = [c.get("quotePrice") for c in candles if c.get("quotePrice") is not None]
    if len(closes) < 4:
        return {"type": "unknown", "confidence": 0.0}

    delta = closes[-1] - closes[0]
    pct = delta / closes[0] if closes[0] else 0.0
    if pct > 0.15:
        return {"type": "ascending", "confidence": min(1.0, abs(pct))}
    elif pct < -0.15:
        return {"type": "descending", "confidence": min(1.0, abs(pct))}
    else:
        return {"type": "flat", "confidence": 0.4}

# === Wallet Profile Analysis ===
async def analyze_wallet_profile(wallet_address: str, tx_history: list = None) -> dict:
    """
    Analyze a wallet's behavior using the LLM brain.
    Returns a structured dict with traits, sentiment, and profile summary.
    """
    try:
        # Build prompt context
        history_summary = ""
        if tx_history:
            history_summary = "\nRecent Transactions:\n" + "\n".join(
                f"- {tx.get('side', 'unknown').upper()} {tx.get('token', '')} @ {tx.get('price', '?')} on {tx.get('timestamp', '')}"
                for tx in tx_history[-10:]
            )

        prompt = (
            f"Analyze wallet {wallet_address}.\n"
            f"Describe its trading style, sentiment, and risk behavior.\n"
            f"Use available data:{history_summary}"
        )

        from llm.llm_interface import query_llm
        response = await query_llm(prompt)

        return {
            "wallet": wallet_address,
            "profile_summary": response.get("text", response) if isinstance(response, dict) else response,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        from utils.logger import log_event
        log_event(f"[LLM] analyze_wallet_profile failed for {wallet_address}: {e}")
        return {
            "wallet": wallet_address,
            "profile_summary": "Analysis failed",
            "timestamp": datetime.utcnow().isoformat()
        }

def get_llm_explanation(token: str) -> str:
    """
    Generate a short explanation of the AI's reasoning for a token.
    """
    from cortex.chart_cortex import ChartCortex
    from cortex.risk_cortex import RiskCortex
    from cortex.wallet_cortex import WalletCortex
    from librarian.data_librarian import librarian

    info = librarian.build_context(token)

    # Dummy Cortex for explanation
    risk = RiskCortex()
    chart = ChartCortex()
    wallet = WalletCortex()

    risk_score = risk.assess_token_risk(token)
    chart_score = chart.analyze_chart(token)
    wallet_score = wallet.analyze_wallets(info.get("wallets", []))

    return (
        f"🧠 *LLM Summary for {token}*\n"
        f"- Risk Score: {risk_score.get('score', '?')}\n"
        f"- Chart Signal: {chart_score.get('signal', '?')}\n"
        f"- Wallet Behavior: {wallet_score.get('profile', '?')}\n"
        f"\n"
        f"Summary:\n"
        f"The token has a {risk_score.get('score', '?')} risk score with key flags: {', '.join(risk_score.get('flags', [])) or 'None'}. "
        f"Chart shows {chart_score.get('signal', '?')} behavior. "
        f"Wallets appear {wallet_score.get('profile', '?')}."
    )

# === Strategy Rotation Log (Daily Summary) ===
def get_strategy_rotation_log() -> list[str]:
    """
    Return human-readable logs of recent strategy shifts.
    Used in daily summary reports or LLM insights.
    """
    from strategy.strategy_rotation import get_rotation_history

    history = get_rotation_history(limit=10)
    logs = []
    for entry in history:
        time = entry.get("timestamp", "unknown")
        old = entry.get("from", "N/A")
        new = entry.get("to", "N/A")
        reason = entry.get("reason", "manual")
        logs.append(f"• `{time}`: `{old}` → `{new}` _({reason})_")

    return logs


# simple singleton for convenience
llm_brain: NyxLLMBrain = None
def init_llm_brain() -> NyxLLMBrain:
    global llm_brain
    if llm_brain is None:
        llm_brain = NyxLLMBrain.load()
    return llm_brain
