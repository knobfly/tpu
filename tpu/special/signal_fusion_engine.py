# /signal_fusion_engine.py
# Phase 8 — Cross-Signal Intelligence & Fusion
# Combines wallet signals, chart patterns, sentiment, and reflex data into a single unified score.

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional

from core.llm.style_evolution import style_evolution
from special.adaptive_trade_controller import adaptive_controller
from utils.logger import log_event

SignalType = Literal["wallet", "chart", "sentiment", "volume", "nft", "reflex", "meta"]


@dataclass
class SignalSnapshot:
    token: str
    timestamp: float
    signals: Dict[SignalType, float]
    fused_score: float
    reason_tags: List[str]


class SignalFusionEngine:
    """
    Collects signals from all subsystems, normalizes them, and fuses into a single decision score.
    Also tags trades with 'cause-effect' context for post-trade analysis.
    """

    def __init__(self):
        self.snapshots: Dict[str, SignalSnapshot] = {}
        self.weight_table = {
            "wallet": 0.25,
            "chart": 0.25,
            "sentiment": 0.15,
            "volume": 0.15,
            "nft": 0.05,
            "reflex": 0.10,
            "meta": 0.05,
        }
        self.history: List[SignalSnapshot] = []
        self.max_history = 500  # Keep last 500 events

    def ingest_signals(
        self,
        token: str,
        wallet_score: float,
        chart_score: float,
        sentiment_score: float,
        volume_score: float,
        nft_score: float = 0.0,
        meta_score: float = 0.0,
    ) -> SignalSnapshot:
        """
        Accept raw signal scores (0..1) from multiple subsystems for a given token.
        Automatically merges with reflex boosts and style cues.
        """
        reflex_data = adaptive_controller().get_reflex_for_token(token)
        reflex_boost = reflex_data.get("buy_boost", 1.0)
        reflex_signal = min(reflex_boost / 2.0, 1.0)  # compress reflex to 0..1 scale

        # Weighted fusion
        signals = {
            "wallet": self._clip(wallet_score),
            "chart": self._clip(chart_score),
            "sentiment": self._clip(sentiment_score),
            "volume": self._clip(volume_score),
            "nft": self._clip(nft_score),
            "reflex": reflex_signal,
            "meta": self._clip(meta_score),
        }

        fused = sum(self.weight_table[k] * signals[k] for k in self.weight_table)
        fused = self._clip(fused)

        # Build reason tags
        reasons = self._generate_reasons(signals, reflex_data)

        snap = SignalSnapshot(
            token=token,
            timestamp=time.time(),
            signals=signals,
            fused_score=fused,
            reason_tags=reasons,
        )
        self.snapshots[token] = snap
        self._add_to_history(snap)

        log_event(f"[SignalFusion] {token}: fused={fused:.3f}, reasons={', '.join(reasons)}")
        return snap

    def get_snapshot(self, token: str) -> Optional[SignalSnapshot]:
        return self.snapshots.get(token)

    def last_history(self, n: int = 10) -> List[SignalSnapshot]:
        return self.history[-n:]

    def top_tokens(self, n: int = 5) -> List[SignalSnapshot]:
        """
        Return the tokens with highest fused score in the last update.
        """
        return sorted(self.snapshots.values(), key=lambda s: s.fused_score, reverse=True)[:n]

    # ---------------- Internals ----------------

    def _add_to_history(self, snap: SignalSnapshot):
        self.history.append(snap)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def _generate_reasons(self, signals: Dict[SignalType, float], reflex_data: Dict[str, Any]) -> List[str]:
        reasons = []
        for k, v in signals.items():
            if v > 0.7:
                reasons.append(f"{k}_high")
            elif v < 0.3:
                reasons.append(f"{k}_low")
        if reflex_data.get("reason"):
            reasons.append(f"reflex:{reflex_data['reason']}")
        return reasons

    @staticmethod
    def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return hi if x > hi else lo if x < lo else x


# ---------------- Singleton Helpers ----------------

_ENGINE: Optional[SignalFusionEngine] = None


def signal_fusion_engine() -> SignalFusionEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = SignalFusionEngine()
    return _ENGINE
