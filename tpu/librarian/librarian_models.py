# librarian_models.py
# Dataclasses and models for DataLibrarian (TokenRecord, WalletRecord, etc.)

from dataclasses import dataclass, field
from typing import Deque, Dict, Any, Set
from collections import deque
from librarian.librarian_config import MAX_TOKEN_EVENTS, MAX_WALLET_EVENTS

@dataclass
class TokenRecord:
    token: str
    last_ts: float = 0.0
    events: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_TOKEN_EVENTS))
    tags: Set[str] = field(default_factory=set)
    scores: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=256))
    chart: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    scanners: Set[str] = field(default_factory=set)

@dataclass
class WalletRecord:
    wallet: str
    last_ts: float = 0.0
    events: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_WALLET_EVENTS))
    reputation: float = 0.0
    tags: Set[str] = field(default_factory=set)
    clusters: Set[str] = field(default_factory=set)
    meta: Dict[str, Any] = field(default_factory=dict)
