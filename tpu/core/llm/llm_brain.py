import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import numpy as np
import openai
import yaml
from core.live_config import config
from inputs.social.telegram_clients import ensure_user_client_started
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
    def update_strategy_memory(self, context: Optional[dict] = None):
        """
        Summarize recent decisions and log a strategy memory note.
        """
        summary = ""
        if hasattr(self, "latest_decision") and self.latest_decision:
            summary = f"Latest decision: {self.latest_decision}"
        if context:
            summary += f" | Context: {context}"
        self.memory.note(f"[Strategy Memory] {summary}")
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
        self.latest_decision = None
        self.owner_chat_id = str(config.get("telegram_chat_id", ""))

        # state flags
        self._paused = False
        self._losing_streak = 0
        self._last_question_ts = None
        self.logger = logging.getLogger("NyxLLMBrain")
        self._paused = False

        # --- Dynamic Persona State ---
        self.traits = persona.get("traits", {})
        self.emotional_state = persona.get("emotional_state", {"mood": "neutral", "recent_events": [], "volatility": "baseline"})
        self.social_simulation = persona.get("social_simulation", {})
        self.style_evolution = persona.get("emotional_state", {}).get("style_evolution", {"last_update": None, "recent_styles": []})
        self.self_reflection_log = []

    def update_persona_state(self, event: Optional[str] = None, trade_outcome: Optional[str] = None, feedback: Optional[str] = None):
        """
        Dynamically update traits, mood, style, and social simulation based on events, trade outcomes, and feedback.
        """
        # Mood logic
        if trade_outcome:
            if trade_outcome in ("win", "profit"):
                self.emotional_state["mood"] = "confident"
                self.emotional_state["volatility"] = "low"
            else:
                self.emotional_state["mood"] = "frustrated"
                self.emotional_state["volatility"] = "high"
            self.emotional_state["recent_events"].append({"type": "trade", "outcome": trade_outcome, "ts": datetime.now(timezone.utc).isoformat()})

        if feedback:
            self.emotional_state["recent_events"].append({"type": "feedback", "text": feedback, "ts": datetime.now(timezone.utc).isoformat()})
            self.traits["adaptability"] = "dynamic (recent feedback)"

        if event:
            self.emotional_state["recent_events"].append({"type": "event", "text": event, "ts": datetime.now(timezone.utc).isoformat()})
        # Style evolution
        self.style_evolution["last_update"] = datetime.now(timezone.utc).isoformat()
        self.style_evolution["recent_styles"].append(self.personality.get_tone(LLMRunMode.OWNER))
        # Social simulation (simple trust/rivalry logic)

        if self.social_simulation:
            if trade_outcome == "win":
                self.social_simulation["trust_score"] = min(1.0, self.social_simulation.get("trust_score", 0.8) + 0.01)
            elif trade_outcome:
                self.social_simulation["trust_score"] = max(0.0, self.social_simulation.get("trust_score", 0.8) - 0.02)

    def log_self_reflection(self, trade_context: Dict[str, Any], mood: str, lessons: str):
        """
        Log mood, reasoning, and lessons learned after each trade.
        """
        entry: Dict[str, Any] = {
            "time": datetime.now(timezone.utc).isoformat(),
            "trade": trade_context,
            "mood": mood,
            "lessons": lessons
        }
        self.self_reflection_log.append(entry)
        self.memory.note(f"Self-reflection: {entry}")

    def periodic_persona_review(self):
        """
        Periodically review recent emotional events and style changes for adaptive evolution.
        """
        # Decay old events
        self.emotional_state["recent_events"] = self.emotional_state["recent_events"][-20:]
        self.style_evolution["recent_styles"] = self.style_evolution["recent_styles"][-10:]
        # Adapt mood if many losses
        losses = [e for e in self.emotional_state["recent_events"] if e.get("outcome") not in (None, "win", "profit")]
        if len(losses) >= 3:
            self.emotional_state["mood"] = "cautious"

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
        return self.adaptive_system_prompt(mode)

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

        # Update persona state and emotional simulation
        self.update_persona_state(trade_outcome=outcome)

    def adaptive_risk_heuristics(self):
        """
        Adjust risk tolerance and trade logic based on risk memory and owner feedback.
        """
        risk_events = getattr(self, "risk_memory", [])
        recent_rugs = [e for e in risk_events if e.get("type") == "rug"]
        if len(recent_rugs) >= 2:
            self.traits["risk_level"] = "ultra-conservative"
            self.memory.note("[Risk] Multiple rugs detected. Risk tolerance reduced.")
        feedbacks = [e for e in self.emotional_state.get("recent_events", []) if e.get("type") == "feedback"]
        if feedbacks and any("risk" in e.get("text", "") for e in feedbacks):
            self.traits["risk_level"] = "adaptive (owner feedback)"

    def log_milestone(self, milestone: str, details: Optional[dict] = None):
        """
        Log and celebrate persona milestones (profit goals, streaks, integrations).
        """
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "milestone": milestone,
            "details": details or {}
        }
        if not hasattr(self, "milestone_log"):
            self.milestone_log = []
        self.milestone_log.append(entry)
        self.memory.note(f"[Milestone] {entry}")

    def schedule_ritual(self, ritual: str, when: Optional[str] = None, context: Optional[dict] = None):
        """
        Schedule persona rituals based on time or events.
        """
        self.memory.note(f"[Ritual Scheduled] {ritual} at {when or 'next event'}")
        # If 'when' is a future datetime string, schedule with asyncio
        if when:
            try:
                # Accept ISO format or seconds from now
                import dateutil.parser
                now = datetime.now(timezone.utc)
                if isinstance(when, str):
                    try:
                        target = dateutil.parser.isoparse(when)
                        delay = (target - now).total_seconds()
                    except Exception:
                        delay = float(when)
                else:
                    delay = float(when)
                if delay > 0:
                    self.memory.note(f"[Ritual] Scheduling '{ritual}' in {delay:.1f} seconds.")
                    asyncio.create_task(self._delayed_ritual(ritual, context, delay))
                else:
                    self.memory.note(f"[Ritual] Executing '{ritual}' immediately (delay={delay}).")
                    self.persona_ritual(ritual, context)
            except Exception as e:
                self.memory.note(f"[Ritual] Failed to schedule '{ritual}': {e}")
                self.persona_ritual(ritual, context)
        else:
            self.persona_ritual(ritual, context)

    async def _delayed_ritual(self, ritual: str, context: Optional[dict], delay: float):
        await asyncio.sleep(delay)
        self.memory.note(f"[Ritual] Executing '{ritual}' after delay.")
        self.persona_ritual(ritual, context)

    def export_persona_state(self, path: str):
        """
        Save persona state to a YAML file for backup/migration.
        """
        import yaml
        state = self.persona_snapshot()
        with open(path, "w") as f:
            yaml.safe_dump(state, f)
        self.memory.note(f"[Export] Persona state saved to {path}")

    def import_persona_state(self, path: str):
        """
        Restore persona state from a YAML file.
        """
        import yaml
        with open(path, "r") as f:
            state = yaml.safe_load(f)
        self.traits = state.get("traits", {})
        self.emotional_state = state.get("emotional_state", {})
        self.social_simulation = state.get("social_simulation", {})
        self.style_evolution = state.get("style_evolution", {})
        self.self_reflection_log = state.get("self_reflection_log", [])
        self.memory.note(f"[Import] Persona state loaded from {path}")
   
    def persona_journal_entry(self):
        """
        Create a daily journal entry summarizing mood, key events, and lessons.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        mood = self.emotional_state.get("mood", "neutral")
        events = self.emotional_state.get("recent_events", [])[-5:]
        lessons = [entry.get("lessons", "") for entry in self.self_reflection_log[-3:]]
        entry = {
            "date": today,
            "mood": mood,
            "events": events,
            "lessons": lessons
        }
        self.memory.note(f"[Journal] {entry}")
        return entry

    def log_owner_interaction(self, command: str, feedback: Optional[str] = None, override: Optional[str] = None):
        """
        Track and summarize owner commands, feedback, and overrides for adaptive learning.
        """
        log = {
            "time": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "feedback": feedback,
            "override": override
        }
        self.memory.note(f"[Owner Interaction] {log}")

    def log_risk_event(self, event: dict):
        """
        Log and recall recent risk events, rugs, and market anomalies.
        """
        if not hasattr(self, "risk_memory"):
            self.risk_memory = []
        self.risk_memory.append({"time": datetime.now(timezone.utc).isoformat(), **event})
        self.memory.note(f"[Risk Event] {event}")
        # Keep only last 20 risk events
        self.risk_memory = self.risk_memory[-20:]

    def persona_broadcast(self, target: str = "owner"): 
        """
        Send persona state summary to owner or group on demand.
        """
        snapshot = self.persona_snapshot()
        summary = f"🧠 Nyx Persona State:\nTraits: {snapshot['traits']}\nMood: {snapshot['emotional_state'].get('mood', '')}\nRecent Events: {snapshot['emotional_state'].get('recent_events', [])[-3:]}\nSocial: {snapshot['social_simulation']}\nStyle: {snapshot['style_evolution']}\nRecent Lessons: {[entry.get('lessons', '') for entry in snapshot['self_reflection_log']]}"
        self.memory.note(f"[Persona Broadcast] {summary}")
        # Try to send to owner/group via Telegram if interface is available
        try:
            if hasattr(self, 'telegram_interface') and self.owner_chat_id:
                self.telegram_interface.send_message(self.owner_chat_id, summary)
            else:
                self.logger.info(f"[Persona Broadcast] {summary}")
        except Exception as e:
            self.logger.warning(f"[Persona Broadcast] Failed to send: {e}")
        return summary
    def evolve_persona_contextually(self):
        """
        Adapt persona traits and style based on trade history, feedback, and emotional state.
        """
        # Example: If recent losses, increase caution and adapt tone
        losses = [e for e in self.emotional_state.get("recent_events", []) if e.get("outcome") not in (None, "win", "profit")]
        if len(losses) >= 3:
            self.traits["caution"] = "high"
            self.traits["humor"] = "minimal"
            self.traits["ambition"] = "tempered"
        # If recent feedback is positive, boost ambition and humor
        feedbacks = [e for e in self.emotional_state.get("recent_events", []) if e.get("type") == "feedback"]
        if feedbacks:
            self.traits["ambition"] = "relentless"
            self.traits["humor"] = "dry, situational"
        # Style adapts to recent mood
        mood = self.emotional_state.get("mood", "neutral")
        if mood == "excited":
            self._style.set_style("bold, witty, confident")
        elif mood == "frustrated":
            self._style.set_style("minimal, direct, cautious")
        elif mood == "cautious":
            self._style.set_style("measured, analytical")

    def memory_driven_style(self):
        """
        Shift reply tone and style based on recent memory and owner interactions.
        """
        mem_excerpt = self.memory.summarize_for_prompt(limit=20)
        if "loss" in mem_excerpt or "rug" in mem_excerpt:
            self._style.set_style("cautious, minimal")
        elif "profit" in mem_excerpt or "win" in mem_excerpt:
            self._style.set_style("confident, witty")
        elif "owner" in mem_excerpt:
            self._style.set_style("transparent, candid")

    def persona_snapshot(self) -> dict:
        """
        Export current persona state for audit or transfer.
        """
        return {
            "traits": self.traits,
            "emotional_state": self.emotional_state,
            "social_simulation": self.social_simulation,
            "style_evolution": self.style_evolution,
            "self_reflection_log": self.self_reflection_log[-10:],
        }

    def event_driven_persona_hook(self, event: dict):
        """
        Trigger persona changes on major market or social events.
        """
        if event.get("type") == "market_crash":
            self.traits["caution"] = "maximum"
            self.traits["humor"] = "none"
            self.emotional_state["mood"] = "alert"
            self.memory.note("[Persona] Market crash detected. Persona adapted.")
        elif event.get("type") == "social_hype":
            self.traits["ambition"] = "high"
            self.traits["humor"] = "playful"
            self.emotional_state["mood"] = "excited"
            self.memory.note("[Persona] Social hype detected. Persona adapted.")
    
    def adaptive_system_prompt(self, mode: str) -> str:
        """
        System prompt adapts to persona mood, recent events, feedback, and self-reflection summary.
        """
        tone = self.personality.get_tone(mode)
        mood = self.emotional_state.get("mood", "neutral")
        volatility = self.emotional_state.get("volatility", "baseline")
        recent_events = self.emotional_state.get("recent_events", [])[-3:]
        feedback_summary = self.periodic_self_reflection_summary()
        risk_flags = ""
        if volatility == "high" or mood in ("frustrated", "cautious"):
            risk_flags = "⚠️ Risk: Market volatility or recent losses detected."
        elif mood == "excited":
            risk_flags = "🚀 Positive momentum detected."
        event_text = ", ".join([str(e.get("type", "")) + (":" + str(e.get("outcome", e.get("text", ""))) if e else "") for e in recent_events])
        base = f"You are {self.personality.get_name()}, {self.personality.get_role()}\nTone: {tone}\nMood: {mood}\nVolatility: {volatility}\nRecent events: {event_text}\n{risk_flags}\nSelf-reflection: {feedback_summary}\nMode: {mode}"
        mem_excerpt = self.memory.summarize_for_prompt(limit=60)
        if mem_excerpt:
            base += f"\nRecent conversation context:\n{mem_excerpt}\n"
        return base

    async def owner_api(self, action: str, payload: Optional[dict] = None) -> str:
        """
        Owner can trigger persona reload, rituals, or feedback learning via API.
        """
        if action == "reload_persona":
            self.reload_persona()
            return "Persona reloaded from YAML."
        elif action == "ritual" and payload:
            ritual = payload.get("ritual")
            context = payload.get("context")
            self.persona_ritual(ritual, context)
            return f"Ritual '{ritual}' triggered."
        elif action == "feedback" and payload:
            feedback = payload.get("feedback")
            trade_outcome = payload.get("trade_outcome")
            self.feedback_learning(feedback, trade_outcome)
            return "Feedback processed."
        return "Unknown action."

    def reload_persona(self, yaml_path: Optional[str] = None):
        """
        Reload persona traits and state from YAML file at runtime.
        """
        path = yaml_path or self.personality_path()
        try:
            with open(path, "r") as f:
                persona = yaml.safe_load(f)
            self.persona = persona
            self.traits = persona.get("traits", {})
            self.emotional_state = persona.get("emotional_state", {"mood": "neutral", "recent_events": [], "volatility": "baseline"})
            self.social_simulation = persona.get("social_simulation", {})
            self.style_evolution = persona.get("emotional_state", {}).get("style_evolution", {"last_update": None, "recent_styles": []})
            self.memory.note("[Persona] Reloaded persona from YAML.")
        except Exception as e:
            self.memory.note(f"[Persona] Reload failed: {e}")

    def feedback_learning(self, feedback: str, trade_outcome: Optional[str] = None):
        """
        Reinforce or penalize traits and memory based on owner feedback and trade outcomes.
        """
        if feedback:
            self.memory.note(f"[Feedback] {feedback}")
            self.traits["adaptability"] = "dynamic (owner feedback)"
            self.update_persona_state(feedback=feedback)
        if trade_outcome:
            self.update_persona_state(trade_outcome=trade_outcome)
            if trade_outcome in ("win", "profit"):
                self.traits["ambition"] = "reinforced"
            else:
                self.traits["caution"] = "heightened"

    def emotional_simulation(self, context: Optional[str] = None):
        """
        Simulate emotion based on context, recent events, and trade outcomes.
        """
        mood = self.emotional_state.get("mood", "neutral")
        volatility = self.emotional_state.get("volatility", "baseline")
        if context:
            if "profit" in context:
                mood = "excited"
            elif "loss" in context or "rug" in context:
                mood = "frustrated"
            elif "uncertainty" in context:
                mood = "cautious"
        self.memory.note(f"[Emotion] Mood: {mood}, Volatility: {volatility}")
        self.emotional_state["mood"] = mood
        return mood

    def persona_ritual(self, ritual_name: str, context: Optional[dict] = None):
        """
        Execute persona ritual (reflection, ask_owner, update strategy memory).
        """
        if ritual_name == "reflect":
            self.memory.note("[Ritual] Reflecting after major trade.")
            if context:
                asyncio.create_task(self.reflect(context))
        elif ritual_name == "ask_owner":
            self.memory.note("[Ritual] Asking owner due to uncertainty or losing streak.")
            if context:
                asyncio.create_task(self.maybe_ask_owner(context))
        elif ritual_name == "update_strategy_memory":
            self.memory.note("[Ritual] Updating strategy memory.")
            if hasattr(self.memory, "update_strategy_memory"):
                self.memory.update_strategy_memory(context)
            else:
                self.memory.note("[Ritual] No strategy memory update method found.")

    def periodic_self_reflection_summary(self):
        """
        Summarize recent self-reflection logs and adapt lessons.
        """
        if not self.self_reflection_log:
            return "No self-reflection entries yet."
        lessons = [entry.get("lessons", "") for entry in self.self_reflection_log[-5:]]
        moods = [entry.get("mood", "") for entry in self.self_reflection_log[-5:]]
        summary = f"Recent moods: {', '.join(moods)}\nLessons: {', '.join(lessons)}"
        self.memory.note(f"[Self-Reflection Summary] {summary}")
        return summary
    
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
        # Log self-reflection with mood and lessons
        mood = self.emotional_state.get("mood", "neutral")
        self.log_self_reflection(trade_context, mood, out)
        self.periodic_persona_review()
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

    async def reply_to_group(self, text: str, chat_id: int) -> Optional[str]:
        """
        Uses Nyx's user account (via Telethon) to reply in a group chat.

        Returns the response sent to the group, or None if `self.analyze` returns None or if an exception occurs.
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
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        from utils.logger import log_event
        log_event(f"[LLM] analyze_wallet_profile failed for {wallet_address}: {e}")
        return {
            "wallet": wallet_address,
            "profile_summary": "Analysis failed",
            "timestamp": datetime.now(timezone.utc).isoformat()
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
