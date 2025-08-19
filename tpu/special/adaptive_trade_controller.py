# /adaptive_trade_controller.py
# Phase 6 — Reflex Intelligence + Micro-Adaptive Tuning
# Monitors live events (price/volume/liquidity) and adjusts buy/sell signals in real-time.

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Literal, Optional

from core.llm.style_evolution import style_evolution
from utils.logger import log_event


@dataclass
class ReflexState:
    token: str
    last_price: float = 0.0
    last_volume: float = 0.0
    buy_boost: float = 1.0     # Multiplier on buy confidence
    sell_boost: float = 1.0    # Multiplier on sell urgency
    last_update: float = time.time()
    reason: str = ""


class AdaptiveTradeController:
    """
    Watches token metrics in near-real-time and adjusts buy/sell scores.
    Integrates with trade_score_engine and snipe_score_engine.
    """

    def __init__(self):
        self.reflex_map: Dict[str, ReflexState] = {}
        self.price_spike_threshold = 0.05   # 5% sudden move
        self.volume_spike_threshold = 0.20  # 20% jump in volume
        self.cooldown_seconds = 15
        self.reflex_decay = 0.98            # soft decay per step
        self.callback: Optional[Callable[[str, ReflexState], None]] = None

    def set_callback(self, fn: Callable[[str, ReflexState], None]):
        """
        Provide a callback that will be invoked when reflex changes occur.
        Typically, this is used to feed updated confidence to the scoring engine.
        """
        self.callback = fn

    async def watch_token(self, token: str, price_feed, volume_feed):
        """
        Continuously monitor a token’s price & volume to detect reflex triggers.
        price_feed and volume_feed must be async callables returning floats.
        """
        if token not in self.reflex_map:
            self.reflex_map[token] = ReflexState(token=token)
        state = self.reflex_map[token]

        while True:
            try:
                current_price = await price_feed(token)
                current_volume = await volume_feed(token)

                self._update_reflex(token, current_price, current_volume)
            except Exception as e:
                log_event(f"[AdaptiveTradeController] Error for {token}: {e}")

            await asyncio.sleep(1.0)  # monitor every second

    def _update_reflex(self, token: str, price: float, volume: float):
        state = self.reflex_map.get(token)
        if not state:
            state = ReflexState(token=token)
            self.reflex_map[token] = state

        price_change = 0.0
        vol_change = 0.0
        now = time.time()

        if state.last_price > 0:
            price_change = (price - state.last_price) / state.last_price
        if state.last_volume > 0:
            vol_change = (volume - state.last_volume) / max(state.last_volume, 1e-6)

        state.last_price = price
        state.last_volume = volume

        # Reflex logic
        triggered = False
        reasons = []

        # Price spike (up)
        if price_change >= self.price_spike_threshold:
            state.buy_boost = min(state.buy_boost * 1.2, 3.0)
            reasons.append(f"Price spike +{price_change:.2%}")
            triggered = True
        # Price dump (down)
        elif price_change <= -self.price_spike_threshold:
            state.sell_boost = min(state.sell_boost * 1.3, 3.0)
            reasons.append(f"Price dump {price_change:.2%}")
            triggered = True

        # Volume spike
        if vol_change >= self.volume_spike_threshold:
            state.buy_boost = min(state.buy_boost * 1.15, 3.0)
            reasons.append(f"Volume spike +{vol_change:.2%}")
            triggered = True

        # Decay old boosts
        if now - state.last_update > self.cooldown_seconds:
            state.buy_boost = max(state.buy_boost * self.reflex_decay, 1.0)
            state.sell_boost = max(state.sell_boost * self.reflex_decay, 1.0)

        state.last_update = now
        state.reason = ", ".join(reasons)

        if triggered:
            log_event(f"[Reflex] {token}: buy_boost={state.buy_boost:.2f}, "
                      f"sell_boost={state.sell_boost:.2f}, reason={state.reason}")
            # Optionally adjust tone based on reflex events
            style_evolution().record_message_feedback(
                engagement=0.5,  # neutral
                sentiment=0.0,
                length_tokens=5,
                context="reflex_trigger"
            )
            if self.callback:
                self.callback(token, state)

    def get_reflex_for_token(self, token: str) -> Dict[str, Any]:
        state = self.reflex_map.get(token)
        if not state:
            return {"buy_boost": 1.0, "sell_boost": 1.0, "reason": ""}
        return asdict(state)

    def reset_token_reflex(self, token: str):
        if token in self.reflex_map:
            self.reflex_map[token] = ReflexState(token=token)


# ------------- Singleton-style helper ----------------

_CONTROLLER: Optional[AdaptiveTradeController] = None


def adaptive_controller() -> AdaptiveTradeController:
    global _CONTROLLER
    if _CONTROLLER is None:
        _CONTROLLER = AdaptiveTradeController()
    return _CONTROLLER
