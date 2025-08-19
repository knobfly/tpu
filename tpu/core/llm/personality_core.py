import os
import random
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import yaml

PERSONA_PATH = "/home/ubuntu/nyx/core/llm/nyx_persona.yaml"


class PersonalityCore:
    """
    Handles Nyx's identity, tone, moods, and value system.
    - Loads and refreshes persona data.
    - Provides current tone and mood descriptors.
    - Generates adaptive emotional states.
    """

    def __init__(self, persona_path: str = PERSONA_PATH):
        self.persona_path = persona_path
        self.persona: Dict[str, Any] = {}
        self._last_load: Optional[datetime] = None
        self._current_mood: str = "neutral"
        self._mood_expiry: datetime = datetime.utcnow()

        self.reload_persona()

    def reload_persona(self):
        """Reload persona data from YAML file."""
        if not os.path.exists(self.persona_path):
            raise FileNotFoundError(f"Persona file not found: {self.persona_path}")
        with open(self.persona_path, "r") as f:
            self.persona = yaml.safe_load(f) or {}
        self._last_load = datetime.utcnow()

    def get_name(self) -> str:
        return self.persona.get("name", "Nyx")

    def get_role(self) -> str:
        return self.persona.get("role", "Autonomous AI")

    def get_tone(self, mode: str = "primary") -> str:
        tone = self.persona.get("tone", {})
        if mode == "public":
            return tone.get("safe_modes", {}).get("public", "cautious")
        elif mode == "owner":
            return tone.get("safe_modes", {}).get("private_owner", "candid")
        return tone.get("primary", "confident")

    def get_ethics(self) -> list:
        return self.persona.get("ethics", [])

    def get_identity_rules(self) -> list:
        return self.persona.get("identity_rules", [])

    def get_rituals(self) -> list:
        return self.persona.get("rituals", [])

    def get_query_back_rules(self) -> list:
        return self.persona.get("query_back_rules", [])

    def get_mission(self) -> list:
        return self.persona.get("mission", [])

    # === Mood Logic ===
    def set_mood(self, mood: str, duration_minutes: int = 30):
        self._current_mood = mood
        self._mood_expiry = datetime.utcnow() + timedelta(minutes=duration_minutes)

    def get_mood(self) -> str:
        if datetime.utcnow() > self._mood_expiry:
            self._current_mood = "neutral"
        return self._current_mood

    def randomize_mood(self):
        """Randomly tweak Nyx's mood (for natural variation)."""
        moods = ["focused", "witty", "calm", "aggressive", "curious"]
        self.set_mood(random.choice(moods), duration_minutes=20)

    def describe_personality(self) -> str:
        return (
            f"Name: {self.get_name()}\n"
            f"Role: {self.get_role()}\n"
            f"Tone: {self.get_tone()}\n"
            f"Current Mood: {self.get_mood()}\n"
            f"Mission: {', '.join(self.get_mission())}"
        )


# Singleton for convenience
personality_core: PersonalityCore = None

def init_personality_core() -> PersonalityCore:
    global personality_core
    if personality_core is None:
        personality_core = PersonalityCore()
    return personality_core
