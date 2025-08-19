# runtime/event_bus.py
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Set, Type

# ---- Event data classes (keep these available to import) -------------------

@dataclass
class TradeDecisionEvent:
    id: str
    ts: float
    token: str
    decision: str          # enter | skip | exit
    confidence: float
    fused_score: float
    signals: Dict[str, float]
    reason_tags: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class TradeOutcomeEvent:
    id: str
    ts: float
    token: str
    pnl: float
    holding_time_s: float
    strategy_type: str
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class MessageFeedbackEvent:
    ts: float
    channel: str           # telegram | x | internal
    engagement: float      # 0..1
    sentiment: float       # -1..1
    length_tokens: int
    content: str
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ProfileShiftEvent:
    ts: float
    profile: str
    throttles: Dict[str, float]
    market_heat: float
    system_stress: float

# ---- Helpers ---------------------------------------------------------------

def now() -> float:
    return time.time()

def new_decision_id(token: str) -> str:
    return f"{token}-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"

# ---- Unified EventBus ------------------------------------------------------

Handler = Callable[[Any], Any]  # may return coroutine or not

class EventBus:
    """
    Unified bus:
      - String events: subscribe("lp_unlock", handler), emit({"type":"lp_unlock", ...})
      - Typed events:  subscribe(TradeOutcomeEvent, handler), emit(TradeOutcomeEvent(...))
      - Decorators:    @event_bus().on("lp_unlock"), @event_bus().on_types(TradeOutcomeEvent)
    """
    def __init__(self):
        self._subs_by_str: Dict[str, Set[Handler]] = defaultdict(set)
        self._subs_by_type: Dict[Type, Set[Handler]] = defaultdict(set)

    # --- DECORATORS ---
    def on(self, *event_types: str):
        """Decorator for string event names."""
        def _decorator(fn: Handler):
            setattr(fn, "_event_types", list(event_types))
            # auto-register right away
            self.subscribe(fn)
            return fn
        return _decorator

    # --- SUBSCRIBE (flexible) ---
    def subscribe(self, event_key_or_handler: Any, handler: Handler | None = None):
        """
        - subscribe("event_name", handler)
        - subscribe(EventClass, handler)
        - subscribe(handler)  # wildcard (gets ALL events) or decorated @on/@on_types
        """
        # 1) Single-arg callable (either decorated OR wildcard)
        if handler is None and callable(event_key_or_handler):
            fn = event_key_or_handler
            str_types: List[str] = getattr(fn, "_event_types", []) or []
            class_types: List[Type] = getattr(fn, "_event_classes", []) or []
            if not str_types and not class_types:
                # no decorators → register as wildcard
                self._subs_by_str["*"].add(fn)
                return fn
            for t in str_types:
                self._subs_by_str[str(t)].add(fn)
            for cls in class_types:
                self._subs_by_type[cls].add(fn)
            return fn

        # 2) Normal (key, handler)
        key = event_key_or_handler
        if isinstance(key, str):
            self._subs_by_str[key].add(handler)  # type: ignore[arg-type]
        elif isinstance(key, type):
            self._subs_by_type[key].add(handler)  # type: ignore[arg-type]
        else:
            raise TypeError("First argument must be str (event name) or class (event type).")
        return handler

    # --- EMIT ---
    async def emit(self, event: Any):
        """
        Accepts:
          - dict with 'type'
          - dataclass/object (typed). Also mirrored to string listeners using
            event.type or ClassName.
        """
        typed_handlers: List[Handler] = []
        string_handlers: List[Handler] = []
        wildcard: Set[Handler] = self._subs_by_str.get("*", set())
        string_payload: Dict[str, Any] | None = None

        if isinstance(event, dict):
            et = event.get("type")
            if not et:
                logging.warning("[EventBus] Ignored dict event without 'type': %r", event)
                return
            string_handlers = list(self._subs_by_str.get(str(et), set()) | wildcard)
            string_payload = event
        else:
            evt_type = type(event)
            typed_handlers = list(self._subs_by_type.get(evt_type, set()))
            event_name = getattr(event, "type", evt_type.__name__)
            try:
                from dataclasses import asdict, is_dataclass
                payload = asdict(event) if is_dataclass(event) else dict(getattr(event, "__dict__", {}))
            except Exception:
                payload = {}
            payload["type"] = event_name
            string_payload = payload
            string_handlers = list(self._subs_by_str.get(str(event_name), set()) | wildcard)

        async def _call(h: Handler, data: Any):
            try:
                res = h(data)
                if asyncio.iscoroutine(res) or isinstance(res, Awaitable):
                    asyncio.create_task(res)
            except Exception as e:
                logging.warning("[EventBus] handler %r failed: %s", h, e)

        for h in typed_handlers:
            await _call(h, event)
        if string_payload is not None:
            for h in string_handlers:
                await _call(h, string_payload)

    def on_types(self, *event_classes: Type):
        """Decorator for typed events."""
        def _decorator(fn: Handler):
            setattr(fn, "_event_classes", list(event_classes))
            self.subscribe(fn)
            return fn
        return _decorator

    # --- UNSUBSCRIBE ---
    def unsubscribe(self, event_key: Any, handler: Handler):
        if isinstance(event_key, str):
            self._subs_by_str[str(event_key)].discard(handler)
        elif isinstance(event_key, type):
            self._subs_by_type[event_key].discard(handler)


# ---- Singleton -------------------------------------------------------------

_GLOBAL: Optional[EventBus] = None

def event_bus() -> EventBus:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = EventBus()
    return _GLOBAL
