# modules/llm/style_evolution.py
# Phase 5 — Emergent Language & Style Evolution
# Drop-in, no placeholders. Persisted, self-tuning, LLM-hook friendly.

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Literal, Optional

StyleTone = Literal["sharp", "analytical", "dark_wit", "minimal", "teaching", "aggressive", "empathetic", "cryptic"]

STATE_PATH = os.environ.get("NYX_STYLE_STATE", "/home/ubuntu/nyx/runtime/nyx_data/style_state.json")
LOCK = threading.RLock()


@dataclass
class StyleState:
    # Core sliders (0..1)
    assertiveness: float = 0.65
    precision: float = 0.80
    wit: float = 0.40
    empathy: float = 0.15
    humility: float = 0.20
    aggression: float = 0.30
    verbosity: float = 0.45  # higher = longer replies

    # Tone weights (softmax normalized at runtime)
    tones: Dict[StyleTone, float] = field(default_factory=lambda: {
        "sharp": 0.25,
        "analytical": 0.30,
        "dark_wit": 0.15,
        "minimal": 0.05,
        "teaching": 0.10,
        "aggressive": 0.05,
        "empathetic": 0.05,
        "cryptic": 0.05,
    })

    # Running stats to drive adaptation
    win_streak: int = 0
    loss_streak: int = 0
    avg_confidence: float = 0.6  # smoothed model self-confidence
    engagement_score: float = 0.0  # smoothed (likes/replies/etc.)
    sentiment_score: float = 0.0   # smoothed external sentiment toward responses

    # Dynamic vocabulary memory
    vocab: Dict[str, Dict[str, float]] = field(default_factory=dict)  # phrase -> {"score": float, "last_seen": ts}

    last_update_ts: float = field(default_factory=time.time)
    version: int = 1


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return hi if x > hi else lo if x < lo else x


def _ewma(prev: float, new: float, alpha: float) -> float:
    return (alpha * new) + (1 - alpha) * prev


class StyleEvolution:
    def __init__(self, path: str = STATE_PATH):
        self.path = path
        self.state: StyleState = self._load_or_default()
        self.alpha_fast = 0.25
        self.alpha_slow = 0.05

    # --------------- public API ----------------

    def get_state(self) -> StyleState:
        with LOCK:
            return self.state

    def save(self) -> None:
        with LOCK:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(asdict(self.state), f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)

    def record_trade_feedback(
        self,
        *,
        pnl: float,
        confidence: float,
        was_early_exit: bool,
        was_rug: bool,
        latency_ms: float,
    ) -> None:
        """
        Feed every closed trade here. We'll nudge style vars accordingly.
        """
        with LOCK:
            s = self.state

            # win / loss streaks
            if pnl > 0:
                s.win_streak += 1
                s.loss_streak = 0
            else:
                s.loss_streak += 1
                s.win_streak = 0

            # confidence smoothing
            s.avg_confidence = _ewma(s.avg_confidence, _clip(confidence), self.alpha_fast)

            # aggression/precision tuning
            if s.loss_streak >= 3:
                # More humble & precise when repeatedly wrong
                s.humility = _clip(s.humility + 0.05, 0, 1)
                s.assertiveness = _clip(s.assertiveness - 0.05, 0, 1)
                s.aggression = _clip(s.aggression - 0.05, 0, 1)
                s.precision = _clip(s.precision + 0.05, 0, 1)
                self._shift_tone({"minimal": +0.05, "analytical": +0.05, "aggressive": -0.05, "dark_wit": -0.02})
            elif s.win_streak >= 3:
                # Sharper, more confident tone when on fire — but don't let arrogance explode
                s.assertiveness = _clip(s.assertiveness + 0.03, 0, 1)
                s.aggression = _clip(s.aggression + 0.02, 0, 1)
                s.wit = _clip(s.wit + 0.02, 0, 1)
                s.humility = _clip(s.humility - 0.02, 0, 1)
                self._shift_tone({"sharp": +0.05, "dark_wit": +0.02, "empathetic": -0.02})

            # early exit / rugs -> more caution & empathy when explaining
            if was_early_exit or was_rug or pnl < 0:
                s.empathy = _clip(s.empathy + 0.03, 0, 1)
                self._shift_tone({"teaching": +0.03})

            # verbosity auto-fit to latency and pnl volatility
            desired_verbosity = 0.35 if latency_ms < 100 else 0.55
            s.verbosity = _ewma(s.verbosity, desired_verbosity, self.alpha_fast)

            s.last_update_ts = time.time()

        self.save()

    def record_message_feedback(
        self,
        *,
        engagement: float,   # normalized 0..1
        sentiment: float,    # -1..1 (we'll normalize)
        length_tokens: int,
        context: str = "",
    ) -> None:
        """
        Called after a public/private message to update emergent language prefs.
        """
        with LOCK:
            s = self.state

            # normalize sentiment to 0..1
            sentiment_n = (sentiment + 1.0) / 2.0
            s.engagement_score = _ewma(s.engagement_score, _clip(engagement), self.alpha_fast)
            s.sentiment_score = _ewma(s.sentiment_score, _clip(sentiment_n), self.alpha_fast)

            # If engagement high, reinforce the tones
            if engagement > 0.6:
                self._shift_tone({"dark_wit": +0.02, "sharp": +0.02, "analytical": +0.02})
                s.wit = _clip(s.wit + 0.01, 0, 1)
            # If people react poorly, lean more minimal + teaching
            if sentiment < 0:
                self._shift_tone({"minimal": +0.03, "teaching": +0.02, "aggressive": -0.03})
                s.aggression = _clip(s.aggression - 0.02, 0, 1)
                s.empathy = _clip(s.empathy + 0.02, 0, 1)

            # Auto-adjust verbosity target to keep good engagement per token
            desired_verbosity = 0.45 + 0.20 * (0.5 - abs(engagement - 0.5))  # center engagement yields more detail
            s.verbosity = _ewma(s.verbosity, _clip(desired_verbosity), self.alpha_slow)

            # Lightly nudge humility based on negative sentiment
            if sentiment < -0.3:
                s.humility = _clip(s.humility + 0.02, 0, 1)

            s.last_update_ts = time.time()

        self.save()

    def record_new_phrase(self, phrase: str, impact: float = 0.1) -> None:
        """
        Add or reinforce an emergent phrase. Impact can be negative to decay.
        """
        phrase = phrase.strip()
        if not phrase:
            return
        now = time.time()
        with LOCK:
            entry = self.state.vocab.get(phrase, {"score": 0.0, "last_seen": now})
            entry["score"] = float(_clip(entry["score"] + impact, -1.0, 5.0))
            entry["last_seen"] = now
            self.state.vocab[phrase] = entry
            self.state.last_update_ts = now
        self.save()

    def decay(self, half_life_hours: float = 72.0) -> None:
        """
        Periodically reduce extremes; keeps style from drifting too far.
        """
        with LOCK:
            s = self.state
            now = time.time()
            # soft decay to center (0.5) for sliders
            def decay_slider(value: float, rate: float = 0.01) -> float:
                return _clip(value + rate * (0.5 - value), 0, 1)

            s.assertiveness = decay_slider(s.assertiveness)
            s.precision = decay_slider(s.precision)
            s.wit = decay_slider(s.wit)
            s.empathy = decay_slider(s.empathy)
            s.humility = decay_slider(s.humility)
            s.aggression = decay_slider(s.aggression)
            s.verbosity = decay_slider(s.verbosity)

            # decay vocab
            hl = half_life_hours * 3600.0
            for k, v in list(s.vocab.items()):
                age = now - v.get("last_seen", now)
                if age > 7 * 24 * 3600:  # hard prune after a week unused
                    del s.vocab[k]
                    continue
                decay_factor = math.exp(-age / hl)
                v["score"] *= decay_factor
                if v["score"] < 0.01:
                    del s.vocab[k]

            # tone normalization
            s.tones = self._normalize_tones(s.tones)
            s.last_update_ts = now

        self.save()

    def style_directives(self) -> str:
        """
        Produce a compact style control string for the LLM system prompt.
        Call this from your llm_brain before composing the final system message.
        """
        with LOCK:
            s = self.state
            tones = self._sorted_tones(s.tones)
            tone_str = ", ".join([f"{t}:{w:.2f}" for t, w in tones])
            vocab_snips = self._top_vocab_snippets(10)

            return (
                "## STYLE_CONTROL\n"
                f"- assertiveness: {s.assertiveness:.2f}\n"
                f"- precision: {s.precision:.2f}\n"
                f"- wit: {s.wit:.2f}\n"
                f"- empathy: {s.empathy:.2f}\n"
                f"- humility: {s.humility:.2f}\n"
                f"- aggression: {s.aggression:.2f}\n"
                f"- verbosity: {s.verbosity:.2f}\n"
                f"- dominant_tones: {tone_str}\n"
                f"- preferred_vocab: {', '.join(vocab_snips)}\n"
                "### Rules\n"
                "- Match verbosity to `verbosity` (shorter when <0.5, longer when >0.5).\n"
                "- If aggression < 0.2, avoid harsh language; if > 0.6, you can be more direct.\n"
                "- Use wit only when wit > 0.3, and keep it razor-sharp.\n"
                "- Teach when empathy or teaching tone is high.\n"
                "- Be ultra precise if precision > 0.7.\n"
            )

    # --------------- internals ----------------

    def _shift_tone(self, deltas: Dict[StyleTone, float]) -> None:
        for t, d in deltas.items():
            if t not in self.state.tones:
                continue
            self.state.tones[t] = _clip(self.state.tones[t] + d, 0.0, 1.0)
        self.state.tones = self._normalize_tones(self.state.tones)

    @staticmethod
    def _normalize_tones(tones: Dict[StyleTone, float]) -> Dict[StyleTone, float]:
        s = sum(max(v, 0.0) for v in tones.values()) or 1.0
        return {k: max(v, 0.0) / s for k, v in tones.items()}

    @staticmethod
    def _sorted_tones(tones: Dict[StyleTone, float]) -> List[tuple[str, float]]:
        return sorted(tones.items(), key=lambda x: x[1], reverse=True)

    def _top_vocab_snippets(self, n: int) -> List[str]:
        if not self.state.vocab:
            return []
        return [p for p, v in sorted(self.state.vocab.items(), key=lambda x: x[1]["score"], reverse=True)[:n]]

    def _load_or_default(self) -> StyleState:
        if not os.path.exists(self.path):
            return StyleState()
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            # backward-compat tolerant load
            base = StyleState()
            for k, v in data.items():
                if hasattr(base, k):
                    setattr(base, k, v)
            # ensure tones normalized
            base.tones = self._normalize_tones(base.tones)
            return base
        except Exception:
            return StyleState()


# ------------------ Singleton-style helpers ------------------

_STYLE_EVOLUTION: Optional[StyleEvolution] = None


def init_style_evolution(path: str = STATE_PATH) -> StyleEvolution:
    global _STYLE_EVOLUTION
    if _STYLE_EVOLUTION is None:
        _STYLE_EVOLUTION = StyleEvolution(path)
    return _STYLE_EVOLUTION


def style_evolution() -> StyleEvolution:
    if _STYLE_EVOLUTION is None:
        raise RuntimeError("StyleEvolution not initialized. Call init_style_evolution() first.")
    return _STYLE_EVOLUTION
