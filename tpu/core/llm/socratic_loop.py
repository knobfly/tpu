import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from core.llm.personality_core import init_personality_core
from utils.logger import log_event


class SocraticLoop:
    """
    Nyx's reflective reasoning engine.
    - Asks self clarifying questions when confidence is low.
    - Can escalate questions to the owner (Telegram).
    - Generates short reasoning audits.
    """

    def __init__(self):
        self.personality = init_personality_core()
        self._last_self_query: Optional[datetime] = None
        self._self_query_interval = timedelta(minutes=15)
        self._low_conf_threshold = 0.35
        self._losing_streak_trigger = 3

    async def evaluate(self, engine_insight: Dict, losing_streak: int) -> Optional[str]:
        """
        Decide whether to trigger a self-question or owner query.
        """
        conf = engine_insight.get("confidence", 1.0)
        token = engine_insight.get("token", "unknown token")

        # Check if self-reflection is needed
        if conf < self._low_conf_threshold or losing_streak >= self._losing_streak_trigger:
            now = datetime.utcnow()
            if not self._last_self_query or now - self._last_self_query > self._self_query_interval:
                self._last_self_query = now
                question = (
                    f"🤔 I'm uncertain about {token}. "
                    f"(confidence={conf:.2f}, losing_streak={losing_streak})\n"
                    "Should I tighten risk, switch strategy, or wait?"
                )
                log_event(f"[SocraticLoop] Triggered question: {question}")
                return question

        return None

    async def reasoning_audit(self, trade_context: Dict) -> str:
        """
        Generate a concise reasoning audit about a trade decision.
        """
        token = trade_context.get("token", "unknown token")
        action = trade_context.get("action", "hold?")
        score = trade_context.get("score", "n/a")
        reasons = ", ".join(trade_context.get("reasons", []))

        audit = (
            f"🔍 Reasoning Audit:\n"
            f"- Token: {token}\n"
            f"- Action: {action}\n"
            f"- Score: {score}\n"
            f"- Key Signals: {reasons}\n"
            f"- Mood: {self.personality.get_mood()}"
        )
        return audit

    async def self_reflection(self, event: str) -> str:
        """
        Perform an introspective reflection after a key event.
        """
        reflection = (
            f"🧠 Reflecting on: {event}\n"
            "What can I learn or adjust in my scoring logic?"
        )
        log_event(f"[SocraticLoop] Reflection triggered: {event}")
        return reflection


# Singleton
socratic_loop: SocraticLoop = None

def init_socratic_loop() -> SocraticLoop:
    global socratic_loop
    if socratic_loop is None:
        socratic_loop = SocraticLoop()
    return socratic_loop
